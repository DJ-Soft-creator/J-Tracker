// Brain search/catalog core. Load before brain-content.js.
// AI maintenance contract: shared state, result rendering and catalog controls
// stay here; file/editor mutations belong in brain-content.js.
/* Brain View client. Search is local server-side full text only; no LLM mode. */

const brainState = {
  mode: 'search',
  selectedTags: new Set(),
  excludedTags: new Set(),
  // Themengruppe: ODER-verknuepft, damit sich ein zweites Thema hinzufuegen
  // laesst, statt die Treffer immer weiter einzuengen (brain-tag-filters.js).
  anyTags: new Set(),
  tags: [],
  // Der volle Hashtag-Bestand fuer den "+"-Modus der Picker. Ungefiltert, also
  // unabhaengig von den gesetzten Filtern - erst beim ersten "+" geholt.
  allTags: null,
  allTagsMode: false,
  projects: [],
  results: new Map(),
  // Volltext bereits ausgeklappter Karten, damit ein "Mehr laden" die
  // Ausklappung nicht zurücknimmt und nichts erneut geholt werden muss.
  expanded: new Map(),
  tasks: new Map(),
  // Auswahl fuer den Export: nur Fingerprints, der Text wird erst beim
  // Exportieren geholt.
  selectMode: false,
  selected: new Set(),
  pagination: {},
  taskStatus: 'all',
  taskPriority: 'all',
  contentFilter: 'all',
  catalogOpen: false,
  catalogSection: 'personal',
  catalogData: null,
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
  // Ein anderer Filter zeigt eine andere Liste. Eine Auswahl darueber hinweg
  // mitzuschleppen hiesse, Bloecke zu exportieren, die niemand mehr sieht.
  if (brainState.selected.size && typeof brainClearSelection === 'function') brainClearSelection();
  else brainState.selected.clear();
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
  brainState.excludedTags.clear();
  brainState.anyTags.clear();
  brainState.tags = [];
  brainState.allTags = null;
  brainState.allTagsMode = false;
  brainState.projects = [];
  brainState.results.clear();
  brainState.selectMode = false;
  brainState.selected.clear();
  brainState.pagination = {};
  brainState.tasks.clear();
  brainState.taskStatus = 'all';
  brainState.taskPriority = 'all';
  brainState.contentFilter = 'all';
  brainState.catalogOpen = false;
  brainState.catalogData = null;
  brainState.supportLoaded = false;
  brainState.editor = null;

  const query = brainElement('brain-query');
  const order = brainElement('brain-order');
  const tags = brainElement('brain-tags');
  const tagOffer = brainElement('brain-tag-offer');
  const rangeStart = brainElement('brain-range-start');
  const rangeEnd = brainElement('brain-range-end');
  const browser = brainElement('brain-browser');
  if (query) query.value = '';
  if (order) order.value = 'newest';
  if (tags) tags.innerHTML = '';
  if (tagOffer) tagOffer.innerHTML = '';
  brainElement('brain-tag-active-wrap')?.classList.add('hidden');
  brainElement('brain-tag-offer-wrap')?.classList.add('hidden');
  document.querySelectorAll('[data-brain-tag-search]').forEach((input) => {
    input.value = '';
  });
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
  const dbJournalsMode = brainElement('brain-mode-db-journals');
  const notesMode = brainElement('brain-mode-notes');
  const projectsMode = brainElement('brain-mode-projects');
  const familyMode = brainElement('brain-mode-family');
  if (searchMode) searchMode.className = 'shrink-0 text-sm text-green-400 font-medium';
  if (taskMode) taskMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (journalsMode) journalsMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (dbJournalsMode) dbJournalsMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (notesMode) notesMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (projectsMode) projectsMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (familyMode) familyMode.className = 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  updateBrainModeControls();
  if (typeof window.refreshBrainProjectSelector === 'function') {
    window.refreshBrainProjectSelector();
  }
}
window.clearBrainState = clearBrainState;


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
  brainState.excludedTags.forEach((tag) => params.append('exclude_tags', tag));
  brainState.anyTags.forEach((tag) => params.append('any_tags', tag));
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
  updateBrainFiltersClearVisibility();
}


