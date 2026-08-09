/* Private writing-tab AI workflow.  In KI-Modus the #ai-… tag in the editor
 * selects the workflow; no journal entry is created by this path. */
(function () {
  let draftRevision = null;
  let draftTimer = null;
  let writeWorkflows = [];
  let activeHostJob = null;

  function editor() { return document.getElementById('simple-input'); }
  function isWritingMode() { return document.getElementById('template-select')?.value === '__ai_mode__'; }
  function safeShow(message, error) { if (typeof window.showToast === 'function') window.showToast(message, error); }

  async function loadDraft() {
    // The private AI draft must never populate the normal writing templates.
    if (!isWritingMode()) return;
    try {
      const res = await window.apiFetch('/api/write-ai/draft');
      if (!res.ok) return;
      const data = await res.json();
      draftRevision = data.revision;
      if (editor() && !editor().value) editor().value = data.content || '';
    } catch (_) { /* Offline editing remains available. */ }
  }
  async function saveDraft() {
    const field = editor();
    // Normal journal submissions are final and must not revive a private AI
    // draft on the next page load.
    if (!isWritingMode() || !field || draftRevision === null) return;
    const res = await window.apiFetch('/api/write-ai/draft', { method: 'POST', body: JSON.stringify({ content: field.value, revision: draftRevision }) });
    const data = await res.json().catch(() => ({}));
    if (res.ok) draftRevision = data.revision;
    // A remote device wins only when this local editor has not changed again.
    else if (res.status === 409 && field.value === (data.content || '')) draftRevision = data.revision;
  }
  function queueDraftSave() { clearTimeout(draftTimer); draftTimer = setTimeout(saveDraft, 450); }

  async function refreshWorkflows() {
    try {
      const res = await window.apiFetch('/api/brain/tag-catalog');
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return;
      // KI-Modus dispatches by the hashtag definition itself.  Existing
      // document-session definitions remain valid here as well; this client
      // path still uses the private draft endpoint and never saves a journal.
      writeWorkflows = Object.entries(data.catalog?.ai || {});
    } catch (_) { /* The submit handler shows a useful error if needed. */ }
  }
  function workflowFromText(text) {
    const tags = new Set();
    for (const match of text.matchAll(/(?:^|[^\p{L}\p{N}_#])#([\p{L}\p{N}_-]+)/gu)) {
      tags.add(match[1].normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('de-DE'));
    }
    return writeWorkflows.find(([tag]) => tags.has(tag));
  }
  function setBusy(busy) {
    const button = document.getElementById('submit-btn');
    const status = document.getElementById('ai-response-area');
    if (!button || !status) return;
    button.disabled = busy;
    button.classList.toggle('write-ai-working', busy);
    button.setAttribute('aria-busy', String(busy));
    button.textContent = busy ? 'KI arbeitet…' : 'KI Senden';
    if (busy) { status.classList.remove('hidden'); status.textContent = 'KI-Anfrage wird vorbereitet – du kannst weiter schreiben.'; }
  }

  function jobActionControls() {
    let controls = document.getElementById('write-ai-job-actions');
    if (controls) return controls;
    const submit = document.getElementById('submit-btn');
    if (!submit?.parentElement) return null;
    controls = document.createElement('div');
    controls.id = 'write-ai-job-actions';
    controls.className = 'relative hidden shrink-0';
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'h-10 w-9 rounded-lg border border-gray-700/60 text-gray-300 hover:bg-gray-800';
    toggle.textContent = '⋯';
    toggle.title = 'KI-Job-Aktionen';
    toggle.setAttribute('aria-label', 'KI-Job-Aktionen');
    const menu = document.createElement('div');
    menu.className = 'absolute right-0 z-30 mt-1 hidden min-w-36 rounded-lg border border-gray-700 bg-gray-900 p-1 shadow-xl';
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.dataset.jobAction = 'cancel';
    cancel.className = 'hidden w-full rounded px-3 py-2 text-left text-xs text-red-300 hover:bg-red-950/50 disabled:opacity-50';
    cancel.textContent = 'Abbrechen';
    const undo = document.createElement('button');
    undo.type = 'button'; undo.dataset.jobAction = 'undo';
    undo.className = 'hidden w-full rounded px-3 py-2 text-left text-xs text-sky-200 hover:bg-sky-950/50';
    undo.textContent = 'Rückgängig';
    const apply = document.createElement('button');
    apply.type = 'button'; apply.dataset.jobAction = 'apply';
    apply.className = 'hidden w-full rounded px-3 py-2 text-left text-xs text-amber-200 hover:bg-amber-950/50';
    apply.textContent = 'Vorschlag anwenden';
    toggle.addEventListener('click', () => menu.classList.toggle('hidden'));
    cancel.addEventListener('click', () => cancelHostJob());
    undo.addEventListener('click', () => undoCancelledHostJob());
    apply.addEventListener('click', () => applyHostProposal());
    menu.append(cancel, undo, apply); controls.append(toggle, menu);
    submit.insertAdjacentElement('afterend', controls);
    return controls;
  }

  function renderJobActions(job) {
    const controls = jobActionControls();
    if (!controls) return;
    const cancel = controls.querySelector('[data-job-action="cancel"]');
    const undo = controls.querySelector('[data-job-action="undo"]');
    const menu = controls.querySelector('div');
    const apply = controls.querySelector('[data-job-action="apply"]');
    const cancellable = ['queued', 'running', 'cancelling'].includes(job?.status);
    const undoable = job?.status === 'cancelled' && job?.can_undo;
    const applicable = job?.status === 'proposed' && job?.can_apply;
    controls.classList.toggle('hidden', !cancellable && !undoable && !applicable);
    cancel.classList.toggle('hidden', !cancellable);
    cancel.disabled = job?.status === 'cancelling';
    cancel.textContent = job?.status === 'cancelling' ? 'Abbruch angefragt' : 'Abbrechen';
    undo.classList.toggle('hidden', !undoable);
    apply.classList.toggle('hidden', !applicable);
    if (!cancellable && !undoable) menu.classList.add('hidden');
  }

  async function applyHostProposal() {
    if (!activeHostJob?.can_apply) return;
    const response = await window.apiFetch(`/api/write-ai/jobs/${encodeURIComponent(activeHostJob.id)}/apply`, { method: 'POST', body: JSON.stringify({}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { safeShow(data.error || 'Vorschlag konnte nicht angewendet werden.', true); return; }
    if (data.status === 'apply_requested') {
      const status = document.getElementById('ai-response-area');
      if (status) { status.classList.remove('hidden'); status.textContent = data.summary || 'Host-Worker wendet den Vorschlag an…'; }
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1000));
        const poll = await window.apiFetch(`/api/write-ai/jobs/${encodeURIComponent(activeHostJob.id)}`);
        const job = await poll.json().catch(() => ({}));
        if (!poll.ok) { safeShow(job.error || 'Anwendungsstatus konnte nicht gelesen werden.', true); return; }
        if (job.status === 'applied') { await finishHostResponse(job.apply_summary || 'Externes Schreibziel angewendet.', activeHostJob.snapshot); return; }
        if (job.status === 'error') { safeShow(job.error || 'Externes Schreibziel konnte nicht angewendet werden.', true); return; }
      }
      safeShow('Host-Worker hat die Anwendung noch nicht abgeschlossen.', true);
      return;
    }
    const status = document.getElementById('ai-response-area');
    if (status) { status.classList.remove('hidden'); status.textContent = data.summary || 'Schreibziel angewendet.'; }
    await finishHostResponse(data.summary || 'Schreibziel angewendet.', activeHostJob.snapshot);
  }

  function clearJobActions() {
    activeHostJob = null;
    renderJobActions(null);
  }

  async function cancelHostJob() {
    if (!activeHostJob) return;
    const response = await window.apiFetch(`/api/write-ai/jobs/${encodeURIComponent(activeHostJob.id)}/cancel`, { method: 'POST', body: JSON.stringify({}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { safeShow(data.error || 'KI-Job konnte nicht abgebrochen werden.', true); return; }
    activeHostJob.status = data.status;
    activeHostJob.can_undo = data.can_undo;
    renderJobActions(activeHostJob);
    const status = document.getElementById('ai-response-area');
    if (status) { status.classList.remove('hidden'); status.textContent = data.status === 'cancelled' ? 'KI-Job abgebrochen.' : 'Abbruch für Pi angefragt…'; }
  }

  async function finishHostResponse(responseText, snapshot) {
    const field = editor();
    if (!field) return;
    const merged = field.value !== snapshot ? `${responseText}\n\n${field.value}` : responseText;
    field.value = merged;
    await saveDraft();
    const status = document.getElementById('ai-response-area');
    if (status) { status.classList.remove('hidden'); status.textContent = 'KI-Ergebnis in den temporären Schreibstand übernommen.'; }
    clearJobActions();
    safeShow('KI-Antwort übernommen');
  }

  async function undoCancelledHostJob() {
    if (!activeHostJob?.can_undo) return;
    const response = await window.apiFetch(`/api/write-ai/jobs/${encodeURIComponent(activeHostJob.id)}/undo-cancel`, { method: 'POST', body: JSON.stringify({}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { safeShow(data.error || 'Abbruch konnte nicht rückgängig gemacht werden.', true); return; }
    activeHostJob.status = data.status;
    activeHostJob.can_undo = false;
    renderJobActions(activeHostJob);
    setBusy(true);
    try {
      const answer = await waitForHostJob(activeHostJob.id);
      await finishHostResponse(answer, activeHostJob.snapshot);
    } catch (error) {
      const status = document.getElementById('ai-response-area');
      if (status) { status.classList.remove('hidden'); status.textContent = `KI-Fehler: ${error?.message || 'KI-Job fehlgeschlagen.'}`; }
    } finally { setBusy(false); }
  }

  function activateWritingMode() {
    const writeTab = document.getElementById('tab-write');
    const select = document.getElementById('template-select');
    if (!select || writeTab?.classList.contains('hidden')) return false;
    if (select.value !== '__ai_mode__') {
      select.value = '__ai_mode__';
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }
    editor()?.focus({ preventScroll: true });
    safeShow('KI-Modus aktiviert');
    return true;
  }
  async function waitForHostJob(jobId) {
    const status = document.getElementById('ai-response-area');
    for (let attempt = 0; attempt < 360; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const response = await window.apiFetch(`/api/write-ai/jobs/${encodeURIComponent(jobId)}`);
      const job = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(job.error || 'KI-Job konnte nicht gelesen werden.');
      if (activeHostJob?.id === jobId) {
        activeHostJob.status = job.status;
        activeHostJob.can_undo = job.can_undo;
        activeHostJob.can_apply = job.can_apply;
        renderJobActions(activeHostJob);
      }
      if (job.status === 'completed') return job.response || '';
      if (job.status === 'proposed') {
        if (activeHostJob?.id === jobId) { activeHostJob.can_apply = job.can_apply; activeHostJob.proposal = job.proposal; renderJobActions(activeHostJob); }
        const status = document.getElementById('ai-response-area');
        if (status) { status.classList.remove('hidden'); status.textContent = `${job.proposal?.summary || 'Änderungsvorschlag bereit.'} Bitte „⋯“ → „Vorschlag anwenden“ wählen.`; }
        return null;
      }
      if (job.status === 'error') throw new Error(job.error || 'Pi-Job fehlgeschlagen.');
      if (job.status === 'cancelled') {
        const error = new Error('KI-Job wurde abgebrochen.');
        error.cancelled = true;
        throw error;
      }
      if (status) status.textContent = job.status === 'running'
        ? 'Pi arbeitet – du kannst weiter schreiben.'
        : job.status === 'cancelling' ? 'Pi-Abbruch wird ausgeführt…'
        : 'Wartet auf Host-Worker – du kannst weiter schreiben.';
    }
    throw new Error('Pi-Job hat das Zeitlimit überschritten.');
  }
  async function submitWritingAi() {
    const field = editor();
    if (!field?.value.trim()) { safeShow('Bitte Text eingeben.', true); return; }
    await refreshWorkflows();
    const selected = workflowFromText(field.value);
    if (!selected) { safeShow('Bitte einen konfigurierten #ai-Hashtag in den Text schreiben.', true); return; }
    const [workflowTag, workflow] = selected;
    const useHostWorker = workflow.agent === 'pi' || workflow.provider_id === '__host_worker__';
    const provider = (window.config?.ai_providers || []).find((item) => item.id === workflow.provider_id)
      || (window.config?.ai_providers || []).find((item) => item.model === workflow.model);
    if (!useHostWorker && !provider) { safeShow(`Für #${workflowTag} ist kein passender KI-Anbieter konfiguriert.`, true); return; }
    clearTimeout(draftTimer);
    await saveDraft();
    const snapshot = field.value;
    setBusy(true);
    try {
      const res = await window.apiFetch('/api/write-ai/submit', { method: 'POST', body: JSON.stringify({ workflow_tag: workflowTag, provider_id: useHostWorker ? '__host_worker__' : provider.id, model: useHostWorker ? workflow.model : provider.model, context_type: 'draft', text: snapshot, revision: draftRevision }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { safeShow(data.error || 'KI-Anfrage fehlgeschlagen.', true); return; }
      if (data.queued) {
        activeHostJob = { id: data.job_id, status: 'queued', can_undo: false, snapshot };
        renderJobActions(activeHostJob);
        data.response = await waitForHostJob(data.job_id);
        data.content = data.response;
      }
      // Never discard keystrokes made while the request was running.
      if (data.queued && data.response !== null) await finishHostResponse(data.response, snapshot);
      else field.value = field.value !== snapshot ? `${data.response}\n\n${field.value}` : data.content;
      // Persist both the result and any keystrokes made while Pi was running.
      // The revision-aware draft endpoint prevents a second device from being
      // overwritten if it saved in the meantime.
      if (data.revision) draftRevision = data.revision;
      await saveDraft();
      if (!data.queued) {
        const status = document.getElementById('ai-response-area');
        status.classList.remove('hidden'); status.textContent = 'KI-Ergebnis in den temporären Schreibstand übernommen.';
        safeShow('KI-Antwort übernommen');
      }
    } catch (error) {
      const message = error?.message || 'Netzwerkfehler – dein Text bleibt erhalten.';
      const status = document.getElementById('ai-response-area');
      if (status) { status.classList.remove('hidden'); status.textContent = error?.cancelled ? message : `KI-Fehler: ${message}`; }
      safeShow(message, !error?.cancelled);
    }
    finally { setBusy(false); }
  }

  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.altKey && !event.shiftKey && event.code === 'KeyK') {
      if (activateWritingMode()) event.preventDefault();
      return;
    }
    const field = event.target;
    if (event.key !== 'Tab' || field !== editor()) return;
    event.preventDefault();
    const start = field.selectionStart, end = field.selectionEnd;
    field.setRangeText('  ', start, end, 'end');
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
  document.addEventListener('input', (event) => {
    if (event.target === editor() && isWritingMode()) queueDraftSave();
  });

  const waitForPage = setInterval(() => {
    if (typeof window.handleAiMode !== 'function' || typeof window.handleSubmit !== 'function') return;
    clearInterval(waitForPage);
    const oldAiMode = window.handleAiMode;
    window.handleAiMode = function () {
      oldAiMode();
      refreshWorkflows();
      loadDraft();
      document.getElementById('submit-btn').textContent = isWritingMode() ? 'KI Senden' : 'Senden';
    };
    const oldTemplateChange = window.handleTemplateChange;
    window.handleTemplateChange = function (template) { oldTemplateChange(template); };
    const oldSubmit = window.handleSubmit;
    window.handleSubmit = function () { return isWritingMode() ? submitWritingAi() : oldSubmit(); };
    const select = document.getElementById('template-select');
    if (select) select.title = 'Template wählen · KI-Modus: Ctrl/⌘ + Alt/⌥ + K';
  }, 25);
})();
