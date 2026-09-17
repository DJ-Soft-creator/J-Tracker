/* Shared Whisper quality-step picker for every transcription control. */
(function () {
  const STORAGE_KEY = 'journl-whisper-model';
  const ENDPOINT = '/api/write-sessions/transcriptions/models';
  let catalogue = null;
  let pending = null;

  function stored() {
    try { return window.localStorage.getItem(STORAGE_KEY) || ''; }
    catch (_) { return ''; }
  }

  function remember(value) {
    try { window.localStorage.setItem(STORAGE_KEY, value); }
    catch (_) { /* Private-mode browsers keep the choice for this page only. */ }
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (character) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]
    ));
  }

  // The stored choice only counts while the service still offers that step.
  function currentModel() {
    const choice = stored();
    if (!catalogue) return choice;
    const known = catalogue.models.some((item) => item.id === choice);
    return known ? choice : (catalogue.default || '');
  }

  function optionsHtml() {
    if (!catalogue) return '';
    const selected = currentModel();
    return catalogue.models.map((item) => {
      const suffix = item.id === catalogue.default ? ' · Standard' : '';
      const label = `${item.label || item.id}${suffix}`;
      return `<option value="${escapeHtml(item.id)}"${item.id === selected ? ' selected' : ''} title="${escapeHtml(item.note || '')}">${escapeHtml(label)}</option>`;
    }).join('');
  }

  function fillSelect(select) {
    if (!catalogue) return;
    select.innerHTML = optionsHtml();
    select.value = currentModel();
    select.disabled = catalogue.models.length < 2;
    const note = select.dataset.whisperModelNote
      ? document.getElementById(select.dataset.whisperModelNote)
      : null;
    if (note) {
      const active = catalogue.models.find((item) => item.id === select.value);
      note.textContent = catalogue.available === false
        ? 'Whisper-Dienst nicht erreichbar – es gilt das konfigurierte Standardmodell.'
        : (active?.note || '');
    }
  }

  function fillAll(root) {
    (root || document).querySelectorAll('select[data-whisper-model]').forEach(fillSelect);
  }

  function load() {
    if (catalogue) return Promise.resolve(catalogue);
    if (pending) return pending;
    pending = fetch(ENDPOINT, { credentials: 'same-origin', cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error('unavailable'))))
      .then((data) => {
        catalogue = {
          default: data.default || '',
          available: data.available !== false,
          models: Array.isArray(data.models) ? data.models.filter((item) => item && item.id) : [],
        };
        fillAll();
        return catalogue;
      })
      .catch(() => {
        // Without a catalogue every control silently keeps the server default.
        catalogue = { default: '', available: false, models: [] };
        return catalogue;
      })
      .finally(() => { pending = null; });
    return pending;
  }

  document.addEventListener('change', (event) => {
    const select = event.target.closest ? event.target.closest('select[data-whisper-model]') : null;
    if (!select) return;
    remember(select.value);
    fillAll();
  });

  window.journlWhisperModel = function () {
    return currentModel() || undefined;
  };
  window.journlWhisperModels = {
    load,
    fill: fillAll,
    optionsHtml,
    catalogue: () => catalogue,
  };

  if (document.querySelector('select[data-whisper-model]')) load();
  else document.addEventListener('DOMContentLoaded', () => { if (document.querySelector('select[data-whisper-model]')) load(); });
})();
