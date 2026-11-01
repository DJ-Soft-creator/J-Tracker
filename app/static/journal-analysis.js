/* Journal-Analyse-Verwaltung — ein eigenständiger Bereich im Burger-Menü.
 *
 * Bewusst getrennt vom AI-Worker (`agent-workbench.js`): keine Admin-Bindung,
 * keine Verbindung zum Ticket-System. Die Unterseiten werden aus `PANELS`
 * gerendert, damit keine Unterseite ohne Container entstehen kann.
 *
 * Klassisches Script, geteilter globaler Namensraum; `apiFetch` kommt aus der
 * jeweiligen App-Shell. Kein Build, keine Module.
 */
(function () {
  const ENDPOINT = '/api/journal-analysis';
  const PANELS = [
    { id: 'jobs', label: 'Jobs' },
    { id: 'prompts', label: 'Vorlagen' },
    { id: 'notifications', label: 'Hinweise' },
    { id: 'log', label: 'Protokoll' },
  ];
  const STATUS_CLASS = {
    completed: 'text-green-400', running: 'text-blue-400', queued: 'text-amber-400',
    failed: 'text-red-400', cancelled: 'text-gray-500',
  };

  let activePanel = 'jobs';
  let loaded = {};
  let activeRunId = null;

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const INPUT = 'w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100';

  async function request(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 20000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Abruf fehlgeschlagen');
    return data;
  }

  function shortTime(value) {
    const text = String(value || '');
    return text.length >= 16 ? text.slice(0, 16).replace('T', ' ') : text;
  }

  // Lokale Anbieter (lm_*) und Cloud-Anbieter, die die Anbieterverwaltung
  // ausdruecklich fuer die Journal-Analyse freigegeben hat (HTTPS). Die
  // verbindliche Pruefung macht der Server (resolve_analysis_provider).
  function isCloudProvider(provider) {
    return !String(provider.id || '').startsWith('lm_');
  }
  function analysisProviders() {
    const providers = window.config?.ai_providers || [];
    return providers.filter((provider) => String(provider.id || '').startsWith('lm_')
      || (provider.journal_analysis === true && String(provider.api_url || '').startsWith('https://')));
  }
  function cloudHost(provider) {
    try { return new URL(provider.api_url).host; } catch (_) { return provider.label || provider.id; }
  }
  // Die Warnung steht direkt unter der Auswahl, solange ein Cloud-Anbieter gewaehlt ist.
  function syncCloudWarning(form) {
    const select = form?.querySelector('[data-field="provider_id"]');
    const note = form?.querySelector('[data-cloud-warning]');
    if (!select || !note) return;
    const provider = analysisProviders().find((item) => item.id === select.value);
    const cloud = Boolean(provider && isCloudProvider(provider));
    note.classList.toggle('hidden', !cloud);
    if (cloud) {
      note.textContent = `Cloud-Anbieter: Die Journalinhalte dieses Jobs (samt Zusatzquellen) gehen an ${cloudHost(provider)}. `
        + 'Lokale Anbieter behalten sie im Haus.';
    }
  }

  // ─── Rahmen ───────────────────────────────────────────────────────
  function buildShell() {
    const shell = document.querySelector('[data-analysis-shell]');
    if (!shell || shell.dataset.ready === 'true') return;
    const tabs = PANELS.map((panel) =>
      `<button type="button" data-analysis-tab="${panel.id}"
        class="border-b-2 border-transparent pb-1.5 text-gray-500 transition hover:text-gray-300"
        >${panel.label}</button>`).join('');
    const bodies = PANELS.map((panel) =>
      `<div data-analysis-panel="${panel.id}" class="hidden text-sm text-gray-300"></div>`).join('');
    shell.innerHTML = `<div class="flex gap-4 border-b border-gray-800 mb-3 text-sm">${tabs}</div>${bodies}`;
    shell.dataset.ready = 'true';
  }

  function showPanel(id) {
    activePanel = id;
    PANELS.forEach((panel) => {
      const button = document.querySelector(`[data-analysis-tab="${panel.id}"]`);
      const body = document.querySelector(`[data-analysis-panel="${panel.id}"]`);
      const active = panel.id === id;
      button?.classList.toggle('text-white', active);
      button?.classList.toggle('border-gray-500', active);
      button?.classList.toggle('text-gray-500', !active);
      body?.classList.toggle('hidden', !active);
    });
    render(id);
  }

  function render(id) {
    const body = document.querySelector(`[data-analysis-panel="${id}"]`);
    if (!body) return;
    if (loaded[id]) return;
    loaded[id] = true;
    if (id === 'jobs') loadJobs(body);
    if (id === 'prompts') loadPrompts(body);
    if (id === 'log') loadLog(body);
    if (id === 'notifications') window.JournlAnalysisNotify?.loadPanel(body);
  }

  // ─── Jobs ─────────────────────────────────────────────────────────
  // Kleine Pillen statt unterstrichener Textlinks: sie brechen auf dem Handy
  // in eine eigene Zeile um, statt den Jobnamen auf null Breite zu druecken.
  const ICONS = {
    run: '<path d="M7 5v14l11-7z"/>',
    rerun: '<path d="M4 12a8 8 0 0 1 13.7-5.6L20 8.7M20 4v4.7h-4.7M20 12a8 8 0 0 1-13.7 5.6L4 15.3M4 20v-4.7h4.7"/>',
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    trash: '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13"/>',
  };
  const icon = (name) => `<svg aria-hidden="true" viewBox="0 0 24 24" class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;
  const PILL = 'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition';
  function pill(attribute, id, name, label, tone) {
    return `<button type="button" ${attribute}="${escapeAttr(id)}" class="${PILL} ${tone}">${icon(name)}<span>${label}</span></button>`;
  }
  function trashButton(attribute, id, label) {
    return `<button type="button" ${attribute}="${escapeAttr(id)}" aria-label="${label}" title="${label}"
      class="ml-auto grid h-7 w-7 place-items-center rounded-full text-gray-500 transition hover:bg-red-950/60 hover:text-red-300">${icon('trash')}</button>`;
  }

  const { scheduleLabel, customScheduleFields, readScheduleRule, rangeLabel, rangeFields } = window.JournlAnalysisSchedule;

  function jobRow(job) {
    const range = rangeLabel(job);
    const schedule = scheduleLabel(job);
    const meta = [schedule, range, job.last_run_date ? `zuletzt ${job.last_run_date}` : '']
      .filter(Boolean).map(escapeHtml).join(' · ');
    return `<li class="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2.5" data-job="${escapeAttr(job.id)}">
      <p class="break-words text-sm font-medium text-gray-100">${escapeHtml(job.name)}
        ${job.enabled ? '' : '<span class="ml-1 rounded-full bg-gray-800 px-1.5 py-0.5 text-[10px] font-normal text-gray-400">pausiert</span>'}</p>
      <p class="mt-0.5 text-xs text-gray-500">${meta}</p>
      <div class="mt-2 flex flex-wrap items-center gap-1.5">
        ${pill('data-analysis-run', job.id, 'run', 'Ausführen', 'border-green-700/60 text-green-300 hover:bg-green-900/40')}
        ${pill('data-analysis-rerun', job.id, 'rerun', 'Neu analysieren', 'border-amber-700/50 text-amber-300 hover:bg-amber-900/30')}
        ${pill('data-analysis-edit', job.id, 'edit', 'Bearbeiten', 'border-gray-700 text-gray-300 hover:bg-gray-800')}
        ${trashButton('data-analysis-delete', job.id, 'Job löschen')}
      </div>
    </li>`;
  }

  async function loadJobs(container) {
    container.innerHTML = '<p class="text-gray-500">Lade…</p>';
    let data;
    try {
      data = await request(`${ENDPOINT}/state`);
    } catch (error) {
      container.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`;
      return;
    }
    const jobs = data.jobs || [];
    container.innerHTML = `
      <div class="mb-3 flex items-center justify-between">
        <p class="text-xs text-gray-500">${jobs.length} Job(s)</p>
        <button type="button" data-analysis-new
          class="rounded bg-green-700 px-3 py-1 text-xs font-semibold text-white hover:bg-green-600">Neuer Job</button>
      </div>
      <div data-tagging-job class="mb-3"></div>
      <div data-analysis-form class="mb-4"></div>
      <div class="mb-2 flex items-center gap-2 text-xs text-gray-400">
        <span data-analysis-run-status></span>
        <button type="button" data-analysis-run-cancel
          class="hidden rounded bg-red-700 px-2 py-0.5 text-white hover:bg-red-600">Abbrechen</button>
      </div>
      <ul class="space-y-2">${jobs.length ? jobs.map(jobRow).join('') : '<li class="text-gray-500">Noch kein Job angelegt.</li>'}</ul>`;
    container._jobs = jobs;
    container._prompts = data.prompts || [];
    window.JournlTaggingJob?.mountJob(container.querySelector('[data-tagging-job]'));
    try {
      const active = await request('/api/runs?kind=journal_analysis');
      const running = (active.runs || []).find(
        (entry) => entry.status === 'queued' || entry.status === 'running');
      if (running) pollRun(running.id);
    } catch (_) {
      // Ohne Lauf-Übersicht bleibt die Liste nutzbar.
    }
  }

  function providerOptions(selected) {
    const options = analysisProviders();
    if (!options.length) {
      return '<option value="">Kein lokaler Anbieter konfiguriert</option>';
    }
    return options.map((provider) => {
      const label = `${provider.label || provider.id}${provider.model ? ' · ' + provider.model : ''}${isCloudProvider(provider) ? ' · Cloud' : ''}`;
      return `<option value="${escapeAttr(provider.id)}"${provider.id === selected ? ' selected' : ''}
        data-model="${escapeAttr(provider.model || '')}">${escapeHtml(label)}</option>`;
    }).join('');
  }

  function jobForm(job, prompts) {
    const isEdit = Boolean(job);
    const scheduleMode = job?.schedule_mode || 'daily';
    const modelValue = job?.model || '';
    const promptOptions = ['<option value="">— keine —</option>'].concat(
      (prompts || []).map((template) =>
        `<option value="${escapeAttr(template.id)}"${template.id === job?.prompt_template_id ? ' selected' : ''}>${escapeHtml(template.name)}</option>`)
    ).join('');
    return `<form data-job-form="${isEdit ? escapeAttr(job.id) : ''}"
        class="space-y-3 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
      <label class="block text-xs text-gray-400">Name
        <input data-field="name" required value="${escapeAttr(job?.name || '')}" class="${INPUT}" /></label>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label class="text-xs text-gray-400">Zeitplan
          <select data-field="schedule_mode" class="${INPUT}">
            <option value="daily"${scheduleMode === 'daily' ? ' selected' : ''}>täglich</option>
            <option value="interval"${scheduleMode === 'interval' ? ' selected' : ''}>Takt</option>
            <option value="custom"${scheduleMode === 'custom' ? ' selected' : ''}>benutzerdefiniert</option>
            <option value="manual"${scheduleMode === 'manual' ? ' selected' : ''}>manuell</option>
          </select></label>
        <label data-schedule-time class="text-xs text-gray-400 ${['daily', 'custom'].includes(scheduleMode) ? '' : 'hidden'}">Uhrzeit
          <input type="time" data-field="schedule_time" class="${INPUT}" value="${escapeAttr(job?.schedule_time || '06:00')}" /></label>
        <label data-schedule-interval class="text-xs text-gray-400 ${scheduleMode === 'interval' ? '' : 'hidden'}">Stunden
          <input type="number" min="1" max="720" data-field="schedule_interval_hours" class="${INPUT}" value="${escapeAttr(job?.schedule_interval_hours || '')}" /></label>
      </div>
      ${customScheduleFields(job, scheduleMode !== 'custom', INPUT)}
      <div class="text-xs text-gray-400">Zeitraum<div class="mt-0.5">${rangeFields(job, INPUT)}</div></div>
      <div class="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label class="text-xs text-gray-400">Anbieter
          <select data-field="provider_id" class="${INPUT}">${providerOptions(job?.provider_id)}</select>
          <span data-cloud-warning class="mt-1 hidden block rounded border border-amber-800/60 bg-amber-950/40 px-2 py-1 text-[11px] leading-snug text-amber-300"></span></label>
        <div class="flex flex-col gap-1">
          <span class="text-xs text-gray-400">Modell</span>
          <select data-field="model" class="${INPUT}">
            <option value="">(automatisch auslesen)</option>
            ${modelValue ? `<option value="${escapeAttr(modelValue)}" selected>${escapeHtml(modelValue)}</option>` : ''}
          </select>
          <button type="button" data-analysis-model-refresh
            class="rounded bg-gray-700 px-2 py-1 text-xs text-white hover:bg-gray-600">Modelle auslesen</button>
          <span data-model-hint class="text-[11px] text-gray-500"></span>
        </div>
      </div>
      <label class="block text-xs text-gray-400">Prompt-Vorlage
        <select data-field="prompt_template_id" class="${INPUT}">${promptOptions}</select></label>
      <label class="block text-xs text-gray-400">Zusätzliche Quellen (eine relative Pfadangabe je Zeile,
        z. B. <code>Auswertungen/check.md</code> oder <code>familie/Notizen/plan.md</code>; je Tag eine eigene Datei
        mit <code>{YYYY}</code> <code>{MM}</code> <code>{DD}</code> <code>{day}</code>, z. B.
        <code>W-Tracker-data/{YYYY}/{MM}/{DD}/Weedtracker_{day}.md</code>)
        <textarea data-field="extra_sources" rows="2" class="${INPUT}">${escapeHtml((job?.extra_sources || []).join('\n'))}</textarea></label>
      <div class="grid grid-cols-1 gap-x-4 gap-y-2 text-xs text-gray-300 sm:grid-cols-2 [&>label]:flex [&>label]:items-start [&>label]:gap-2 [&_input]:mt-0.5">
        <label><input type="checkbox" data-field="output_notification"${job?.output_notification !== false ? ' checked' : ''} /> Benachrichtigung am Ende des Laufs</label>
        <label><input type="checkbox" data-field="output_journal"${job?.output_journal ? ' checked' : ''} /> Ins Journal schreiben</label>
        <label><input type="checkbox" data-field="output_family"${job?.output_family ? ' checked' : ''} /> In Familiendokument</label>
        <label><input type="checkbox" data-field="output_notes"${job?.output_notes ? ' checked' : ''} /> In eigene Notizen schreiben</label>
        <label><input type="checkbox" data-field="output_run_summary"${job?.output_run_summary !== false ? ' checked' : ''} /> Zusammenfassung am Ende (ein Modellaufruf)</label>
        <label><input type="checkbox" data-field="carry_summary"${job?.carry_summary ? ' checked' : ''} /> Zusammenfassung an nächsten Lauf übergeben</label>
        <label><input type="checkbox" data-field="enabled"${job?.enabled !== false ? ' checked' : ''} /> aktiv</label>
      </div>
      <label class="block text-xs text-gray-400">Familien-Ziel (relativ im Familienordner)
        <input data-field="family_target" class="${INPUT}" value="${escapeAttr(job?.family_target || '')}" /></label>
      <label class="block text-xs text-gray-400">Notiz-Ziel (relativ in <code>notes/</code>, leer = Jobname.md)
        <input data-field="notes_target" class="${INPUT}" value="${escapeAttr(job?.notes_target || '')}" /></label>
      <div class="flex gap-2">
        <button type="submit" class="rounded bg-green-700 px-3 py-1 text-xs font-semibold text-white hover:bg-green-600">
          ${isEdit ? 'Speichern' : 'Anlegen'}</button>
        <button type="button" data-analysis-cancel class="rounded bg-gray-700 px-3 py-1 text-xs text-white hover:bg-gray-600">Abbrechen</button>
      </div>
    </form>`;
  }

  function readJobForm(form) {
    const value = (name) => form.querySelector(`[data-field="${name}"]`);
    const checked = (name) => Boolean(value(name)?.checked);
    const sources = (value('extra_sources')?.value || '').split('\n').map((line) => line.trim()).filter(Boolean);
    return {
      name: value('name')?.value || '',
      schedule_mode: value('schedule_mode')?.value || 'daily',
      schedule_time: value('schedule_time')?.value || '',
      schedule_interval_hours: value('schedule_interval_hours')?.value || '',
      schedule_rule: value('schedule_mode')?.value === 'custom' ? readScheduleRule(form) : null,
      date_range_mode: value('date_range_mode')?.value || 'previous_day',
      range_days: value('range_days')?.value || '',
      custom_start: value('custom_start')?.value || '',
      custom_end: value('custom_end')?.value || '',
      provider_id: value('provider_id')?.value || '',
      model: value('model')?.value || '',
      prompt_template_id: value('prompt_template_id')?.value || '',
      output_notification: checked('output_notification'),
      output_journal: checked('output_journal'),
      output_family: checked('output_family'),
      output_notes: checked('output_notes'),
      output_run_summary: checked('output_run_summary'),
      carry_summary: checked('carry_summary'),
      family_target: value('family_target')?.value || '',
      notes_target: value('notes_target')?.value || '',
      extra_sources: sources,
      enabled: checked('enabled'),
    };
  }

  async function refreshModelOptions(form, { selectFirst = false } = {}) {
    const provider = form.querySelector('[data-field="provider_id"]');
    const model = form.querySelector('[data-field="model"]');
    const hint = form.querySelector('[data-model-hint]');
    if (!provider || !model) return;
    const current = model.value;
    model.innerHTML = '<option value="">(automatisch auslesen)</option>';
    if (!provider.value) {
      if (hint) hint.textContent = 'Kein lokaler Anbieter gewählt.';
      return;
    }
    if (hint) hint.textContent = 'Lade Modelle …';
    try {
      const data = await request(`${ENDPOINT}/providers/${encodeURIComponent(provider.value)}/models`);
      const models = data.models || [];
      model.insertAdjacentHTML('beforeend', models.map((name) =>
        `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`).join(''));
      if (current && !models.includes(current)) {
        model.insertAdjacentHTML('beforeend',
          `<option value="${escapeAttr(current)}">${escapeHtml(current)}</option>`);
      }
      if (current) model.value = current;
      else if (selectFirst && models.length) model.value = models[0];
      if (hint) hint.textContent = models.length
        ? `${models.length} Modell(e) gefunden` : 'Der Endpunkt meldet kein Modell.';
    } catch (error) {
      if (current) {
        model.insertAdjacentHTML('beforeend',
          `<option value="${escapeAttr(current)}" selected>${escapeHtml(current)}</option>`);
        model.value = current;
      }
      if (hint) hint.textContent = error.message || 'Modelle konnten nicht ausgelesen werden.';
    }
  }

  function openJobForm(job) {
    const body = document.querySelector('[data-analysis-panel="jobs"]');
    const host = body?.querySelector('[data-analysis-form]');
    if (!host) return;
    const prompts = body._prompts || [];
    host.innerHTML = jobForm(job, prompts);
    const form = host.querySelector('form');
    const provider = form?.querySelector('[data-field="provider_id"]');
    const model = form?.querySelector('[data-field="model"]');
    if (provider && model && !model.value) {
      model.value = provider.selectedOptions[0]?.dataset.model || '';
    }
    if (form) refreshModelOptions(form, { selectFirst: true });
    syncCloudWarning(form);
    form?.scrollIntoView({ block: 'nearest' });
  }

  async function saveJob(form) {
    const jobId = form.dataset.jobForm;
    const payload = readJobForm(form);
    const url = jobId ? `${ENDPOINT}/jobs/${encodeURIComponent(jobId)}` : `${ENDPOINT}/jobs`;
    await request(url, { method: jobId ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    window.showToast?.(jobId ? 'Job gespeichert.' : 'Job angelegt.');
    loaded = {};
    loadJobs(document.querySelector('[data-analysis-panel="jobs"]'));
  }

  async function runJobNow(jobId, force = false) {
    const status = document.querySelector('[data-analysis-run-status]');
    if (status) status.textContent = force ? 'Neu-Analyse wird gestartet …' : 'Analyse wird gestartet …';
    try {
      const data = await request(`${ENDPOINT}/jobs/${encodeURIComponent(jobId)}/run`, {
        method: 'POST', body: JSON.stringify({ force }),
      });
      window.showToast?.('Analyse gestartet.');
      pollRun(data.run?.id);
    } catch (error) {
      if (status) status.textContent = error.message;
      window.showToast?.(error.message, true);
    }
  }

  function setRunCancelVisible(visible) {
    document.querySelector('[data-analysis-run-cancel]')?.classList.toggle('hidden', !visible);
  }

  async function pollRun(runId) {
    if (!runId) return;
    activeRunId = runId;
    const status = document.querySelector('[data-analysis-run-status]');
    let run;
    try {
      run = await request(`/api/runs/${encodeURIComponent(runId)}`);
    } catch (error) {
      if (status) status.textContent = error.message;
      setRunCancelVisible(false);
      return;
    }
    const processed = run.progress && run.progress.processed != null ? ` · ${run.progress.processed} Tag(e)` : '';
    if (status) status.textContent = `Lauf ${run.status}${processed}`;
    if (run.status === 'queued' || run.status === 'running') {
      setRunCancelVisible(true);
      window.setTimeout(() => pollRun(runId), 3000);
      return;
    }
    activeRunId = null;
    setRunCancelVisible(false);
    if (status) {
      status.textContent = run.error ? `Analyse ${run.status}: ${run.error}` : `Analyse ${run.status}.`;
    }
    loaded = {};
    loadJobs(document.querySelector('[data-analysis-panel="jobs"]'));
  }

  async function cancelRun() {
    if (!activeRunId) return;
    const status = document.querySelector('[data-analysis-run-status]');
    try {
      await request(`/api/runs/${encodeURIComponent(activeRunId)}/cancel`, { method: 'POST' });
      if (status) status.textContent = 'Abbruch angefordert …';
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  async function deleteJob(jobId) {
    if (!window.confirm('Diesen Job samt Verlauf und Benachrichtigungen löschen?')) return;
    try {
      await request(`${ENDPOINT}/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
      window.showToast?.('Job gelöscht.');
      loaded = {};
      loadJobs(document.querySelector('[data-analysis-panel="jobs"]'));
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  // ─── Prompt-Vorlagen ──────────────────────────────────────────────
  function promptForm(template) {
    return `<form data-prompt-form="${template ? escapeAttr(template.id) : ''}"
        class="space-y-2 rounded-lg border border-gray-700 bg-gray-900/60 p-3">
      <input data-prompt-name placeholder="Name" required value="${escapeAttr(template?.name || '')}" class="${INPUT}" />
      <textarea data-prompt-body rows="6" required placeholder="Auftrag an das Modell"
        class="${INPUT}">${escapeHtml(template?.body || '')}</textarea>
      <p class="text-[11px] text-gray-500">Platzhalter: {day} · {summary} · {state} · {sources} (Alias {journal}).
        Was du nicht selbst platzierst, wird automatisch unten angehängt — die Journale also immer mitgegeben.</p>
      <div class="flex gap-2">
        <button type="submit" class="rounded bg-green-700 px-3 py-1 text-xs font-semibold text-white hover:bg-green-600">
          ${template ? 'Speichern' : 'Anlegen'}</button>
        <button type="button" data-analysis-cancel class="rounded bg-gray-700 px-3 py-1 text-xs text-white hover:bg-gray-600">Abbrechen</button>
      </div>
    </form>`;
  }

  async function loadPrompts(container) {
    container.innerHTML = '<p class="text-gray-500">Lade…</p>';
    let data;
    try {
      data = await request(`${ENDPOINT}/prompts`);
    } catch (error) {
      container.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`;
      return;
    }
    const prompts = data.prompts || [];
    container.innerHTML = `
      <div class="mb-3 flex items-center justify-between">
        <p class="text-xs text-gray-500">${prompts.length} Vorlage(n)</p>
        <button type="button" data-analysis-new-prompt
          class="rounded bg-green-700 px-3 py-1 text-xs font-semibold text-white hover:bg-green-600">Neue Vorlage</button>
      </div>
      <div data-prompt-form-host class="mb-4"></div>
      <ul class="space-y-2">${prompts.map((template) => `
        <li class="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2.5" data-prompt="${escapeAttr(template.id)}">
          <p class="break-words text-sm font-medium text-gray-100">${escapeHtml(template.name)}
            <span class="text-xs font-normal text-gray-500">v${escapeHtml(template.version)}</span></p>
          <p class="mt-0.5 line-clamp-2 text-xs text-gray-500">${escapeHtml(String(template.body).slice(0, 200))}</p>
          <div class="mt-2 flex flex-wrap items-center gap-1.5">
            ${pill('data-analysis-edit-prompt', template.id, 'edit', 'Bearbeiten', 'border-gray-700 text-gray-300 hover:bg-gray-800')}
            ${trashButton('data-analysis-delete-prompt', template.id, 'Vorlage löschen')}
          </div>
        </li>`).join('') || '<li class="text-gray-500">Noch keine Vorlage.</li>'}</ul>`;
    container._prompts = prompts;
  }

  function openPromptForm(template) {
    const host = document.querySelector('[data-prompt-form-host]');
    if (!host) return;
    host.innerHTML = promptForm(template);
    host.querySelector('form')?.scrollIntoView({ block: 'nearest' });
  }

  async function savePrompt(form) {
    const templateId = form.dataset.promptForm;
    const payload = {
      name: form.querySelector('[data-prompt-name]')?.value || '',
      body: form.querySelector('[data-prompt-body]')?.value || '',
    };
    const url = templateId ? `${ENDPOINT}/prompts/${encodeURIComponent(templateId)}` : `${ENDPOINT}/prompts`;
    await request(url, { method: templateId ? 'PUT' : 'POST', body: JSON.stringify(payload) });
    window.showToast?.('Vorlage gespeichert.');
    loaded = {};
    loadPrompts(document.querySelector('[data-analysis-panel="prompts"]'));
  }

  async function deletePrompt(templateId) {
    if (!window.confirm('Diese Vorlage löschen?')) return;
    try {
      await request(`${ENDPOINT}/prompts/${encodeURIComponent(templateId)}`, { method: 'DELETE' });
      window.showToast?.('Vorlage gelöscht.');
      loaded = {};
      loadPrompts(document.querySelector('[data-analysis-panel="prompts"]'));
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  // ─── Protokoll ────────────────────────────────────────────────────
  // Jeder Lauf ist aufklappbar: Zeitpunkte, Dauer, Fehler, Ergebnis,
  // Zusammenfassung und alle Hinweise dieses Laufs mit ihrem Status - auch
  // abgehakte oder ignorierte, damit nichts verloren geht, was man aus
  // Versehen weggeklickt hat. Alles steht schon in der DB (analysis_runs,
  // analysis_notifications); hier wird es nur lesbar gemacht.
  const RUN_STATUS_LABEL = {
    completed: 'fertig', failed: 'fehlgeschlagen', running: 'läuft', queued: 'wartet', cancelled: 'abgebrochen',
  };
  const NOTIFY_STATUS_LABEL = { open: 'offen', ignored: 'ignoriert', done: 'abgehakt', taken: 'übernommen' };
  const logState = { runs: [], notifications: [], job: '', status: '' };
  const FILTER = 'rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100';

  function localStamp(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return shortTime(value);
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(date);
  }
  function duration(run) {
    const start = Date.parse(run.started_at || '');
    const end = Date.parse(run.completed_at || '');
    if (!start || !end || end < start) return '';
    const seconds = Math.round((end - start) / 1000);
    return seconds < 90 ? `${seconds} s` : `${Math.floor(seconds / 60)} min ${seconds % 60} s`;
  }
  function textBlock(label, text) {
    if (!String(text || '').trim()) return '';
    return `<div class="mt-2"><p class="text-[11px] uppercase tracking-wide text-gray-500">${label}</p>
      <div class="mt-0.5 max-h-72 overflow-y-auto whitespace-pre-wrap break-words rounded border border-gray-800 bg-gray-950/50 px-2 py-1.5 text-xs leading-relaxed text-gray-300">${escapeHtml(text)}</div></div>`;
  }
  function notificationRows(run) {
    const items = logState.notifications.filter((item) => item.run_id === run.id
      || (!item.run_id && item.job_id === run.job_id && item.run_date === run.run_date));
    if (!items.length) return '';
    return `<div class="mt-2"><p class="text-[11px] uppercase tracking-wide text-gray-500">Hinweise aus diesem Lauf</p>
      <ul class="mt-0.5 space-y-1">${items.map((item) => `
        <li class="rounded border border-gray-800 px-2 py-1 text-xs">
          <div class="flex flex-wrap items-center gap-x-2 text-[11px]">
            <span class="${item.status === 'open' ? 'text-blue-300' : 'text-gray-400'}">${escapeHtml(NOTIFY_STATUS_LABEL[item.status] || item.status)}</span>
            ${item.notify_at ? `<span class="text-gray-500">fällig ${escapeHtml(localStamp(item.notify_at))}</span>` : ''}
            <span class="text-gray-600">erstellt ${escapeHtml(localStamp(item.created_at))}</span>
          </div>
          <p class="mt-0.5 whitespace-pre-wrap break-words text-gray-300">${escapeHtml(item.body || item.title || '')}</p>
        </li>`).join('')}</ul></div>`;
  }
  function runEntry(run) {
    const meta = [
      run.started_at ? `gestartet ${localStamp(run.started_at)}` : `angelegt ${localStamp(run.created_at)}`,
      duration(run) ? `Dauer ${duration(run)}` : '',
      run.job_version ? `Job v${run.job_version}` : '',
    ].filter(Boolean).map(escapeHtml).join(' · ');
    const summary = String(run.summary || '').trim() !== String(run.result || '').trim() ? run.summary : '';
    return `<li><details class="group rounded-lg border border-gray-800 bg-gray-900/40">
      <summary class="cursor-pointer list-none px-3 py-2">
        <div class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
          <span class="rounded-full border border-current/30 px-1.5 text-[11px] ${STATUS_CLASS[run.status] || 'text-gray-300'}">${escapeHtml(RUN_STATUS_LABEL[run.status] || run.status)}</span>
          <span class="font-medium text-gray-200">${escapeHtml(run.job_name || 'Job')}</span>
          <span class="text-gray-400">Tag ${escapeHtml(run.run_date || '')}</span>
          ${run.stale ? `<span class="text-amber-400">veraltet</span>` : ''}
          <span class="ml-auto text-gray-600 transition-transform group-open:rotate-90">›</span>
        </div>
        <p class="mt-0.5 text-[11px] text-gray-500">${meta}</p>
        ${run.error_message ? `<p class="mt-0.5 line-clamp-2 break-words text-[11px] text-red-400">${escapeHtml(run.error_message)}</p>` : ''}
      </summary>
      <div class="border-t border-gray-800 px-3 pb-3 pt-1">
        ${run.completed_at ? `<p class="mt-1 text-[11px] text-gray-500">beendet ${escapeHtml(localStamp(run.completed_at))}</p>` : ''}
        ${run.stale ? `<p class="mt-1 text-[11px] text-amber-400">Veraltet: ${escapeHtml(run.stale_reason || '')}</p>` : ''}
        ${textBlock('Fehler', run.error_message)}
        ${textBlock('Ergebnis', run.result)}
        ${textBlock('Zusammenfassung', summary)}
        ${notificationRows(run)}
        ${!run.result && !run.error_message && !notificationRows(run) ? '<p class="mt-2 text-xs text-gray-500">Kein Ergebnis gespeichert.</p>' : ''}
      </div>
    </details></li>`;
  }
  function renderLog(panel) {
    // Die Läufe von „Hashtags nachtragen“ stehen in einem eigenen Platz darüber
    // (journal-analysis-tagging.js); ein Filterwechsel darf ihn nicht leeren.
    const container = panel.querySelector('[data-analysis-log-list]') || panel;
    const jobs = [...new Map(logState.runs.map((run) => [run.job_id, run.job_name || 'Job'])).entries()];
    const runs = logState.runs.filter((run) => (!logState.job || run.job_id === logState.job)
      && (!logState.status || run.status === logState.status));
    container.innerHTML = `
      <div class="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <select data-log-filter="job" class="${FILTER} max-w-[12rem]">
          <option value="">Alle Jobs</option>${jobs.map(([id, name]) => `<option value="${escapeAttr(id)}"${id === logState.job ? ' selected' : ''}>${escapeHtml(name)}</option>`).join('')}
        </select>
        <select data-log-filter="status" class="${FILTER}">
          <option value="">Jeder Status</option>${Object.entries(RUN_STATUS_LABEL).map(([value, label]) => `<option value="${value}"${value === logState.status ? ' selected' : ''}>${label}</option>`).join('')}
        </select>
        <span class="ml-auto text-gray-500">${runs.length} von ${logState.runs.length} Läufen</span>
      </div>
      <ul class="space-y-1.5">${runs.map(runEntry).join('') || '<li class="text-gray-500">Kein passender Lauf.</li>'}</ul>`;
  }
  async function loadLog(panel) {
    panel.innerHTML = '<div data-tagging-log class="mb-3"></div><div data-analysis-log-list></div>';
    window.JournlTaggingJob?.mountLog(panel.querySelector('[data-tagging-log]'));
    const container = panel.querySelector('[data-analysis-log-list]');
    container.innerHTML = '<p class="text-gray-500">Lade…</p>';
    try {
      const [runs, notifications] = await Promise.all([
        request(`${ENDPOINT}/runs?limit=100`),
        request(`${ENDPOINT}/notifications`).catch(() => ({ notifications: [] })),
      ]);
      logState.runs = runs.runs || [];
      logState.notifications = notifications.notifications || [];
    } catch (error) {
      container.innerHTML = `<p class="text-red-400">${escapeHtml(error.message)}</p>`;
      return;
    }
    renderLog(panel);
  }
  document.addEventListener('change', (event) => {
    const filter = event.target.closest?.('[data-log-filter]');
    if (!filter) return;
    logState[filter.dataset.logFilter] = filter.value;
    const body = document.querySelector('[data-analysis-panel="log"]');
    if (body) renderLog(body);
  });

  // ─── Verdrahtung ──────────────────────────────────────────────────
  document.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-analysis-tab]');
    if (tab) {
      event.preventDefault();
      showPanel(tab.dataset.analysisTab);
      return;
    }
    const body = document.querySelector('[data-analysis-panel="jobs"]');
    if (event.target.closest('[data-analysis-new]')) {
      event.preventDefault();
      openJobForm(null);
      return;
    }
    if (event.target.closest('[data-analysis-run-cancel]')) {
      event.preventDefault();
      cancelRun();
      return;
    }
    const rerun = event.target.closest('[data-analysis-rerun]');
    if (rerun) {
      event.preventDefault();
      runJobNow(rerun.dataset.analysisRerun, true);
      return;
    }
    const run = event.target.closest('[data-analysis-run]');
    if (run) {
      event.preventDefault();
      runJobNow(run.dataset.analysisRun);
      return;
    }
    const edit = event.target.closest('[data-analysis-edit]');
    if (edit && body) {
      event.preventDefault();
      const job = (body._jobs || []).find((item) => item.id === edit.dataset.analysisEdit);
      openJobForm(job || null);
      return;
    }
    const del = event.target.closest('[data-analysis-delete]');
    if (del) {
      event.preventDefault();
      deleteJob(del.dataset.analysisDelete);
      return;
    }
    const refreshModels = event.target.closest('[data-analysis-model-refresh]');
    if (refreshModels) {
      event.preventDefault();
      const form = refreshModels.closest('form');
      if (form) refreshModelOptions(form, { selectFirst: true });
      return;
    }
    const newPrompt = event.target.closest('[data-analysis-new-prompt]');
    if (newPrompt) {
      event.preventDefault();
      openPromptForm(null);
      return;
    }
    const editPrompt = event.target.closest('[data-analysis-edit-prompt]');
    if (editPrompt) {
      event.preventDefault();
      const host = document.querySelector('[data-analysis-panel="prompts"]');
      const template = (host?._prompts || []).find((item) => item.id === editPrompt.dataset.analysisEditPrompt);
      openPromptForm(template || null);
      return;
    }
    const delPrompt = event.target.closest('[data-analysis-delete-prompt]');
    if (delPrompt) {
      event.preventDefault();
      deletePrompt(delPrompt.dataset.analysisDeletePrompt);
      return;
    }
    if (event.target.closest('[data-analysis-cancel]')) {
      event.preventDefault();
      const form = event.target.closest('form');
      if (form) form.remove();
    }
  });

  document.addEventListener('change', (event) => {
    const field = event.target.closest('[data-field]');
    if (!field) return;
    const form = field.closest('form');
    if (!form) return;
    if (field.dataset.field === 'schedule_mode') {
      const mode = field.value;
      form.querySelector('[data-schedule-time]')?.classList.toggle('hidden', !['daily', 'custom'].includes(mode));
      form.querySelector('[data-schedule-interval]')?.classList.toggle('hidden', mode !== 'interval');
      form.querySelector('[data-schedule-custom]')?.classList.toggle('hidden', mode !== 'custom');
    }
    if (field.dataset.field === 'provider_id') {
      const model = form.querySelector('[data-field="model"]');
      if (model) model.value = field.selectedOptions[0]?.dataset.model || '';
      refreshModelOptions(form, { selectFirst: true });
      syncCloudWarning(form);
    }
  });

  document.addEventListener('submit', async (event) => {
    const jobFormElement = event.target.closest('[data-job-form]');
    if (jobFormElement) {
      event.preventDefault();
      try {
        await saveJob(jobFormElement);
      } catch (error) {
        window.showToast?.(error.message, true);
      }
      return;
    }
    const promptFormElement = event.target.closest('[data-prompt-form]');
    if (promptFormElement) {
      event.preventDefault();
      try {
        await savePrompt(promptFormElement);
      } catch (error) {
        window.showToast?.(error.message, true);
      }
    }
  });

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'analysis') return;
    buildShell();
    loaded = {};
    showPanel(activePanel);
  });

  // Deep-Link aus einer Pushover-Meldung von „Hashtags nachtragen“.
  function openTaggingLog() {
    activePanel = 'log';
    window.openSettingsGroup?.('analysis', 'Journal Analyse Verwaltung');
  }

  window.journlJournalAnalysis = {
    refresh: () => { loaded = {}; render(activePanel); },
    openNotifications: () => window.JournlAnalysisNotify?.open(),
    openTaggingLog,
  };
})();