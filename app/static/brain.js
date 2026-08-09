/* Brain View client. Search is local server-side full text only; no LLM mode. */

const brainState = {
  mode: 'search',
  selectedTags: new Set(),
  tags: [],
  projects: [],
  results: new Map(),
  tasks: new Map(),
  taskStatus: 'all',
  taskPriority: 'all',
  contentFilter: 'all',
  catalogOpen: false,
  supportLoaded: false,
  editor: null,
  searchTimer: null,
  generation: 0,
  requests: new Map(),
};

const brainReadRequests = new Set(['bootstrap', 'projects', 'tags', 'results', 'results-more', 'tasks', 'files', 'document']);


function brainElement(id) {
  return document.getElementById(id);
}


function beginBrainRequest(key, contextSensitive = true) {
  const previous = brainState.requests.get(key);
  if (previous) previous.abort();
  const controller = new AbortController();
  const request = { key, controller, generation: brainState.generation, contextSensitive };
  brainState.requests.set(key, controller);
  return request;
}


function isCurrentBrainRequest(request) {
  return (!request.contextSensitive || request.generation === brainState.generation)
    && brainState.requests.get(request.key) === request.controller;
}


function finishBrainRequest(request) {
  if (brainState.requests.get(request.key) === request.controller) {
    brainState.requests.delete(request.key);
  }
}


function abortBrainRequest(key) {
  const controller = brainState.requests.get(key);
  if (controller) controller.abort();
  brainState.requests.delete(key);
}


function invalidateBrainContext() {
  brainState.generation += 1;
  brainReadRequests.forEach(abortBrainRequest);
}


function clearBrainState() {
  closeBrainInlineEditor();
  if (typeof window.clearHashtagCatalog === 'function') window.clearHashtagCatalog();
  brainState.generation += 1;
  brainState.requests.forEach((controller) => controller.abort());
  brainState.requests.clear();
  clearTimeout(brainState.searchTimer);
  brainState.searchTimer = null;
  brainState.mode = 'search';
  brainState.selectedTags.clear();
  brainState.tags = [];
  brainState.projects = [];
  brainState.results.clear();
  brainState.tasks.clear();
  brainState.taskStatus = 'all';
  brainState.taskPriority = 'all';
  brainState.contentFilter = 'all';
  brainState.catalogOpen = false;
  brainState.supportLoaded = false;
  brainState.editor = null;

  const query = brainElement('brain-query');
  const order = brainElement('brain-order');
  const tags = brainElement('brain-tags');
  const rangeStart = brainElement('brain-range-start');
  const rangeEnd = brainElement('brain-range-end');
  const browser = brainElement('brain-browser');
  if (query) query.value = '';
  if (order) order.value = 'newest';
  if (tags) tags.innerHTML = '';
  if (rangeStart) rangeStart.value = '';
  if (rangeEnd) rangeEnd.value = '';
  updateBrainRangeSummary();
  if (browser) {
    browser.innerHTML = '';
    browser.classList.remove('hidden');
  }
  setBrainIndexStatus(null);
  const searchMode = brainElement('brain-mode-search');
  const taskMode = brainElement('brain-mode-tasks');
  const journalsMode = brainElement('brain-mode-journals');
  const notesMode = brainElement('brain-mode-notes');
  const projectsMode = brainElement('brain-mode-projects');
  const familyMode = brainElement('brain-mode-family');
  if (searchMode) searchMode.className = 'text-sm text-green-400 font-medium';
  if (taskMode) taskMode.className = 'text-sm text-gray-500 hover:text-gray-300';
  if (journalsMode) journalsMode.className = 'text-sm text-gray-500 hover:text-gray-300';
  if (notesMode) notesMode.className = 'text-sm text-gray-500 hover:text-gray-300';
  if (projectsMode) projectsMode.className = 'text-sm text-gray-500 hover:text-gray-300';
  if (familyMode) familyMode.className = 'text-sm text-gray-500 hover:text-gray-300';
  updateBrainModeControls();
  if (typeof window.refreshBrainProjectSelector === 'function') {
    window.refreshBrainProjectSelector();
  }
}
window.clearBrainState = clearBrainState;


function brainProjectTitle(path) {
  const project = brainState.projects.find((item) => item.path === path);
  return project ? project.title : path;
}


window.brainProjectOptions = function brainProjectOptions() {
  return brainState.projects.slice();
};


function setBrainProjects(projects) {
  const optionsChanged = projects.length !== brainState.projects.length || projects.some((project, index) => (
    project.path !== brainState.projects[index]?.path || project.title !== brainState.projects[index]?.title
  ));
  brainState.projects = projects;
  if (optionsChanged && typeof window.refreshBrainProjectSelector === 'function') {
    window.refreshBrainProjectSelector();
  }
}


async function loadBrainProjects() {
  const request = beginBrainRequest('projects');
  try {
    const response = await apiFetch('/api/brain/projects', { signal: request.controller.signal });
    if (!isCurrentBrainRequest(request) || !response.ok) return;
    const data = await response.json();
    if (!isCurrentBrainRequest(request)) return;
    setBrainProjects(data.projects || []);
  } catch {
    // Projects are optional for search and task browsing.
  } finally {
    finishBrainRequest(request);
  }
}
window.loadBrainProjects = loadBrainProjects;


function brainQueryParams(includeTaskFilters) {
  const params = new URLSearchParams();
  const query = brainElement('brain-query').value.trim();
  if (query) params.set('q', query);
  brainState.selectedTags.forEach((tag) => params.append('tags', tag));
  const rangeStart = brainElement('brain-range-start').value;
  const rangeEnd = brainElement('brain-range-end').value;
  if (rangeStart) params.set('start_date', rangeStart);
  if (rangeEnd) params.set('end_date', rangeEnd);
  if (brainState.contentFilter !== 'all') params.set('kind', brainState.contentFilter);
  if (includeTaskFilters) {
    params.set('status', brainState.taskStatus);
    params.set('priority', brainState.taskPriority);
  } else {
    params.set('order', brainElement('brain-order').value);
  }
  return params;
}


function formatBrainRangeDate(value) {
  if (!value) return '';
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}.${month}.${year}` : value;
}


function updateBrainRangeSummary() {
  const start = brainElement('brain-range-start')?.value || '';
  const end = brainElement('brain-range-end')?.value || '';
  const summary = brainElement('brain-range-summary');
  const clear = brainElement('brain-range-clear');
  if (summary) {
    summary.textContent = start || end
      ? `${formatBrainRangeDate(start) || '…'} – ${formatBrainRangeDate(end) || '…'}`
      : '';
  }
  if (clear) clear.classList.toggle('hidden', !start && !end);
}


function showBrainDatePicker(inputId) {
  const input = brainElement(inputId);
  if (!input) return;
  if (typeof input.showPicker === 'function') input.showPicker();
  else input.focus();
}


function setBrainIndexStatus(pending) {
  const status = brainElement('brain-index-status');
  if (!status) return;
  status.textContent = pending === null ? '' : (pending ? 'Erstindex wird im Hintergrund aufgebaut.' : 'Lokale Volltextsuche');
}


async function loadBrainTags() {
  if (!brainElement('brain-tags')) return;
  const request = beginBrainRequest('tags');
  try {
    const response = await apiFetch('/api/brain/tags', { signal: request.controller.signal });
    if (!isCurrentBrainRequest(request) || !response.ok) return;
    const data = await response.json();
    if (!isCurrentBrainRequest(request)) return;
    setBrainTags(data.tags || [], data.index_pending);
  } catch {
    if (isCurrentBrainRequest(request)) setBrainTags([], null);
  } finally {
    finishBrainRequest(request);
  }
}


function setBrainTags(items, indexPending) {
  const tags = brainElement('brain-tags');
  if (!tags) return;
  if (indexPending !== null) setBrainIndexStatus(indexPending);
  brainState.tags = items;
  const availableTags = new Set(items.map((tag) => tag.name));
  brainState.selectedTags.forEach((tag) => {
    if (!availableTags.has(tag)) brainState.selectedTags.delete(tag);
  });
  tags.innerHTML = items.map((tag) => {
      const active = brainState.selectedTags.has(tag.name);
      const status = typeof window.hashtagApprovalStatus === 'function'
        ? window.hashtagApprovalStatus(tag.name) : 'approved';
      const color = tag.scope === 'family'
        ? (active ? 'border-pink-400 bg-pink-500/15 text-pink-200' : 'border-pink-900 text-pink-300 hover:text-pink-200')
        : status === 'ai'
        ? (active ? 'border-sky-400 bg-sky-500/15 text-sky-200' : 'border-sky-900 text-sky-200 hover:text-sky-100')
        : (status === 'approved'
          ? (active ? 'border-green-500 bg-green-500/15 text-green-300' : 'border-gray-700 text-gray-400 hover:text-white')
          : (active ? 'border-orange-500 bg-orange-500/15 text-orange-300' : 'border-orange-900 text-orange-300 hover:text-orange-100'));
      return `<button type="button" data-brain-tag="${escapeHtmlAttr(tag.name)}" class="text-xs rounded-full px-2 py-1 border ${color}">${escapeHtml(tag.name)} <span class="text-gray-500">${tag.count}</span></button>`;
    }).join('');
  tags.querySelectorAll('[data-brain-tag]').forEach((button) => {
    button.addEventListener('click', () => {
      const tag = button.getAttribute('data-brain-tag');
      if (brainState.selectedTags.has(tag)) brainState.selectedTags.delete(tag);
      else brainState.selectedTags.add(tag);
      invalidateBrainContext();
      setBrainTags(brainState.tags, null);
      loadBrainCurrentMode();
    });
  });
}


function brainSourceClass(source) {
  if (source === 'family') return 'bg-pink-950 text-pink-200 border-pink-800';
  if (source === 'archive') return 'bg-amber-950 text-amber-300 border-amber-800';
  return 'bg-gray-800 text-gray-300 border-gray-700';
}


function brainTagBadges(tags) {
  return (tags || []).map((tag) => {
    const name = typeof tag === 'string' ? tag : tag.name;
    const type = typeof tag === 'object' ? tag.type : (
      typeof window.hashtagApprovalStatus === 'function' ? window.hashtagApprovalStatus(name) : 'standard'
    );
    const approved = typeof tag === 'object'
      ? Boolean(tag.approved)
      : (typeof window.hashtagApprovalStatus !== 'function' || window.hashtagApprovalStatus(name) === 'approved');
    const color = type === 'ai' ? 'text-sky-200' : (approved ? 'text-green-300' : 'text-orange-300');
    return `<span class="text-xs ${color}">#${escapeHtml(name)}</span>`;
  }).join(' ');
}


