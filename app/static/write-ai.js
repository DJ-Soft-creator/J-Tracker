/* Private writing-tab AI workflow.  It augments, but does not replace, the
 * established generic KI-Modus. */
(function () {
  let draftRevision = null;
  let draftTimer = null;
  let writeWorkflows = [];

  function editor() { return document.getElementById('simple-input'); }
  function controls() { return document.getElementById('ai-controls'); }
  function selectedWorkflow() { return document.getElementById('write-ai-workflow')?.value || ''; }
  function isWritingMode() { return document.getElementById('template-select')?.value === '__ai_mode__' && Boolean(selectedWorkflow()); }
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
      writeWorkflows = Object.entries(data.catalog?.ai || {}).filter(([, workflow]) => workflow.target === 'write_tab');
      const select = document.getElementById('write-ai-workflow');
      if (!select) return;
      const previous = select.value;
      select.replaceChildren(new Option('AI-Template wählen', ''));
      writeWorkflows.forEach(([tag, workflow]) => select.add(new Option(`#${tag}${workflow.provider_id ? '' : ' (Anbieter wählen)'}`, tag)));
      if (writeWorkflows.some(([tag]) => tag === previous)) select.value = previous;
      if (!select.value && writeWorkflows.length === 1) select.value = writeWorkflows[0][0];
    } catch (_) { /* Generic KI mode continues to work. */ }
  }
  function renderControls() {
    const parent = controls();
    if (!parent || document.getElementById('write-ai-workflow')) return;
    const wrapper = document.createElement('div');
    wrapper.id = 'write-ai-controls';
    wrapper.className = 'flex flex-nowrap gap-2 items-center shrink-0';
    wrapper.innerHTML = '<select id="write-ai-workflow" aria-label="AI-Template" class="h-10 w-44 rounded-lg bg-gray-800/10 border border-gray-700/10 px-3 text-sm text-gray-100"><option>AI-Template wählen</option></select><select id="write-ai-model" aria-label="KI-Modell" class="h-10 w-36 rounded-lg bg-gray-800/10 border border-gray-700/10 px-3 text-sm text-gray-100"></select><select id="write-ai-context" aria-label="KI-Kontext" class="h-10 w-44 rounded-lg bg-gray-800/10 border border-gray-700/10 px-3 text-sm text-gray-100"><option value="draft">Aktuelles Textfeld</option><option value="today_journal">Heutiges Journal</option></select>';
    parent.appendChild(wrapper);
    wrapper.querySelector('#write-ai-workflow').addEventListener('change', syncWorkflowProvider);
    document.getElementById('ai-provider-select')?.addEventListener('change', syncModel);
    syncModel();
    refreshWorkflows();
  }
  function syncWorkflowProvider() {
    const workflow = writeWorkflows.find(([tag]) => tag === selectedWorkflow())?.[1];
    const provider = document.getElementById('ai-provider-select');
    if (workflow?.provider_id && provider) provider.value = workflow.provider_id;
    syncModel();
  }
  function syncModel() {
    const providerId = document.getElementById('ai-provider-select')?.value;
    const provider = (window.config?.ai_providers || []).find((item) => item.id === providerId);
    const model = document.getElementById('write-ai-model');
    if (!model) return;
    model.replaceChildren(new Option(provider?.model || 'Kein Modell', provider?.model || ''));
  }
  function setBusy(busy) {
    const button = document.getElementById('submit-btn');
    const status = document.getElementById('ai-response-area');
    if (!button || !status) return;
    button.disabled = busy;
    button.classList.toggle('write-ai-working', busy);
    button.setAttribute('aria-busy', String(busy));
    button.textContent = busy ? 'KI arbeitet…' : 'An KI senden';
    if (busy) { status.classList.remove('hidden'); status.textContent = 'KI arbeitet – du kannst weiter schreiben.'; }
  }
  async function submitWritingAi() {
    const field = editor();
    const provider = document.getElementById('ai-provider-select');
    const workflow = writeWorkflows.find(([tag]) => tag === selectedWorkflow())?.[1];
    if (!field?.value.trim() || !workflow || !provider?.value) { safeShow('Bitte AI-Template, Anbieter und Text wählen.', true); return; }
    clearTimeout(draftTimer);
    await saveDraft();
    const snapshot = field.value;
    setBusy(true);
    try {
      const res = await window.apiFetch('/api/write-ai/submit', { method: 'POST', body: JSON.stringify({ workflow_tag: selectedWorkflow(), provider_id: provider.value, model: document.getElementById('write-ai-model')?.value || '', context_type: document.getElementById('write-ai-context').value, text: snapshot, revision: draftRevision }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { safeShow(data.error || 'KI-Anfrage fehlgeschlagen.', true); return; }
      // Never discard keystrokes made while the request was running.
      const localChanged = field.value !== snapshot;
      const merged = localChanged ? `${data.response}\n\n${field.value}` : data.content;
      field.value = merged;
      draftRevision = data.revision;
      if (localChanged) await saveDraft();
      const status = document.getElementById('ai-response-area');
      status.classList.remove('hidden'); status.textContent = 'KI-Ergebnis in den temporären Schreibstand übernommen.';
      safeShow('KI-Antwort übernommen');
    } catch (_) { safeShow('Netzwerkfehler – dein Text bleibt erhalten.', true); }
    finally { setBusy(false); }
  }

  document.addEventListener('keydown', (event) => {
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
    window.handleAiMode = function () { oldAiMode(); renderControls(); refreshWorkflows(); document.getElementById('submit-btn').textContent = isWritingMode() ? 'An KI senden' : 'KI senden'; };
    const oldTemplateChange = window.handleTemplateChange;
    window.handleTemplateChange = function (template) { oldTemplateChange(template); };
    const oldSubmit = window.handleSubmit;
    window.handleSubmit = function () { return isWritingMode() ? submitWritingAi() : oldSubmit(); };
    loadDraft();
  }, 25);
})();
