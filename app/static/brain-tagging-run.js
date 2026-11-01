// Nachtraegliches Verschlagworten alter Journale: Start, Fortschritt, Abbruch.
// AI maintenance contract: reine Oberflaeche. Der Lauf selbst gehoert dem Server
// (app/historical_tagging.py + app/background_runs.py); hier wird nur gestartet,
// abgefragt und angezeigt. Nichts davon darf den Lauf beenden.
//
// Wird nach brain.js und vor brain-content.js geladen: dort haengen die
// Klick-Handler an runHistoricalTagging.

// Rendered from data, never from markup: a hashtag is user input and must not
// reach the DOM as HTML.
function appendTaggingLogEntry(file) {
  const log = brainElement('brain-tag-log');
  if (!log) return;
  log.classList.remove('hidden');
  const entry = document.createElement('div');
  entry.className = 'rounded bg-gray-800/60 px-2 py-1';
  const head = document.createElement('div');
  head.className = 'flex items-center justify-between gap-2 text-gray-200';
  const title = document.createElement('span');
  title.textContent = file.date || file.file || 'Journal';
  const state = document.createElement('span');
  const labels = {
    processed: 'geschrieben', preview: 'nur angesehen',
    error: 'Fehler', empty: 'keine Blöcke',
  };
  state.textContent = labels[file.status] || file.status;
  state.className = file.status === 'error' ? 'text-red-300'
    : file.status === 'preview' ? 'text-amber-300' : 'text-green-300';
  head.append(title, state);
  entry.append(head);
  if (file.error) {
    const message = document.createElement('div');
    message.className = 'text-red-300';
    message.textContent = file.error;
    entry.append(message);
  }
  for (const block of file.blocks || []) {
    const line = document.createElement('div');
    line.className = 'flex gap-2 text-gray-400';
    const time = document.createElement('span');
    time.className = 'font-mono text-gray-500';
    // A legacy day without any usable time is classified as a whole: showing
    // its midnight anchor would claim a time that was never written down.
    time.textContent = block.scope === 'day'
      ? 'ganzer Tag'
      : (block.time || block.anchor || '');
    const tags = document.createElement('span');
    if ((block.ai_tags || []).length) {
      tags.className = 'text-sky-300';
      tags.textContent = block.ai_tags.map((tag) => `#${tag}`).join(' ');
    } else {
      tags.className = 'text-gray-600';
      tags.textContent = 'keine neue Zuordnung';
    }
    line.append(time, tags);
    if ((block.own_tags || []).length) {
      const own = document.createElement('span');
      own.className = 'text-gray-600';
      own.textContent = block.own_tags.map((tag) => `#${tag}`).join(' ');
      own.title = 'bereits im Text vorhanden';
      line.append(own);
    }
    entry.append(line);
  }
  log.append(entry);
  log.scrollTop = log.scrollHeight;
}


// Der Lauf gehoert dem Server, nicht diesem Tab. Der Browser startet ihn, fragt
// den Stand ab und darf jederzeit weggehen: ein gesperrtes Handy beendet den
// Lauf nicht mehr, und beim naechsten Oeffnen haengt sich die Ansicht wieder an.
const TAGGING_RUN_KIND = 'historical_tagging';
const TAGGING_POLL_MS = 2000;
const taggingRun = { id: '', logCursor: 0, timer: null, active: false };

function taggingStatusElement() {
  return brainElement('brain-tag-status');
}

function setTaggingStatus(text, tone) {
  const status = taggingStatusElement();
  if (!status) return;
  status.textContent = text || '';
  status.classList.toggle('hidden', !text);
  status.className = `${text ? '' : 'hidden '}mt-2 text-xs ${
    tone === 'error' ? 'text-red-300' : tone === 'busy' ? 'text-amber-300' : 'text-gray-400'}`;
}