function brainEditButton(docId, fingerprint = '') {
  return `<button type="button" data-brain-edit data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" class="text-green-500 hover:text-green-400 transition" title="Bearbeiten" aria-label="Bearbeiten">✏️</button>`;
}


function brainManageButton(docId, label) {
  return `<button type="button" data-brain-manage data-doc-id="${escapeHtmlAttr(docId)}" class="inline-flex items-center gap-1 px-1 py-0.5 text-[11px] text-gray-600 hover:text-gray-300" title="${escapeHtmlAttr(label)} verwalten"><span aria-hidden="true">⚙</span><span>${escapeHtml(label)} verwalten</span></button>`;
}


function bindBrainCardActions(container) {
  container.querySelectorAll('[data-brain-edit]').forEach((button) => {
    button.addEventListener('click', () => openBrainDocument(
      button.dataset.docId,
      button.dataset.fingerprint || '',
      button.closest('[data-brain-card]'),
    ));
  });
  container.querySelectorAll('[data-brain-manage]').forEach((button) => {
    button.addEventListener('click', () => openBrainAccessManagerForDocument(button.dataset.docId));
  });
}


function renderBrainResults(results, pagination = {}) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  brainState.results = new Map(results.map((item) => [item.fingerprint, item]));
  if (!results.length) {
    container.innerHTML = '<p class="text-gray-500 text-center mt-8">Keine sichtbaren Markdown-Eintraege gefunden.</p>';
    return;
  }
  const more = pagination.has_more
    ? `<div class="flex justify-center pt-2"><button type="button" data-brain-load-more class="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:border-gray-500 hover:text-white">Mehr laden (${results.length} von ${pagination.total})</button></div>`
    : '';
  container.innerHTML = results.map((item) => {
    const tags = brainTagBadges(item.tags);
    const project = item.project ? `<span class="text-xs text-amber-300">${escapeHtml(brainProjectTitle(item.project))}</span>` : '';
    return `<article data-brain-card data-doc-id="${escapeHtmlAttr(item.doc_id)}" class="min-w-0 max-w-full overflow-hidden bg-gray-900 border border-gray-800 rounded-xl p-4 ${item.source === 'family' ? 'brain-family-entry' : ''}">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass(item.source)}">${escapeHtml(item.source_label)}</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(item.path)}</span>
          ${tags}${project}
          <span>${escapeHtml(item.kind)}</span><span>${escapeHtml(item.date)}</span>
          ${item.read_only ? '<span class="text-amber-400">nur lesen</span>' : ''}
        </div>
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-2">
          ${item.management?.can_manage ? brainManageButton(item.doc_id, item.path.startsWith('projects/') ? 'Projekt' : 'Notiz') : ''}
          ${item.read_only ? '' : brainEditButton(item.doc_id, item.fingerprint)}
        </div>
      </div>
      <div data-brain-display class="min-w-0 max-w-full w-full break-words whitespace-pre-wrap text-sm leading-relaxed text-gray-200">${typeof window.renderJournalText === 'function' ? window.renderJournalText(item.snippet) : escapeHtml(item.snippet)}</div>
      <div class="min-w-0 max-w-full mt-3 flex flex-wrap gap-2 items-center">
        <button type="button" data-brain-metadata data-reference-type="block" data-doc-id="${escapeHtmlAttr(item.doc_id)}" data-fingerprint="${escapeHtmlAttr(item.fingerprint)}" class="text-xs text-gray-500 hover:text-white">Tags bearbeiten</button>
      </div>
    </article>`;
  }).join('') + more;
  bindBrainCardActions(container);
  container.querySelectorAll('[data-brain-metadata]').forEach((button) => {
    button.addEventListener('click', () => editBrainMetadata(
      button.dataset.referenceType, button.dataset.docId, button.dataset.fingerprint,
    ));
  });
  container.querySelector('[data-brain-load-more]')?.addEventListener('click', loadMoreBrainResults);
}


async function loadBrainResults() {
  if (!['search', 'journals'].includes(brainState.mode)) return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Suche...</p>';
  const request = beginBrainRequest('results');
  try {
    const params = brainQueryParams(false);
    params.set('limit', '100');
    const response = await apiFetch('/api/brain/search?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Suche fehlgeschlagen.')}</p>`;
      return;
    }
    setBrainIndexStatus(data.index_pending);
    renderBrainResults(data.results || [], data);
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler bei der Suche.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadMoreBrainResults(event) {
  if (!['search', 'journals'].includes(brainState.mode)) return;
  const button = event.currentTarget;
  button.disabled = true;
  const request = beginBrainRequest('results-more');
  const params = brainQueryParams(false);
  params.set('limit', '100');
  params.set('offset', String(brainState.results.size));
  try {
    const response = await apiFetch('/api/brain/search?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Weitere Ergebnisse konnten nicht geladen werden.', true);
      button.disabled = false;
      return;
    }
    const combined = [...brainState.results.values(), ...(data.results || [])];
    renderBrainResults(combined, data);
  } catch {
    if (isCurrentBrainRequest(request)) {
      showToast('Netzwerkfehler beim Nachladen der Ergebnisse.', true);
      button.disabled = false;
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function updateTagCatalog(payload) {
  const response = await apiFetch('/api/brain/tag-catalog', { method: 'POST', body: JSON.stringify(payload) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Katalog konnte nicht aktualisiert werden.');
  if (typeof window.loadFamilyHashtags === 'function') await window.loadFamilyHashtags(true);
  if (typeof window.refreshWriteHashtagCatalog === 'function') await window.refreshWriteHashtagCatalog();
  await Promise.all([loadBrainTags(), loadTagCatalog()]);
}


function tagCatalogSection(title, scope, catalog, canManage) {
  const tagColor = scope === 'family' ? ' journal-family-tag' : '';
  const canonical = (catalog.canonical || []).map((tag) => `<span class="rounded bg-gray-800 px-2 py-1${tagColor}">#${escapeHtml(tag)}${canManage ? ` <button type="button" data-tag-remove="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-red-400">×</button>` : ''}</span>`).join(' ') || '<span class="text-gray-600">Keine</span>';
  const proposals = (catalog.proposals || []).map((tag) => `<span class="rounded border border-amber-800 px-2 py-1${tagColor}">#${escapeHtml(tag)}${canManage ? ` <button type="button" data-tag-approve="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-green-400">Freigeben</button>` : ''}</span>`).join(' ') || '<span class="text-gray-600">Keine Vorschläge</span>';
  const aliases = Object.entries(catalog.aliases || {}).map(([alias, target]) => `<span class="rounded bg-gray-800 px-2 py-1${tagColor}">#${escapeHtml(alias)} → #${escapeHtml(target)}${canManage ? ` <button type="button" data-tag-remove-alias="${escapeHtmlAttr(alias)}" data-tag-scope="${scope}" class="text-red-400">×</button>` : ''}</span>`).join(' ') || '<span class="text-gray-600">Keine Aliase</span>';
  return `<section class="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-2"><div class="flex justify-between gap-2"><h3 class="font-medium text-gray-100">${title}</h3>${canManage ? `<button type="button" data-tag-alias data-tag-scope="${scope}" class="text-sm text-green-400">Alias hinzufügen</button>` : ''}</div><p class="text-xs text-gray-500">Freigegeben</p><div class="flex flex-wrap gap-1">${canonical}</div><p class="text-xs text-gray-500">Vorschläge</p><div class="flex flex-wrap gap-1">${proposals}</div><p class="text-xs text-gray-500">Aliase</p><div class="flex flex-wrap gap-1">${aliases}</div></section>`;
}