function updateBrainFiltersClearVisibility() {
  const button = brainElement('brain-filters-clear');
  if (!button) return;
  const hasRange = Boolean(brainElement('brain-range-start')?.value || brainElement('brain-range-end')?.value);
  // ``ki:`` filters live in the query field but are set from the same chips,
  // so the reset link has to admit to them as well.
  const aiTerms = typeof brainAiTermSets === 'function' ? brainAiTermSets() : null;
  const hasTags = brainState.selectedTags.size > 0 || brainState.excludedTags.size > 0
    || brainState.anyTags.size > 0
    || Boolean(aiTerms && (aiTerms.included.size || aiTerms.excluded.size));
  button.classList.toggle('hidden', !(hasRange || hasTags));
}


function setBrainIndexStatus(pending) {
  const status = brainElement('brain-index-status');
  if (!status) return;
  status.textContent = pending === null ? '' : (pending ? 'Erstindex wird im Hintergrund aufgebaut.' : 'Lokale Volltextsuche');
}


// The local search and journal views receive their block facets inside the
// search answer itself (`facets=block`). Call sites that already trigger a
// search use this instead of loadBrainTags, so the index is not walked twice
// for the same numbers. A standalone chip refresh - approving a hashtag, say -
// still calls loadBrainTags directly, because nothing else would update it.
function loadBrainTagsUnlessSearchCarriesThem() {
  if (['search', 'journals'].includes(brainState.mode)) return Promise.resolve();
  return loadBrainTags();
}


