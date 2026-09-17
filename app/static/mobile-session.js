// AI maintenance contract: Sitzung der mobilen Shell - Startpruefung, Login,
// Logout. Laeuft nach mobile-app-core.js und nutzt dessen Zustand (csrfToken,
// currentUser, configPrefetch) sowie apiFetch/loadConfig; keine Journal-,
// Family- oder Brain-Logik hier.
    // ─── auth check on startup ───────────────────────────────────────
    // Merker der letzten erfolgreichen Anmeldung. Er entscheidet nur, was
    // waehrend des Kaltstarts zu sehen ist - Zugriff gibt weiterhin
    // ausschliesslich die Server-Session.
    const AUTH_HINT_KEY = 'journl.was-authenticated';

    function rememberAuthentication(authenticated) {
      try {
        if (authenticated) localStorage.setItem(AUTH_HINT_KEY, '1');
        else localStorage.removeItem(AUTH_HINT_KEY);
      } catch (_) { /* Privater Modus ohne Storage: dann eben ohne Merker. */ }
    }

    let wasAuthenticated = false;
    try { wasAuthenticated = localStorage.getItem(AUTH_HINT_KEY) === '1'; } catch (_) { /* s.o. */ }

    /* Die Sitzung haelt vier Wochen. Bis /api/health geantwortet hat die
       Login-Maske zu zeigen, kostet beim Start deshalb fast immer unnoetig
       Zeit - war die letzte Sitzung angemeldet, wird die App sofort
       aufgedeckt und nur bei einer Absage auf Login zurueckgestellt. */
    if (wasAuthenticated) {
      document.getElementById('login-overlay').classList.add('hidden');
      document.getElementById('app').classList.remove('hidden');
    }

    /* Config, CSRF-Token und Health haengen alle nur am Session-Cookie. Fuer
       eine bestehende Sitzung gehen sie zusammen raus, statt die Config erst
       nach der Health-Antwort zu holen: das spart beim Kaltstart eine volle
       Rundreise, bis Editor und Template-Auswahl bedienbar sind. */
    // Die Variable gehoert mobile-app-core.js, wo loadConfig() sie einloest.
    configPrefetch = wasAuthenticated
      ? fetch('/api/config', { credentials: 'include' }).catch(() => null)
      : null;

    (async function checkAuth() {
      try {
        const [res, csrfRes] = await Promise.all([
          fetch('/api/health', { credentials: 'include' }),
          fetch('/api/csrf-token', { credentials: 'include' }).catch(() => null),
        ]);
        const data = await res.json();
        if (data.authenticated) {
          currentUser = data.username || null;
          if (csrfRes && csrfRes.ok) {
            csrfToken = (await csrfRes.json()).csrf_token;
          } else {
            await refreshCsrfToken();
          }
          rememberAuthentication(true);
          showApp();
        } else {
          rememberAuthentication(false);
          showLogin();
        }
      } catch (err) {
        // Netzwerkfehler sagt nichts ueber die Sitzung aus, der Merker bleibt.
        showLogin();
      }
    })();

    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
    }

    function showLogin() {
      clearInterval(familyNotificationTimer);
      familyNotificationTimer = null;
      if (window.brainAuthenticatedUser && typeof window.clearBrainState === 'function') window.clearBrainState();
      window.brainAuthenticatedUser = null;
      document.getElementById('login-overlay').classList.remove('hidden');
      document.getElementById('app').classList.add('hidden');
    }

    function showApp() {
      const brainUserChanged = Boolean(currentUser && currentUser !== window.brainAuthenticatedUser);
      if (brainUserChanged && typeof window.clearBrainState === 'function') window.clearBrainState();
      window.brainAuthenticatedUser = currentUser || null;
      document.getElementById('login-overlay').classList.add('hidden');
      document.getElementById('app').classList.remove('hidden');
      if (currentUser) {
        const bu = document.getElementById('burger-username');
        if (bu) bu.textContent = currentUser;
        const headerUser = document.getElementById('header-username');
        if (headerUser) headerUser.textContent = currentUser;
      }
      loadConfig();
      loadProfile();
      checkFamilyNotifications();
      clearInterval(familyNotificationTimer);
      familyNotificationTimer = setInterval(checkFamilyNotifications, 60000);
    }

    // ─── login form ──────────────────────────────────────────────────
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('username').value;
      const password = document.getElementById('password').value;
      const errEl = document.getElementById('login-error');
      errEl.classList.add('hidden');

      const res = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password })
      });

      if (res.ok) {
        const data = await res.json();
        try {
          const hres = await fetch('/api/health', { credentials: 'include' });
          const hdata = await hres.json();
          if (!hres.ok || !hdata.authenticated) throw new Error('Session cookie was not accepted');
          currentUser = hdata.username || username;
          await refreshCsrfToken();
          rememberAuthentication(true);
          showApp();
          if (data.notice) showToast(data.notice, false);
        } catch {
          currentUser = null;
          errEl.textContent = 'Anmeldung fehlgeschlagen. Der Browser konnte keine Sitzung aufbauen.';
          errEl.classList.remove('hidden');
        }
      } else if (res.status === 423) {
        const data = await res.json().catch(() => ({}));
        const until = data.locked_until ? new Date(data.locked_until).toLocaleTimeString('de-DE') : '';
        errEl.textContent = 'Konto gesperrt. Bitte später erneut versuchen' + (until ? ' (bis ' + until + ' Uhr)' : '') + '.';
        errEl.classList.remove('hidden');
      } else if (res.status === 429) {
        errEl.textContent = 'Zu viele Versuche. Bitte später erneut versuchen.';
        errEl.classList.remove('hidden');
      } else {
        errEl.textContent = 'Falsche Zugangsdaten.';
        errEl.classList.remove('hidden');
      }
      setTimeout(() => errEl.classList.add('hidden'), 4000);
    });