function aiWorkflowSection(workflows) {
  const providers = '<option value="__host_worker__">Host-Worker · Pi</option>' + (window.config?.ai_providers || []).map((provider) => `<option value="${escapeHtmlAttr(provider.id || '')}">${escapeHtml(provider.label || provider.id || '')} · ${escapeHtml(provider.model || '')}</option>`).join('');
  const cards = Object.entries(workflows || {}).map(([tag, workflow]) => `<article data-ai-workflow-card data-tag="${escapeHtmlAttr(tag)}" class="rounded-lg border border-sky-900/70 bg-sky-950/20 p-3 space-y-2">
    <div class="flex items-center justify-between gap-2"><strong class="text-sm text-sky-200">#${escapeHtml(tag)}</strong><button type="button" data-ai-workflow-remove class="text-xs text-red-400 hover:text-red-300">Entfernen</button></div>
    <div class="grid gap-2 sm:grid-cols-2">
      <label class="text-xs text-gray-500">Agent<select data-ai-agent class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="opencode" ${workflow.agent === 'opencode' ? 'selected' : ''}>OpenCode</option><option value="hermes" ${workflow.agent === 'hermes' ? 'selected' : ''}>Hermes</option><option value="pi" ${workflow.agent === 'pi' ? 'selected' : ''}>Pi</option></select></label>
      <label class="text-xs text-gray-500">Modell<input data-ai-model value="${escapeHtmlAttr(workflow.model || '')}" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100" /></label>
    </div>
    <label class="block text-xs text-gray-500">Schutzklassifizierung<select data-ai-classification class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="public" ${workflow.classification === 'public' ? 'selected' : ''}>Public</option><option value="internal" ${(workflow.classification || 'internal') === 'internal' ? 'selected' : ''}>Intern</option><option value="confidential" ${workflow.classification === 'confidential' ? 'selected' : ''}>Vertraulich</option><option value="secret" ${workflow.classification === 'secret' ? 'selected' : ''}>Geheim</option></select></label>
    <label class="block text-xs text-gray-500">Klassifizierung<select data-ai-context class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="files" ${workflow.context === 'files' ? 'selected' : ''}>Diverse Files</option><option value="journal" ${workflow.context === 'journal' ? 'selected' : ''}>Komplettes Journal</option><option value="block" ${workflow.context === 'block' ? 'selected' : ''}>Nur Blockeintrag</option></select></label>
    <div class="grid gap-2 sm:grid-cols-2"><label class="text-xs text-gray-500">Zielbereich<select data-ai-target class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="document" ${(workflow.target || 'document') === 'document' ? 'selected' : ''}>Dokument-Session</option><option value="write_tab" ${workflow.target === 'write_tab' ? 'selected' : ''}>Schreiben-Tab (temporär)</option></select></label><label class="text-xs text-gray-500">Standard-Anbieter<select data-ai-provider class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="">Bei Anfrage wählen</option>${providers}</select></label></div>
    <label class="block text-xs text-gray-500">Prompt<textarea data-ai-prompt rows="4" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100">${escapeHtml(workflow.prompt || '')}</textarea></label>
    <label class="block text-xs text-gray-500">Kontextdateien, eine pro Zeile<textarea data-ai-files rows="2" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-gray-100">${escapeHtml((workflow.context_files || []).join('\n'))}</textarea></label>
    <button type="button" data-ai-workflow-save class="rounded bg-sky-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-sky-600">Workflow speichern</button>
  </article>`).join('') || '<p class="text-sm text-gray-600">Noch keine AI-Hashtags konfiguriert.</p>';
  return `<section class="bg-gray-900 border border-sky-900/60 rounded-xl p-4 space-y-3"><div class="flex items-center justify-between gap-2"><div><h3 class="font-medium text-sky-200">AI-Hashtags</h3><p class="text-xs text-gray-500">Jedes Hashtag startet eine eigene persistente Agent-Session.</p></div><button type="button" data-ai-workflow-add class="rounded border border-sky-800 px-2 py-1 text-sm text-sky-300 hover:bg-sky-950">+ AI-Hashtag</button></div><div class="space-y-3">${cards}</div></section>`;
}


function knowledgePathOptions(options, scope, selected = '') {
  const matching = (options || []).filter((option) => option.scope === scope);
  const currentExists = matching.some((option) => option.path === selected);
  const choices = matching.map((option) => `<option value="${escapeHtmlAttr(option.path)}"${option.path === selected ? ' selected' : ''}>${escapeHtml(option.label || option.path)}</option>`).join('');
  const missing = selected && !currentExists ? `<option value="${escapeHtmlAttr(selected)}" selected>Nicht mehr verfügbar: ${escapeHtml(selected)}</option>` : '';
  return `<option value="">Quelle auswählen…</option>${missing}${choices}`;
}


function knowledgeKindOptions(selected = 'reference') {
  const labels = { reference: 'Referenz', constraints: 'Rahmenbedingungen', glossary: 'Glossar', examples: 'Beispiele' };
  return Object.entries(labels).map(([value, label]) => `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`).join('');
}


function knowledgeSourcesSection(sources, options, canManagePersonal, canManageFamily) {
  const renderScope = (scope, entries, canManage) => Object.entries(entries || {}).map(([tag, source]) => {
    const path = source.path || '';
    const kind = source.kind || 'reference';
    const description = source.description || '';
    const controls = canManage ? `<div class="mt-2 grid gap-2"><div class="grid gap-2 sm:grid-cols-2"><select data-knowledge-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100">${knowledgePathOptions(options, scope, path)}</select><select data-knowledge-kind class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${knowledgeKindOptions(kind)}</select></div><input data-knowledge-description maxlength="240" value="${escapeHtmlAttr(description)}" placeholder="Zweck für Pi, optional" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100" /><div class="flex gap-2"><button type="button" data-knowledge-save class="text-xs text-green-400 hover:text-green-300">Speichern</button><button type="button" data-knowledge-remove class="text-xs text-red-400 hover:text-red-300">Entfernen</button></div></div>` : '';
    const sourceInfo = `${scope}:${path} · ${kind}`;
    const descriptionInfo = description ? `<p class="mt-1 text-xs text-gray-500">${escapeHtml(description)}</p>` : '';
    return `<article data-knowledge-source-card data-tag="${escapeHtmlAttr(tag)}" data-scope="${scope}" class="rounded border border-violet-900/60 bg-violet-950/20 p-3"><div class="flex flex-wrap items-center justify-between gap-2"><strong class="text-sm text-violet-200">#${escapeHtml(tag)}</strong><span class="font-mono text-xs text-gray-400">${escapeHtml(sourceInfo)}</span></div>${descriptionInfo}${controls}</article>`;
  }).join('') || '<p class="text-sm text-gray-600">Keine Quellen.</p>';
  const scopes = `<option value="personal">Persönlich</option>${canManageFamily ? '<option value="family">Family</option>' : ''}`;
  const hasOptions = (options || []).length > 0;
  const add = canManagePersonal ? `<form data-knowledge-add class="grid gap-2 pt-2"><div class="grid gap-2 sm:grid-cols-4"><input data-knowledge-tag required maxlength="80" placeholder="hashtag ohne #" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><select data-knowledge-scope class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100">${scopes}</select><select data-knowledge-kind class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100">${knowledgeKindOptions()}</select><select data-knowledge-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100"></select></div><div class="flex flex-wrap gap-2"><input data-knowledge-description maxlength="240" placeholder="Zweck für Pi, optional" class="min-w-56 flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><button ${hasOptions ? '' : 'disabled'} class="rounded bg-violet-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-600 disabled:cursor-not-allowed disabled:opacity-50">Quelle hinzufügen</button></div></form>${hasOptions ? '' : '<p class="text-xs text-amber-300">Lege zuerst eine persönliche oder sichtbare Family-Notiz bzw. ein Projekt im Brain an.</p>'}` : '';
  return `<section class="bg-gray-900 border border-violet-900/60 rounded-xl p-4 space-y-3"><div><h3 class="font-medium text-violet-200">Knowledge-Quellen</h3><p class="text-xs text-gray-500">Nur diese ausdrücklich zugeordneten Tags werden bei „KI Senden“ als einmaliger Kontext-Snapshot als Referenzmaterial übergeben; sie sind keine Pi-Anweisungen.</p></div><div class="space-y-2"><p class="text-xs text-gray-500">Persönlich</p>${renderScope('personal', sources.personal, canManagePersonal)}<p class="pt-1 text-xs text-gray-500">Family</p>${renderScope('family', sources.family, canManageFamily)}</div>${add}</section>`;
}


function bindKnowledgeSourceControls(container, knowledgeOptions) {
  const addForm = container.querySelector('[data-knowledge-add]');
  const refreshPaths = (scopeSelect, pathSelect) => {
    pathSelect.innerHTML = knowledgePathOptions(knowledgeOptions, scopeSelect.value);
  };
  if (addForm) {
    const scopeSelect = addForm.querySelector('[data-knowledge-scope]');
    const pathSelect = addForm.querySelector('[data-knowledge-path]');
    refreshPaths(scopeSelect, pathSelect);
    scopeSelect.addEventListener('change', () => refreshPaths(scopeSelect, pathSelect));
    addForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!pathSelect.value) { showToast('Bitte eine vorhandene Quelle auswählen.', true); return; }
      try {
        await updateTagCatalog({ action: 'save', scope: 'knowledge', tag: addForm.querySelector('[data-knowledge-tag]').value, source: { path: pathSelect.value, kind: addForm.querySelector('[data-knowledge-kind]').value, description: addForm.querySelector('[data-knowledge-description]').value.trim(), family: scopeSelect.value === 'family' } });
      } catch (error) { showToast(error.message, true); }
    });
  }
  container.querySelectorAll('[data-knowledge-save]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-knowledge-source-card]');
    const path = card.querySelector('[data-knowledge-path]').value;
    if (!path) { showToast('Bitte eine vorhandene Quelle auswählen.', true); return; }
    try {
      await updateTagCatalog({ action: 'save', scope: 'knowledge', tag: card.dataset.tag, source: { path, kind: card.querySelector('[data-knowledge-kind]').value, description: card.querySelector('[data-knowledge-description]').value.trim(), family: card.dataset.scope === 'family' } });
    } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-knowledge-remove]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-knowledge-source-card]');
    try {
      await updateTagCatalog({ action: 'remove', scope: 'knowledge', tag: card.dataset.tag, source: { family: card.dataset.scope === 'family' } });
    } catch (error) { showToast(error.message, true); }
  }));
}

function writeTargetPathOptions(options, scope, selected = '') {
  return knowledgePathOptions(options, scope, selected);
}

