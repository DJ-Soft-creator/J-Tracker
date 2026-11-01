/* Startzeit-Messung: wie lange braucht die App auf diesem Geraet bis sie
 * bedienbar ist - und woran liegt es?
 *
 * Gemessen wird ohne Eingriff in den Startablauf:
 *   - Navigation Timing (Antwort des Servers, DOM fertig, load),
 *   - „bedienbar“: #app ist sichtbar und window.config geladen (nach Login,
 *     Konfiguration und erstem Tab) - ein Start, der im Login endet, zaehlt nicht,
 *   - Resource Timing: Anzahl Skripte, Bytes aus dem Netz, wie viele aus einem
 *     Cache kamen, und die langsamsten API-Aufrufe der ersten Sekunden.
 *
 * Zeit im Hintergrund zaehlt nicht: iOS laedt eine PWA oft vor oder friert sie
 * beim App-Wechsel ein, requestAnimationFrame steht dann still. Ohne Abzug
 * stand dort „bedienbar nach 680 s“, obwohl der Nutzer nie so lange gewartet
 * hat. Abgezogen wird die Zeit zwischen visibilitychange hidden -> visible.
 *
 * Die letzten Starts liegen je Geraet im localStorage und stehen unter
 * Einstellungen -> Ueber Journl. Zusaetzlich geht je Start eine Zeile mit
 * reinen Zahlen an /api/diagnostics/startup (Server-Log), damit sich die
 * Werte vom iPhone auswerten lassen, ohne sie abzutippen. Inhalte werden nie
 * erfasst, bei API-Aufrufen nur der Pfad ohne Query.
 */