function taggingRunSummary(run) {
  const progress = run.progress || {};
  const parts = [];
  if (typeof progress.processed === 'number') {
    parts.push(`${progress.processed} ${progress.preview ? 'angesehen' : 'verarbeitet'}`);
  }
  if (progress.skipped) parts.push(`${progress.skipped} übersprungen`);
  if (progress.failed) parts.push(`${progress.failed} fehlgeschlagen`);
  if (progress.remaining) parts.push(`noch ${progress.remaining}`);
  if (progress.current_date && run.active) parts.push(`bei ${progress.current_date}`);
  const labels = {
    queued: 'Wartet auf den Start', running: 'Läuft auf dem Server',
    done: 'Fertig', failed: 'Fehlgeschlagen', cancelled: 'Abgebrochen',
    aborted: 'Nach einem Neustart beendet',
  };
  const head = run.cancel_requested && run.active ? 'Wird beendet' : (labels[run.status] || run.status);
  return [head, parts.join(' · ')].filter(Boolean).join(' — ');
}

function renderTaggingRun(run) {
  const button = brainElement('brain-tag-run');
  if (button) {
    const remaining = run.active && run.progress?.remaining ? ` (noch ${run.progress.remaining})` : '';
    button.textContent = run.active ? `Abbrechen${remaining}` : 'Starten';
    button.dataset.running = run.active ? '1' : '';
    button.setAttribute('aria-busy', run.active ? 'true' : 'false');
    button.disabled = false;
  }
  setTaggingStatus(
    run.error ? `${taggingRunSummary(run)}: ${run.error}` : taggingRunSummary(run),
    run.status === 'failed' ? 'error' : run.active ? 'busy' : '',
  );
}

function stopTaggingPolling() {
  if (taggingRun.timer) clearTimeout(taggingRun.timer);
  taggingRun.timer = null;
}

function finishTaggingRun(run) {
  const result = run.result || {};
  const failed = result.failed ? ` ${result.failed} fehlgeschlagen.` : '';
  const stopped = result.stopped ? ` ${result.stopped}` : '';
  if (run.status === 'done' || run.status === 'cancelled') {
    showToast(
      `${result.processed || 0} ${result.preview ? 'Dateien angesehen' : 'Dateien verarbeitet'}, `
      + `${result.skipped || 0} übersprungen, ${(result.proposals || []).length} Vorschläge.${failed}${stopped}`,
      !!result.failed,
    );
  } else if (run.status === 'failed') {
    showToast(run.error || 'Die Verschlagwortung ist fehlgeschlagen.', true);
  } else if (run.status === 'aborted') {
    showToast('Der Lauf wurde durch einen Neustart beendet. Erneut starten setzt fort.', true);
  }
  if (!result.preview && result.processed) loadBrain();
}

async function pollTaggingRun() {
  if (!taggingRun.id) return;
  let run = null;
  try {
    const response = await apiFetch(
      `/api/runs/${taggingRun.id}?log_from=${taggingRun.logCursor}`, { cache: 'no-store' },
    );
    if (response.status === 404) {
      taggingRun.id = '';
      setTaggingStatus('Der Auftrag ist nicht mehr vorhanden.', 'error');
      return;
    }
    run = await response.json();
  } catch {
    // Ein Netzfehler beendet nichts: der Server rechnet weiter, es wird nur
    // spaeter erneut gefragt.
    setTaggingStatus('Kein Kontakt zum Server – der Lauf läuft weiter.', 'busy');
    taggingRun.timer = setTimeout(pollTaggingRun, TAGGING_POLL_MS * 2);
    return;
  }
  for (const entry of run.log || []) appendTaggingLogEntry(entry);
  taggingRun.logCursor = run.log_total || taggingRun.logCursor;
  renderTaggingRun(run);
  if (run.active) {
    taggingRun.timer = setTimeout(pollTaggingRun, TAGGING_POLL_MS);
    return;
  }
  taggingRun.active = false;
  finishTaggingRun(run);
}

function watchTaggingRun(run, { reset = false } = {}) {
  stopTaggingPolling();
  taggingRun.id = run.id;
  taggingRun.active = !!run.active;
  if (reset) {
    taggingRun.logCursor = 0;
    const log = brainElement('brain-tag-log');
    if (log) { log.textContent = ''; log.classList.add('hidden'); }
  }
  renderTaggingRun(run);
  pollTaggingRun();
}