function writeTargetsSection(targets, options, externalRoots, canManagePersonal, canManageFamily) {
  const card = (scope, entries, canManage) => Object.entries(entries || {}).map(([tag, target]) => {
    const policy = target.file_policy || 'markdown_only';
    const external = scope === 'host';
    const rootOptions = (externalRoots || []).map((root) => `<option value="${escapeHtmlAttr(root.id)}"${root.id === target.root_id ? ' selected' : ''}>${escapeHtml(root.label)} · ${escapeHtml(root.path)}</option>`).join('');
    const pathControl = external ? `<div class="grid gap-2 sm:grid-cols-2"><select data-write-target-root class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${rootOptions}</select><input data-write-target-path value="${escapeHtmlAttr(target.path || '')}" placeholder="/docker-storage/Projekt/Ordner" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100" /></div>` : `<select data-write-target-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100">${writeTargetPathOptions(options, scope, target.path || '')}</select>`;
    const controls = canManage ? `<div class="mt-2 grid gap-2">${pathControl}<label class="flex items-center gap-2 text-xs text-gray-300"><input data-write-target-all type="checkbox" ${policy === 'all_regular_files' ? 'checked' : ''} /> Alle regulären Dateien</label><div class="flex gap-2"><button type="button" data-write-target-save class="text-xs text-green-400">Speichern</button><button type="button" data-write-target-remove class="text-xs text-red-400">Entfernen</button></div></div>` : '';
    return `<article data-write-target-card data-tag="${escapeHtmlAttr(tag)}" data-scope="${scope}" class="rounded border border-amber-900/60 bg-amber-950/20 p-3"><div class="flex flex-wrap justify-between gap-2"><strong class="text-sm text-amber-200">#${escapeHtml(tag)}</strong><span class="font-mono text-xs text-gray-400">${escapeHtml(scope)}:${escapeHtml(target.path || '')}</span></div><p class="mt-1 text-xs text-gray-500">${policy === 'all_regular_files' ? 'Alle regulären Textdateien' : 'Nur Markdown-Dateien (.md)'}</p>${controls}</article>`;
  }).join('') || '<p class="text-xs text-gray-600">Keine</p>';
  const add = (canManagePersonal || canManageFamily) ? `<form data-write-target-add class="grid gap-2"><div class="grid gap-2 sm:grid-cols-2"><input data-write-target-tag placeholder="schreibziel-projekt" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><select data-write-target-scope class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100"><option value="personal">Persönlich</option>${canManageFamily ? '<option value="family">Family</option>' : ''}</select></div><select data-write-target-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100"></select><label class="text-xs text-gray-300"><input data-write-target-all type="checkbox" /> Alle regulären Dateien statt nur .md</label><button class="rounded bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white">Schreibziel hinzufügen</button></form>` : '';
  const externalAdd = externalRoots.length ? `<form data-external-write-target-add class="grid gap-2"><input data-write-target-tag placeholder="schreibziel-feature-request" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><div class="grid gap-2 sm:grid-cols-2"><select data-write-target-root class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${externalRoots.map((root) => `<option value="${escapeHtmlAttr(root.id)}">${escapeHtml(root.label)} · ${escapeHtml(root.path)}</option>`).join('')}</select><input data-write-target-path placeholder="Absoluter Linux-Pfad unter der Wurzel" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100" /></div><label class="text-xs text-gray-300"><input data-write-target-all type="checkbox" /> Alle regulären Dateien statt nur .md</label><button class="rounded bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white">Externes Schreibziel hinzufügen</button></form>` : '<p class="text-xs text-gray-500">Keine externe Host-Wurzel in host_worker.json freigegeben.</p>';
  return `<section class="bg-gray-900 border border-amber-900/60 rounded-xl p-4 space-y-3"><div><h3 class="font-medium text-amber-200">KI-Schreibziele</h3><p class="text-xs text-gray-500">Ein #schreibziel-… erlaubt Pi nur einen katalogisierten Ordnerbaum. Pi erstellt erst einen Vorschlag; die Anwendung schreibt erst nach „Anwenden“ revisionsgesichert mit Backup.</p></div><p class="text-xs text-gray-500">Persönlich</p>${card('personal', targets.personal, canManagePersonal)}<p class="text-xs text-gray-500">Family</p>${card('family', targets.family, canManageFamily)}<p class="text-xs text-gray-500">Externe Host-Ziele</p>${card('host', targets.host, canManagePersonal)}${externalAdd}${add}</section>`;
}

function bindWriteTargetControls(container, options) {
  const payload = (card) => ({ path: card.querySelector('[data-write-target-path]').value.trim(), root_id: card.querySelector('[data-write-target-root]')?.value || '', file_policy: card.querySelector('[data-write-target-all]').checked ? 'all_regular_files' : 'markdown_only', family: card.dataset.scope === 'family' });
  const form = container.querySelector('[data-write-target-add]');
  const refresh = (scope, path) => { path.innerHTML = writeTargetPathOptions(options, scope.value); };
  if (form) { const scope = form.querySelector('[data-write-target-scope]'); const path = form.querySelector('[data-write-target-path]'); refresh(scope, path); scope.addEventListener('change', () => refresh(scope, path)); form.addEventListener('submit', async (event) => { event.preventDefault(); try { await updateTagCatalog({ scope: 'write_target', action: 'save', tag: form.querySelector('[data-write-target-tag]').value, target: { path: path.value, file_policy: form.querySelector('[data-write-target-all]').checked ? 'all_regular_files' : 'markdown_only', family: scope.value === 'family' } }); } catch (error) { showToast(error.message, true); } }); }
  const externalForm = container.querySelector('[data-external-write-target-add]');
  if (externalForm) externalForm.addEventListener('submit', async (event) => { event.preventDefault(); try { await updateTagCatalog({ scope: 'write_target', action: 'save', tag: externalForm.querySelector('[data-write-target-tag]').value, target: { path: externalForm.querySelector('[data-write-target-path]').value.trim(), root_id: externalForm.querySelector('[data-write-target-root]').value, file_policy: externalForm.querySelector('[data-write-target-all]').checked ? 'all_regular_files' : 'markdown_only' } }); } catch (error) { showToast(error.message, true); } });
  container.querySelectorAll('[data-write-target-save]').forEach((button) => button.addEventListener('click', async () => { const card = button.closest('[data-write-target-card]'); try { await updateTagCatalog({ scope: 'write_target', action: 'save', tag: card.dataset.tag, target: payload(card) }); } catch (error) { showToast(error.message, true); } }));
  container.querySelectorAll('[data-write-target-remove]').forEach((button) => button.addEventListener('click', async () => { const card = button.closest('[data-write-target-card]'); try { await updateTagCatalog({ scope: 'write_target', action: 'remove', tag: card.dataset.tag, target: { family: card.dataset.scope === 'family' } }); } catch (error) { showToast(error.message, true); } }));
}


function workflowPayload(card) {
  return {
    agent: card.querySelector('[data-ai-agent]').value,
    model: card.querySelector('[data-ai-model]').value.trim(),
    prompt: card.querySelector('[data-ai-prompt]').value.trim(),
    classification: card.querySelector('[data-ai-classification]').value,
    context: card.querySelector('[data-ai-context]').value,
    context_files: card.querySelector('[data-ai-files]').value.split('\n').map((value) => value.trim()).filter(Boolean),
    target: card.querySelector('[data-ai-target]').value,
    provider_id: card.querySelector('[data-ai-provider]').value,
  };
}


