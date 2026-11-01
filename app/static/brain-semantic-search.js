// Hybride Suche im Brain-Tab: Worttreffer und Bedeutungstreffer, per RRF gemischt.
//
// Ist die semantische Suche fuer das Konto an, bekommt die Sortierung die
// Option „Relevanz" und steht zunaechst darauf. Die Wortsuche bleibt der erste
// Schritt und erscheint sofort (brain.js). Danach fragt diese Datei
// /api/brain/semantic/cards mit denselben Filtern und ersetzt die Liste durch
// die gemischte Rangfolge. Hat jemand inzwischen gescrollt, aufgeklappt,
// ausgewaehlt oder den Editor geoeffnet, springt nichts: dann steht nur ein
// Knopf „Nach Relevanz ordnen" ueber der Liste.
// Faellt die semantische Seite aus, bleibt die Wortliste und der Grund steht
// darueber - nie still.
//
// „Ähnliche Einträge" im Kartenmenue zeigt die Bloecke, die einer Karte
// aehneln (/api/brain/semantic/similar, gespeicherter Vektor, kein
// Modellaufruf), mit „Zurück zur Suche" darueber.

const brainSemantic = { enabled: false, known: false, loading: null, controller: null, pending: null };


function brainOriginBadge(origin) {
  if (origin === 'semantic') {
    return '<span class="rounded-full border border-sky-800 px-2 py-0.5 text-sky-300" title="Über die Bedeutung gefunden, nicht über ein Wort">ähnlich</span>';
  }
  if (origin === 'both') {
    return '<span class="rounded-full border border-green-800 px-2 py-0.5 text-green-300" title="Über Wort und Bedeutung gefunden">Wort + Bedeutung</span>';
  }
  return '';
}


// Nur ein Umschalten in den Settings laedt neu; beim Seitenstart sucht brain.js ohnehin.
function brainSemanticApply(enabled, reload = false) {
  brainSemantic.enabled = enabled;
  brainSemantic.known = true;
  // Den Chat-Schalter „Bedeutungssuche" gibt es nur, wenn die Suche fuer das Konto an ist.
  brainElement('brain-chat-scope-semantic')?.classList.toggle('hidden', !enabled);
  const order = brainElement('brain-order');
  if (!order) return;
  const option = order.querySelector('option[value="relevance"]');
  let changed = false;
  if (enabled && !option) {
    order.insertAdjacentHTML('afterbegin', '<option value="relevance">Relevanz (Wort + Bedeutung)</option>');
    if (order.value === 'newest') {
      order.value = 'relevance';
      changed = true;
    }
  } else if (!enabled && option) {
    changed = order.value === 'relevance';
    option.remove();
    if (changed) order.value = 'newest';
  }
  if (changed && reload) order.dispatchEvent(new Event('change'));
}


function brainSemanticLoad() {
  if (!brainSemantic.loading) {
    brainSemantic.loading = apiFetch('/api/brain/semantic/settings')
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data) brainSemanticApply(Boolean(data.user?.enabled)); })
      .catch(() => {})
      .finally(() => { brainSemantic.loading = null; });
  }
  return brainSemantic.loading;
}


// ``action``: optional { label, run } fuer einen Knopf neben dem Hinweis.
function brainSemanticNote(text, action = null) {
  const container = brainElement('brain-browser');
  if (!container) return;
  container.querySelector('[data-brain-semantic-note]')?.remove();
  if (!text) return;
  const button = action
    ? `<button type="button" data-brain-semantic-action class="underline decoration-dotted underline-offset-2 hover:text-gray-300">${escapeHtml(action.label)}</button>`
    : '';
  container.insertAdjacentHTML('afterbegin', `<div data-brain-semantic-note class="flex flex-wrap items-center gap-2 text-xs text-gray-500"><span>${escapeHtml(text)}</span>${button}</div>`);
  if (action) container.querySelector('[data-brain-semantic-action]')?.addEventListener('click', action.run);
}


function brainSemanticSnapshot() {
  return {
    scroll: brainElement('brain-browser')?.scrollTop || 0,
    expanded: brainState.expanded.size,
    selected: brainState.selected.size,
    editor: Boolean(brainState.editor),
  };
}


function brainSemanticMoved(before) {
  const now = brainSemanticSnapshot();
  return Math.abs(now.scroll - before.scroll) > 40 || now.expanded !== before.expanded
    || now.selected !== before.selected || now.editor !== before.editor;
}


function brainSemanticShow(data) {
  brainSemantic.pending = null;
  renderBrainResults(data.results, data);
  const semanticOnly = data.results.filter((item) => item.origin === 'semantic').length;
  brainSemanticNote(`Nach Relevanz: ${data.results.length} Treffer, davon ${semanticOnly} nur über die Bedeutung`);
}


