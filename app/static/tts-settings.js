/* Vorlesen (TTS) im Einstellungen-Modal, Gruppe `write` (Burger → Schreiben).

   Anbieter, Modell und Stimme fuer „Text vorlesen“ im „⋯“-Menue des
   Schreiben-Tabs. Gespeichert wird in der `voice`-Sektion der config.json
   (`tts_provider_id`, `tts_model`, `tts_voice`) - global wie Anbieter und
   Templates, deshalb aendern nur Administratoren. Zur Auswahl stehen nur
   lokale Anbieter (D-015); die Modell-Liste kommt live vom gewaehlten
   Anbieter. Die Felder entstehen hier statt im Template, damit Desktop und
   Mobile das Markup nicht doppelt tragen.

   Gesendet werden nur die eigenen drei Felder: der Server mischt sie in den
   bestehenden Stand, die Aufnahme-Einstellungen bleiben unberuehrt. */
(function () {
  const container = document.getElementById('settings-tts');
  if (!container) return;

  const LABEL = 'block text-xs text-gray-400 mb-1';
  const SELECT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-60';

  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  let settings = null;
  let providers = [];
  let canEdit = false;
  let loaded = false;

  async function requestJson(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 20000, cache: 'no-store', ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen.');
    return data;
  }

  const field = (selector) => container.querySelector(selector);
  const setStatus = (text) => { const status = field('[data-status]'); if (status) status.textContent = text; };

  function modelOptions(models, selected) {
    const list = [...models];
    if (selected && !list.includes(selected)) list.unshift(selected);
    return list.map((name) =>
      `<option value="${escapeHtml(name)}"${name === selected ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('')
      || '<option value="">keine Modelle gefunden</option>';
  }

  // Leerer Anbieter heisst „wie die Aufnahme“ - der Server faellt dann auf
  // den Anbieter der Spracheingabe zurueck.
  function render(activeProvider) {
    const disabled = canEdit ? '' : ' disabled';
    container.innerHTML = `
      <p class="text-xs text-gray-500 mb-3">Für „Text vorlesen“ im ⋯-Menü: der Inhalt des Schreibfelds geht an diesen lokalen Sprach-Endpunkt.${canEdit ? '' : ' Ändern dürfen nur Administratoren.'}</p>
      <div class="space-y-3">
        <div>
          <label class="${LABEL}" for="settings-tts-provider">Schnittstelle</label>
          <select id="settings-tts-provider" class="${SELECT}"${disabled}>
            <option value="">wie Spracheingabe</option>
            ${providers.map((item) => `<option value="${escapeHtml(item.id)}"${item.id === activeProvider ? ' selected' : ''}>${escapeHtml(item.label || item.id)}</option>`).join('')}
          </select>
          ${providers.length ? '' : '<p class="text-xs text-gray-500 mt-1.5">Kein lokaler Anbieter eingerichtet (AI-Worker → Anbieter).</p>'}
        </div>
        <div>
          <label class="${LABEL}" for="settings-tts-model">Modell</label>
          <select id="settings-tts-model" class="${SELECT}"${disabled}><option>wird geladen…</option></select>
          <p data-models-error class="hidden text-xs text-amber-300/90 mt-1.5"></p>
        </div>
        <div>
          <label class="${LABEL}" for="settings-tts-voice">Stimme</label>
          <input id="settings-tts-voice" class="${SELECT} placeholder:text-gray-600" placeholder="alloy" value="${escapeHtml(settings.tts_voice || '')}"${disabled} />
        </div>
        <div class="flex items-center justify-between gap-3 pt-1">
          <button type="button" data-test class="bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold py-1.5 px-3 rounded transition">Probe hören</button>
          <span data-status class="text-xs text-gray-500" role="status" aria-live="polite"></span>
        </div>
      </div>`;
    field('#settings-tts-provider').addEventListener('change', async () => {
      await save({ tts_provider_id: field('#settings-tts-provider').value });
      loadModels();
    });
    field('#settings-tts-model').addEventListener('change', () => save({ tts_model: field('#settings-tts-model').value }));
    field('#settings-tts-voice').addEventListener('change', () => save({ tts_voice: field('#settings-tts-voice').value.trim() }));
    field('[data-test]').addEventListener('click', async () => {
      if (!window.journlSpeak) { setStatus('Vorlesen ist hier nicht verfügbar.'); return; }
      setStatus('Wird geladen…');
      try {
        await window.journlSpeak('Hallo, so klingt das Vorlesen.');
        setStatus('');
      } catch (error) { setStatus(error.message || 'Vorlesen fehlgeschlagen.'); }
    });
    loadModels();
  }

  async function loadModels() {
    const select = field('#settings-tts-model');
    const errorLine = field('[data-models-error]');
    const providerId = field('#settings-tts-provider').value || settings.provider_id || '';
    let models = [];
    let error = '';
    try {
      const data = await requestJson(`/api/voice/models?provider_id=${encodeURIComponent(providerId)}`);
      models = data.models || [];
      error = data.error || '';
    } catch (exc) { error = exc.message; }
    select.innerHTML = modelOptions(models, settings.tts_model);
    errorLine.textContent = error;
    errorLine.classList.toggle('hidden', !error);
  }

  async function save(patch) {
    setStatus('Wird gespeichert…');
    try {
      const data = await requestJson('/api/voice/settings', {
        method: 'PUT', body: JSON.stringify(patch), timeoutMs: 30000,
      });
      settings = data.settings;
      setStatus('Gespeichert');
    } catch (error) { setStatus(error.message || 'Nicht gespeichert.'); }
  }

  async function load() {
    try {
      const data = await requestJson('/api/voice/settings');
      settings = data.settings;
      providers = data.providers || [];
      canEdit = !!data.can_edit;
      render(settings.tts_provider_id || '');
      loaded = true;
    } catch (_) {
      container.innerHTML = '<p class="text-xs text-gray-500">Vorlese-Einstellungen nicht erreichbar.</p>';
    }
  }

  // /api/voice/settings fragt den Endpunkt live nach Modellen - erst laden,
  // wenn die Gruppe wirklich geoeffnet wird.
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'write') return;
    if (!loaded) load();
  });
}());