async function loadTagCatalog() {
  brainState.catalogOpen = true;
  const container = brainElement('brain-browser');
  showBrainBrowser();
  brainElement('brain-tags').classList.add('hidden');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Katalog wird geladen...</p>';
  const response = await apiFetch('/api/brain/tag-catalog');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    showToast(data.error || 'Katalog konnte nicht geladen werden.', true);
    return;
  }
  const catalog = data.catalog || {};
  const knowledgeOptions = data.knowledge_source_options || [];
  const writeTargetOptions = data.write_target_options || [];
  container.innerHTML = `<div class="space-y-3"><button type="button" data-tag-catalog-back class="text-sm text-green-400 hover:text-green-300">Zurueck zu Timeline &amp; Suche</button>${aiWorkflowSection(catalog.ai || {})}${knowledgeSourcesSection(catalog.knowledge || {}, knowledgeOptions, data.can_manage_personal ?? true, data.can_manage_family ?? data.can_manage)}${writeTargetsSection(catalog.write_targets || {}, writeTargetOptions, data.external_write_roots || [], data.can_manage_personal ?? true, data.can_manage_family ?? data.can_manage)}${tagCatalogSection('Meine Hashtags', 'personal', catalog.personal || {}, data.can_manage_personal ?? true)}${tagCatalogSection('Family-Hashtags', 'family', catalog.family || {}, data.can_manage_family ?? data.can_manage)}</div>`;
  container.querySelectorAll('[data-ai-workflow-card]').forEach((card) => {
    const workflow = (catalog.ai || {})[card.dataset.tag] || {};
    const provider = card.querySelector('[data-ai-provider]');
    if (provider) provider.value = workflow.provider_id || '';
  });
  container.querySelector('[data-tag-catalog-back]').addEventListener('click', () => {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    loadBrainCurrentMode();
  });
  container.querySelectorAll('[data-tag-approve]').forEach((button) => button.addEventListener('click', async () => {
    try { await updateTagCatalog({ action: 'approve', tag: button.dataset.tagApprove, scope: button.dataset.tagScope }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-tag-remove]').forEach((button) => button.addEventListener('click', async () => {
    try { await updateTagCatalog({ action: 'remove', tag: button.dataset.tagRemove, scope: button.dataset.tagScope }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-tag-remove-alias]').forEach((button) => button.addEventListener('click', async () => {
    try { await updateTagCatalog({ action: 'remove_alias', tag: button.dataset.tagRemoveAlias, scope: button.dataset.tagScope }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-tag-alias]').forEach((button) => button.addEventListener('click', async () => {
    const tag = window.prompt('Alias (ohne #):');
    const target = tag && window.prompt('Freigegebenes Ziel-Hashtag (ohne #):');
    if (!tag || !target) return;
    try { await updateTagCatalog({ action: 'alias', tag, target, scope: button.dataset.tagScope }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-ai-workflow-save]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-ai-workflow-card]');
    try { await updateTagCatalog({ action: 'save', scope: 'ai', tag: card.dataset.tag, workflow: workflowPayload(card) }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-ai-workflow-remove]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-ai-workflow-card]');
    if (!window.confirm(`#${card.dataset.tag} wirklich entfernen?`)) return;
    try { await updateTagCatalog({ action: 'remove', scope: 'ai', tag: card.dataset.tag }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelector('[data-ai-workflow-add]').addEventListener('click', async () => {
    const rawTag = window.prompt('AI-Hashtag, zum Beispiel ai-einkaufsliste:');
    if (!rawTag) return;
    const tag = rawTag.trim().replace(/^#/, '').toLocaleLowerCase('de-DE');
    const model = window.prompt('Modellkennung:');
    const prompt = model && window.prompt('Prompt für diesen Workflow:');
    if (!model || !prompt) return;
    try {
      await updateTagCatalog({ action: 'save', scope: 'ai', tag, workflow: { agent: 'opencode', model, prompt, context: 'block', context_files: [] } });
    } catch (error) { showToast(error.message, true); }
  });
  bindKnowledgeSourceControls(container, knowledgeOptions);
  bindWriteTargetControls(container, writeTargetOptions);
}


async function runHistoricalTagging() {
  const start = brainElement('brain-tag-start').value;
  const end = brainElement('brain-tag-end').value;
  if (!start || !end) { showToast('Bitte Start- und Enddatum wählen.', true); return; }
  const button = brainElement('brain-tag-run');
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = 'Läuft...';
  button.setAttribute('aria-busy', 'true');
  showToast('Hashtags werden erstellt. Bitte warten.');
  try {
    const response = await apiFetch('/api/brain/tagging/run', { method: 'POST', body: JSON.stringify({ start_date: start, end_date: end, provider_id: brainElement('brain-tag-provider').value }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Nachträgliche Verschlagwortung fehlgeschlagen.', true); return; }
    const failures = (data.errors || []).length;
    if (failures) {
      const firstError = data.errors[0];
      showToast(`${firstError.file || 'Tagging'}: ${firstError.error || 'Unbekannter Fehler'}`, true);
    } else {
      showToast(`${data.processed || 0} Dateien verarbeitet, ${data.skipped || 0} übersprungen, ${(data.proposals || []).length} Vorschläge.`);
    }
    await loadBrain();
  } catch {
    showToast('Netzwerkfehler bei der Verschlagwortung.', true);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
    button.removeAttribute('aria-busy');
  }
}


function brainProjectOptionsForItem(item) {
  let options = '<option value="">Ohne Projekt</option>';
  for (const project of brainState.projects) {
    const selected = project.path === item.project ? ' selected' : '';
    options += `<option value="${escapeHtmlAttr(project.path)}"${selected}>${escapeHtml(project.title)}</option>`;
  }
  return options;
}


function renderBrainTasks(tasks) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  brainState.tasks = new Map(tasks.map((item) => [item.fingerprint, item]));
  const taskControls = `<div class="flex flex-wrap items-center gap-2 mb-3">
    <select id="brain-task-status" class="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100"><option value="all">Alle</option><option value="open">Offen</option><option value="done">Erledigt</option></select>
    <select id="brain-task-priority" class="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100"><option value="all">Alle Prioritaeten</option><option value="high">Hoch</option><option value="normal">Normal</option><option value="low">Niedrig</option></select>
    <button type="button" data-brain-create-project class="text-sm text-green-400 hover:text-green-300">+ Projekt</button>
  </div>`;
  if (!tasks.length) {
    container.innerHTML = taskControls + '<p class="text-gray-500 text-center mt-8">Keine passenden sichtbaren Aufgaben.</p>';
  } else {
    container.innerHTML = taskControls + tasks.map((task) => {
      const tags = brainTagBadges(task.tags);
      const checked = task.completed ? 'checked' : '';
      const disabled = task.read_only ? 'disabled' : '';
      const textClass = task.completed ? 'line-through text-gray-500' : 'text-gray-100';
      return `<article data-brain-card data-doc-id="${escapeHtmlAttr(task.doc_id)}" class="bg-gray-900 border border-gray-800 rounded-xl p-4 ${task.source === 'family' ? 'brain-family-entry' : ''}">
        <div class="mb-2 flex items-start justify-between gap-2">
          <div class="min-w-0 flex flex-wrap items-center gap-2 text-xs">
            <span class="border rounded-full px-2 py-0.5 ${brainSourceClass(task.source)}">${escapeHtml(task.source_label)}</span>
            <span class="break-all font-mono text-gray-600">${escapeHtml(task.path)}</span>
            ${tags}
            <span class="text-gray-500">erstellt ${escapeHtml(task.created_at)}</span>
          </div>
          <div class="shrink-0">${task.read_only ? '' : brainEditButton(task.doc_id, task.block_fingerprint)}</div>
        </div>
        <div data-brain-display>
        <div class="flex items-start gap-3">
          <input type="checkbox" ${checked} ${disabled} data-brain-toggle data-doc-id="${escapeHtmlAttr(task.doc_id)}" data-fingerprint="${escapeHtmlAttr(task.fingerprint)}" class="mt-0.5 accent-green-500 disabled:opacity-50" aria-label="Aufgabe umschalten" />
          <div class="flex-1 min-w-0">
            <div class="w-full text-sm leading-relaxed ${textClass}">${typeof window.renderJournalText === 'function' ? window.renderJournalText(task.text) : escapeHtml(task.text)}</div>
            <div class="mt-3 flex flex-wrap gap-2 items-center">
              <label class="text-xs text-gray-500">Prioritaet <select data-brain-metadata-select data-reference-type="task" data-field="priority" data-doc-id="${escapeHtmlAttr(task.doc_id)}" data-fingerprint="${escapeHtmlAttr(task.fingerprint)}" class="ml-1 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-gray-200"><option value="high" ${task.priority === 'high' ? 'selected' : ''}>Hoch</option><option value="normal" ${task.priority === 'normal' ? 'selected' : ''}>Normal</option><option value="low" ${task.priority === 'low' ? 'selected' : ''}>Niedrig</option></select></label>
              <label class="text-xs text-gray-500">Projekt <select data-brain-metadata-select data-reference-type="task" data-field="project" data-doc-id="${escapeHtmlAttr(task.doc_id)}" data-fingerprint="${escapeHtmlAttr(task.fingerprint)}" class="ml-1 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-gray-200">${brainProjectOptionsForItem(task)}</select></label>
              <button type="button" data-brain-metadata data-reference-type="task" data-doc-id="${escapeHtmlAttr(task.doc_id)}" data-fingerprint="${escapeHtmlAttr(task.fingerprint)}" class="text-xs text-gray-400 hover:text-white">Tags</button>
            </div>
          </div>
        </div>
        </div>
      </article>`;
    }).join('');
  }
  const status = brainElement('brain-task-status');
  const priority = brainElement('brain-task-priority');
  const createProject = container.querySelector('[data-brain-create-project]');
  if (createProject) createProject.addEventListener('click', createBrainProject);
  container.querySelectorAll('[data-brain-toggle]').forEach((checkbox) => {
    checkbox.addEventListener('change', () => toggleBrainTask(checkbox.dataset.docId, checkbox.dataset.fingerprint, checkbox));
  });
  bindBrainCardActions(container);
  container.querySelectorAll('[data-brain-metadata]').forEach((button) => {
    button.addEventListener('click', () => editBrainMetadata(
      button.dataset.referenceType, button.dataset.docId, button.dataset.fingerprint,
    ));
  });
  container.querySelectorAll('[data-brain-metadata-select]').forEach((select) => {
    select.addEventListener('change', () => saveBrainMetadata(
      select.dataset.referenceType, select.dataset.docId, select.dataset.fingerprint,
      { [select.dataset.field]: select.value },
    ));
  });
  if (status) {
    status.value = brainState.taskStatus;
    status.addEventListener('change', () => {
      brainState.taskStatus = status.value;
      invalidateBrainContext();
      loadBrain();
    });
  }
  if (priority) {
    priority.value = brainState.taskPriority;
    priority.addEventListener('change', () => {
      brainState.taskPriority = priority.value;
      invalidateBrainContext();
      loadBrain();
    });
  }
}


async function loadBrainTasks() {
  if (brainState.mode !== 'tasks') return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Aufgaben werden geladen...</p>';
  const request = beginBrainRequest('tasks');
  try {
    const response = await apiFetch('/api/brain/tasks?' + brainQueryParams(true).toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Aufgaben konnten nicht geladen werden.')}</p>`;
      return;
    }
    setBrainIndexStatus(data.index_pending);
    renderBrainTasks(data.tasks || []);
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden der Aufgaben.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


function formatBrainModified(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('de-DE', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}


function renderAiSession(file) {
  const session = file.ai_session;
  const events = (session.events || []).map((event) => {
    const isAgent = !['user-reply', 'permission-decision'].includes(event.type);
    const permissionActions = event.type === 'permission-request' && !session.archived && event.permission_id
      ? `<div class="mt-2 flex gap-2"><button type="button" data-ai-session-permission="allow" data-session-id="${escapeHtmlAttr(session.session_id)}" data-permission-id="${escapeHtmlAttr(event.permission_id)}" class="rounded bg-green-700 px-2 py-1 text-xs text-white hover:bg-green-600">Erlauben</button><button type="button" data-ai-session-permission="deny" data-session-id="${escapeHtmlAttr(session.session_id)}" data-permission-id="${escapeHtmlAttr(event.permission_id)}" class="rounded bg-red-800 px-2 py-1 text-xs text-white hover:bg-red-700">Ablehnen</button></div>` : '';
    return `<section class="rounded-lg border border-gray-800 bg-gray-950/50 p-3"><div class="mb-1 flex flex-wrap items-center gap-2 text-xs text-gray-500"><span>${escapeHtml(event.type || 'event')}</span><span>${escapeHtml(formatBrainModified(event.at || ''))}</span></div><div class="whitespace-pre-wrap text-sm leading-relaxed ${isAgent ? 'journal-ai-text' : 'text-gray-200'}">${typeof window.renderJournalText === 'function' ? window.renderJournalText(event.body || '') : escapeHtml(event.body || '')}</div>${permissionActions}</section>`;
  }).join('') || '<p class="text-sm text-gray-600">Noch keine Session-Ereignisse.</p>';
  const interaction = session.archived ? '<span class="text-xs text-amber-400">Archiviert</span>' : `<div class="mt-3 flex gap-2"><textarea data-ai-session-reply rows="2" placeholder="Antwort an den Agenten..." class="min-w-0 flex-1 rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100"></textarea><button type="button" data-ai-session-send data-session-id="${escapeHtmlAttr(session.session_id)}" class="self-end rounded bg-sky-700 px-3 py-2 text-sm font-semibold text-white hover:bg-sky-600">Senden</button></div>`;
  return `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-sky-900/60 rounded-xl p-4">
    <div class="mb-3 flex flex-wrap items-start justify-between gap-2"><div><div class="flex flex-wrap items-center gap-2 text-xs"><span class="rounded-full border border-sky-800 bg-sky-950/50 px-2 py-0.5 text-sky-200">AI-Session</span><span class="text-sky-200">#${escapeHtml(session.workflow_tag)}</span><span class="text-gray-500">${escapeHtml(session.agent)} · ${escapeHtml(session.model)}</span></div><div class="mt-1 break-all font-mono text-xs text-gray-600">${escapeHtml(file.path)}</div></div>${session.archived ? '' : `<button type="button" data-ai-session-archive data-session-id="${escapeHtmlAttr(session.session_id)}" class="text-xs text-gray-400 hover:text-white">Abschließen &amp; archivieren</button>`}</div>
    <div class="space-y-2">${events}</div>${interaction}
  </article>`;
}


function bindAiSessionActions(container) {
  container.querySelectorAll('[data-ai-session-send]').forEach((button) => button.addEventListener('click', async () => {
    const input = button.parentElement.querySelector('[data-ai-session-reply]');
    const text = input.value.trim();
    if (!text) return;
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/reply`, { method: 'POST', body: JSON.stringify({ text }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Antwort konnte nicht gespeichert werden.', true); button.disabled = false; return; }
    showToast('Antwort an die Session angehängt.');
    await loadBrainFiles('project');
  }));
  container.querySelectorAll('[data-ai-session-permission]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/permission`, { method: 'POST', body: JSON.stringify({ permission_id: button.dataset.permissionId, decision: button.dataset.aiSessionPermission }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Entscheidung konnte nicht gespeichert werden.', true); button.disabled = false; return; }
    showToast('Permission-Entscheidung gespeichert.');
    await loadBrainFiles('project');
  }));
  container.querySelectorAll('[data-ai-session-archive]').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Session abschließen und archivieren?')) return;
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/archive`, { method: 'POST', body: JSON.stringify({}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Session konnte nicht archiviert werden.', true); button.disabled = false; return; }
    showToast('Session archiviert.');
    await loadBrainFiles('project');
  }));
}


function renderBrainFiles(kind, files) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  const isNote = kind === 'note';
  const createLabel = isNote ? '+ Notiz' : '+ Projekt';
  const emptyLabel = isNote ? 'Keine passenden Notizen gefunden.' : 'Keine passenden Projekte gefunden.';
  const controls = `<div class="flex items-center justify-end mb-3">
    <button type="button" data-brain-create-file class="text-sm text-green-400 hover:text-green-300">${createLabel}</button>
  </div>`;
  if (!files.length) {
    container.innerHTML = controls + `<p class="text-gray-500 text-center mt-8">${emptyLabel}</p>`;
  } else {
    container.innerHTML = controls + files.map((file) => file.ai_session ? renderAiSession(file) : `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass('personal')}">Privat</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(file.path)}</span>
          ${brainTagBadges(file.tags)}
        </div>
        <div class="shrink-0">${brainEditButton(file.doc_id)}</div>
      </div>
      <div data-brain-display>
        <div class="text-base font-medium text-gray-100">${escapeHtml(file.title)}</div>
        <div class="mt-1 text-xs text-gray-500">Geändert ${escapeHtml(formatBrainModified(file.modified_at))}</div>
      </div>
    </article>`).join('');
  }
  const createButton = container.querySelector('[data-brain-create-file]');
  if (createButton) {
    createButton.addEventListener('click', async () => {
      createButton.disabled = true;
      try {
        await createBrainFile(kind);
      } finally {
        if (createButton.isConnected) createButton.disabled = false;
      }
    });
  }
  bindBrainCardActions(container);
  bindAiSessionActions(container);
}


async function loadBrainFiles(kind) {
  const mode = kind === 'note' ? 'notes' : 'projects';
  const endpoint = kind === 'note' ? '/api/brain/notes' : '/api/brain/projects';
  if (brainState.mode !== mode) return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Dateien werden geladen...</p>';
  const request = beginBrainRequest('files');
  const params = brainQueryParams(false);
  params.delete('order');
  params.delete('kind');
  const query = brainElement('brain-query').value.trim();
  try {
    const response = await apiFetch(`${endpoint}?${params.toString()}`, {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Dateien konnten nicht geladen werden.')}</p>`;
      return;
    }
    if (kind === 'project' && !query) setBrainProjects(data.projects || []);
    renderBrainFiles(kind, data[mode] || []);
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden der Dateien.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


function renderBrainFamilyFiles(notes, projects) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  const renderSection = (title, kind, files) => {
    const createLabel = kind === 'note' ? '+ Familiennotiz' : '+ Familienprojekt';
    const emptyLabel = kind === 'note' ? 'Keine passenden Familiennotizen gefunden.' : 'Keine passenden Familienprojekte gefunden.';
    const cards = files.length ? files.map((file) => `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-gray-800 rounded-xl p-4 brain-family-entry">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass('family')}">Familie</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(file.path)}</span>
          ${brainTagBadges(file.tags)}
        </div>
        <div class="flex shrink-0 flex-wrap items-center justify-end gap-2">
          ${file.management?.can_manage ? brainManageButton(file.doc_id, kind === 'note' ? 'Notiz' : 'Projekt') : ''}
          ${brainEditButton(file.doc_id)}
        </div>
      </div>
      <div data-brain-display>
        <div class="text-base font-medium text-gray-100">${escapeHtml(file.title)}</div>
        <div class="mt-1 text-xs text-gray-500">Geändert ${escapeHtml(formatBrainModified(file.modified_at))}</div>
      </div>
    </article>`).join('') : `<p class="text-gray-500 text-center py-4">${emptyLabel}</p>`;
    return `<section class="space-y-3"><div class="flex items-center justify-between gap-3"><h2 class="text-sm font-medium text-gray-300">${title}</h2><button type="button" data-brain-create-family="${kind}" class="text-sm text-green-400 hover:text-green-300">${createLabel}</button></div>${cards}</section>`;
  };
  container.innerHTML = `<div class="space-y-6">${renderSection('Familienprojekte', 'project', projects)}${renderSection('Familiennotizen', 'note', notes)}</div>`;
  container.querySelectorAll('[data-brain-create-family]').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await createFamilyBrainFile(button.dataset.brainCreateFamily);
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });
  });
  bindBrainCardActions(container);
}


async function loadBrainFamilyFiles() {
  if (brainState.mode !== 'family') return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Familien-Dateien werden geladen...</p>';
  const request = beginBrainRequest('files');
  const params = brainQueryParams(false);
  params.delete('order');
  params.delete('kind');
  try {
    const response = await apiFetch(`/api/brain/family?${params.toString()}`, {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Familien-Dateien konnten nicht geladen werden.')}</p>`;
      return;
    }
    renderBrainFamilyFiles(data.notes || [], data.projects || []);
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden der Familien-Dateien.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadBrainCurrentMode() {
  if (brainState.catalogOpen) return;
  if (brainState.mode === 'tasks') await loadBrainTasks();
  else if (brainState.mode === 'notes') await loadBrainFiles('note');
  else if (brainState.mode === 'projects') await loadBrainFiles('project');
  else if (brainState.mode === 'family') await loadBrainFamilyFiles();
  else await loadBrainResults();
}


async function loadBrainBootstrap(preserveResults = false) {
  const container = brainElement('brain-browser');
  if (!preserveResults) {
    container.innerHTML = '<p class="text-gray-500 text-center mt-8">Brain wird geladen...</p>';
  }
  const request = beginBrainRequest('bootstrap');
  try {
    const params = brainQueryParams(false);
    params.set('limit', '100');
    const response = await apiFetch('/api/brain/bootstrap?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Brain konnte nicht geladen werden.')}</p>`;
      return;
    }
    if (typeof window.setHashtagCatalog === 'function') window.setHashtagCatalog(data.catalog || {});
    setBrainProjects(data.projects || []);
    setBrainTags(data.tags || [], data.index_pending);
    renderBrainResults(data.results || [], data);
    brainState.supportLoaded = true;
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden von Brain.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadBrain() {
  if (!brainElement('brain-browser')) return;
  if (brainState.catalogOpen) return;
  if (brainState.supportLoaded) {
    await loadBrainCurrentMode();
    return;
  }
  if (brainState.mode === 'search') {
    await loadBrainBootstrap();
    return;
  }
  const generation = brainState.generation;
  await Promise.all([
    loadBrainProjects(),
    loadBrainTags(),
    typeof window.loadFamilyHashtags === 'function' ? window.loadFamilyHashtags() : Promise.resolve(),
  ]);
  if (generation !== brainState.generation) return;
  brainState.supportLoaded = true;
  await loadBrainCurrentMode();
}
window.loadBrain = loadBrain;


function openBrainTab() {
  const query = brainElement('brain-query');
  if (query) query.focus({ preventScroll: true });
  if (brainState.requests.has('bootstrap')) return;
  if (!brainState.supportLoaded) loadBrain();
  else if (brainState.mode === 'search' || brainState.mode === 'journals') loadBrainBootstrap(true);
}
window.openBrainTab = openBrainTab;


function loadBrainForQuery() {
  return brainState.supportLoaded ? loadBrainCurrentMode() : loadBrain();
}


function setBrainMode(mode) {
  if (!['search', 'journals', 'tasks', 'notes', 'projects', 'family'].includes(mode)) return;
  if (mode !== brainState.mode || brainState.catalogOpen) invalidateBrainContext();
  brainState.catalogOpen = false;
  brainState.mode = mode;
  brainState.contentFilter = mode === 'journals' ? 'journal' : 'all';
  brainElement('brain-mode-search').className = mode === 'search' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-journals').className = mode === 'journals' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-tasks').className = mode === 'tasks' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-notes').className = mode === 'notes' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-projects').className = mode === 'projects' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-family').className = mode === 'family' ? 'text-sm text-green-400 font-medium' : 'text-sm text-gray-500 hover:text-gray-300';
  updateBrainModeControls();
  showBrainBrowser();
  loadBrain();
}


function updateBrainModeControls() {
  const query = brainElement('brain-query');
  const order = brainElement('brain-order');
  const tags = brainElement('brain-tags');
  if (query) {
    query.placeholder = {
      search: 'Journale, Notizen und Projekte durchsuchen',
      journals: 'Journale durchsuchen',
      tasks: 'Aufgaben durchsuchen',
      notes: 'Notizen durchsuchen',
      projects: 'Projekte durchsuchen',
      family: 'Familiennotizen und -projekte durchsuchen',
    }[brainState.mode];
  }
  if (order) order.classList.toggle('hidden', !['search', 'journals'].includes(brainState.mode));
  if (tags) tags.classList.remove('hidden');
}


function showBrainBrowser() {
  abortBrainRequest('document');
  closeBrainInlineEditor();
  brainElement('brain-browser').classList.remove('hidden');
}


function closeBrainInlineEditor() {
  const editor = brainState.editor;
  if (!editor?.card) return;
  editor.card.querySelector('[data-brain-display]')?.classList.remove('hidden');
  editor.card.querySelector('[data-brain-inline-editor]')?.remove();
  brainState.editor = null;
}


async function openBrainDocument(docId, fingerprint, card = null) {
  const targetCard = card || Array.from(document.querySelectorAll('[data-brain-card]')).find(
    (item) => item.dataset.docId === docId,
  );
  if (!targetCard) return;
  closeBrainInlineEditor();
  const request = beginBrainRequest('document');
  try {
    const params = new URLSearchParams({ doc_id: docId, fingerprint });
    const response = await apiFetch('/api/brain/document?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Datei konnte nicht geoeffnet werden.', true);
      return;
    }
    const display = targetCard.querySelector('[data-brain-display]');
    if (display) display.classList.add('hidden');
    const inlineEditor = document.createElement('div');
    inlineEditor.dataset.brainInlineEditor = '';
    inlineEditor.className = 'mt-3';
    const agentSession = data.agent_session;
    const agentControls = !data.read_only && agentSession && !agentSession.repair
      ? `<span class="self-center text-xs text-sky-300">KI-Session: ${escapeHtml(agentSession.status)}</span>${agentSession.status === 'active' ? '<button type="button" data-brain-agent-action="update" class="text-xs text-sky-300 hover:text-sky-200">Session aktualisieren</button><button type="button" data-brain-agent-action="pause" class="text-xs text-yellow-300 hover:text-yellow-200">Pausieren</button><button type="button" data-brain-agent-action="end" class="text-xs text-red-300 hover:text-red-200">Beenden</button>' : ''}${agentSession.status === 'paused' ? '<button type="button" data-brain-agent-action="resume" class="text-xs text-green-300 hover:text-green-200">Fortsetzen</button>' : ''}`
      : (agentSession?.state === 'repair' ? '<span class="self-center text-xs text-red-300">KI-Metadaten defekt – Session bitte beenden oder reparieren.</span>' : '');
    inlineEditor.innerHTML = `<textarea data-brain-inline-content class="w-full min-h-64 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 font-mono text-sm leading-relaxed text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y" rows="12" ${data.read_only ? 'readonly' : ''}>${escapeHtml(data.content || '')}</textarea><div class="mt-2 flex flex-wrap gap-2">${data.read_only ? '' : '<button type="button" data-brain-inline-save class="bg-green-600 hover:bg-green-500 text-white text-xs py-1.5 px-3 rounded transition">Speichern</button>'}<button type="button" data-brain-inline-cancel class="bg-gray-700 hover:bg-gray-600 text-white text-xs py-1.5 px-3 rounded transition">${data.read_only ? 'Schließen' : 'Abbrechen'}</button>${agentControls}${data.management?.can_manage ? '<button type="button" data-brain-inline-manage class="text-green-500 hover:text-green-400 text-xs px-2">Notiz/Projekt verwalten</button>' : ''}</div>`;
    targetCard.appendChild(inlineEditor);
    const textarea = inlineEditor.querySelector('[data-brain-inline-content]');
    brainState.editor = { ...data, card: targetCard, textarea };
    inlineEditor.querySelector('[data-brain-inline-cancel]').addEventListener('click', closeBrainInlineEditor);
    inlineEditor.querySelector('[data-brain-inline-save]')?.addEventListener('click', saveBrainDocument);
    inlineEditor.querySelector('[data-brain-inline-manage]')?.addEventListener('click', () => openBrainAccessManager(data));
    inlineEditor.querySelectorAll('[data-brain-agent-action]').forEach((button) => button.addEventListener('click', () => updateBrainAgentSession(button.dataset.brainAgentAction)));
    if (data.block_start_line) {
      const lines = textarea.value.split('\n');
      const offset = lines.slice(0, data.block_start_line - 1).join('\n').length + (data.block_start_line > 1 ? 1 : 0);
      textarea.setSelectionRange(offset, offset);
      textarea.scrollTop = Math.max(0, (data.block_start_line - 3) * 20);
    }
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Oeffnen der Datei.', true);
  } finally {
    finishBrainRequest(request);
  }
}


async function updateBrainAgentSession(action) {
  const editor = brainState.editor;
  if (!editor || brainState.requests.has('agent-session')) return;
  const request = beginBrainRequest('agent-session', false);
  try {
    const response = await apiFetch('/api/brain/document/agent-session', {
      method: 'POST',
      body: JSON.stringify({ doc_id: editor.doc_id, action, confirm_family_edit: editor.source === 'family' }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Session konnte nicht aktualisiert werden.');
    showToast(`KI-Session ${data.agent_session?.status || 'aktualisiert'}.`);
    closeBrainInlineEditor();
    await loadBrainCurrentMode();
  } catch (error) {
    if (isCurrentBrainRequest(request)) showToast(error.message || 'Session konnte nicht aktualisiert werden.', true);
  } finally {
    finishBrainRequest(request);
  }
}
window.openBrainDocument = openBrainDocument;


async function saveBrainDocument() {
  const editor = brainState.editor;
  if (!editor || editor.read_only || brainState.requests.has('document-save')) return;
  const saveButton = editor.card.querySelector('[data-brain-inline-save]');
  saveButton.disabled = true;
  const request = beginBrainRequest('document-save', false);
  try {
    const response = await apiFetch('/api/brain/document', {
      method: 'PUT',
      body: JSON.stringify({
        doc_id: editor.doc_id,
        content: editor.textarea.value,
        content_hash: editor.content_hash,
        confirm_family_edit: editor.source === 'family',
      }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request) || brainState.editor !== editor) return;
    if (!response.ok) {
      showToast(data.error || 'Speichern fehlgeschlagen.', true);
      return;
    }
    showToast('Originaldatei gespeichert. Quelle und Suchindex wurden sofort aktualisiert.');
    await Promise.all([loadBrainProjects(), loadBrainTags()]);
    closeBrainInlineEditor();
    await loadBrainCurrentMode();
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Speichern.', true);
  } finally {
    finishBrainRequest(request);
    if (saveButton.isConnected) saveButton.disabled = false;
  }
}


function openBrainAccessManager(editor) {
  if (!editor?.management?.can_manage) return;
  const users = Array.from(new Map(
    (((typeof config !== 'undefined' && config?.users) || []).map((user) => [user.id, user])),
  ).values());
  const assigned = new Set(editor.management.assigned_users || []);
  const entityLabel = editor.path?.startsWith('projects/') ? 'Projekt' : 'Notiz';
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/50';
  modal.innerHTML = `<div class="w-full max-w-md rounded-xl border border-gray-800 bg-gray-900 p-5 shadow-xl"><h2 class="text-lg font-semibold">${entityLabel} verwalten</h2><p class="mt-1 text-xs text-gray-500">Der Ersteller behält immer Zugriff.</p><div class="mt-4 space-y-2">${users.map((user) => `<label class="flex items-center gap-2 text-sm text-gray-200"><input type="checkbox" data-brain-access-user="${escapeHtmlAttr(user.id)}" ${assigned.has(user.id) ? 'checked' : ''} class="accent-green-500" /><span>${escapeHtml(user.username || user.id)}</span></label>`).join('')}</div><div class="mt-5 flex gap-2"><button type="button" data-brain-access-save class="rounded bg-green-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-green-500">Speichern</button><button type="button" data-brain-access-cancel class="rounded bg-gray-700 px-3 py-1.5 text-sm text-white hover:bg-gray-600">Abbrechen</button></div></div>`;
  document.body.appendChild(modal);
  modal.querySelector('[data-brain-access-cancel]').addEventListener('click', () => modal.remove());
  modal.querySelector('[data-brain-access-save]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const assignedUsers = Array.from(modal.querySelectorAll('[data-brain-access-user]:checked')).map((input) => input.dataset.brainAccessUser);
    try {
      const response = await apiFetch('/api/brain/document/access', {
        method: 'PUT',
        body: JSON.stringify({ doc_id: editor.doc_id, assigned_users: assignedUsers, content_hash: editor.content_hash }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Zugriff konnte nicht gespeichert werden.');
      editor.content_hash = data.content_hash;
      editor.management.assigned_users = data.assigned_users || [];
      modal.remove();
      showToast('Zugriff gespeichert.');
      if (brainState.editor?.doc_id === editor.doc_id) closeBrainInlineEditor();
      await loadBrainCurrentMode();
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  });
}


async function openBrainAccessManagerForDocument(docId) {
  const request = beginBrainRequest('document');
  try {
    const response = await apiFetch('/api/brain/document?' + new URLSearchParams({ doc_id: docId }).toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Datei konnte nicht geöffnet werden.', true);
      return;
    }
    openBrainAccessManager(data);
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Öffnen der Verwaltung.', true);
  } finally {
    finishBrainRequest(request);
  }
}


async function toggleBrainTask(docId, fingerprint, checkbox) {
  const completed = checkbox.checked;
  checkbox.disabled = true;
  const request = beginBrainRequest(`task-toggle:${fingerprint}`, false);
  try {
    const response = await apiFetch('/api/brain/task/toggle', {
      method: 'POST',
      body: JSON.stringify({ doc_id: docId, fingerprint, completed }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      checkbox.checked = !completed;
      showToast(data.error || 'Aufgabe konnte nicht geaendert werden.', true);
      return;
    }
    showToast(completed ? 'Aufgabe erledigt.' : 'Aufgabe wieder geoeffnet.');
    if (brainState.mode === 'tasks') await loadBrainTasks();
    else await loadBrainResults();
  } catch {
    if (isCurrentBrainRequest(request)) {
      checkbox.checked = !completed;
      showToast('Netzwerkfehler beim Aendern der Aufgabe.', true);
    }
  } finally {
    finishBrainRequest(request);
    checkbox.disabled = false;
  }
}
window.toggleBrainTask = toggleBrainTask;


async function saveBrainMetadata(referenceType, docId, fingerprint, changes) {
  const source = referenceType === 'task' ? brainState.tasks.get(fingerprint) : brainState.results.get(fingerprint);
  if (!source) return;
  const payload = { doc_id: docId, reference_type: referenceType, fingerprint };
  ['tags', 'priority', 'project'].forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(changes, field)) payload[field] = changes[field];
  });
  const request = beginBrainRequest(`metadata:${referenceType}:${fingerprint}`, false);
  try {
    const response = await apiFetch('/api/brain/metadata', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Metadaten konnten nicht gespeichert werden.', true);
      return;
    }
    showToast('Brain-Metadaten gespeichert.');
    await Promise.all([loadBrainTags(), brainState.mode === 'tasks' ? loadBrainTasks() : loadBrainResults()]);
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Speichern der Metadaten.', true);
  } finally {
    finishBrainRequest(request);
  }
}


function editBrainMetadata(referenceType, docId, fingerprint) {
  const source = referenceType === 'task' ? brainState.tasks.get(fingerprint) : brainState.results.get(fingerprint);
  if (!source) return;
  const value = window.prompt('Manuelle Tags, durch Komma getrennt:', (source.manual_tags || []).join(', '));
  if (value === null) return;
  const tags = value.split(',').map((tag) => tag.trim()).filter(Boolean);
  saveBrainMetadata(referenceType, docId, fingerprint, { tags });
}
window.editBrainMetadata = editBrainMetadata;


async function createBrainFile(kind) {
  const isNote = kind === 'note';
  const entity = isNote ? 'note' : 'project';
  const requestKey = `${entity}-create`;
  if (brainState.requests.has(requestKey)) return;
  const initiatingMode = brainState.mode;
  const title = window.prompt(isNote ? 'Titel der neuen Notiz:' : 'Name des persoenlichen Projekts:');
  if (!title || !title.trim()) return;
  const mode = isNote ? 'notes' : 'projects';
  const endpoint = isNote ? '/api/brain/notes' : '/api/brain/projects';
  const request = beginBrainRequest(requestKey, false);
  try {
    const response = await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify({ title: title.trim() }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || (isNote ? 'Notiz konnte nicht erstellt werden.' : 'Projekt konnte nicht erstellt werden.'), true);
      return;
    }
    await loadBrainProjects();
    showToast(isNote ? 'Notiz erstellt.' : 'Persoenliches Projekt erstellt.');
    const brainTab = document.getElementById('tab-brain');
    const contextUnchanged = request.generation === brainState.generation
      && brainState.mode === initiatingMode
      && brainTab && !brainTab.classList.contains('hidden');
    if (contextUnchanged) {
      await loadBrainCurrentMode();
      await openBrainDocument(data[entity].doc_id, '');
    }
  } catch {
    if (isCurrentBrainRequest(request)) {
      showToast(isNote ? 'Netzwerkfehler beim Erstellen der Notiz.' : 'Netzwerkfehler beim Erstellen des Projekts.', true);
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function createFamilyBrainFile(kind) {
  const isNote = kind === 'note';
  const entity = isNote ? 'note' : 'project';
  const requestKey = `family-${entity}-create`;
  if (brainState.requests.has(requestKey)) return;
  const initiatingMode = brainState.mode;
  const title = window.prompt(isNote ? 'Titel der neuen Familiennotiz:' : 'Name des neuen Familienprojekts:');
  if (!title || !title.trim()) return;
  const endpoint = isNote ? '/api/brain/family/notes' : '/api/brain/family/projects';
  const request = beginBrainRequest(requestKey, false);
  try {
    const response = await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify({ title: title.trim() }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || (isNote ? 'Familiennotiz konnte nicht erstellt werden.' : 'Familienprojekt konnte nicht erstellt werden.'), true);
      return;
    }
    showToast(isNote ? 'Familiennotiz erstellt.' : 'Familienprojekt erstellt.');
    const brainTab = document.getElementById('tab-brain');
    const contextUnchanged = request.generation === brainState.generation
      && brainState.mode === initiatingMode
      && brainTab && !brainTab.classList.contains('hidden');
    if (contextUnchanged) {
      await loadBrainCurrentMode();
      await openBrainDocument(data[entity].doc_id, '');
    }
  } catch {
    if (isCurrentBrainRequest(request)) {
      showToast(isNote ? 'Netzwerkfehler beim Erstellen der Familiennotiz.' : 'Netzwerkfehler beim Erstellen des Familienprojekts.', true);
    }
  } finally {
    finishBrainRequest(request);
  }
}


function createBrainProject() {
  return createBrainFile('project');
}
window.createBrainProject = createBrainProject;


brainElement('brain-query').addEventListener('input', () => {
  clearTimeout(brainState.searchTimer);
  invalidateBrainContext();
  brainState.searchTimer = setTimeout(() => {
    brainState.searchTimer = null;
    loadBrainForQuery();
  }, 180);
});
brainElement('brain-query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    clearTimeout(brainState.searchTimer);
    brainState.searchTimer = null;
    invalidateBrainContext();
    loadBrainForQuery();
  }
});
brainElement('brain-order').addEventListener('change', () => {
  if (brainState.mode === 'search' || brainState.mode === 'journals') {
    invalidateBrainContext();
    loadBrainForQuery();
  }
});
['brain-range-start', 'brain-range-end'].forEach((id) => {
  brainElement(id).addEventListener('change', () => {
    updateBrainRangeSummary();
    invalidateBrainContext();
    loadBrainForQuery();
  });
});
document.querySelectorAll('[data-brain-date-picker]').forEach((button) => {
  button.addEventListener('click', () => showBrainDatePicker(button.dataset.brainDatePicker));
});
brainElement('brain-range-clear').addEventListener('click', () => {
  brainElement('brain-range-start').value = '';
  brainElement('brain-range-end').value = '';
  updateBrainRangeSummary();
  invalidateBrainContext();
  loadBrainForQuery();
});
updateBrainRangeSummary();
brainElement('brain-mode-search').addEventListener('click', () => setBrainMode('search'));
brainElement('brain-mode-journals').addEventListener('click', () => setBrainMode('journals'));
brainElement('brain-mode-tasks').addEventListener('click', () => setBrainMode('tasks'));
brainElement('brain-mode-notes').addEventListener('click', () => setBrainMode('notes'));
brainElement('brain-mode-projects').addEventListener('click', () => setBrainMode('projects'));
brainElement('brain-mode-family').addEventListener('click', () => setBrainMode('family'));
brainElement('brain-tag-history').addEventListener('click', () => {
  brainElement('brain-tagging-controls').classList.toggle('hidden');
});
brainElement('brain-tag-run').addEventListener('click', runHistoricalTagging);
brainElement('brain-tag-catalog').addEventListener('click', async () => {
  if (typeof window.closeSettings === 'function') window.closeSettings();
  if (typeof window.switchTab === 'function') window.switchTab('brain');
  invalidateBrainContext();
  try {
    await loadTagCatalog();
  } catch {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    await loadBrainCurrentMode();
    showToast('Netzwerkfehler beim Laden des Katalogs.', true);
  }
});
brainElement('brain-rebuild').addEventListener('click', async () => {
  const request = beginBrainRequest('index-rebuild', false);
  try {
    const response = await apiFetch('/api/brain/index/rebuild', {
      method: 'POST',
      body: JSON.stringify({}),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Index konnte nicht eingeplant werden.', true);
      return;
    }
    showToast(data.queued ? 'Indexierung wurde eingeplant.' : 'Indexierung laeuft bereits.');
    invalidateBrainContext();
    await loadBrain();
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Einplanen des Index.', true);
  } finally {
    finishBrainRequest(request);
  }
});