// Aufruf aus loadBrainResults, sobald die Wortliste steht.
async function brainSemanticRefine(params) {
  brainSemantic.controller?.abort();
  brainSemantic.controller = null;
  brainSemantic.pending = null;
  if (!brainSemantic.known) await brainSemanticLoad();
  const query = params.get('q') || '';
  if (!brainSemantic.enabled || params.get('order') !== 'relevance' || !query
    || !['search', 'journals'].includes(brainState.mode)) return;
  const controller = new AbortController();
  brainSemantic.controller = controller;
  const generation = brainState.generation;
  const mode = brainState.mode;
  const before = brainSemanticSnapshot();
  brainSemanticNote('Bedeutungssuche läuft …');
  const cardParams = new URLSearchParams(params);
  cardParams.delete('order');
  cardParams.delete('facets');
  cardParams.set('limit', '50');
  let data;
  try {
    const response = await apiFetch('/api/brain/semantic/cards?' + cardParams.toString(), { signal: controller.signal });
    data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Bedeutungssuche fehlgeschlagen.');
  } catch (error) {
    if (controller.signal.aborted) return;
    data = { results: [], semantic: { status: 'unavailable', reason: error.message } };
  }
  const stale = controller.signal.aborted || brainSemantic.controller !== controller
    || generation !== brainState.generation || mode !== brainState.mode
    || (brainElement('brain-query')?.value.trim() || '') !== query;
  if (stale) return;
  brainSemantic.controller = null;
  const semantic = data.semantic || {};
  if (semantic.status === 'off') {
    brainSemanticNote('');
    return;
  }
  if (semantic.status !== 'ok') {
    brainSemanticNote(`Nur Wortsuche: ${semantic.reason || 'Bedeutungssuche nicht verfügbar.'}`);
    return;
  }
  if (!data.results?.length) {
    brainSemanticNote('Nichts hinreichend Ähnliches gefunden.');
    return;
  }
  if (brainSemanticMoved(before)) {
    brainSemantic.pending = data;
    brainSemanticNote('Die gemischte Rangfolge ist da.', {
      label: 'Nach Relevanz ordnen',
      run: () => { if (brainSemantic.pending) brainSemanticShow(brainSemantic.pending); },
    });
    return;
  }
  brainSemanticShow(data);
}


const BRAIN_SIMILAR_ICON = '<svg viewBox="0 0 24 24" class="h-4 w-4 pointer-events-none shrink-0" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="6" cy="6" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="10" cy="18" r="2"/><path d="m7.8 6.6 8.3 1"/><path d="m6.9 7.8 2.3 8.4"/><path d="m16.6 9.5-5.2 7"/></svg>';


// Menueeintrag einer Karte (brain-cards.js); leer ohne semantische Suche.
function brainSimilarButton(item) {
  if (!brainSemantic.enabled || item.db_journal) return '';
  return `<button type="button" data-brain-similar data-doc-id="${escapeHtmlAttr(item.doc_id)}" data-fingerprint="${escapeHtmlAttr(item.fingerprint)}" data-menu-label="Ähnliche Einträge" class="text-green-500 hover:text-green-400 transition text-xs" title="Ähnliche Einträge" aria-label="Ähnliche Einträge">${BRAIN_SIMILAR_ICON}</button>`;
}


async function brainSemanticSimilar(docId, fingerprint) {
  brainSemantic.controller?.abort();
  const controller = new AbortController();
  brainSemantic.controller = controller;
  brainSemantic.pending = null;
  const origin = brainState.results.get(fingerprint);
  const name = String(origin?.path || '').split('/').pop() || 'diesem Eintrag';
  const label = origin?.date ? `${name} (${formatBrainRangeDate(String(origin.date).slice(0, 10))})` : name;
  const back = { label: 'Zurück zur Suche', run: () => loadBrainResults() };
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Suche ähnliche Einträge …</p>';
  try {
    const params = new URLSearchParams({ doc_id: docId, fingerprint });
    const response = await apiFetch('/api/brain/semantic/similar?' + params.toString(), { signal: controller.signal });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Ähnliche Einträge konnten nicht geladen werden.');
    if (brainSemantic.controller !== controller) return;
    brainSemantic.controller = null;
    renderBrainResults(data.results || [], data);
    brainSemanticNote(`Ähnlich zu ${label}: ${(data.results || []).length} Einträge`, back);
  } catch (error) {
    if (controller.signal.aborted || brainSemantic.controller !== controller) return;
    brainSemantic.controller = null;
    container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(error.message)}</p>`;
    brainSemanticNote(' ', back);
  }
}


document.addEventListener('journl:semantic-search-changed', (event) => {
  brainSemanticApply(Boolean(event.detail?.enabled), true);
});
brainSemanticLoad();
