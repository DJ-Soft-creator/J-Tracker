/* Erweiterte Aufnahme im Schreiben-Tab.
 *
 * Das Sternchen-Mikrofon arbeitet immer mit dem lokalen KI-Server (MacMini,
 * OpenAI-kompatibel, WAV) - das alte Mikrofon links bleibt bei Whisper. Drei
 * Arten: „Nur Aufnahme“ (ablegen, spaeter an der Aufnahme per MacMini
 * transkribieren), „Transkription“ (Vorschau im Panel, final ins Feld) oder ein
 * Preset (Transkription plus Nachbearbeitung mit dem Prompt des Presets).
 *
 * Das wiederverwendbare Vorlesen (TTS, window.journlSpeak) liegt in
 * write-speech.js und haengt sich an window.journlVoice an.
 *
 * Der MacMini-ASR akzeptiert nur WAV (kein webm/m4a), deshalb nimmt die
 * Aufnahme rohe PCM-Samples ueber die Web-Audio-API und kodiert WAV im Browser.
 */
(function () {
  const field = document.getElementById('simple-input');
  const actions = document.querySelector('.write-session-actions');
  if (!field || !actions) return;
  const writeSession = window.journlWriteSession;
  if (!writeSession) return;

  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const toast = (message, isError) => window.showToast?.(message, !!isError);

  async function requestJson(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 180000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Sprachanfrage fehlgeschlagen');
    return data;
  }

  // ─── Zustand ──────────────────────────────────────────────────────
  let settings = null;
  let models = [];
  let providers = [];
  let resolvedProviderId = '';
  let canEdit = false;
  let recording = false;
  // Jede gestoppte Aufnahme wird ein eigener Job (sichern -> transkribieren ->
  // nachbearbeiten -> benennen), damit schon die naechste Aufnahme laufen
  // kann. Solange ein Job laeuft, ist Senden gesperrt: sonst laeuft der
  // fertige Text erst nach dem Absenden ein und landet im naechsten Eintrag.
  let jobs = [];
  // Gesichert wird sofort, alles Weitere strikt nacheinander: der MacMini
  // arbeitet eine Anfrage nach der anderen ab, parallele Jobs bremsten sich
  // gegenseitig aus. Dadurch kommt der Text auch in Aufnahme-Reihenfolge.
  let processChain = Promise.resolve();
  let idleStatus = 'Bereit';
  let audioContext = null;
  let mediaStream = null;
  let sourceNode = null;
  let processorNode = null;
  let muteNode = null;
  let samples = [];
  let sampleTotal = 0;
  let startedAt = 0;
  let tickTimer = null;
  let previewStart = 0;
  let previewText = '';
  let previewError = '';
  let lastTranscript = '';

  const PRESET_STORAGE_KEY = 'journl.voice.preset';
  // Art der Aufnahme: RECORD_ONLY = nur ablegen (spaeter per MacMini
  // transkribieren), '' = Transkription, sonst die Preset-Kennung
  // (Transkription plus Nachbearbeitung mit dem Prompt des Presets).
  const RECORD_ONLY = '__record__';
  let selectedPreset = '';
  try { selectedPreset = window.localStorage.getItem(PRESET_STORAGE_KEY) || ''; } catch (_) { /* privat */ }

  // ─── UI ───────────────────────────────────────────────────────────
  const icon = (paths, cls = 'h-4 w-4') => `<svg aria-hidden="true" viewBox="0 0 24 24" class="${cls}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  const ICON_GEAR = icon('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>');
  const ICON_CLOSE = icon('<path d="M6 6l12 12M18 6 6 18"/>', 'h-3.5 w-3.5');
  const ICON_EDIT = icon('<path d="M4 20h4L19 9l-4-4L4 16z"/>', 'h-3.5 w-3.5');
  const ICON_TRASH = icon('<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/>', 'h-3.5 w-3.5');
  const RECORD_IDLE = '<span class="block h-3.5 w-3.5 rounded-full bg-current"></span>';
  const RECORD_STOP = '<span class="block h-3.5 w-3.5 rounded-[3px] bg-current"></span>';
  // Ruhend grau mit gedaempft rotbraunem Punkt - erst die laufende Aufnahme
  // leuchtet rot, damit der Zustand auf einen Blick klar ist.
  const RECORD_IDLE_CLASSES = 'border-gray-700 bg-gray-800 text-red-900 hover:bg-gray-700 hover:text-red-800';
  const RECORD_LIVE_CLASSES = 'border-red-400 bg-red-600 text-white shadow-[0_0_16px_rgba(239,68,68,0.55)] animate-pulse';
  const SPINNER = '<svg aria-hidden="true" viewBox="0 0 24 24" class="h-3 w-3 shrink-0 animate-spin" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 12a9 9 0 1 1-9-9" stroke-linecap="round"/></svg>';

  const wrap = document.createElement('div');
  wrap.className = 'relative shrink-0';
  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'write-voice-button';
  button.className = 'write-session-action h-10 w-10 rounded-lg border border-gray-700/50 text-gray-300 hover:border-gray-600 hover:text-white shrink-0';
  button.setAttribute('aria-label', 'Erweiterte Aufnahme');
  button.title = 'Erweiterte Aufnahme';
  button.innerHTML = '<svg aria-hidden="true" viewBox="0 0 24 24" class="mx-auto h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8.5" y="3" width="7" height="12" rx="3.5"></rect><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"></path><path d="M19 5l2-2M21 5l-2-2"></path></svg>'
    + '<span data-voice-busy class="pointer-events-none absolute -right-0.5 -top-0.5 hidden h-2.5 w-2.5 animate-pulse rounded-full bg-amber-400 ring-2 ring-gray-950"></span>';
  button.classList.add('relative');
  const busyBadge = button.querySelector('[data-voice-busy]');
  wrap.appendChild(button);
  actions.appendChild(wrap);

  // Kompakte Auswahl: rechtsbuendiger Text ohne Kasten, Rahmen erst bei Hover.
  const selectClass = 'min-w-0 max-w-[11rem] truncate rounded-md border border-transparent bg-gray-900 px-1.5 py-0.5 text-right text-xs text-gray-300 hover:border-gray-700 focus:border-gray-600 focus:outline-none';
  const row = (label, control) => `<label class="flex items-center justify-between gap-3"><span class="shrink-0 text-[11px] text-gray-500">${label}</span>${control}</label>`;
  const iconButton = 'grid h-7 w-7 place-items-center rounded-md text-gray-500 hover:bg-gray-800 hover:text-gray-200';

  const panel = document.createElement('div');
  panel.id = 'write-voice-panel';
  panel.className = 'fixed z-[60] hidden w-72 max-w-[94vw] rounded-xl border border-gray-800 bg-gray-900/95 p-3 text-sm text-gray-200 shadow-2xl backdrop-blur';
  panel.innerHTML = `
    <div class="flex items-center gap-3">
      <button type="button" data-voice-record aria-label="Aufnahme starten" title="Aufnahme starten"
              class="grid h-11 w-11 shrink-0 place-items-center rounded-full border transition disabled:opacity-50 ${RECORD_IDLE_CLASSES}">${RECORD_IDLE}</button>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 text-xs">
          <span data-voice-status class="truncate tabular-nums text-gray-400">Bereit</span>
        </div>
        <div data-voice-jobs class="mt-1 hidden items-center gap-1.5 text-[11px] text-amber-300/90">
          ${SPINNER}
          <span data-voice-jobs-label class="min-w-0 truncate"></span>
          <button type="button" data-voice-skip class="hidden shrink-0 text-gray-500 underline-offset-2 hover:text-gray-200 hover:underline">Überspringen</button>
        </div>
        <div data-voice-chips class="mt-1.5 flex flex-wrap gap-1"></div>
      </div>
      <div class="flex shrink-0 flex-col items-center self-start">
        <button type="button" data-voice-close class="${iconButton}" aria-label="Schließen" title="Schließen">${ICON_CLOSE}</button>
        <button type="button" data-voice-settings-toggle class="${iconButton}" aria-expanded="false" aria-label="Einstellungen" title="Einstellungen">${ICON_GEAR}</button>
      </div>
    </div>
    <div data-voice-preview-wrap class="mt-2 hidden">
      <div class="flex items-center gap-2 text-[11px] text-gray-500">
        <span data-voice-preview-label class="min-w-0 flex-1 truncate"></span>
        <button type="button" data-voice-preview-original class="hidden shrink-0 underline-offset-2 hover:text-gray-200 hover:underline"></button>
        <button type="button" data-voice-preview-dismiss class="${iconButton} h-5 w-5" aria-label="Text ausblenden" title="Ausblenden">${ICON_CLOSE}</button>
      </div>
      <div data-voice-preview class="mt-0.5 max-h-28 overflow-y-auto whitespace-pre-wrap border-l-2 border-gray-800 pl-2 text-xs leading-relaxed text-gray-400"></div>
    </div>
    <div data-voice-settings class="mt-3 hidden space-y-1.5 border-t border-gray-800 pt-2.5">
      ${row('Anbieter', `<select data-voice-provider class="${selectClass}"></select>`)}
      ${row('Erkennung', `<select data-voice-asr class="${selectClass}"></select>`)}
      ${row('Nachbearbeitung', `<select data-voice-polish class="${selectClass}"></select>`)}
      <div class="flex flex-wrap gap-x-4 gap-y-1 pt-1 text-[11px] text-gray-400">
        <label class="flex items-center gap-1.5"><input type="checkbox" data-voice-keep checked class="accent-green-600"> Aufnahme behalten</label>
        <label class="flex items-center gap-1.5"><input type="checkbox" data-voice-auto checked class="accent-green-600"> Automatisch einfügen</label>
      </div>
      <details data-voice-naming class="pt-1">
        <summary class="cursor-pointer text-[11px] text-gray-500 hover:text-gray-300">Benennen &amp; Hashtags</summary>
        <form data-voice-naming-form class="mt-2 space-y-1">
          <label class="flex items-center gap-1.5 text-[11px] text-gray-400"><input type="checkbox" data-voice-naming-enabled class="accent-green-600"> Aufnahme nach dem Einfügen benennen</label>
          <textarea data-voice-naming-prompt rows="3" class="w-full rounded-md border border-gray-800 bg-gray-950 px-2 py-1 text-xs text-gray-200" placeholder="Worauf sollen Titel und Hashtags achten?"></textarea>
          <p class="text-[10px] leading-snug text-gray-600">Das Antwortformat ergänzt Journl selbst. Vorhandene Hashtags werden bevorzugt, Workflow-Hashtags nie gesetzt.</p>
          <button type="submit" class="rounded-md bg-green-700/80 px-2 py-1 text-xs text-white hover:bg-green-600">Speichern</button>
        </form>
      </details>
      <details data-voice-manage class="pt-1">
        <summary class="cursor-pointer text-[11px] text-gray-500 hover:text-gray-300">Presets verwalten</summary>
        <div class="mt-2 space-y-2">
          <div data-voice-presets class="space-y-0.5"></div>
          <form data-voice-preset-form class="space-y-1">
            <input data-voice-preset-id type="hidden">
            <input data-voice-preset-label class="w-full rounded-md border border-gray-800 bg-gray-950 px-2 py-1 text-xs text-gray-200" placeholder="Name">
            <textarea data-voice-preset-prompt rows="2" class="w-full rounded-md border border-gray-800 bg-gray-950 px-2 py-1 text-xs text-gray-200" placeholder="Anweisung an das Modell"></textarea>
            <div class="flex gap-2">
              <button type="submit" class="rounded-md bg-green-700/80 px-2 py-1 text-xs text-white hover:bg-green-600">Speichern</button>
              <button type="button" data-voice-preset-reset class="rounded-md px-2 py-1 text-xs text-gray-500 hover:text-gray-200">Neu</button>
            </div>
          </form>
        </div>
      </details>
    </div>`;
  document.body.appendChild(panel);

  const providerSelect = panel.querySelector('[data-voice-provider]');
  const asrSelect = panel.querySelector('[data-voice-asr]');
  const polishSelect = panel.querySelector('[data-voice-polish]');
  const previewBox = panel.querySelector('[data-voice-preview]');
  const statusLine = panel.querySelector('[data-voice-status]');
  const skipButton = panel.querySelector('[data-voice-skip]');
  const jobsRow = panel.querySelector('[data-voice-jobs]');
  const jobsLabel = panel.querySelector('[data-voice-jobs-label]');
  const chipsBox = panel.querySelector('[data-voice-chips]');
  const settingsBox = panel.querySelector('[data-voice-settings]');
  const settingsToggle = panel.querySelector('[data-voice-settings-toggle]');
  const recordButton = panel.querySelector('[data-voice-record]');
  const keepInput = panel.querySelector('[data-voice-keep]');
  const autoInput = panel.querySelector('[data-voice-auto]');
  const presetsBox = panel.querySelector('[data-voice-presets]');
  const presetForm = panel.querySelector('[data-voice-preset-form]');
  const namingForm = panel.querySelector('[data-voice-naming-form]');
  const namingEnabled = panel.querySelector('[data-voice-naming-enabled]');
  const namingPrompt = panel.querySelector('[data-voice-naming-prompt]');

  const previewWrap = panel.querySelector('[data-voice-preview-wrap]');
  const previewLabel = panel.querySelector('[data-voice-preview-label]');
  const previewOriginal = panel.querySelector('[data-voice-preview-original]');
  const previewDismiss = panel.querySelector('[data-voice-preview-dismiss]');

  // Text im Panel: waehrend der Aufnahme die Vorschau, danach das Ergebnis.
  // Wurde nachbearbeitet, bleibt der Rohtext per Umschalter lesbar - ins Feld
  // kommt trotzdem nur der verbesserte Text.
  let shown = { text: '', original: '', showOriginal: false, pending: '' };
  function renderPreview() {
    const { text, original, showOriginal, pending } = shown;
    const value = showOriginal && original ? original : text;
    previewWrap.classList.toggle('hidden', !value);
    previewBox.textContent = value || '';
    previewLabel.textContent = pending
      || (original ? (showOriginal ? 'Original-Transkript' : 'Verbesserter Text') : recording ? 'Vorschau' : 'Transkript');
    previewOriginal.classList.toggle('hidden', !original);
    previewOriginal.textContent = showOriginal ? 'Verbesserten zeigen' : 'Original zeigen';
    previewDismiss.classList.toggle('hidden', recording);
    previewBox.classList.toggle('italic', !!pending);
    previewBox.classList.toggle('text-gray-500', !!pending);
    previewBox.classList.toggle('border-amber-500/40', !!pending);
    previewBox.scrollTop = showOriginal ? 0 : previewBox.scrollHeight;
  }
  // pending: der Text ist noch nicht fertig (Rohtext waehrend der
  // Nachbearbeitung) und wird gedimmt mit Hinweis gezeigt.
  function setPreview(text, pending) {
    shown = { text: text || '', original: '', showOriginal: false, pending: pending || '' };
    renderPreview();
  }
  function setResult(text, original) {
    shown = { text: text || '', original: original && original !== text ? original : '', showOriginal: false, pending: '' };
    renderPreview();
  }
  previewOriginal.addEventListener('click', () => { shown.showOriginal = !shown.showOriginal; renderPreview(); });
  previewDismiss.addEventListener('click', () => setPreview(''));

  const PHASE_LABELS = {
    saving: 'Sichere Aufnahme…',
    transcribing: 'Transkribiere…',
    polishing: 'Bearbeite nach…',
    queued: 'Wartet auf die vorherige Aufnahme…',
    naming: 'Benenne Sprachnachricht…',
  };
  const jobText = (job) => (job.phase === 'polishing' && job.presetLabel
    ? `Bearbeite nach (${job.presetLabel})…` : PHASE_LABELS[job.phase] || 'In Arbeit…');
  // Ueberspringen nur, wo es etwas Sinnvolles gibt: der Vorschautext bzw.
  // die unbearbeitete Transkription wird dann sofort verwendet.
  const canSkip = (job) => job.phase === 'polishing' || job.phase === 'naming'
    || (job.phase === 'transcribing' && !!job.preview);

  // Solange ein Job den MacMini beschaeftigt, pausiert die Live-Vorschau einer
  // neuen Aufnahme - sie wuerde nur die Nachbearbeitung verzoegern. Das
  // Audio bleibt erhalten; die Vorschau holt danach in einem Stueck auf.
  const SERVER_PHASES = new Set(['transcribing', 'polishing', 'naming']);
  const serverBusy = () => jobs.some((job) => SERVER_PHASES.has(job.phase));

  // Display an, solange aufgenommen oder verarbeitet wird - wie bei der
  // normalen Aufnahme (write-session.js). Geht das iPhone aus, stoppt die
  // Aufnahme und die laufende Verarbeitung haengt im Hintergrund.
  let wakeLock = null;
  let wakeLockPending = false;
  const wakeLockWanted = () => recording || jobs.length > 0;
  async function syncWakeLock() {
    if (!wakeLockWanted()) {
      if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
      return;
    }
    if ((wakeLock && !wakeLock.released) || wakeLockPending) return;
    if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
    wakeLockPending = true;
    try { wakeLock = await navigator.wakeLock.request('screen'); } catch (_) { wakeLock = null; }
    wakeLockPending = false;
    if (!wakeLockWanted()) syncWakeLock(); // inzwischen fertig geworden
  }

  function refreshStatus() {
    syncWakeLock();
    const busy = jobs.length > 0;
    // is-recording haelt den Knopf auch in der eingeklappten Desktop-Leiste
    // sichtbar - waehrend der Verarbeitung soll man das Badge sehen.
    button.classList.toggle('is-recording', recording || busy);
    button.classList.toggle('text-red-400', recording);
    button.classList.toggle('text-amber-300', busy && !recording);
    busyBadge.classList.toggle('hidden', !busy);
    const label = !busy ? '' : jobs.length === 1 ? jobText(jobs[0]) : `${jobs.length} Aufnahmen in Arbeit · ${jobText(jobs[0])}`;
    button.title = recording ? 'Aufnahme läuft' : label || 'Erweiterte Aufnahme';
    jobsRow.classList.toggle('hidden', !busy);
    jobsRow.classList.toggle('flex', busy);
    jobsLabel.textContent = label;
    skipButton.classList.toggle('hidden', !jobs.some(canSkip));
    if (recording) renderRecording(); else statusLine.textContent = idleStatus;
    window.writeSubmitState?.refresh?.();
  }
  function busyReason() {
    if (recording) return 'Die erweiterte Aufnahme läuft – erst stoppen, dann senden.';
    if (jobs.length && jobs.every((job) => job.phase === 'naming')) return 'Die Sprachnachricht wird noch benannt – gleich fertig.';
    if (jobs.length) return 'Die Aufnahme wird noch verarbeitet – der Text kommt gleich ins Feld.';
    return '';
  }

  function modelOptions(selected) {
    const list = models.length ? [...models] : [];
    if (selected && !list.includes(selected)) list.unshift(selected);
    return list.map((name) => `<option value="${escapeHtml(name)}"${name === selected ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('');
  }

  function renderChips() {
    const presets = (settings && settings.presets) || [];
    if (selectedPreset && selectedPreset !== RECORD_ONLY && !presets.some((item) => item.id === selectedPreset)) selectedPreset = '';
    const chip = (id, label) => {
      const active = id === selectedPreset;
      return `<button type="button" data-voice-chip="${escapeHtml(id)}" aria-pressed="${active}" class="rounded-full border px-2 py-0.5 text-[11px] transition ${active
        ? 'border-gray-500 bg-gray-800 text-gray-100'
        : 'border-gray-800 text-gray-500 hover:border-gray-700 hover:text-gray-300'}">${escapeHtml(label)}</button>`;
    };
    // „Nur Aufnahme“ und „Transkription“ stehen immer da; die Presets
    // (Transkription plus Nachbearbeitung) kommen dazu.
    chipsBox.innerHTML = chip(RECORD_ONLY, 'Nur Aufnahme') + chip('', 'Transkription') + (presets.length
      ? presets.map((item) => chip(item.id, item.label)).join('')
      : '<span class="self-center text-[11px] text-gray-600">KI-Presets hinterm Zahnrad</span>');
  }

  function fillSelects() {
    if (!settings) return;
    const activeProvider = settings.provider_id || resolvedProviderId;
    providerSelect.innerHTML = providers.map((item) =>
      `<option value="${escapeHtml(item.id)}"${item.id === activeProvider ? ' selected' : ''}>${escapeHtml(item.label || item.id)}</option>`).join('')
      || '<option value="">kein lokaler Anbieter</option>';
    asrSelect.innerHTML = modelOptions(settings.asr_model);
    polishSelect.innerHTML = `<option value="">wie Anbieter</option>` + modelOptions(settings.polish_model);
    renderChips();
    renderPresets();
    namingEnabled.checked = settings.name_enabled !== false;
    if (document.activeElement !== namingPrompt) namingPrompt.value = settings.name_prompt || '';
  }

  function renderPresets() {
    if (!settings) return;
    presetsBox.innerHTML = (settings.presets || []).map((item) => `
      <div class="flex items-center gap-1 text-[11px]">
        <span class="min-w-0 flex-1 truncate text-gray-300">${escapeHtml(item.label)}</span>
        <button type="button" data-preset-edit="${escapeHtml(item.id)}" class="${iconButton} h-6 w-6" aria-label="Preset bearbeiten" title="Bearbeiten">${ICON_EDIT}</button>
        <button type="button" data-preset-delete="${escapeHtml(item.id)}" class="${iconButton} h-6 w-6 hover:text-red-400" aria-label="Preset löschen" title="Löschen">${ICON_TRASH}</button>
      </div>`).join('') || '<p class="text-[11px] text-gray-600">Keine Presets.</p>';
  }

  chipsBox.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-voice-chip]');
    if (!chip) return;
    selectedPreset = chip.dataset.voiceChip;
    try { window.localStorage.setItem(PRESET_STORAGE_KEY, selectedPreset); } catch (_) { /* privat */ }
    renderChips();
  });
  settingsToggle.addEventListener('click', () => {
    const open = settingsBox.classList.toggle('hidden') === false;
    settingsToggle.setAttribute('aria-expanded', String(open));
    settingsToggle.classList.toggle('text-gray-200', open);
    positionPanel();
  });

  async function loadSettings(force) {
    if (settings && !force) return settings;
    const data = await requestJson('/api/voice/settings', { method: 'GET', timeoutMs: 20000 });
    settings = data.settings;
    models = data.models || [];
    providers = data.providers || [];
    resolvedProviderId = data.resolved_provider_id || '';
    canEdit = !!data.can_edit;
    fillSelects();
    return settings;
  }

  // Die Vorlese-Felder (tts_*) gehoeren der Einstellung Burger → Schreiben
  // (tts-settings.js) und werden hier nie mitgeschickt - sonst ueberschriebe
  // ein veralteter Panel-Stand die dort gewaehlten Werte.
  async function persistSettings(patch) {
    const payload = {
      provider_id: settings.provider_id,
      asr_model: settings.asr_model,
      polish_model: settings.polish_model,
      ...(patch || {}),
    };
    const data = await requestJson('/api/voice/settings', {
      method: 'PUT', body: JSON.stringify(payload), timeoutMs: 20000,
    });
    settings = data.settings;
    fillSelects();
    return settings;
  }

  // ─── Panel-Positionierung ─────────────────────────────────────────
  function positionPanel() {
    const rect = button.getBoundingClientRect();
    panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - panel.offsetWidth - 8)) + 'px';
    panel.style.bottom = (window.innerHeight - rect.top + 8) + 'px';
  }
  function openPanel() {
    panel.classList.remove('hidden');
    positionPanel();
    loadSettings().catch((error) => toast(error.message, true));
  }
  function closePanel() { panel.classList.add('hidden'); }
  button.addEventListener('click', () => {
    if (panel.classList.contains('hidden')) openPanel(); else closePanel();
  });
  panel.querySelector('[data-voice-close]').addEventListener('click', closePanel);
  document.addEventListener('pointerdown', (event) => {
    if (recording || jobs.length) return; // Waehrend Aufnahme/Verarbeitung bleibt der Status sichtbar.
    if (!panel.contains(event.target) && !button.contains(event.target)) closePanel();
  });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closePanel(); });
  window.addEventListener('resize', () => { if (!panel.classList.contains('hidden')) positionPanel(); });

  // ─── Aufnahme: Web-Audio -> WAV ───────────────────────────────────
  function currentSamples() {
    const total = samples.reduce((sum, chunk) => sum + chunk.length, 0);
    const output = new Float32Array(total);
    let offset = 0;
    for (const chunk of samples) { output.set(chunk, offset); offset += chunk.length; }
    return output;
  }

  function encodeWav(input, sampleRate) {
    const buffer = new ArrayBuffer(44 + input.length * 2);
    const view = new DataView(buffer);
    const writeText = (offset, text) => { for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i)); };
    writeText(0, 'RIFF'); view.setUint32(4, 36 + input.length * 2, true); writeText(8, 'WAVE');
    writeText(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    writeText(36, 'data'); view.setUint32(40, input.length * 2, true);
    let offset = 44;
    for (let i = 0; i < input.length; i += 1) {
      const value = Math.max(-1, Math.min(1, input[i]));
      view.setInt16(offset, value < 0 ? value * 0x8000 : value * 0x7fff, true);
      offset += 2;
    }
    return new Blob([view], { type: 'audio/wav' });
  }

  async function transcribeBlob(blob, model) {
    const form = new FormData();
    form.append('file', blob, 'aufnahme.wav');
    form.append('model', model || settings.asr_model || asrSelect.value || '');
    form.append('provider_id', providerSelect.value || '');
    form.append('language', 'de');
    const data = await requestJson('/api/voice/transcribe', { method: 'POST', body: form });
    return String(data.text || '').trim();
  }

  function formatDuration() {
    const total = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
    return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
  }

  function renderRecording() {
    recordButton.innerHTML = recording ? RECORD_STOP : RECORD_IDLE;
    recordButton.title = recording ? 'Aufnahme stoppen' : 'Aufnahme starten';
    recordButton.setAttribute('aria-label', recordButton.title);
    RECORD_IDLE_CLASSES.split(' ').forEach((name) => recordButton.classList.toggle(name, !recording));
    RECORD_LIVE_CLASSES.split(' ').forEach((name) => recordButton.classList.toggle(name, recording));
    if (recording) {
      const hint = selectedPreset === RECORD_ONLY ? '' : serverBusy() ? ' · Vorschau pausiert'
        : previewError ? ' · Vorschau gestört' : previewText ? '' : ' · Vorschau folgt…';
      statusLine.textContent = `Aufnahme ${formatDuration()}${hint}`;
    }
  }

  // Die Vorschau steht nur im Panel; ins Schreibfeld kommt erst das finale
  // Ergebnis nach dem Stopp.
  async function previewTick() {
    if (!recording) return;
    if (serverBusy()) { renderRecording(); return; }
    const all = currentSamples();
    const rate = audioContext?.sampleRate || 16000;
    const start = Math.max(0, previewStart - rate); // 1s Ueberlapp gegen Wortabschnitte
    const segment = all.subarray(Math.min(start, all.length));
    if (segment.length < rate) return;
    previewStart = all.length;
    const session = startedAt;
    try {
      const text = await transcribeBlob(encodeWav(segment, rate));
      if (!recording || session !== startedAt) return; // Aufnahme inzwischen gestoppt
      previewError = '';
      if (text) {
        previewText = (previewText ? previewText + ' ' : '') + text;
        setPreview(previewText);
      }
    } catch (error) {
      if (!recording || session !== startedAt) return;
      // Die Aufnahme laeuft weiter, aber der Fehler bleibt sichtbar: eine still
      // leere Vorschau sah aus wie „funktioniert nur langsam“.
      previewError = error.message || 'Vorschau fehlgeschlagen.';
      if (!previewText) setPreview(`Vorschau: ${previewError}`);
    }
    if (recording) renderRecording();
  }

  async function startRecording() {
    await loadSettings();
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioContext = new Ctx({ sampleRate: 16000 });
    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);
    samples = [];
    sampleTotal = 0;
    processorNode.onaudioprocess = (event) => {
      const chunk = new Float32Array(event.inputBuffer.getChannelData(0));
      samples.push(chunk);
      sampleTotal += chunk.length;
    };
    muteNode = audioContext.createGain();
    muteNode.gain.value = 0;
    sourceNode.connect(processorNode);
    processorNode.connect(muteNode);
    muteNode.connect(audioContext.destination);
    recording = true;
    startedAt = Date.now();
    previewStart = 0;
    previewText = '';
    previewError = '';
    setPreview('');
    refreshStatus();
    tickTimer = setInterval(renderRecording, 1000);
    if (selectedPreset !== RECORD_ONLY) schedulePreview();
  }

  let previewTimer = null;
  // Genau eine Vorschau-Schleife pro Aufnahme: eine noch laufende Anfrage der
  // vorigen Aufnahme darf nach ihrer Rueckkehr keine zweite Schleife starten
  // (sonst liefen bei schnellem Neustart mehrere parallel).
  function schedulePreview() {
    clearTimeout(previewTimer);
    const session = startedAt;
    previewTimer = setTimeout(async () => {
      await previewTick();
      if (recording && session === startedAt) schedulePreview();
    }, 4000);
  }

  function teardownAudio() {
    clearInterval(tickTimer);
    clearTimeout(previewTimer);
    try { processorNode?.disconnect(); } catch (_) { /* already gone */ }
    try { sourceNode?.disconnect(); } catch (_) { /* already gone */ }
    try { muteNode?.disconnect(); } catch (_) { /* already gone */ }
    mediaStream?.getTracks().forEach((track) => track.stop());
    audioContext?.close?.().catch?.(() => {});
    processorNode = sourceNode = muteNode = null;
    mediaStream = null;
    audioContext = null;
  }

  // Ueberspringen beendet das Warten aller Jobs, die gerade etwas Sinnvolles
  // stehen lassen koennen; ihr spaetes Ergebnis wird verworfen.
  const SKIPPED = Symbol('skipped');
  const orSkip = (job, promise) => Promise.race([promise, job.skipped]);
  skipButton.addEventListener('click', () => {
    jobs.filter(canSkip).forEach((job) => job.skip());
  });

  // Jede Phase bekommt ein eigenes Ueberspringen - wer die Transkription
  // ueberspringt, will die Nachbearbeitung des Vorschautexts trotzdem.
  function setJobPhase(job, next) {
    job.phase = next;
    job.skipped = new Promise((resolve) => { job.skip = () => resolve(SKIPPED); });
    refreshStatus();
  }

  async function stopRecording() {
    if (!recording) return;
    recording = false;
    const rate = audioContext?.sampleRate || 16000;
    teardownAudio();
    renderRecording();
    const all = currentSamples();
    samples = [];
    if (!all.length) { idleStatus = 'Keine Audiodaten aufgenommen.'; refreshStatus(); return; }

    let finish;
    const finished = new Promise((resolve) => { finish = resolve; });
    const job = {
      phase: 'saving',
      startedAt,
      preview: previewText,
      mode: selectedPreset,
      presetLabel: ((settings && settings.presets) || []).find((item) => item.id === selectedPreset)?.label || '',
      keep: keepInput.checked,
      auto: autoInput.checked,
      turn: processChain,
    };
    // Auch ein frueh fertiger Job (Nur Aufnahme, Fehler) gibt erst frei,
    // wenn sein Vorgaenger fertig ist - die Reihenfolge bleibt erhalten.
    processChain = Promise.all([job.turn, finished]);
    job.turn.then(() => { job.turnDone = true; });
    jobs.push(job);
    idleStatus = 'Aufnahme beendet';
    setJobPhase(job, 'saving');
    try {
      idleStatus = await runJob(job, encodeWav(all, rate));
    } catch (error) {
      idleStatus = 'Verarbeitung fehlgeschlagen';
      toast(error.message || 'Verarbeitung fehlgeschlagen.', true);
    } finally {
      finish();
      jobs = jobs.filter((item) => item !== job);
      refreshStatus();
    }
  }

  // Ein Job: sichern -> (warten, bis der vorige fertig ist) -> transkribieren
  // -> nachbearbeiten -> einfuegen -> benennen. Gibt den Abschluss-Status
  // fuers Panel zurueck.
  async function runJob(job, blob) {
    const recordOnly = job.mode === RECORD_ONLY;
    let mediaId = '';
    if (job.keep || recordOnly) {
      setJobPhase(job, 'saving');
      try { mediaId = await writeSession.attachAudioBlob(blob, 'audio/wav', new Date(job.startedAt).toISOString(), 'voice'); }
      catch (error) {
        toast(error.message || 'Aufnahme konnte nicht gesichert werden.', true);
        if (recordOnly) return 'Aufnahme nicht gesichert';
      }
    }
    if (recordOnly) return 'Abgelegt – später „Mit MacMini transkribieren“';

    if (!job.turnDone) setJobPhase(job, 'queued');
    await job.turn;
    setJobPhase(job, 'transcribing');
    let text;
    try {
      text = await orSkip(job, transcribeBlob(blob));
    } catch (error) {
      toast(error.message || 'Transkription fehlgeschlagen.', true);
      if (!job.preview) return 'Transkription fehlgeschlagen';
      text = SKIPPED; // Vorschautext statt nichts
    }
    if (text === SKIPPED) text = job.preview;
    if (!text) return 'Kein Text erkannt.';

    const raw = text;
    lastTranscript = text;

    // Mit Preset kommt erst der fertig ueberarbeitete Text ins Feld; bis dahin
    // steht der Rohtext gedimmt im Panel. Scheitert oder uebersprungen, wird
    // der Rohtext eingefuegt.
    let status = job.auto ? 'Eingefügt' : 'Fertig';
    if (job.mode) {
      setJobPhase(job, 'polishing');
      if (!recording) setPreview(text, `Wird überarbeitet${job.presetLabel ? ` (${job.presetLabel})` : ''}…`);
      try {
        const data = await orSkip(job, requestJson('/api/voice/polish', {
          method: 'POST',
          body: JSON.stringify({ text, preset_id: job.mode, provider_id: providerSelect.value || '' }),
        }));
        if (data === SKIPPED) {
          status = 'Übersprungen – Text unbearbeitet';
        } else {
          const improved = String(data.text || '').trim();
          if (improved) { text = improved; lastTranscript = improved; }
        }
      } catch (error) {
        toast(error.message || 'Nachbearbeitung fehlgeschlagen.', true);
        status = 'Nachbearbeitung fehlgeschlagen – Text unbearbeitet';
      }
    }

    // Der Rohtext gehoert an die Aufnahme: der Chip zeigt ihn, auch wenn ins
    // Feld nur der verbesserte Text kommt. Steht er unveraendert im Feld,
    // schreibt das Senden ihn nicht noch einmal unter die Aufnahme.
    if (mediaId) {
      try { await storeTranscript(mediaId, raw, job.auto && text === raw); }
      catch (error) { toast(error.message || 'Originaltext konnte nicht an der Aufnahme abgelegt werden.', true); }
    }

    if (job.auto) writeSession.insertText(text);
    if (!recording) setResult(text, raw);
    if (mediaId && namingOn()) {
      const named = await nameRecording(mediaId, text, job);
      if (named) status += ` · ${named}`;
    }
    return status;
  }

  // Rohtext an der Sprachnachricht ablegen. inline: derselbe Text steht schon
  // im Editor, das Senden schreibt dann keinen zweiten Block.
  async function storeTranscript(mediaId, text, inline) {
    const updated = await requestJson(`/api/write-sessions/media/${encodeURIComponent(mediaId)}/transcribe`, {
      method: 'POST', body: JSON.stringify({ transcript: text, inline: Boolean(inline) }),
    });
    writeSession.mediaUpdated?.(updated);
  }

  // Ein vom Nutzer gesetzter oder gerade getippter Titel bleibt stehen.
  async function suggestTitle(mediaId, title) {
    const item = writeSession.media?.(mediaId);
    if (!item || !item.session_id || String(item.title || '').trim()) return;
    if (document.activeElement?.dataset?.mediaTitle === mediaId) return;
    const session = await requestJson(`/api/write-sessions/${encodeURIComponent(item.session_id)}/media/${encodeURIComponent(mediaId)}`, {
      method: 'PUT', body: JSON.stringify({ title }),
    });
    const updated = (session.media || []).find((entry) => entry.id === mediaId);
    if (updated) writeSession.mediaUpdated?.(updated);
  }

  const namingOn = () => settings?.name_enabled !== false;

  // Name und Hashtags fuer die abgelegte Aufnahme. Scheitert das Modell,
  // bleibt die Aufnahme unbenannt - ohne Toast, aber dezent in der
  // Statuszeile (vorher still, deshalb fiel ein dauerhafter 502 nicht auf).
  // Gibt den gesetzten Titel bzw. einen kurzen Hinweis zurueck.
  async function nameRecording(mediaId, text, job) {
    if (job) setJobPhase(job, 'naming');
    try {
      const request = requestJson('/api/voice/name', {
        method: 'POST', timeoutMs: 120000,
        body: JSON.stringify({ text, provider_id: providerSelect.value || settings?.provider_id || '' }),
      });
      const data = job ? await orSkip(job, request) : await request;
      if (data === SKIPPED) return '';
      const tags = (data.hashtags || []).map((tag) => `#${tag}`).join(' ');
      const title = `${String(data.title || '').trim()} ${tags}`.trim().slice(0, 160);
      if (title) await suggestTitle(mediaId, title);
      return title;
    } catch (error) {
      console.warn('Sprachnachricht nicht benannt:', error);
      return 'nicht benannt';
    }
  }

  recordButton.addEventListener('click', async () => {
    // Stoppen ist sofort erledigt; die Verarbeitung laeuft als Job weiter und
    // die naechste Aufnahme kann gleich beginnen.
    if (recording) { stopRecording(); return; }
    if (recordButton.disabled) return;
    recordButton.disabled = true;
    try {
      await startRecording();
    } catch (error) {
      teardownAudio();
      recording = false;
      renderRecording();
      idleStatus = 'Mikrofon nicht verfügbar';
      refreshStatus();
      toast(error.message || 'Mikrofon nicht verfügbar.', true);
    } finally {
      recordButton.disabled = false;
    }
  });

  // ─── Abgelegte Sprachnachricht ueber den MacMini transkribieren ───
  // Die normale Aufnahme liegt als webm/m4a vor; der MacMini nimmt nur WAV und
  // im Container gibt es kein ffmpeg. Der Browser dekodiert die Aufnahme
  // deshalb selbst, rechnet sie auf 16 kHz mono herunter und schickt die WAV
  // mit. Der Server legt das Transkript wie bei Whisper an der Nachricht ab.
  async function wavFromUrl(url) {
    const response = await window.apiFetch(url, { timeoutMs: 120000 });
    if (!response.ok) throw new Error('Aufnahme konnte nicht geladen werden.');
    const encoded = await response.arrayBuffer();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const context = new Ctx();
    let decoded;
    try {
      decoded = await new Promise((resolve, reject) => {
        const result = context.decodeAudioData(encoded, resolve, reject);
        if (result && typeof result.then === 'function') result.then(resolve, reject);
      });
    } finally {
      context.close?.().catch?.(() => {});
    }
    const rate = 16000;
    const Offline = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    const offline = new Offline(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
    const source = offline.createBufferSource();
    source.buffer = decoded;
    source.connect(offline.destination);
    source.start(0);
    const rendered = await offline.startRendering();
    return encodeWav(rendered.getChannelData(0), rate);
  }

  document.addEventListener('click', async (event) => {
    const trigger = event.target.closest?.('[data-transcribe-voice]');
    if (!trigger || trigger.disabled) return;
    const mediaId = trigger.dataset.transcribeVoice;
    trigger.disabled = true;
    trigger.textContent = 'MacMini transkribiert…';
    try {
      await loadSettings();
      const wav = await wavFromUrl(`/api/write-sessions/media/${encodeURIComponent(mediaId)}/original`);
      const form = new FormData();
      form.append('engine', 'voice');
      form.append('file', wav, 'aufnahme.wav');
      form.append('model', settings?.asr_model || '');
      form.append('provider_id', providerSelect.value || settings?.provider_id || '');
      form.append('language', 'de');
      const updated = await requestJson(`/api/write-sessions/media/${encodeURIComponent(mediaId)}/transcribe`, {
        method: 'POST', body: form, timeoutMs: 300000,
      });
      writeSession.mediaUpdated?.(updated);
      if (updated.transcription_status === 'error') toast('Transkription fehlgeschlagen.', true);
      else {
        toast('Transkribiert (MacMini).');
        if (updated.transcript_text && namingOn()) nameRecording(mediaId, updated.transcript_text);
      }
    } catch (error) {
      toast(error.message || 'Transkription fehlgeschlagen.', true);
      trigger.disabled = false;
      trigger.textContent = 'Mit MacMini transkribieren';
    }
  });

  // ─── Modell-/Preset-Verwaltung ────────────────────────────────────
  providerSelect.addEventListener('change', () => {
    persistSettings({ provider_id: providerSelect.value })
      .then(() => loadSettings(true))
      .catch((error) => toast(error.message, true));
  });
  asrSelect.addEventListener('change', () => {
    persistSettings({ asr_model: asrSelect.value }).catch((error) => toast(error.message, true));
  });
  polishSelect.addEventListener('change', () => {
    persistSettings({ polish_model: polishSelect.value }).catch((error) => toast(error.message, true));
  });

  presetsBox.addEventListener('click', async (event) => {
    const edit = event.target.closest('[data-preset-edit]');
    const remove = event.target.closest('[data-preset-delete]');
    if (edit) {
      const item = settings.presets.find((preset) => preset.id === edit.dataset.presetEdit);
      if (!item) return;
      presetForm.querySelector('[data-voice-preset-id]').value = item.id;
      presetForm.querySelector('[data-voice-preset-label]').value = item.label;
      presetForm.querySelector('[data-voice-preset-prompt]').value = item.prompt;
    } else if (remove) {
      const id = remove.dataset.presetDelete;
      if (!window.confirm('Preset wirklich löschen?')) return;
      try { await persistSettings({ presets: settings.presets.filter((preset) => preset.id !== id) }); }
      catch (error) { toast(error.message, true); }
    }
  });

  presetForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const idInput = presetForm.querySelector('[data-voice-preset-id]');
    const label = presetForm.querySelector('[data-voice-preset-label]').value.trim();
    const prompt = presetForm.querySelector('[data-voice-preset-prompt]').value.trim();
    if (!label || !prompt) { toast('Name und Anweisung sind nötig.', true); return; }
    const slug = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'preset';
    const presets = [...settings.presets];
    const existing = idInput.value;
    if (existing) {
      const index = presets.findIndex((preset) => preset.id === existing);
      if (index !== -1) presets[index] = { id: existing, label, prompt };
    } else {
      let id = slug;
      let suffix = 2;
      while (presets.some((preset) => preset.id === id)) { id = `${slug}-${suffix}`; suffix += 1; }
      presets.push({ id, label, prompt });
    }
    try {
      await persistSettings({ presets });
      presetForm.reset();
      idInput.value = '';
    } catch (error) { toast(error.message, true); }
  });
  namingEnabled.addEventListener('change', () => {
    persistSettings({ name_enabled: namingEnabled.checked }).catch((error) => toast(error.message, true));
  });
  namingForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await persistSettings({ name_enabled: namingEnabled.checked, name_prompt: namingPrompt.value.trim() });
      toast('Benennen gespeichert.');
    } catch (error) { toast(error.message, true); }
  });
  presetForm.querySelector('[data-voice-preset-reset]').addEventListener('click', () => {
    presetForm.reset();
    presetForm.querySelector('[data-voice-preset-id]').value = '';
  });

  // ─── Oeffentliche Schnittstelle fuer andere Features ──────────────
  // Vorlesen (write-speech.js) ergaenzt speak/stop/isSpeaking.
  window.journlVoice = Object.assign(window.journlVoice || {}, {
    // Fuer die Senden-Sperre in write-session.js.
    busyReason,
    loadSettings,
    settings: () => settings,
    // Rueckfall fuers Vorlesen, wenn das Schreibfeld leer ist.
    lastText: () => lastTranscript || previewText,
  });

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden' && recording) stopRecording();
    // Das System gibt den Wake Lock beim Wegwechseln frei.
    if (document.visibilityState === 'visible') syncWakeLock();
  });
  window.addEventListener('pagehide', () => { if (recording) stopRecording(); });
})();
