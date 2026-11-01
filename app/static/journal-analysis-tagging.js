/* Job „Hashtags nachtragen“ in der Journal-Analyse-Verwaltung.
 *
 * Der Job ist kein Analyse-Job aus der Datenbank, sondern genau einer pro
 * Nutzer, gespeichert neben seiner Verbindung (app/tagging_job.py). Diese Datei
 * zeigt ihn als feste Karte oben im Reiter „Jobs“ und seine Läufe im Reiter
 * „Protokoll“; `journal-analysis.js` stellt dafür nur die beiden Plätze bereit.
 * Die Läufe selbst gehören dem Server (`/api/runs`, background_runs.py).
 *
 * Klassisches Script, nach `journal-analysis-schedule.js` und vor
 * `journal-analysis.js` geladen; stellt `window.JournlTaggingJob` bereit.
 */
(function () {
  const RUN_KIND = 'historical_tagging';
  const INPUT = 'w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100';
  const PILL = 'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition';
  const MODE_LABEL = { missing: 'neue Einträge', refresh: 'alles neu' };
  const RUN_LABEL = {
    queued: 'wartet', running: 'läuft', done: 'fertig', failed: 'fehlgeschlagen',
    cancelled: 'abgebrochen', aborted: 'durch Neustart beendet',
  };
  const RUN_CLASS = {
    queued: 'text-amber-400', running: 'text-blue-400', done: 'text-green-400',
    failed: 'text-red-400', cancelled: 'text-gray-500', aborted: 'text-amber-400',
  };
  const DAY_LABEL = {
    processed: 'geschrieben', preview: 'Vorschau', skipped: 'übersprungen',
    error: 'Fehler', empty: 'keine Einträge',
  };
  const EVENT_LABEL = {
    tagging_error: 'Fehler', tagging_result: 'Ergebnis', tagging_proposals: 'Vorschläge',
  };
  const NOTIFY = [
    ['errors', 'Fehler', 'fehlgeschlagener Lauf oder Tage mit Fehler'],
    ['results', 'Ergebnis', 'wenn ein Lauf Hashtags geschrieben hat'],
    ['proposals', 'Neue Vorschläge', 'neue Hashtags warten auf Freigabe'],
  ];

  const state = { job: null, runTimer: null, runId: '', logOnlyErrors: false };

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  async function request(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 20000, cache: 'no-store', ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || 'Abruf fehlgeschlagen');
      error.data = data;
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function stamp(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(date);
  }
  const icon = (path) => `<svg aria-hidden="true" viewBox="0 0 24 24" class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
  const ICON_RUN = '<path d="M7 5v14l11-7z"/>';
  const ICON_EYE = '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>';
  const ICON_EDIT = '<path d="M4 20h4L19 9l-4-4L4 16z"/>';
  const ICON_LOG = '<path d="M6 4h12v16H6zM9 8h6M9 12h6M9 16h4"/>';
  function pill(action, path, label, tone) {
    return `<button type="button" data-tagging-action="${action}" class="${PILL} ${tone}">${icon(path)}<span>${label}</span></button>`;
  }

  function host(baseUrl) {
    try { return new URL(baseUrl).host; } catch (_) { return baseUrl || ''; }
  }

  function runCounts(run) {
    const values = { ...(run.progress || {}), ...(run.result || {}) };
    const parts = [];
    if (values.processed != null) parts.push(`${values.processed} ${values.preview ? 'angesehen' : 'verarbeitet'}`);
    if (values.skipped) parts.push(`${values.skipped} übersprungen`);
    if (values.failed) parts.push(`${values.failed} Fehler`);
    if (values.remaining && run.active) parts.push(`noch ${values.remaining}`);
    const proposals = Array.isArray(values.proposals) ? values.proposals.length : values.proposals;
    if (proposals) parts.push(`${proposals} Vorschläge`);
    return parts.join(' · ');
  }
  function triggerLabel(run) {
    if (run.scope?.preview) return 'Vorschau';
    return run.trigger === 'schedule' ? 'Zeitplan' : 'manuell';
  }

  // ─── Karte im Reiter „Jobs“ ───────────────────────────────────────
  function jobMeta(settings) {
    const schedule = settings.enabled
      ? window.JournlAnalysisSchedule.scheduleLabel(settings) : 'Zeitplan aus';
    return [
      schedule,
      `letzte ${settings.range_days} Tage inkl. heute`,
      MODE_LABEL[settings.mode] || settings.mode,
      settings.configured ? `${settings.model} · ${host(settings.base_url)}` : 'keine Verbindung',
    ].map(escapeHtml).join(' · ');
  }
  function lastRunLine(data) {
    const run = data.last_run;
    const retry = data.state?.attempts
      ? `<p class="mt-0.5 text-[11px] text-amber-400">Zeitplan: Versuch ${escapeHtml(data.state.attempts)} von ${escapeHtml(data.max_attempts)} fehlgeschlagen, nächster ab ${escapeHtml(stamp(data.state.retry_after))}</p>`
      : '';
    if (!run) return `<p class="mt-1 text-xs text-gray-500">Noch kein Lauf.</p>${retry}`;
    const error = run.error ? `<p class="mt-0.5 break-words text-[11px] text-red-400">${escapeHtml(run.error)}</p>` : '';
    return `<p class="mt-1 text-xs text-gray-400">Zuletzt ${escapeHtml(stamp(run.created_at))} (${escapeHtml(triggerLabel(run))}):
        <span class="${RUN_CLASS[run.status] || 'text-gray-300'}">${escapeHtml(RUN_LABEL[run.status] || run.status)}</span>
        ${runCounts(run) ? ` · ${escapeHtml(runCounts(run))}` : ''}</p>${error}${retry}`;
  }
  function cardHtml(data) {
    const settings = data.settings;
    const paused = settings.enabled ? '' : '<span class="ml-1 rounded-full bg-gray-800 px-1.5 py-0.5 text-[10px] font-normal text-gray-400">pausiert</span>';
    return `<div class="rounded-lg border border-sky-900/60 bg-sky-950/20 px-3 py-2.5">
      <p class="break-words text-sm font-medium text-gray-100">Hashtags nachtragen${paused}
        <span class="ml-1 rounded-full border border-sky-800/60 px-1.5 py-0.5 text-[10px] font-normal text-sky-300">eingebaut</span></p>
      <p class="mt-0.5 text-xs text-gray-500">${jobMeta(settings)}</p>
      <div data-tagging-last>${lastRunLine(data)}</div>
      <div class="mt-2 flex flex-wrap items-center gap-1.5">
        ${pill('run', ICON_RUN, 'Ausführen', 'border-green-700/60 text-green-300 hover:bg-green-900/40')}
        ${pill('preview', ICON_EYE, 'Vorschau', 'border-amber-700/50 text-amber-300 hover:bg-amber-900/30')}
        ${pill('edit', ICON_EDIT, 'Bearbeiten', 'border-gray-700 text-gray-300 hover:bg-gray-800')}
        ${pill('log', ICON_LOG, 'Protokoll', 'border-gray-700 text-gray-300 hover:bg-gray-800')}
      </div>
      <div class="mt-2 flex items-center gap-2 text-xs text-gray-400">
        <span data-tagging-status></span>
        <button type="button" data-tagging-action="cancel" class="hidden rounded bg-red-700 px-2 py-0.5 text-white hover:bg-red-600">Abbrechen</button>
      </div>
      <div data-tagging-form></div>
    </div>`;
  }

  async function mountJob(slot) {
    if (!slot) return;
    slot.innerHTML = '<p class="text-xs text-gray-500">Lade Hashtags nachtragen …</p>';
    try {
      state.job = await request('/api/brain/tagging/job');
    } catch (error) {
      slot.innerHTML = `<p class="text-xs text-red-400">Hashtags nachtragen: ${escapeHtml(error.message)}</p>`;
      return;
    }
    slot.innerHTML = cardHtml(state.job);
    if (state.job.last_run?.active) watchRun(state.job.last_run.id);
  }
  function jobSlot() {
    return document.querySelector('[data-analysis-panel="jobs"] [data-tagging-job]');
  }
  function setStatus(text, tone) {
    const status = jobSlot()?.querySelector('[data-tagging-status]');
    if (!status) return;
    status.textContent = text || '';
    status.className = tone === 'error' ? 'text-red-400' : tone === 'busy' ? 'text-amber-300' : 'text-gray-400';
  }

  // ─── Formular ─────────────────────────────────────────────────────
  function formHtml(settings) {
    const schedule = window.JournlAnalysisSchedule;
    const mode = settings.schedule_mode || 'daily';
    const option = (value, label, current) => `<option value="${value}"${value === current ? ' selected' : ''}>${label}</option>`;
    const notify = NOTIFY.map(([key, label, hint]) => `<label class="flex items-start gap-2">
        <input type="checkbox" data-tagging-notify="${key}" class="mt-0.5"${settings.notify?.[key] ? ' checked' : ''} />
        <span>${label} <span class="text-gray-500">– ${hint}</span></span></label>`).join('');
    return `<form data-tagging-job-form class="mt-3 space-y-3 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
      <fieldset class="space-y-2">
        <legend class="text-[11px] uppercase tracking-wide text-gray-500">Verbindung (lokal, OpenAI-kompatibel)</legend>
        <label class="block text-xs text-gray-400">Endpunkt
          <input data-tagging-field="base_url" type="url" inputmode="url" placeholder="http://192.168.1.50:1234" class="${INPUT}" value="${escapeAttr(settings.base_url)}" /></label>
        <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label class="text-xs text-gray-400">API-Schlüssel
            <input data-tagging-field="api_key" type="password" autocomplete="off" class="${INPUT}"
              placeholder="${settings.has_api_key ? 'gespeichert – leer lassen = unverändert' : 'optional'}" /></label>
          <div class="flex flex-col gap-1">
            <span class="text-xs text-gray-400">Modell</span>
            <select data-tagging-field="model" class="${INPUT}">
              ${settings.model ? `<option value="${escapeAttr(settings.model)}" selected>${escapeHtml(settings.model)}</option>` : '<option value="">– Modelle laden –</option>'}
            </select>
            <button type="button" data-tagging-action="models" class="rounded bg-gray-700 px-2 py-1 text-xs text-white hover:bg-gray-600">Modelle laden</button>
          </div>
        </div>
      </fieldset>
      <fieldset class="space-y-2">
        <legend class="text-[11px] uppercase tracking-wide text-gray-500">Zeitplan</legend>
        <label class="flex items-center gap-2 text-xs text-gray-300">
          <input type="checkbox" data-tagging-field="enabled"${settings.enabled ? ' checked' : ''} /> automatisch ausführen</label>
        <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label class="text-xs text-gray-400">Zeitplan
            <select data-field="schedule_mode" class="${INPUT}">
              ${option('daily', 'täglich', mode)}${option('interval', 'Takt', mode)}${option('custom', 'benutzerdefiniert', mode)}${option('manual', 'manuell', mode)}
            </select></label>
          <label data-schedule-time class="text-xs text-gray-400 ${['daily', 'custom'].includes(mode) ? '' : 'hidden'}">Uhrzeit
            <input type="time" data-field="schedule_time" class="${INPUT}" value="${escapeAttr(settings.schedule_time || '05:00')}" /></label>
          <label data-schedule-interval class="text-xs text-gray-400 ${mode === 'interval' ? '' : 'hidden'}">Stunden
            <input type="number" min="1" max="720" data-field="schedule_interval_hours" class="${INPUT}" value="${escapeAttr(settings.schedule_interval_hours || '')}" /></label>
        </div>
        ${schedule.customScheduleFields(settings, mode !== 'custom', INPUT)}
        <p class="text-[11px] text-gray-500">Schlägt ein Lauf des Zeitplans fehl (z. B. Rechner mit dem Modell schläft), versucht er es nach 30 Minuten erneut, höchstens dreimal.</p>
      </fieldset>
      <fieldset class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <legend class="mb-1 text-[11px] uppercase tracking-wide text-gray-500">Umfang</legend>
        <label class="text-xs text-gray-400">Zeitraum: letzte Tage (inkl. heute)
          <input type="number" min="1" max="62" data-tagging-field="range_days" class="${INPUT}" value="${escapeAttr(settings.range_days)}" /></label>
        <label class="text-xs text-gray-400">Modus
          <select data-tagging-field="mode" class="${INPUT}">
            ${option('missing', 'Nur neue Einträge', settings.mode)}${option('refresh', 'Alles neu verschlagworten', settings.mode)}
          </select></label>
        <p class="text-[11px] text-gray-500 sm:col-span-2">„Nur neue Einträge“: jeder Eintrag, den die KI noch nicht gesehen hat – auch wenn er schon eigene Hashtags trägt –, und Einträge ganz ohne Hashtag. Etablierte Hashtags werden bevorzugt.</p>
      </fieldset>
      <fieldset class="space-y-1.5 text-xs text-gray-300">
        <legend class="mb-1 text-[11px] uppercase tracking-wide text-gray-500">Benachrichtigungen</legend>
        ${notify}
        <p class="text-[11px] text-gray-500">Versand über Pushover (Menü → Benachrichtigungen, Schalter „Hashtags nachtragen“). Ruhezeiten gelten; eine Vorschau meldet nie etwas.</p>
      </fieldset>
      <div class="flex flex-wrap items-center gap-2">
        <button type="submit" class="rounded bg-green-700 px-3 py-1 text-xs font-semibold text-white hover:bg-green-600">Speichern</button>
        <button type="button" data-tagging-action="close" class="rounded bg-gray-700 px-3 py-1 text-xs text-white hover:bg-gray-600">Abbrechen</button>
        <span data-tagging-form-status class="text-xs text-gray-500" role="status" aria-live="polite"></span>
      </div>
    </form>`;
  }
  function openForm() {
    const slot = jobSlot()?.querySelector('[data-tagging-form]');
    if (!slot || !state.job) return;
    slot.innerHTML = formHtml(state.job.settings);
    slot.querySelector('form')?.scrollIntoView({ block: 'nearest' });
  }
  function formStatus(form, text, error) {
    const status = form.querySelector('[data-tagging-form-status]');
    if (!status) return;
    status.textContent = text || '';
    status.className = `text-xs ${error ? 'text-red-400' : 'text-gray-500'}`;
  }
  function readForm(form) {
    const field = (name) => form.querySelector(`[data-tagging-field="${name}"]`);
    const shared = (name) => form.querySelector(`[data-field="${name}"]`);
    const mode = shared('schedule_mode')?.value || 'daily';
    const payload = {
      enabled: Boolean(field('enabled')?.checked),
      schedule_mode: mode,
      schedule_time: shared('schedule_time')?.value || '05:00',
      schedule_interval_hours: shared('schedule_interval_hours')?.value || null,
      schedule_rule: mode === 'custom' ? window.JournlAnalysisSchedule.readScheduleRule(form) : null,
      range_days: Number.parseInt(field('range_days')?.value || '7', 10),
      mode: field('mode')?.value || 'missing',
      notify: Object.fromEntries(NOTIFY.map(([key]) => [key, Boolean(form.querySelector(`[data-tagging-notify="${key}"]`)?.checked)])),
    };
    const baseUrl = field('base_url')?.value.trim() || '';
    // Ohne Endpunkt bleibt die gespeicherte Verbindung, wie sie ist.
    if (baseUrl) {
      payload.base_url = baseUrl;
      payload.model = field('model')?.value || '';
    }
    const key = field('api_key')?.value;
    if (key) payload.api_key = key;
    return payload;
  }
  async function saveForm(form) {
    formStatus(form, 'Wird gespeichert …');
    try {
      await request('/api/brain/tagging/settings', { method: 'POST', body: JSON.stringify(readForm(form)) });
    } catch (error) {
      formStatus(form, error.message, true);
      return;
    }
    window.showToast?.('Hashtags nachtragen gespeichert.');
    mountJob(jobSlot());
  }
  async function loadModels(form) {
    const base = form.querySelector('[data-tagging-field="base_url"]')?.value.trim();
    if (!base) { formStatus(form, 'Bitte zuerst den Endpunkt eintragen.', true); return; }
    formStatus(form, 'Modelle werden gelesen …');
    try {
      const data = await request(`/api/brain/tagging/models?${new URLSearchParams({ base_url: base })}`);
      const select = form.querySelector('[data-tagging-field="model"]');
      const current = select?.value || '';
      if (select) {
        select.innerHTML = (data.models || []).map((name) =>
          `<option value="${escapeAttr(name)}"${name === current ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('')
          || '<option value="">– keine Modelle –</option>';
      }
      formStatus(form, `${(data.models || []).length} Modelle gefunden.`);
    } catch (error) {
      formStatus(form, error.message, true);
    }
  }

  // ─── Lauf starten und verfolgen ──────────────────────────────────
  async function startRun(preview) {
    setStatus(preview ? 'Vorschau wird gestartet …' : 'Lauf wird gestartet …', 'busy');
    try {
      const data = await request('/api/brain/tagging/job/run', { method: 'POST', body: JSON.stringify({ preview }) });
      window.showToast?.('Der Lauf läuft auf dem Server. Die App darf geschlossen werden.');
      watchRun(data.run?.id);
    } catch (error) {
      if (error.status === 409 && error.data?.run) {
        window.showToast?.('Es läuft bereits ein Lauf – er wird jetzt angezeigt.', true);
        watchRun(error.data.run.id);
        return;
      }
      setStatus(error.message, 'error');
      window.showToast?.(error.message, true);
    }
  }
  async function watchRun(runId) {
    if (!runId) return;
    if (state.runTimer) clearTimeout(state.runTimer);
    state.runId = runId;
    let run;
    try {
      run = await request(`/api/runs/${encodeURIComponent(runId)}?log_from=999999`);
    } catch (error) {
      setStatus(error.message, 'error');
      return;
    }
    const cancel = jobSlot()?.querySelector('[data-tagging-action="cancel"]');
    cancel?.classList.toggle('hidden', !run.active);
    const counts = runCounts(run);
    if (run.active) {
      const head = run.cancel_requested ? 'Wird beendet' : `${triggerLabel(run)} läuft`;
      const at = run.progress?.current_date ? ` · bei ${run.progress.current_date}` : '';
      setStatus(`${head}${counts ? ` · ${counts}` : ''}${at}`, 'busy');
      state.runTimer = setTimeout(() => watchRun(runId), 2500);
      return;
    }
    state.runId = '';
    setStatus('');
    window.showToast?.(run.status === 'failed'
      ? `Hashtags nachtragen fehlgeschlagen: ${run.error || ''}`
      : `Hashtags nachtragen ${RUN_LABEL[run.status] || run.status}${counts ? `: ${counts}` : ''}.`, run.status === 'failed');
    mountJob(jobSlot());
    const log = document.querySelector('[data-analysis-panel="log"] [data-tagging-log]');
    if (log) mountLog(log);
  }
  async function cancelRun() {
    if (!state.runId) return;
    try {
      await request(`/api/runs/${encodeURIComponent(state.runId)}/cancel`, { method: 'POST' });
      setStatus('Abbruch angefordert – der Lauf endet nach dem aktuellen Tag.', 'busy');
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  // ─── Protokoll ────────────────────────────────────────────────────
  function dayEntry(entry) {
    const tone = entry.status === 'error' ? 'text-red-400'
      : entry.status === 'skipped' || entry.status === 'empty' ? 'text-gray-500'
        : entry.status === 'preview' ? 'text-amber-300' : 'text-green-400';
    const blocks = (entry.blocks || []).map((block) => {
      const time = block.scope === 'day' ? 'ganzer Tag' : (block.time || block.anchor || '');
      const ai = (block.ai_tags || []).length
        ? `<span class="text-sky-300">${block.ai_tags.map((tag) => `#${escapeHtml(tag)}`).join(' ')}</span>`
        : '<span class="text-gray-600">keine neue Zuordnung</span>';
      const own = (block.own_tags || []).length
        ? ` <span class="text-gray-600" title="bereits im Text">${block.own_tags.map((tag) => `#${escapeHtml(tag)}`).join(' ')}</span>` : '';
      return `<div class="flex gap-2 pl-2 text-gray-400"><span class="font-mono text-gray-500">${escapeHtml(time)}</span><span>${ai}${own}</span></div>`;
    }).join('');
    const detail = entry.error || entry.reason || '';
    return `<li class="rounded bg-gray-800/50 px-2 py-1 text-xs">
      <div class="flex flex-wrap items-center gap-x-2"><span class="text-gray-200">${escapeHtml(entry.date || entry.file || '')}</span>
        <span class="${tone}">${escapeHtml(DAY_LABEL[entry.status] || entry.status)}</span>
        ${detail ? `<span class="break-words ${entry.status === 'error' ? 'text-red-300' : 'text-gray-500'}">${escapeHtml(detail)}</span>` : ''}</div>
      ${blocks}</li>`;
  }
  async function loadRunDetail(details) {
    const body = details.querySelector('[data-tagging-run-body]');
    if (!body || body.dataset.loaded) return;
    body.dataset.loaded = '1';
    body.innerHTML = '<p class="text-xs text-gray-500">Lade …</p>';
    try {
      const run = await request(`/api/runs/${encodeURIComponent(details.dataset.taggingRun)}`);
      const entries = (run.log || []).filter((entry) => !state.logOnlyErrors || entry.status === 'error');
      const params = run.scope || {};
      const range = params.start_date ? `${params.start_date} bis ${params.end_date} · ${MODE_LABEL[params.mode] || params.mode || ''}` : '';
      body.innerHTML = `${range ? `<p class="text-[11px] text-gray-500">${escapeHtml(range)}</p>` : ''}
        ${run.error ? `<p class="mt-1 break-words text-xs text-red-400">${escapeHtml(run.error)}</p>` : ''}
        ${run.result?.stopped ? `<p class="mt-1 text-xs text-amber-400">${escapeHtml(run.result.stopped)}</p>` : ''}
        ${(run.result?.proposals || []).length ? `<p class="mt-1 text-xs text-gray-400">Neue Vorschläge: ${run.result.proposals.map((tag) => `#${escapeHtml(tag)}`).join(' ')}</p>` : ''}
        <ul class="mt-1 space-y-1">${entries.map(dayEntry).join('') || `<li class="text-xs text-gray-500">${state.logOnlyErrors ? 'Keine Fehler.' : 'Keine Tage protokolliert.'}</li>`}</ul>`;
    } catch (error) {
      body.innerHTML = `<p class="text-xs text-red-400">${escapeHtml(error.message)}</p>`;
    }
  }
  function runRow(run) {
    const counts = runCounts(run);
    return `<li><details data-tagging-run="${escapeAttr(run.id)}" class="group rounded-lg border border-gray-800 bg-gray-900/40">
      <summary class="cursor-pointer list-none px-3 py-2">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
          <span class="rounded-full border border-current/30 px-1.5 text-[11px] ${RUN_CLASS[run.status] || 'text-gray-300'}">${escapeHtml(RUN_LABEL[run.status] || run.status)}</span>
          <span class="font-medium text-gray-200">Hashtags nachtragen</span>
          <span class="text-gray-400">${escapeHtml(triggerLabel(run))}</span>
          <span class="ml-auto text-gray-600 transition-transform group-open:rotate-90">›</span>
        </div>
        <p class="mt-0.5 text-[11px] text-gray-500">${escapeHtml([stamp(run.created_at), counts].filter(Boolean).join(' · '))}</p>
        ${run.error ? `<p class="mt-0.5 line-clamp-2 break-words text-[11px] text-red-400">${escapeHtml(run.error)}</p>` : ''}
      </summary>
      <div data-tagging-run-body class="border-t border-gray-800 px-3 pb-3 pt-1"></div>
    </details></li>`;
  }
  function eventRows(events) {
    if (!events.length) return '<p class="text-xs text-gray-500">Noch keine Meldung erzeugt.</p>';
    return `<ul class="space-y-0.5 text-xs">${events.slice().reverse().slice(0, 8).map((item) => `
      <li class="flex flex-wrap gap-x-2"><span class="text-gray-500">${escapeHtml(stamp(item.created_at))}</span>
        <span class="${item.kind === 'tagging_error' ? 'text-red-400' : 'text-gray-300'}">${escapeHtml(EVENT_LABEL[item.kind] || item.kind)} (${escapeHtml(item.count)})</span>
        <span class="text-gray-600">${item.trigger === 'schedule' ? 'Zeitplan' : 'manuell'}</span></li>`).join('')}</ul>`;
  }
  async function mountLog(slot) {
    if (!slot) return;
    slot.innerHTML = '<p class="text-xs text-gray-500">Lade Hashtags nachtragen …</p>';
    let runs;
    let job;
    try {
      [runs, job] = await Promise.all([
        request(`/api/runs?kind=${RUN_KIND}`),
        request('/api/brain/tagging/job'),
      ]);
    } catch (error) {
      slot.innerHTML = `<p class="text-xs text-red-400">${escapeHtml(error.message)}</p>`;
      return;
    }
    const list = (runs.runs || []).filter((run) => !state.logOnlyErrors || run.status === 'failed' || run.result?.failed);
    slot.innerHTML = `<details class="rounded-lg border border-sky-900/60 bg-sky-950/10 px-3 py-2" open>
      <summary class="cursor-pointer text-xs font-medium text-gray-200">Hashtags nachtragen – Läufe der letzten 30 Tage
        <span class="text-gray-500">(${list.length})</span></summary>
      <label class="mt-2 flex items-center gap-2 text-xs text-gray-400">
        <input type="checkbox" data-tagging-only-errors${state.logOnlyErrors ? ' checked' : ''} /> nur Läufe und Tage mit Fehlern</label>
      <ul class="mt-2 space-y-1.5">${list.map(runRow).join('') || '<li class="text-xs text-gray-500">Kein passender Lauf.</li>'}</ul>
      <p class="mt-3 text-[11px] uppercase tracking-wide text-gray-500">Erzeugte Meldungen</p>
      <div class="mt-1">${eventRows(job.events || [])}</div>
    </details>`;
  }

  // ─── Verdrahtung ──────────────────────────────────────────────────
  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-tagging-action]');
    if (!action) return;
    event.preventDefault();
    const name = action.dataset.taggingAction;
    if (name === 'run') startRun(false);
    if (name === 'preview') startRun(true);
    if (name === 'edit') openForm();
    if (name === 'cancel') cancelRun();
    if (name === 'log') document.querySelector('[data-analysis-tab="log"]')?.click();
    if (name === 'close') {
      const slot = jobSlot()?.querySelector('[data-tagging-form]');
      if (slot) slot.innerHTML = '';
    }
    if (name === 'models') loadModels(action.closest('form'));
  });
  document.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-tagging-job-form]');
    if (!form) return;
    event.preventDefault();
    saveForm(form);
  });
  document.addEventListener('toggle', (event) => {
    const details = event.target;
    if (details?.dataset?.taggingRun && details.open) loadRunDetail(details);
  }, true);
  document.addEventListener('change', (event) => {
    if (!event.target.closest?.('[data-tagging-only-errors]')) return;
    state.logOnlyErrors = event.target.checked;
    mountLog(document.querySelector('[data-analysis-panel="log"] [data-tagging-log]'));
  });

  window.JournlTaggingJob = { mountJob, mountLog };
})();
