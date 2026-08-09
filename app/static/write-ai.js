/* Private writing-tab AI workflow.  In KI-Modus the #ai-… tag in the editor
 * selects the workflow; no journal entry is created by this path. */
(function () {
  let draftRevision = null;
  let draftTimer = null;
  let writeWorkflows = [];

  function editor() { return document.getElementById('simple-input'); }
  function isWritingMode() { return document.getElementById('template-select')?.value === '__ai_mode__'; }
  function safeShow(message, error) { if (typeof window.showToast === 'function') window.showToast(message, error); }

  async function loadDraft() {
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
    if (!field || draftRevision === null) return;
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
      if (job.status === 'completed') return job.response || '';
      if (job.status === 'error') throw new Error(job.error || 'Pi-Job fehlgeschlagen.');
      if (status) status.textContent = job.status === 'running'
        ? 'Pi arbeitet – du kannst weiter schreiben.'
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
        data.response = await waitForHostJob(data.job_id);
        data.content = data.response;
      }
      // Never discard keystrokes made while the request was running.
      const localChanged = field.value !== snapshot;
      const merged = localChanged ? `${data.response}\n\n${field.value}` : data.content;
      field.value = merged;
      // Persist both the result and any keystrokes made while Pi was running.
      // The revision-aware draft endpoint prevents a second device from being
      // overwritten if it saved in the meantime.
      if (data.revision) draftRevision = data.revision;
      await saveDraft();
      const status = document.getElementById('ai-response-area');
      status.classList.remove('hidden'); status.textContent = 'KI-Ergebnis in den temporären Schreibstand übernommen.';
      safeShow('KI-Antwort übernommen');
    } catch (error) {
      const message = error?.message || 'Netzwerkfehler – dein Text bleibt erhalten.';
      const status = document.getElementById('ai-response-area');
      if (status) { status.classList.remove('hidden'); status.textContent = `KI-Fehler: ${message}`; }
      safeShow(message, true);
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
  document.addEventListener('input', (event) => { if (event.target === editor()) queueDraftSave(); });

  const waitForPage = setInterval(() => {
    if (typeof window.handleAiMode !== 'function' || typeof window.handleSubmit !== 'function') return;
    clearInterval(waitForPage);
    const oldAiMode = window.handleAiMode;
    window.handleAiMode = function () { oldAiMode(); refreshWorkflows(); document.getElementById('submit-btn').textContent = isWritingMode() ? 'KI Senden' : 'Senden'; };
    const oldTemplateChange = window.handleTemplateChange;
    window.handleTemplateChange = function (template) { oldTemplateChange(template); };
    const oldSubmit = window.handleSubmit;
    window.handleSubmit = function () { return isWritingMode() ? submitWritingAi() : oldSubmit(); };
    const select = document.getElementById('template-select');
    if (select) select.title = 'Template wählen · KI-Modus: Ctrl/⌘ + Alt/⌥ + K';
    loadDraft();
  }, 25);
})();