(function () {
  const STORAGE_KEY = 'journl.startup-timings';
  const KEEP = 20;
  const GIVE_UP_MS = 60000;

  // Hintergrundzeit seit Navigationsstart. Startet die Seite schon verborgen
  // (Vorladen), gilt sie ab 0 als verborgen.
  let hiddenMs = 0;
  let hiddenSince = document.visibilityState === 'hidden' ? 0 : null;
  const hiddenUntil = (now) => hiddenMs + (hiddenSince == null ? 0 : now - hiddenSince);
  document.addEventListener('visibilitychange', () => {
    const now = performance.now();
    if (document.visibilityState === 'hidden') {
      if (hiddenSince == null) hiddenSince = now;
    } else if (hiddenSince != null) {
      hiddenMs += now - hiddenSince;
      hiddenSince = null;
    }
  });

  const round = (value) => (Number.isFinite(value) ? Math.round(value) : null);

  function deviceClass() {
    const agent = navigator.userAgent || '';
    const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone === true;
    const kind = /iPhone/.test(agent) ? 'iPhone' : /iPad/.test(agent) ? 'iPad'
      : /Android/.test(agent) ? 'Android' : /Macintosh/.test(agent) ? 'Mac' : /Windows/.test(agent) ? 'Windows' : 'anderes';
    return `${kind}${standalone ? ' (PWA)' : ''}`;
  }

  function stored() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]') || []; } catch (_) { return []; }
  }
  function store(entry) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify([entry, ...stored()].slice(0, KEEP))); } catch (_) { /* privat */ }
  }

  function collect(readyAt, hidden) {
    const nav = performance.getEntriesByType?.('navigation')?.[0];
    const resources = performance.getEntriesByType?.('resource') || [];
    const scripts = resources.filter((entry) => entry.initiatorType === 'script' || /\.js(\?|$)/.test(entry.name));
    // transferSize 0 = nichts uebers Netz (HTTP-Cache oder Service Worker).
    // Safari meldet bei Antworten aus dem Service Worker auch decodedBodySize 0 -
    // die fruehere Bedingung „decodedBodySize > 0“ zeigte deshalb immer 0/68.
    const fromCache = scripts.filter((entry) => entry.transferSize === 0).length;
    const api = resources
      .filter((entry) => { try { return new URL(entry.name).pathname.startsWith('/api/') && entry.startTime < readyAt + 5000; } catch (_) { return false; } })
      .map((entry) => ({ path: new URL(entry.name).pathname.slice(0, 80), ms: round(entry.duration) }))
      .sort((a, b) => b.ms - a.ms)
      .slice(0, 5);
    return {
      at: new Date().toISOString(),
      device: deviceClass(),
      version: document.documentElement.dataset.appVersion || '',
      ready_ms: round(readyAt - hidden),
      hidden_ms: round(hidden),
      response_ms: round(nav?.responseEnd),
      dom_ms: round(nav?.domContentLoadedEventEnd),
      load_ms: round(nav?.loadEventEnd),
      scripts: scripts.length,
      scripts_cached: fromCache,
      network_kb: round(scripts.reduce((sum, entry) => sum + (entry.transferSize || 0), 0) / 1024),
      service_worker: Boolean(navigator.serviceWorker?.controller),
      api,
    };
  }

  function report(entry) {
    store(entry);
    render();
    // Erst wenn der Start durch ist - die Meldung soll ihn nicht selbst bremsen.
    setTimeout(() => {
      window.apiFetch?.('/api/diagnostics/startup', { method: 'POST', body: JSON.stringify(entry), timeoutMs: 10000 })
        .catch(() => {});
    }, 3000);
  }

  // „Bedienbar“: App sichtbar und Konfiguration geladen.
  const started = performance.now();
  function watch() {
    const app = document.getElementById('app');
    const login = document.getElementById('login-overlay');
    if (login && !login.classList.contains('hidden') && performance.now() > 1500) return; // Login: kein Messstart
    if (app && !app.classList.contains('hidden') && window.config) {
      const readyAt = performance.now();
      const hidden = hiddenUntil(readyAt);
      // load abwarten, damit load_ms und die Ressourcen vollstaendig sind.
      const finish = () => setTimeout(() => report(collect(readyAt, hidden)), 500);
      if (document.readyState === 'complete') finish();
      else window.addEventListener('load', finish, { once: true });
      return;
    }
    const now = performance.now();
    if (now - started - hiddenUntil(now) < GIVE_UP_MS) requestAnimationFrame(watch);
  }
  requestAnimationFrame(watch);

  // ─── Anzeige unter Einstellungen -> Ueber Journl ──────────────────
  const seconds = (ms) => (ms == null ? '–' : `${(ms / 1000).toFixed(1).replace('.', ',')} s`);
  function render() {
    const box = document.getElementById('settings-startup-timing');
    if (!box) return;
    const entries = stored();
    if (!entries.length) {
      box.innerHTML = '<p class="text-xs text-gray-500">Noch keine Messung auf diesem Gerät.</p>';
      return;
    }
    // Eine kleine Liste statt Tabelle: auf dem Handy passt sonst die
    // API-Spalte nicht mehr in die Breite. Alles als Text gesetzt.
    const list = document.createElement('ul');
    list.className = 'space-y-1.5';
    entries.slice(0, 10).forEach((entry) => {
      const when = new Date(entry.at);
      const label = Number.isNaN(when.getTime()) ? '' : new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(when);
      const slow = (entry.api || []).filter((item) => item.ms >= 300).map((item) => `${item.path} ${seconds(item.ms)}`);
      const item = document.createElement('li');
      item.className = 'rounded-md border border-gray-800 px-2 py-1.5 text-[11px] text-gray-400';
      const head = document.createElement('p');
      head.className = 'flex items-baseline justify-between gap-2';
      const time = document.createElement('span');
      time.textContent = label;
      const ready = document.createElement('span');
      ready.className = `tabular-nums text-sm font-medium ${entry.ready_ms > 4000 ? 'text-amber-300' : 'text-gray-100'}`;
      // Alte Eintraege ohne hidden_ms enthalten noch die Hintergrundzeit.
      const legacyBackground = entry.hidden_ms == null && entry.ready_ms >= GIVE_UP_MS;
      ready.textContent = legacyBackground ? 'im Hintergrund unterbrochen' : `bedienbar nach ${seconds(entry.ready_ms)}`;
      if (legacyBackground) ready.className = 'text-sm text-gray-500';
      head.append(time, ready);
      const detail = document.createElement('p');
      detail.className = 'tabular-nums text-gray-500';
      const background = entry.hidden_ms >= 1000 ? ` · ${seconds(entry.hidden_ms)} im Hintergrund nicht gezählt` : '';
      detail.textContent = `Server ${seconds(entry.response_ms)} · Skripte ${entry.scripts_cached}/${entry.scripts} aus Cache, ${entry.network_kb} KB übers Netz${background}`;
      item.append(head, detail);
      if (slow.length) {
        const api = document.createElement('p');
        api.className = 'break-words text-amber-300/80';
        api.textContent = `langsam: ${slow.join(', ')}`;
        item.appendChild(api);
      }
      list.appendChild(item);
    });
    box.textContent = '';
    const caption = document.createElement('p');
    caption.className = 'mb-1 text-xs text-gray-500';
    caption.textContent = `${entries[0].device || ''} · letzte ${Math.min(entries.length, 10)} Starts`;
    box.append(caption, list);
  }

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'about') render();
  });
  window.journlStartupTiming = { entries: stored, render };
})();