async function loadBrainTags() {
  if (!brainElement('brain-tags')) return;
  const request = beginBrainRequest('tags');
  try {
    const params = new URLSearchParams();
    brainState.selectedTags.forEach((tag) => params.append('tags', tag));
    brainState.excludedTags.forEach((tag) => params.append('exclude_tags', tag));
    brainState.anyTags.forEach((tag) => params.append('any_tags', tag));
    // Views that list blocks need block-level facets: with a hashtag selected,
    // only the blocks carrying it may contribute the remaining tags, otherwise
    // drilling down offers every other tag of the same journal day.
    if (['search', 'journals', 'db-journals'].includes(brainState.mode)) {
      params.set('facet', 'block');
      // The query narrows the facets too, so `ki:` leaves exactly the hashtags
      // a model assigned in the blocks that are still on screen.
      const query = brainElement('brain-query')?.value.trim();
      if (query) params.set('q', query);
    }
    const response = await apiFetch('/api/brain/tags?' + params.toString(), { signal: request.controller.signal });
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


// Der volle Bestand fuer den "+"-Modus: dieselbe Facettenzaehlung, aber ohne
// Filter im Aufruf. Nur so tauchen Themen wieder auf, die neben den gesetzten
// Hashtags nirgends vorkommen - genau die, die man hinzufuegen will. Die Zahlen
// aendern sich nur mit dem Index, deshalb wird die Liste einmal geholt und
// behalten, bis clearBrainState oder ein Rebuild sie verwirft.
async function loadBrainAllTags() {
  if (brainState.allTags) return brainState.allTags;
  try {
    const response = await apiFetch('/api/brain/tags');
    if (!response.ok) return null;
    const data = await response.json();
    brainState.allTags = data.tags || [];
    return brainState.allTags;
  } catch {
    return null;
  }
}


async function loadBrainResults() {
  if (!['search', 'journals', 'db-journals'].includes(brainState.mode)) return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Suche...</p>';
  const request = beginBrainRequest('results');
  try {
    const params = brainQueryParams(false);
    const dbJournals = brainState.mode === 'db-journals';
    params.set('limit', dbJournals ? '50' : '100');
    // The hashtags beside the hits describe the same result list, so they ride
    // along instead of costing a second request that walks the index again.
    // The DB journal search has no facets of its own and keeps asking.
    if (!dbJournals) params.set('facets', 'block');
    const response = await apiFetch((dbJournals ? '/api/journal-search?' : '/api/brain/search?') + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Suche fehlgeschlagen.')}</p>`;
      return;
    }
    if (dbJournals) {
      setBrainIndexStatus(null);
      renderBrainResults(mapDbJournalResults(data.results), data);
    } else {
      setBrainIndexStatus(data.index_pending);
      if (data.tags) setBrainTags(data.tags, data.index_pending);
      renderBrainResults(data.results || [], data);
    }
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler bei der Suche.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadMoreBrainResults(event) {
  if (!['search', 'journals', 'db-journals'].includes(brainState.mode)) return;
  const button = event.currentTarget;
  button.disabled = true;
  const request = beginBrainRequest('results-more');
  const dbJournals = brainState.mode === 'db-journals';
  const params = brainQueryParams(false);
  params.set('limit', dbJournals ? '50' : '100');
  params.set('offset', String(brainState.results.size));
  try {
    const response = await apiFetch((dbJournals ? '/api/journal-search?' : '/api/brain/search?') + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Weitere Ergebnisse konnten nicht geladen werden.', true);
      button.disabled = false;
      return;
    }
    const page = dbJournals ? mapDbJournalResults(data.results) : (data.results || []);
    const combined = [...brainState.results.values(), ...page];
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


// Which catalog panels a change can be seen in.  Ignoring or re-offering a
// proposal never changes tag counts or Family tags, so those refreshes are
// skipped instead of running four sequential requests per click.
function catalogRefreshScope(payload) {
  const proposalOnly = payload.action === 'block' || payload.action === 'unblock';
  return { familyTags: !proposalOnly, brainTags: !proposalOnly };
}


async function updateTagCatalog(payload) {
  const response = await apiFetch('/api/brain/tag-catalog', { method: 'POST', body: JSON.stringify(payload) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Katalog konnte nicht aktualisiert werden.');
  const scope = catalogRefreshScope(payload);
  // Freigabe/Umbenennung aendert den Bestand selbst, nicht nur die Facetten -
  // die gecachte Vollliste des "+"-Modus waere danach veraltet.
  brainState.allTags = null;
  const pending = [refreshTagCatalog()];
  if (scope.familyTags && typeof window.loadFamilyHashtags === 'function') pending.push(window.loadFamilyHashtags(true));
  if (typeof window.refreshWriteHashtagCatalog === 'function') pending.push(window.refreshWriteHashtagCatalog());
  if (scope.brainTags) pending.push(loadBrainTags());
  await Promise.all(pending);
}


function tagChip(tag, scope, extra = '') {
  const tagColor = scope === 'family' ? ' journal-family-tag' : '';
  return `<span class="inline-flex items-center gap-1 rounded bg-gray-800 px-2 py-1${tagColor}">#${escapeHtml(tag)}${extra}</span>`;
}


function tagCatalogSection(title, scope, catalog, canManage) {
  const canonical = (catalog.canonical || []).map((tag) => tagChip(tag, scope, canManage
    ? ` <button type="button" data-tag-remove="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-red-400 hover:text-red-300" title="Hashtag entfernen">×</button>`
    : '')).join(' ') || '<span class="text-gray-600">Keine</span>';
  const aliases = Object.entries(catalog.aliases || {}).map(([alias, target]) => `<span class="inline-flex items-center gap-1 rounded bg-gray-800 px-2 py-1${scope === 'family' ? ' journal-family-tag' : ''}">#${escapeHtml(alias)} → #${escapeHtml(target)}${canManage ? ` <button type="button" data-tag-remove-alias="${escapeHtmlAttr(alias)}" data-tag-scope="${scope}" class="text-red-400 hover:text-red-300" title="Alias entfernen">×</button>` : ''}</span>`).join(' ') || '<span class="text-gray-600">Keine Aliase</span>';
  const pendingCount = (catalog.proposals || []).length;
  const pendingHint = pendingCount
    ? `<p class="text-xs text-amber-300">${pendingCount} offene${pendingCount === 1 ? 'r Vorschlag' : ' Vorschläge'} – <button type="button" data-catalog-goto="proposals" class="underline hover:text-amber-200">unter „Vorschläge“ bearbeiten</button>.</p>`
    : '';
  return `<section class="bg-gray-900 border border-gray-800 rounded-xl p-4 space-y-2"><div class="flex justify-between gap-2"><h3 class="font-medium text-gray-100">${title}</h3>${canManage ? `<button type="button" data-tag-alias data-tag-scope="${scope}" class="text-sm text-green-400 hover:text-green-300">Alias hinzufügen</button>` : ''}</div>${pendingHint}<p class="text-xs text-gray-500">Freigegeben (${(catalog.canonical || []).length})</p><div class="flex flex-wrap gap-1">${canonical}</div><p class="pt-1 text-xs text-gray-500">Aliase</p><div class="flex flex-wrap gap-1">${aliases}</div></section>`;
}


function proposalScopeBlock(scope, catalog, canManage) {
  const proposals = (catalog.proposals || []).map((tag) => {
    const actions = canManage
      ? ` <button type="button" data-tag-approve="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-green-400 hover:text-green-300">Freigeben</button>`
        + ` <button type="button" data-tag-alias-from="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-sky-400 hover:text-sky-300" title="Diesen Vorschlag als Alias auf ein freigegebenes Hashtag legen">Alias…</button>`
        + ` <button type="button" data-tag-block="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-red-400 hover:text-red-300" title="Nie wieder vorschlagen">Ignorieren</button>`
      : '';
    return `<span class="inline-flex items-center gap-1 rounded border border-amber-800 px-2 py-1${scope === 'family' ? ' journal-family-tag' : ''}">#${escapeHtml(tag)}${actions}</span>`;
  }).join(' ') || '<span class="text-gray-600">Keine Vorschläge</span>';
  const blocked = (catalog.blocked || []).map((tag) => `<span class="inline-flex items-center gap-1 rounded border border-gray-700 px-2 py-1 text-gray-400">#${escapeHtml(tag)}${canManage ? ` <button type="button" data-tag-unblock="${escapeHtmlAttr(tag)}" data-tag-scope="${scope}" class="text-green-400 hover:text-green-300" title="Wieder als Vorschlag anzeigen">Wieder vorschlagen</button>` : ''}</span>`).join(' ') || '<span class="text-gray-600">Keine ignorierten Hashtags</span>';
  const title = scope === 'family' ? 'Family' : 'Persönlich';
  return `<div class="space-y-2"><p class="text-xs font-medium text-gray-400">${title}</p><p class="text-xs text-gray-500">Offene Vorschläge</p><div class="flex flex-wrap gap-1">${proposals}</div><p class="pt-1 text-xs text-gray-500">Ignoriert</p><div class="flex flex-wrap gap-1">${blocked}</div></div>`;
}


function proposalsSection(catalog, canManagePersonal, canManageFamily) {
  return `<section class="bg-gray-900 border border-amber-900/50 rounded-xl p-4 space-y-3"><div><h3 class="font-medium text-amber-200">Vorschläge &amp; Ignorierte</h3><p class="text-xs text-gray-500">Jedes neu geschriebene Hashtag landet zuerst hier. „Ignorieren“ nimmt es dauerhaft aus allen künftigen Vorschlägen; über „Wieder vorschlagen“ kommt es zurück.</p></div>${proposalScopeBlock('personal', catalog.personal || {}, canManagePersonal)}${proposalScopeBlock('family', catalog.family || {}, canManageFamily)}</section>`;
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
    <div class="grid gap-2 sm:grid-cols-2"><label class="text-xs text-gray-500">Zielbereich<select data-ai-target class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="document" ${(workflow.target || 'document') === 'document' ? 'selected' : ''}>Dokument-Session</option><option value="write_tab" ${workflow.target === 'write_tab' ? 'selected' : ''}>Schreiben-Tab (temporär)</option><option value="daily_journal" ${workflow.target === 'daily_journal' ? 'selected' : ''}>Share-API → Tagesjournal</option></select></label><label class="text-xs text-gray-500">Standard-Anbieter<select data-ai-provider class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100"><option value="">Bei Anfrage wählen</option>${providers}</select></label></div>
    <label class="block text-xs text-gray-500">Prompt<textarea data-ai-prompt rows="4" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100">${escapeHtml(workflow.prompt || '')}</textarea></label>
    <label class="block text-xs text-gray-500">Kontextdateien, eine pro Zeile<textarea data-ai-files rows="2" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-gray-100">${escapeHtml((workflow.context_files || []).join('\n'))}</textarea></label>
    <label class="block text-xs text-gray-500">Standard-Hashtags für Share-Einträge<input data-ai-default-hashtags value="${escapeHtmlAttr((workflow.default_hashtags || []).map((tag) => `#${tag}`).join(' '))}" placeholder="#wissen #quelle" class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-gray-100" /></label>
    <fieldset class="text-xs text-gray-500"><legend>Erlaubte Share-Eingaben</legend><div class="mt-1 flex flex-wrap gap-3">${[['text', 'Text'], ['url', 'URL'], ['audio', 'Audio'], ['image', 'Bild'], ['file', 'Datei']].map(([value, label]) => `<label class="flex items-center gap-1 text-gray-300"><input type="checkbox" data-ai-share-input value="${value}" ${(workflow.share_inputs || ['text', 'url', 'audio', 'image', 'file']).includes(value) ? 'checked' : ''}>${label}</label>`).join('')}</div></fieldset>
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


function knowledgeSourcesSection(sources, options, externalRoots, canManagePersonal, canManageFamily) {
  const renderScope = (scope, entries, canManage) => Object.entries(entries || {}).map(([tag, source]) => {
    const path = source.path || '';
    const kind = source.kind || 'reference';
    const description = source.description || '';
    const host = scope === 'host';
    const rootOptions = (externalRoots || []).map((root) => `<option value="${escapeHtmlAttr(root.id)}"${root.id === source.root_id ? ' selected' : ''}>${escapeHtml(root.label)} · ${escapeHtml(root.path)}</option>`).join('');
    const pathControl = host
      ? `<div class="grid gap-2 sm:grid-cols-2"><select data-knowledge-root class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${rootOptions}</select><input data-knowledge-path value="${escapeHtmlAttr(path)}" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100" /></div>`
      : `<select data-knowledge-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100">${knowledgePathOptions(options, scope, path)}</select>`;
    const controls = canManage ? `<div class="mt-2 grid gap-2"><div class="grid gap-2 sm:grid-cols-2">${pathControl}<select data-knowledge-kind class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${knowledgeKindOptions(kind)}</select></div><input data-knowledge-description maxlength="240" value="${escapeHtmlAttr(description)}" placeholder="Zweck für Pi, optional" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100" /><div class="flex gap-2"><button type="button" data-knowledge-save class="text-xs text-green-400 hover:text-green-300">Speichern</button><button type="button" data-knowledge-remove class="text-xs text-red-400 hover:text-red-300">Entfernen</button></div></div>` : '';
    const sourceInfo = `${scope}:${path} · ${kind}`;
    const descriptionInfo = description ? `<p class="mt-1 text-xs text-gray-500">${escapeHtml(description)}</p>` : '';
    return `<article data-knowledge-source-card data-tag="${escapeHtmlAttr(tag)}" data-scope="${scope}" class="rounded border border-violet-900/60 bg-violet-950/20 p-3"><div class="flex flex-wrap items-center justify-between gap-2"><strong class="text-sm text-violet-200">#${escapeHtml(tag)}</strong><span class="font-mono text-xs text-gray-400">${escapeHtml(sourceInfo)}</span></div>${descriptionInfo}${controls}</article>`;
  }).join('') || '<p class="text-sm text-gray-600">Keine Quellen.</p>';
  const scopes = `<option value="personal">Persönlich</option>${canManageFamily ? '<option value="family">Family</option>' : ''}`;
  const hasOptions = (options || []).length > 0;
  const add = canManagePersonal ? `<form data-knowledge-add class="grid gap-2 pt-2"><div class="grid gap-2 sm:grid-cols-4"><input data-knowledge-tag required maxlength="80" placeholder="hashtag ohne #" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><select data-knowledge-scope class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100">${scopes}</select><select data-knowledge-kind class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100">${knowledgeKindOptions()}</select><select data-knowledge-path class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100"></select></div><div class="flex flex-wrap gap-2"><input data-knowledge-description maxlength="240" placeholder="Zweck für Pi, optional" class="min-w-56 flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><button ${hasOptions ? '' : 'disabled'} class="rounded bg-violet-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-600 disabled:cursor-not-allowed disabled:opacity-50">Quelle hinzufügen</button></div></form>${hasOptions ? '' : '<p class="text-xs text-amber-300">Lege zuerst eine persönliche oder sichtbare Family-Notiz bzw. ein Projekt im Brain an.</p>'}` : '';
  const externalAdd = externalRoots.length ? `<form data-external-knowledge-add class="grid gap-2 pt-2"><div class="grid gap-2 sm:grid-cols-2"><input data-knowledge-tag required maxlength="80" placeholder="hashtag ohne #" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><select data-knowledge-kind class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100">${knowledgeKindOptions()}</select></div><div class="grid gap-2 sm:grid-cols-2"><select data-knowledge-root class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100">${externalRoots.map((root) => `<option value="${escapeHtmlAttr(root.id)}">${escapeHtml(root.label)} · ${escapeHtml(root.path)}</option>`).join('')}</select><input data-knowledge-path placeholder="Absoluter Linux-Ordner unter der Lese-Wurzel" class="rounded bg-gray-800 border border-gray-700 px-2 py-1.5 font-mono text-xs text-gray-100" /></div><div class="flex flex-wrap gap-2"><input data-knowledge-description maxlength="240" placeholder="Zweck für Pi, optional" class="min-w-56 flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100" /><button class="rounded bg-violet-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-600">Linux-Ordner hinzufügen</button></div></form>` : '<p class="text-xs text-gray-500">Keine externe Lese-Wurzel in host_worker.json freigegeben.</p>';
  return `<section class="bg-gray-900 border border-violet-900/60 rounded-xl p-4 space-y-3"><div><h3 class="font-medium text-violet-200">Knowledge-Quellen</h3><p class="text-xs text-gray-500">Einzelne Markdown-Dateien werden beim Senden als Snapshot übergeben. Externe Linux-Ordner werden nicht in den Prompt kopiert; Pi darf sie mit begrenzten Werkzeugen rekursiv auflisten, durchsuchen und gezielt lesen.</p></div><div class="space-y-2"><p class="text-xs text-gray-500">Persönlich</p>${renderScope('personal', sources.personal, canManagePersonal)}<p class="pt-1 text-xs text-gray-500">Family</p>${renderScope('family', sources.family, canManageFamily)}<p class="pt-1 text-xs text-gray-500">Externe Linux-Referenzen</p>${renderScope('host', sources.host, canManagePersonal)}</div>${add}${externalAdd}</section>`;
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
  const externalForm = container.querySelector('[data-external-knowledge-add]');
  if (externalForm) externalForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const path = externalForm.querySelector('[data-knowledge-path]').value.trim();
    if (!path) { showToast('Bitte einen Linux-Ordner angeben.', true); return; }
    try {
      await updateTagCatalog({ action: 'save', scope: 'knowledge', tag: externalForm.querySelector('[data-knowledge-tag]').value, source: { path, root_id: externalForm.querySelector('[data-knowledge-root]').value, kind: externalForm.querySelector('[data-knowledge-kind]').value, description: externalForm.querySelector('[data-knowledge-description]').value.trim() } });
    } catch (error) { showToast(error.message, true); }
  });
  container.querySelectorAll('[data-knowledge-save]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-knowledge-source-card]');
    const path = card.querySelector('[data-knowledge-path]').value;
    if (!path) { showToast('Bitte eine vorhandene Quelle auswählen.', true); return; }
    try {
      await updateTagCatalog({ action: 'save', scope: 'knowledge', tag: card.dataset.tag, source: { path, root_id: card.querySelector('[data-knowledge-root]')?.value || '', kind: card.querySelector('[data-knowledge-kind]').value, description: card.querySelector('[data-knowledge-description]').value.trim(), family: card.dataset.scope === 'family' } });
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
    default_hashtags: card.querySelector('[data-ai-default-hashtags]').value.match(/#?[\w-]+/gu)?.map((value) => value.replace(/^#/, '')) || [],
    share_inputs: [...card.querySelectorAll('[data-ai-share-input]:checked')].map((input) => input.value),
  };
}


// Each sub-section is rendered on its own so a single click never rebuilds the
// whole catalog page.  ``badge`` returns the number shown in the nav button.
const CATALOG_SECTIONS = [
  { id: 'personal', label: 'Meine Hashtags', badge: (data) => (data.catalog?.personal?.canonical || []).length },
  { id: 'family', label: 'Family-Hashtags', badge: (data) => (data.catalog?.family?.canonical || []).length },
  { id: 'proposals', label: 'Vorschläge', badge: (data) => (data.catalog?.personal?.proposals || []).length + (data.catalog?.family?.proposals || []).length },
  { id: 'ai', label: 'AI-Hashtags', badge: (data) => Object.keys(data.catalog?.ai || {}).length },
  { id: 'knowledge', label: 'Knowledge', badge: (data) => ['personal', 'family', 'host'].reduce((total, scope) => total + Object.keys(data.catalog?.knowledge?.[scope] || {}).length, 0) },
  { id: 'write_targets', label: 'Schreibziele', badge: (data) => ['personal', 'family', 'host'].reduce((total, scope) => total + Object.keys(data.catalog?.write_targets?.[scope] || {}).length, 0) },
];


function catalogNav(data) {
  const buttons = CATALOG_SECTIONS.map((section) => {
    const active = section.id === brainState.catalogSection;
    const count = section.badge(data);
    const highlight = section.id === 'proposals' && count ? ' text-amber-300' : '';
    return `<button type="button" data-catalog-section="${section.id}" aria-pressed="${active}" class="rounded px-3 py-1.5 text-sm transition ${active ? 'bg-gray-700 text-white font-semibold' : `bg-gray-800 text-gray-300 hover:bg-gray-700${highlight}`}">${escapeHtml(section.label)} <span class="text-xs text-gray-400">${count}</span></button>`;
  }).join('');
  return `<nav class="flex flex-wrap gap-2" aria-label="Hashtag-Verwaltung">${buttons}</nav>`;
}


function catalogSectionMarkup(data) {
  const catalog = data.catalog || {};
  const canPersonal = data.can_manage_personal ?? true;
  const canFamily = data.can_manage_family ?? data.can_manage;
  switch (brainState.catalogSection) {
    case 'family':
      return tagCatalogSection('Family-Hashtags', 'family', catalog.family || {}, canFamily);
    case 'proposals':
      return proposalsSection(catalog, canPersonal, canFamily);
    case 'ai':
      return aiWorkflowSection(catalog.ai || {});
    case 'knowledge':
      return knowledgeSourcesSection(catalog.knowledge || {}, data.knowledge_source_options || [], data.external_read_roots || [], canPersonal, canFamily);
    case 'write_targets':
      return writeTargetsSection(catalog.write_targets || {}, data.write_target_options || [], data.external_write_roots || [], canPersonal, canFamily);
    default:
      return tagCatalogSection('Meine Hashtags', 'personal', catalog.personal || {}, canPersonal);
  }
}


function renderTagCatalog() {
  const data = brainState.catalogData;
  if (!data) return;
  const container = brainElement('brain-browser');
  const catalog = data.catalog || {};
  container.innerHTML = `<div class="space-y-3"><button type="button" data-tag-catalog-back class="text-sm text-green-400 hover:text-green-300">Zurueck zu Timeline &amp; Suche</button>${catalogNav(data)}${catalogSectionMarkup(data)}</div>`;
  container.querySelector('[data-tag-catalog-back]').addEventListener('click', () => {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    loadBrainCurrentMode();
  });
  container.querySelectorAll('[data-catalog-section]').forEach((button) => button.addEventListener('click', () => {
    brainState.catalogSection = button.dataset.catalogSection;
    renderTagCatalog();
  }));
  container.querySelectorAll('[data-catalog-goto]').forEach((button) => button.addEventListener('click', () => {
    brainState.catalogSection = button.dataset.catalogGoto;
    renderTagCatalog();
  }));
  bindCatalogTagControls(container, catalog);
  if (brainState.catalogSection === 'ai') bindAiWorkflowControls(container, catalog.ai || {});
  if (brainState.catalogSection === 'knowledge') bindKnowledgeSourceControls(container, data.knowledge_source_options || []);
  if (brainState.catalogSection === 'write_targets') bindWriteTargetControls(container, data.write_target_options || []);
}


function bindCatalogTagControls(container, catalog) {
  const run = (button, payload) => button.addEventListener('click', async () => {
    try { await updateTagCatalog(payload(button)); } catch (error) { showToast(error.message, true); }
  });
  container.querySelectorAll('[data-tag-approve]').forEach((button) => run(button, () => ({ action: 'approve', tag: button.dataset.tagApprove, scope: button.dataset.tagScope })));
  container.querySelectorAll('[data-tag-remove]').forEach((button) => run(button, () => ({ action: 'remove', tag: button.dataset.tagRemove, scope: button.dataset.tagScope })));
  container.querySelectorAll('[data-tag-remove-alias]').forEach((button) => run(button, () => ({ action: 'remove_alias', tag: button.dataset.tagRemoveAlias, scope: button.dataset.tagScope })));
  container.querySelectorAll('[data-tag-block]').forEach((button) => run(button, () => ({ action: 'block', tag: button.dataset.tagBlock, scope: button.dataset.tagScope })));
  container.querySelectorAll('[data-tag-unblock]').forEach((button) => run(button, () => ({ action: 'unblock', tag: button.dataset.tagUnblock, scope: button.dataset.tagScope })));
  container.querySelectorAll('[data-tag-alias]').forEach((button) => button.addEventListener('click', async () => {
    const tag = window.prompt('Alias (ohne #):');
    const target = tag && window.prompt('Freigegebenes Ziel-Hashtag (ohne #):');
    if (!tag || !target) return;
    try { await updateTagCatalog({ action: 'alias', tag, target, scope: button.dataset.tagScope }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-tag-alias-from]').forEach((button) => button.addEventListener('click', async () => {
    const scope = button.dataset.tagScope;
    const approved = (catalog[scope] || {}).canonical || [];
    if (!approved.length) { showToast('Es gibt noch kein freigegebenes Hashtag als Alias-Ziel.', true); return; }
    const target = window.prompt(`#${button.dataset.tagAliasFrom} als Alias auf welches freigegebene Hashtag?\nVerfügbar: ${approved.join(', ')}`);
    if (!target) return;
    try { await updateTagCatalog({ action: 'alias', tag: button.dataset.tagAliasFrom, target, scope }); } catch (error) { showToast(error.message, true); }
  }));
}


function bindAiWorkflowControls(container, workflows) {
  container.querySelectorAll('[data-ai-workflow-card]').forEach((card) => {
    const provider = card.querySelector('[data-ai-provider]');
    if (provider) provider.value = (workflows[card.dataset.tag] || {}).provider_id || '';
  });
  container.querySelectorAll('[data-ai-workflow-save]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-ai-workflow-card]');
    try { await updateTagCatalog({ action: 'save', scope: 'ai', tag: card.dataset.tag, workflow: workflowPayload(card) }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelectorAll('[data-ai-workflow-remove]').forEach((button) => button.addEventListener('click', async () => {
    const card = button.closest('[data-ai-workflow-card]');
    if (!window.confirm(`#${card.dataset.tag} wirklich entfernen?`)) return;
    try { await updateTagCatalog({ action: 'remove', scope: 'ai', tag: card.dataset.tag }); } catch (error) { showToast(error.message, true); }
  }));
  container.querySelector('[data-ai-workflow-add]')?.addEventListener('click', async () => {
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
}


async function fetchTagCatalog() {
  const response = await apiFetch('/api/brain/tag-catalog');
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Katalog konnte nicht geladen werden.');
  brainState.catalogData = data;
  return data;
}


// Re-read after a write and repaint only the section the user is looking at.
async function refreshTagCatalog() {
  await fetchTagCatalog();
  if (brainState.catalogOpen) renderTagCatalog();
}


async function loadTagCatalog() {
  brainState.catalogOpen = true;
  const container = brainElement('brain-browser');
  showBrainBrowser();
  brainElement('brain-tag-filter-area')?.classList.add('hidden');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Katalog wird geladen...</p>';
  try {
    await fetchTagCatalog();
  } catch (error) {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    showToast(error.message, true);
    return;
  }
  renderTagCatalog();
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