// ─── Verbindung und Job ─────────────────────────────────────────────
// Endpunkt, Modell, Zeitplan, Meldungen und das Protokoll pflegt der Job
// „Hashtags nachtragen“ in der Journal-Analyse-Verwaltung
// (journal-analysis-tagging.js). Hier steht nur, womit ein Handlauf rechnet.
function applyTaggingRunAvailability(configured) {
  const button = brainElement('brain-tag-run');
  if (button && !taggingRun.active) button.disabled = !configured;
}

async function loadTaggingSettings() {
  const line = brainElement('brain-tag-connection');
  try {
    const response = await apiFetch('/api/brain/tagging/settings', { cache: 'no-store' });
    const data = response.ok ? await response.json() : {};
    applyTaggingRunAvailability(!!data.configured);
    if (!line) return;
    if (!data.configured) {
      line.textContent = 'Noch keine Verbindung – bitte in der Verwaltung einrichten.';
      line.className = 'min-w-0 flex-1 text-amber-300';
      return;
    }
    let host = data.base_url;
    try { host = new URL(data.base_url).host; } catch { /* Rohwert zeigen */ }
    const schedule = data.enabled && data.schedule_mode !== 'manual'
      ? (window.JournlAnalysisSchedule?.scheduleLabel(data) || 'Zeitplan aktiv') : 'kein Zeitplan';
    line.textContent = `${data.model} · ${host} · ${schedule}`;
    line.className = 'min-w-0 flex-1 text-gray-400';
  } catch {
    if (line) line.textContent = 'Kein Kontakt zum Server.';
  }
}

brainElement('brain-tag-manage')?.addEventListener('click', () => {
  window.openSettingsGroup?.('analysis', 'Journal Analyse Verwaltung');
});

async function attachTaggingRun() {
  await loadTaggingSettings();
  try {
    const response = await apiFetch(`/api/runs?kind=${TAGGING_RUN_KIND}`, { cache: 'no-store' });
    if (!response.ok) return;
    const runs = (await response.json()).runs || [];
    const running = runs.find((item) => item.active);
    if (running && running.id !== taggingRun.id) watchTaggingRun(running, { reset: true });
    else if (!running && !taggingRun.id && runs[0]) renderTaggingRun(runs[0]);
  } catch {
    // Ohne Antwort bleibt die Anzeige, wie sie ist.
  }
}

async function runHistoricalTagging() {
  const button = brainElement('brain-tag-run');
  if (taggingRun.active && taggingRun.id) {
    button.disabled = true;
    try {
      await apiFetch(`/api/runs/${taggingRun.id}/cancel`, { method: 'POST' });
      setTaggingStatus('Abbruch angefordert – der Lauf endet nach dem aktuellen Journal.', 'busy');
    } catch {
      showToast('Der Abbruch konnte nicht übermittelt werden.', true);
    } finally {
      button.disabled = false;
    }
    return;
  }
  const start = brainElement('brain-tag-start').value;
  const end = brainElement('brain-tag-end').value;
  if (!start || !end) { showToast('Bitte Start- und Enddatum wählen.', true); return; }
  const mode = brainElement('brain-tag-mode')?.value || 'missing';
  const staleBefore = brainElement('brain-tag-stale')?.value || '';
  if (mode === 'stale' && !staleBefore) { showToast('Bitte ein Stichdatum wählen.', true); return; }
  const preview = brainElement('brain-tag-preview')?.checked || false;
  button.disabled = true;
  try {
    const response = await apiFetch('/api/brain/tagging/start', {
      method: 'POST',
      body: JSON.stringify({
        start_date: start, end_date: end,
        mode, stale_before: staleBefore, preview,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 409 && data.run) {
        showToast('Es läuft bereits ein Auftrag – er wird jetzt angezeigt.', true);
        watchTaggingRun(data.run, { reset: true });
        return;
      }
      showToast(data.error || `Start fehlgeschlagen (HTTP ${response.status}).`, true);
      setTaggingStatus(data.error || `Start fehlgeschlagen (HTTP ${response.status}).`, 'error');
      return;
    }
    showToast('Der Lauf läuft jetzt auf dem Server. Die App darf geschlossen werden.');
    watchTaggingRun(data.run, { reset: true });
  } catch {
    showToast('Netzwerkfehler beim Starten der Verschlagwortung.', true);
  } finally {
    button.disabled = false;
  }
}
