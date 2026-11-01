/* Pushover im Einstellungen-Modal, Gruppe `notifications` (Burger → Benachrichtigungen).

   Jeder Nutzer verbindet sein eigenes Pushover-Konto (User-Key + API-Token
   einer eigenen Pushover-Anwendung) und waehlt die Ausloeser. Die
   Zugangsdaten kommen vom Server nur maskiert zurueck; ein leeres Feld beim
   Speichern behaelt den bisherigen Wert. Die Nachrichten selbst sind neutral
   (nur Thema und Anzahl) - siehe pushover.py. Mitgeschickt wird
   `location.origin`, damit der Link in der Nachricht auf genau die Adresse
   zeigt, unter der Journl hier geoeffnet ist. */
(function () {
  const container = document.getElementById('settings-pushover');
  if (!container) return;

  const LABEL = 'block text-xs text-gray-400 mb-1';
  const INPUT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder:text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500';
  const TRIGGERS = [
    ['reminders', 'Analyse-Erinnerungen', 'Hinweise der Journal-Analyse mit Datum'],
    ['results', 'Analyse-Ergebnisse', 'fertige Analysen und Zusammenfassungen'],
    ['errors', 'Fehler', 'fehlgeschlagene Analyse-Läufe, mit hoher Priorität'],
    ['family', 'Fällige Familienaufgaben', 'eine Sammelmeldung pro Tag'],
    ['tagging', 'Hashtags nachtragen', 'welche Meldungen, stellt der Job in der Journal-Analyse-Verwaltung ein'],
  ];

  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const field = (selector) => container.querySelector(selector);
  const setStatus = (text, isError) => {
    const status = field('[data-status]');
    if (!status) return;
    status.textContent = text;
    status.classList.toggle('text-red-400', !!isError);
    status.classList.toggle('text-gray-500', !isError);
  };

  async function requestJson(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 30000, cache: 'no-store', ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || 'Anfrage fehlgeschlagen.');
      error.data = data;
      throw error;
    }
    return data;
  }

  function lastResultText(view) {
    if (view.halted) return `Versand angehalten: ${view.halted} – bitte Zugangsdaten prüfen.`;
    const last = view.last_result;
    if (!last) return view.configured ? 'Eingerichtet, noch nichts gesendet.' : 'Noch nicht eingerichtet.';
    let when = last.at || '';
    try { when = new Date(last.at).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' }); } catch (_) { /* roh */ }
    return last.ok ? `Zuletzt gesendet: ${last.what || 'Nachricht'} · ${when}` : `Letzter Versuch fehlgeschlagen (${when}): ${last.error}`;
  }

  function render(view) {
    const triggers = TRIGGERS.map(([key, label, hint]) => `
      <label class="flex items-start gap-2 text-sm text-gray-200">
        <input type="checkbox" data-trigger="${key}" class="mt-0.5 accent-green-600"${view.triggers?.[key] !== false ? ' checked' : ''}>
        <span>${escapeHtml(label)}<span class="block text-xs text-gray-500">${escapeHtml(hint)}</span></span>
      </label>`).join('');
    container.innerHTML = `
      <p class="text-xs text-gray-500 mb-3">Push-Nachrichten über <span class="text-gray-300">Pushover</span>. Sie nennen nur das Thema und eine Anzahl – nie Journal-Inhalte. Ein Tipp öffnet Journl an der passenden Stelle.</p>
      <p data-last class="text-xs mb-3 ${view.halted || (view.last_result && !view.last_result.ok) ? 'text-amber-300/90' : 'text-gray-400'}">${escapeHtml(lastResultText(view))}</p>
      <div class="space-y-3">
        <div>
          <label class="${LABEL}" for="settings-pushover-user">User-Key</label>
          <input id="settings-pushover-user" autocomplete="off" spellcheck="false" class="${INPUT} font-mono" placeholder="${escapeHtml(view.user_key_masked || '30 Zeichen, aus pushover.net')}">
        </div>
        <div>
          <label class="${LABEL}" for="settings-pushover-token">API-Token der Anwendung</label>
          <input id="settings-pushover-token" autocomplete="off" spellcheck="false" class="${INPUT} font-mono" placeholder="${escapeHtml(view.app_token_masked || 'pushover.net → Create an Application')}">
          <p class="text-xs text-gray-500 mt-1.5">${view.configured ? 'Gespeichert. Leer lassen, um die bisherigen Werte zu behalten.' : 'Den User-Key zeigt pushover.net nach dem Login; den Token bekommst du unter „Create an Application/API Token“.'}</p>
        </div>
        <fieldset class="space-y-2 pt-1">
          <legend class="${LABEL}">Benachrichtigen bei</legend>
          ${triggers}
        </fieldset>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="${LABEL}" for="settings-pushover-quiet-start">Ruhezeit ab</label>
            <input id="settings-pushover-quiet-start" type="time" class="${INPUT}" value="${escapeHtml(view.quiet_start || '')}">
          </div>
          <div>
            <label class="${LABEL}" for="settings-pushover-quiet-end">bis</label>
            <input id="settings-pushover-quiet-end" type="time" class="${INPUT}" value="${escapeHtml(view.quiet_end || '')}">
          </div>
        </div>
        <p class="-mt-1 text-xs text-gray-500">Was in der Ruhezeit anfällt, kommt danach gebündelt. Leer = keine Ruhezeit.</p>
        <div>
          <label class="${LABEL}" for="settings-pushover-family-time">Familien-Sammelmeldung um</label>
          <input id="settings-pushover-family-time" type="time" class="${INPUT}" value="${escapeHtml(view.family_time || '')}">
        </div>
        <div class="flex flex-wrap items-center gap-2 pt-1">
          <button type="button" data-save class="bg-green-700/80 hover:bg-green-600 text-white text-sm font-semibold py-1.5 px-3 rounded transition">Speichern</button>
          <button type="button" data-test class="bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold py-1.5 px-3 rounded transition disabled:opacity-50"${view.configured ? '' : ' disabled'}>Testnachricht</button>
          ${view.configured ? '<button type="button" data-clear class="text-xs text-gray-500 hover:text-red-400 px-2 py-1.5">Verbindung entfernen</button>' : ''}
        </div>
        <span data-status class="block text-xs text-gray-500" role="status" aria-live="polite"></span>
      </div>`;
    field('[data-save]').addEventListener('click', save);
    field('[data-test]').addEventListener('click', test);
    field('[data-clear]')?.addEventListener('click', clear);
  }

  function payload() {
    const triggers = {};
    container.querySelectorAll('[data-trigger]').forEach((box) => { triggers[box.dataset.trigger] = box.checked; });
    return {
      user_key: field('#settings-pushover-user').value.trim(),
      app_token: field('#settings-pushover-token').value.trim(),
      triggers,
      quiet_start: field('#settings-pushover-quiet-start').value,
      quiet_end: field('#settings-pushover-quiet-end').value,
      family_time: field('#settings-pushover-family-time').value,
      base_url: window.location.origin,
    };
  }

  async function save() {
    setStatus('Speichere…');
    try {
      render(await requestJson('/api/pushover/settings', { method: 'PUT', body: JSON.stringify(payload()) }));
      setStatus('Gespeichert.');
    } catch (error) { setStatus(error.message, true); }
  }

  async function test() {
    setStatus('Sende Testnachricht…');
    try {
      render(await requestJson('/api/pushover/test', { method: 'POST', body: JSON.stringify({ base_url: window.location.origin }) }));
      setStatus('Testnachricht gesendet – kam sie an?');
    } catch (error) {
      if (error.data && 'configured' in error.data) render(error.data);
      setStatus(error.message, true);
    }
  }

  async function clear() {
    if (!window.confirm('Pushover-Verbindung entfernen? Es werden keine Nachrichten mehr gesendet.')) return;
    try {
      render(await requestJson('/api/pushover/settings', { method: 'PUT', body: JSON.stringify({ clear_credentials: true }) }));
      setStatus('Verbindung entfernt.');
    } catch (error) { setStatus(error.message, true); }
  }

  async function load() {
    try {
      render(await requestJson('/api/pushover/settings', { method: 'GET' }));
    } catch (_) {
      container.innerHTML = '<p class="text-xs text-gray-500">Benachrichtigungs-Einstellungen nicht erreichbar.</p>';
    }
  }

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'notifications') load();
  });
}());
