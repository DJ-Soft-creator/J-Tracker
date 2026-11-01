/* Hashtag-Map im Brain-Tab („⋯“-Menue → Hashtag-Map).
 *
 * Fuenf Sichten auf dieselben Daten aus /api/brain/tag-map:
 *   - Uebersicht: alle Hashtags des Zeitraums (bis 500) ohne Mitte, gross =
 *              haeufig; Zeigen blendet die Verbindungen ein, Klick fuehrt in
 *              den Orbit.
 *   - Hirn:    Themenfelder als Sektoren, Abstand zur Mitte = Abstraktion;
 *              Reindrillen ins Teil-Hirn mit Brotkrumen.
 *   - Orbit:   ein Hashtag in der Mitte, die verwandten kreisen auf Bahnen -
 *              je enger verbunden, desto naeher. Klick macht ihn zur Mitte.
 *   - Cluster: die haeufigsten Hashtags als Kraefte-Layout, Farbe = Themenfeld.
 *   - Zeit:    je Hashtag eine Zeile, je Monat (kurze Zeitraeume: je Tag) ein
 *              Punkt - wann ein Thema auftauchte, wiederkam oder verschwand.
 * Die Zeitfarbe (zuletzt genutzt) ist in allen Sichten dieselbe fuenfstufige
 * Skala hell → dunkel; in Cluster und Hirn traegt sie der Ring, weil die
 * Fuellung dort das Themenfeld zeigt.
 *
 * Von der Map in die Eintraege: Knoten antippen oeffnet eine Karte mit
 * „Einträge ansehen“; Doppelklick bzw. Langdruecken oeffnet das Knotenmenue,
 * ein Klick auf eine Verbindung das Linienmenue (brain-tag-map-actions.js).
 * Gefiltert wird in „Timeline & Suche“ (brainShowTagEntries), mit dem
 * Zeitraum der Map. Filter der Map: freier Text und/oder Chips (state.picked).
 *
 * Zoom: Mausrad zum Cursor, Ziehen auf freier Flaeche verschiebt (im Orbit:
 * dreht, Shift+Ziehen verschiebt), Pfeiltasten verschieben, Shift+←/→ dreht
 * den Orbit, Leertaste haelt ihn an, +/− zoomt, 0 setzt zurueck. Knoten in
 * Uebersicht und Cluster lassen sich mit der Maus verschieben.
 *
 * Diese Datei: Rahmen, Daten, Zoom, Auswahl und Sprung. Die Sichten zeichnet
 * brain-tag-map-views.js (meldet sich in kit.views an), die Vorschlagsliste
 * brain-tag-map-list.js (Hashtag-Liste und Chips), Knoten-/Linienmenue/Reindrillen/Ausblenden/Anordnung
 * brain-tag-map-actions.js - alle laden danach und nutzen kit.
 *
 * Reine Oberflaeche: Sichtbarkeit und Hashtag-Aufloesung entscheidet der
 * Server (brain_tag_map.py). Hashtag-Namen werden nur als Text gesetzt.
 */
(function () {
  const SVG = 'http://www.w3.org/2000/svg';
  const CLUSTER_COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#22d3ee', '#fb7185', '#a3e635'];
  // Frisch → alt. Helligkeit faellt mit dem Alter, benachbarte Stufen liegen
  // auch fuer Farbfehlsichtige weit auseinander (gegen #030712 geprueft).
  const RECENCY_COLORS = ['#fef08a', '#f59e0b', '#ef4444', '#a855f7', '#64748b'];
  const RANGES = [
    ['2d', 'Letzte 2 Tage'], ['3d', 'Letzte 3 Tage'], ['7d', 'Letzte 7 Tage'],
    ['3m', 'Letzte 3 Monate'], ['12m', 'Letzte 12 Monate'], ['year', 'Dieses Jahr'],
    ['all', 'Gesamter Zeitraum'], ['custom', 'Individuell'],
  ];
  const FAMILY_KEY = 'journl.tagMap.family';
  const PAUSE_KEY = 'journl.tagMap.paused';
  const RINGS_KEY = 'journl.tagMap.rings';
  const LOAD_LIMIT = 500;
  const CLUSTER_LIMIT = 80;
  const DAY = 86400000;

  const state = {
    open: false, view: 'overview', range: 'all', data: null, focus: '', selected: '',
    frame: 0, layout: null, overview: null, hover: '', family: readFamily(),
    zoom: { k: 1, x: 0, y: 0 }, spin: 0, filter: '', picked: new Set(),
    orbitTime: 0, paused: readFlag(PAUSE_KEY), orbitRender: null, orbitCenter: null,
    rings: readRings(), drill: [], layouts: {},
  };
  const byId = (id) => document.getElementById(id);
  const hoverDevice = window.matchMedia?.('(hover: hover)').matches;

  function readFamily() {
    try { return localStorage.getItem(FAMILY_KEY) !== '0'; } catch { return true; }
  }

  function readRings() {
    try { return ['1', '2', '3', '4', 'auto'].includes(localStorage.getItem(RINGS_KEY)) ? localStorage.getItem(RINGS_KEY) : '3'; } catch { return '3'; }
  }

  function readFlag(key) {
    try { return localStorage.getItem(key) === '1'; } catch { return false; }
  }

  // ─── Rahmen ──────────────────────────────────────────────────────
  function panel() {
    let element = byId('brain-tag-map');
    if (element) return element;
    const chat = byId('brain-chat');
    if (!chat) return null;
    element = document.createElement('div');
    element.id = 'brain-tag-map';
    // Gleiche Abstaende wie der Chat - Desktop und Handy haben dort eigene.
    element.className = chat.className.split(/\s+/).filter((name) => name && name !== 'flex').join(' ');
    element.classList.add('hidden', 'flex-col');
    element.innerHTML = `
      <div class="flex flex-wrap items-center gap-2 shrink-0 pb-2">
        <button type="button" data-map-back class="text-sm text-green-400 hover:text-green-300">Zurück zu Timeline &amp; Suche</button>
        <div class="ml-auto flex items-center gap-1 rounded-full border border-gray-800 bg-gray-900/60 p-0.5 text-xs" role="tablist">
          <button type="button" data-map-view="overview" class="rounded-full px-3 py-1">Übersicht</button>
          <button type="button" data-map-view="brain" class="rounded-full px-3 py-1">Hirn</button>
          <button type="button" data-map-view="orbit" class="rounded-full px-3 py-1">Orbit</button>
          <button type="button" data-map-view="cluster" class="rounded-full px-3 py-1">Cluster</button>
          <button type="button" data-map-view="time" class="rounded-full px-3 py-1">Zeit</button>
        </div>
      </div>
      <div class="flex flex-wrap items-center gap-2 shrink-0 pb-2 text-xs">
        <select data-map-range class="rounded-md border border-gray-800 bg-gray-900 px-2 py-1 text-gray-300"></select>
        <label class="flex items-center gap-1 text-gray-500">von
          <input type="date" data-map-from class="rounded-md border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-gray-300 [color-scheme:dark]"></label>
        <label class="flex items-center gap-1 text-gray-500">bis
          <input type="date" data-map-to class="rounded-md border border-gray-800 bg-gray-900 px-1.5 py-0.5 text-gray-300 [color-scheme:dark]"></label>
        <button type="button" data-map-family aria-pressed="true"
          class="rounded-full border px-2.5 py-1">Familie</button>
        <span class="relative flex min-w-0 flex-1 sm:max-w-[14rem]">
          <input data-map-search list="brain-tag-map-names" placeholder="Filtern oder Hashtag suchen …"
            class="min-w-0 flex-1 rounded-md border border-gray-800 bg-gray-900 py-1 pl-2 pr-7 text-gray-200 placeholder:text-gray-600">
          <button type="button" data-map-search-clear class="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-0.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300" title="Filter aufheben" aria-label="Filter aufheben">
            <svg viewBox="0 0 24 24" class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="M6 6l12 12"/></svg>
          </button>
        </span>
        <datalist id="brain-tag-map-names"></datalist>
        <span data-map-info class="text-gray-500"></span>
      </div>
      <div data-map-stage class="relative flex-1 min-h-[24rem] sm:min-h-[30rem] overflow-hidden rounded-xl border border-gray-800 bg-gray-950/70">
        <svg data-map-svg class="absolute inset-0 h-full w-full select-none [-webkit-touch-callout:none]" role="img" aria-label="Hashtag-Map"></svg>
        <div data-map-tip class="pointer-events-none absolute hidden max-w-[16rem] rounded-md border border-gray-700 bg-gray-900/95 px-2 py-1 text-[11px] text-gray-200 shadow-lg"></div>
        <div data-map-card class="absolute right-2 top-2 hidden w-[15rem] max-w-[calc(100%-1rem)] rounded-lg border border-gray-700 bg-gray-900/95 p-2.5 text-xs text-gray-300 shadow-lg">
          <div class="flex items-start gap-2">
            <div class="min-w-0 flex-1">
              <div data-card-title class="truncate font-semibold text-gray-100"></div>
              <div data-card-meta class="mt-0.5 text-[11px] text-gray-500"></div>
            </div>
            <button type="button" data-card-close class="shrink-0 rounded p-0.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300" aria-label="Karte schließen">
              <svg viewBox="0 0 24 24" class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="M6 6l12 12"/></svg>
            </button>
          </div>
          <button type="button" data-card-approve class="mt-2 hidden w-full rounded-md border border-amber-700/60 bg-amber-900/20 px-2 py-1 text-amber-200 hover:bg-amber-900/40">Hashtag freigeben</button>
          <button type="button" data-card-open class="mt-2 w-full rounded-md border border-green-700/60 bg-green-900/30 px-2 py-1 text-green-300 hover:bg-green-900/50">Einträge ansehen</button>
        </div>
        <div data-map-legend class="pointer-events-none absolute bottom-2 left-2 mr-36 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-gray-950/85 px-1.5 py-0.5 text-[10px] text-gray-500"></div>
        <div class="absolute bottom-2 right-2 flex items-center gap-1 text-xs">
          <select data-map-rings class="hidden h-7 rounded-md border border-gray-800 bg-gray-900/90 px-1 text-gray-300" title="Abstraktion: feste Ringe oder stufenlos">
            <option value="1">1 Ring</option><option value="2">2 Ringe</option><option value="3">3 Ringe</option><option value="4">4 Ringe</option><option value="auto">stufenlos</option>
          </select>
          <button type="button" data-map-orbit="pause" class="hidden h-7 rounded-md border border-gray-800 bg-gray-900/80 px-2 text-gray-300 hover:bg-gray-800" title="Drehung anhalten/fortsetzen (Leertaste)"></button>
          <button type="button" data-map-reset class="hidden h-7 rounded-md border border-gray-800 bg-gray-900/80 px-2 text-gray-400 hover:bg-gray-800" title="Zurück zur Standardansicht ohne Filter">Zurücksetzen</button>
          <button type="button" data-map-zoom="in" class="h-7 w-7 rounded-md border border-gray-800 bg-gray-900/80 text-gray-300 hover:bg-gray-800" title="Hineinzoomen (+)" aria-label="Hineinzoomen">+</button>
          <button type="button" data-map-zoom="out" class="h-7 w-7 rounded-md border border-gray-800 bg-gray-900/80 text-gray-300 hover:bg-gray-800" title="Herauszoomen (−)" aria-label="Herauszoomen">−</button>
          <button type="button" data-map-zoom="reset" class="h-7 rounded-md border border-gray-800 bg-gray-900/80 px-2 text-gray-400 hover:bg-gray-800" title="Zurücksetzen (0)">Einpassen</button>
        </div>
        <p data-map-empty class="absolute inset-0 hidden grid place-items-center text-sm text-gray-500"></p>
      </div>`;
    chat.insertAdjacentElement('afterend', element);
    element.querySelector('[data-map-range]').innerHTML = RANGES.map(([value, label]) =>
      `<option value="${value}">${label}</option>`).join('');
    element.querySelector('[data-map-back]').addEventListener('click', () => close());
    element.querySelectorAll('[data-map-view]').forEach((button) =>
      button.addEventListener('click', () => setView(button.dataset.mapView)));
    element.querySelector('[data-map-range]').addEventListener('change', (event) => {
      state.range = event.target.value;
      if (state.range !== 'custom') fillDates();
      load();
    });
    element.querySelectorAll('[data-map-from], [data-map-to]').forEach((input) =>
      input.addEventListener('change', () => {
        state.range = 'custom';
        element.querySelector('[data-map-range]').value = 'custom';
        load();
      }));
    element.querySelector('[data-map-family]').addEventListener('click', () => {
      state.family = !state.family;
      try { localStorage.setItem(FAMILY_KEY, state.family ? '1' : '0'); } catch { /* nur Komfort */ }
      familyButton();
      load();
    });
    // Tippen filtert live; Enter bzw. Auswahl aus der Liste holt den Treffer
    // in die Mitte. Liegen nicht alle Hashtags vor, fragt der Filter den
    // Server - sonst endete er an der Kappung.
    let filterTimer = 0;
    element.querySelector('[data-map-search]').addEventListener('input', (event) => {
      clearTimeout(filterTimer);
      filterTimer = setTimeout(() => setFilter(event.target.value), 250);
    });
    element.querySelector('[data-map-search-clear]').addEventListener('click', () => {
      clearTimeout(filterTimer);
      clearFilter();
    });
    element.querySelector('[data-map-search]').addEventListener('change', (event) => {
      const wanted = normaliseFilter(event.target.value);
      const tags = state.data?.tags || [];
      const tag = tags.find((item) => item.name === wanted) || tags.find((item) => item.name.includes(wanted));
      if (wanted && tag) select(tag);
    });
    element.querySelector('[data-card-close]').addEventListener('click', () => { state.selected = ''; card(); });
    element.querySelector('[data-card-open]').addEventListener('click', () => {
      if (state.selected) openEntries([state.selected]);
    });
    element.querySelector('[data-card-approve]').addEventListener('click', () => {
      const tag = state.data?.byName.get(state.selected);
      if (tag) approve(tag.name, tag.scope);
    });
    element.querySelectorAll('[data-map-zoom]').forEach((button) => button.addEventListener('click', () => {
      if (button.dataset.mapZoom === 'reset') resetZoom();
      else zoomBy(button.dataset.mapZoom === 'in' ? 1.25 : 0.8);
    }));
    element.querySelector('[data-map-orbit="pause"]').addEventListener('click', togglePause);
    element.querySelector('[data-map-rings]').value = state.rings;
    element.querySelector('[data-map-rings]').addEventListener('change', (event) => {
      state.rings = event.target.value;
      try { localStorage.setItem(RINGS_KEY, state.rings); } catch { /* nur Komfort */ }
      draw();
    });
    element.querySelector('[data-map-reset]').addEventListener('click', resetAll);
    bindZoom(element.querySelector('[data-map-svg]'));
    new ResizeObserver(() => { if (state.open) draw(); }).observe(element.querySelector('[data-map-stage]'));
    familyButton();
    return element;
  }

  function familyButton() {
    const button = panel()?.querySelector('[data-map-family]');
    if (!button) return;
    button.setAttribute('aria-pressed', String(state.family));
    button.title = state.family ? 'Familien-Inhalte sind dabei' : 'Nur eigene Inhalte';
    button.classList.toggle('border-green-700/60', state.family);
    button.classList.toggle('text-green-300', state.family);
    button.classList.toggle('border-gray-800', !state.family);
    button.classList.toggle('text-gray-500', !state.family);
  }

  function el(name, attributes, parent) {
    const node = document.createElementNS(SVG, name);
    Object.entries(attributes || {}).forEach(([key, value]) => node.setAttribute(key, value));
    if (parent) parent.appendChild(node);
    return node;
  }

  // ─── Zeitraum ────────────────────────────────────────────────────
  const pad = (value) => String(value).padStart(2, '0');
  const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const dayNumber = (value) => Math.floor(Date.parse(`${value}T00:00:00Z`) / DAY);

  function presetStart(range) {
    const today = new Date();
    const back = (days) => iso(new Date(today.getFullYear(), today.getMonth(), today.getDate() - days));
    if (range === '2d') return back(1);
    if (range === '3d') return back(2);
    if (range === '7d') return back(6);
    if (range === '3m') return iso(new Date(today.getFullYear(), today.getMonth() - 3, today.getDate()));
    if (range === '12m') return iso(new Date(today.getFullYear() - 1, today.getMonth(), today.getDate()));
    if (range === 'year') return `${today.getFullYear()}-01-01`;
    return '';
  }

  // Ein Preset zeigt seine Daten in den Feldern - wer dann eines aendert,
  // landet bei „Individuell“ und sieht, wovon er ausgeht.
  function fillDates() {
    const root = panel();
    root.querySelector('[data-map-from]').value = presetStart(state.range);
    root.querySelector('[data-map-to]').value = '';
  }

  function chosenRange() {
    const root = panel();
    if (state.range !== 'custom') return { start: presetStart(state.range), end: '' };
    return { start: root.querySelector('[data-map-from]').value, end: root.querySelector('[data-map-to]').value };
  }

  // ─── Daten ───────────────────────────────────────────────────────
  async function load() {
    const root = panel();
    const info = root.querySelector('[data-map-info]');
    const range = chosenRange();
    if (range.start && range.end && range.start > range.end) {
      info.textContent = '„von“ liegt nach „bis“.';
      return;
    }
    info.textContent = 'Lade …';
    const params = new URLSearchParams({ limit: String(LOAD_LIMIT) });
    if (range.start) params.set('start_date', range.start);
    if (range.end) params.set('end_date', range.end);
    if (!state.family) params.set('family', '0');
    if (state.filter) params.set('match', state.filter);
    if (state.picked.size) params.set('include', [...state.picked].slice(0, 50).join(','));
    try {
      const response = await window.apiFetch(`/api/brain/tag-map?${params}`, { cache: 'no-store', timeoutMs: 30000 });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Hashtag-Map nicht verfügbar');
      state.data = prepare(data, range);
    } catch (error) {
      state.data = null;
      info.textContent = error.message;
      draw();
      return;
    }
    const data = state.data;
    if (!data.byName.has(state.focus)) state.focus = data.tags[0]?.name || '';
    if (!data.byName.has(state.selected)) state.selected = '';
    state.loadedFilter = state.filter;
    info.textContent = infoText();
    root.querySelector('[data-map-search-clear]').classList.toggle('hidden', !filterActive());
    const list = root.querySelector('#brain-tag-map-names');
    list.textContent = '';
    data.tags.forEach((tag) => { const option = document.createElement('option'); option.value = tag.name; list.appendChild(option); });
    state.layout = null;
    state.overview = null;
    state.zoom = null;
    draw();
    kit.hooks.loaded.forEach((hook) => hook());
  }

  function infoText() {
    const data = state.data;
    if (!data) return '';
    if (filterActive()) {
      const hits = data.tags.filter((tag) => matches(tag.name)).length;
      const what = [state.filter && `„${state.filter}“`, state.picked.size && `${state.picked.size} gewählte`].filter(Boolean).join(' + ');
      return `${hits} Treffer für ${what} · ${data.tags.length} Hashtags`;
    }
    return `${data.tags.length} Hashtags · ${data.blocks} Einträge`
      + (data.total_tags > data.tags.length ? ` · die ${data.tags.length} häufigsten von ${data.total_tags}` : '');
  }

  // ─── Filter und Freigabe ─────────────────────────────────────────
  const normaliseFilter = (value) => String(value || '').trim().replace(/^#/, '').toLowerCase();
  // Filter = freier Text (Teilwort) und/oder gewaehlte Hashtags (Chips, wie
  // die Tag-Filter der Timeline-Suche); passt eins, bleibt der Hashtag sichtbar.
  const filterActive = () => Boolean(state.filter || state.picked.size);
  const matches = (name) => !filterActive() || state.picked.has(name) || Boolean(state.filter && name.includes(state.filter));

  function refilter() {
    panel().querySelector('[data-map-search-clear]').classList.toggle('hidden', !filterActive());
    panel().querySelector('[data-map-info]').textContent = infoText();
    draw();
  }

  // Chip an/aus. Liegt der Hashtag nicht in den geladenen, holt ihn der Server.
  function togglePick(name) {
    if (state.picked.has(name)) state.picked.delete(name);
    else state.picked.add(name);
    if (state.picked.has(name) && state.data && !state.data.byName.has(name)) load();
    else refilter();
  }

  function pickMany(names) {
    names.forEach((name) => state.picked.add(name));
    if (state.data && names.some((name) => !state.data.byName.has(name))) load();
    else refilter();
  }

  function clearFilter() {
    state.picked.clear();
    setFilter('');
  }

  function setFilter(value) {
    state.filter = normaliseFilter(value);
    const input = panel().querySelector('[data-map-search]');
    if (normaliseFilter(input.value) !== state.filter) input.value = state.filter;
    const data = state.data;
    // Nur neu laden, wenn der Server Treffer jenseits der Kappung haben kann.
    if (data && data.total_tags > data.tags.length && state.filter && state.filter !== state.loadedFilter) load();
    else refilter();
  }

  async function catalogAction(payload) {
    const response = await window.apiFetch('/api/brain/tag-catalog', { method: 'POST', body: JSON.stringify(payload) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Hashtag-Katalog konnte nicht geändert werden.');
    return data;
  }

  // Dieselbe Freigabe wie in der Hashtag-Verwaltung; danach kennen auch die
  // Hervorhebung und die Schreib-Vorschlaege den Hashtag als freigegeben.
  async function approve(name, scope = 'personal') {
    await approveMany([{ name, scope }]);
  }

  // Mehrere nacheinander freigeben (je eine Katalog-Aktion), danach nur einmal
  // neu laden. Bricht eine ab, bleiben die schon freigegebenen freigegeben.
  async function approveMany(items) {
    const info = panel().querySelector('[data-map-info]');
    let done = 0;
    try {
      for (const item of items) {
        await catalogAction({ scope: item.scope || 'personal', action: 'approve', tag: item.name });
        done += 1;
      }
    } catch (error) {
      info.textContent = `${done} freigegeben, dann: ${error.message}`;
    }
    if (!done) return;
    window.loadFamilyHashtags?.(true);
    window.refreshWriteHashtagCatalog?.();
    await load();
  }

  async function hide(name) {
    await catalogAction({ scope: 'personal', action: 'map_hide', tag: name });
    if (state.data) state.data.suggestions = (state.data.suggestions || []).filter((item) => item.name !== name);
  }

  // Nachbarn, Zeitstufe und Cluster einmal je Ladevorgang.
  function prepare(data, range) {
    const byName = new Map(data.tags.map((tag) => [tag.name, { ...tag, links: [] }]));
    data.edges.forEach((edge) => {
      byName.get(edge.a)?.links.push({ name: edge.b, weight: edge.weight, strength: edge.strength });
      byName.get(edge.b)?.links.push({ name: edge.a, weight: edge.weight, strength: edge.strength });
    });
    const scale = recencyScale(data, range);
    byName.forEach((tag) => {
      tag.step = tag.last ? scale.step(tag.last) : RECENCY_COLORS.length - 1;
      tag.links.sort((a, b) => b.strength - a.strength);
    });
    // Themenfelder rechnet der Server (brain_tag_map.categorise) - Cluster und
    // Hirn zeigen damit dieselben Gruppen in denselben Farben.
    byName.forEach((tag) => { tag.clusterIndex = tag.category ?? 0; });
    return { ...data, tags: data.tags.map((tag) => byName.get(tag.name)), byName, range, scale };
  }

  // Fuenf Stufen „zuletzt genutzt“, bezogen auf das Ende des Zeitraums. Die
  // Grenzen wachsen logarithmisch mit der Spanne (7 Tage: 1/2/3/5 Tage,
  // drei Jahre: 4 Tage/2 Wochen/2 Monate/9 Monate); bis 5 Tage ist jeder Tag
  // eine eigene Stufe. So laufen auch kurze Zeitraeume nicht zusammen.
  function recencyScale(data, range) {
    const todayNumber = dayNumber(iso(new Date()));
    const end = range.end ? dayNumber(range.end) : todayNumber;
    const firsts = data.tags.map((tag) => tag.first).filter(Boolean).map(dayNumber);
    const start = range.start ? dayNumber(range.start) : (firsts.length ? Math.min(...firsts) : end);
    const span = Math.max(1, end - start + 1);
    const limits = [];
    for (let index = 1; index < RECENCY_COLORS.length; index += 1) {
      const raw = span <= 5 ? index : Math.round(span ** (index / RECENCY_COLORS.length));
      limits.push(Math.max(raw, (limits[index - 2] || 0) + 1));
    }
    const step = (value) => {
      const age = Math.max(0, end - dayNumber(value));
      const found = limits.findIndex((limit) => age < limit);
      return found === -1 ? limits.length : found;
    };
    return { end, limits, step, endIsToday: end === todayNumber };
  }

  const recencyColor = (step) => RECENCY_COLORS[Math.max(0, Math.min(RECENCY_COLORS.length - 1, step))];
  // "Sonstige" bleibt grau, damit sie nicht wie ein Themenfeld aussieht.
  const clusterColor = (tag) => (state.data?.categories?.[tag.clusterIndex]?.other
    ? '#64748b' : CLUSTER_COLORS[tag.clusterIndex % CLUSTER_COLORS.length]);
  const radiusFor = (count, max) => 4 + 14 * Math.sqrt(count / Math.max(1, max));

  // ─── Zeichnen ────────────────────────────────────────────────────
  function stage() {
    const root = panel();
    const box = root.querySelector('[data-map-stage]');
    const svg = root.querySelector('[data-map-svg]');
    const width = Math.max(240, box.clientWidth);
    const height = Math.max(240, box.clientHeight);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.textContent = '';
    el('style', {}, svg).textContent = `
      @keyframes journl-map-flow { to { stroke-dashoffset: -40; } }
      .map-flow { stroke-dasharray: 3 7; animation: journl-map-flow 6s linear infinite; }
      @media (prefers-reduced-motion: reduce) { .map-flow { animation: none; } }
      .map-label { font: 11px system-ui, sans-serif; fill: #d1d5db; paint-order: stroke; stroke: #030712; stroke-width: 3px; }
      .map-node { cursor: pointer; transition: opacity .3s; }
      .map-hit { fill: none; stroke: transparent; pointer-events: stroke; cursor: pointer; }`;
    // Alles Gezeichnete liegt in einer Gruppe, die Zoom und Verschiebung traegt.
    const viewport = el('g', { 'data-map-viewport': '' }, svg);
    return { svg: viewport, width, height };
  }

  // ─── Zoom und Verschieben ────────────────────────────────────────
  function applyZoom() {
    const viewport = panel().querySelector('[data-map-viewport]');
    if (!viewport || !state.zoom) return;
    const { k, x, y } = state.zoom;
    viewport.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${k.toFixed(3)})`);
    if (state.view === 'overview' || state.view === 'brain') placeOverviewLabels(viewport, k);
  }

  // Uebersicht: Namen behalten ihre Bildschirmgroesse, damit Hineinzoomen sie
  // entzerrt. Gezeigt wird - nach Haeufigkeit - nur, was keinen schon
  // gezeigten Namen ueberdeckt.
  function placeOverviewLabels(viewport, k) {
    const taken = [];
    // Filtertreffer zuerst, damit ihre Namen sicher Platz bekommen.
    const labels = [...viewport.querySelectorAll('[data-label-r]')];
    if (filterActive()) labels.sort((a, b) => matches(b.dataset.name) - matches(a.dataset.name));
    labels.forEach((label) => {
      if (filterActive() && !matches(label.dataset.name)) { label.style.display = 'none'; return; }
      const r = Number(label.dataset.labelR);
      const x = Number(label.dataset.x) * k + (r + 4) * k;
      const y = Number(label.dataset.y) * k - 7;
      const width = label.textContent.length * 6.4;
      const free = r * k >= 3 && !taken.some((box) => x < box.x + box.w && box.x < x + width && y < box.y + 14 && box.y < y + 14);
      label.style.display = free ? '' : 'none';
      if (!free) return;
      taken.push({ x, y, w: width });
      label.setAttribute('x', (Number(label.dataset.x) + r + 4 / k).toFixed(2));
      label.setAttribute('y', (Number(label.dataset.y) + 4 / k).toFixed(2));
      label.setAttribute('style', `font-size:${(11 / k).toFixed(2)}px;stroke-width:${(3 / k).toFixed(2)}px`);
    });
  }

  function resetZoom() {
    state.zoom = state.view === 'overview' && state.overview ? state.overview.fit : { k: 1, x: 0, y: 0 };
    state.zoom = { ...state.zoom };
    applyZoom();
  }

  // Zoomen um einen Punkt der Buehne (Standard: ihre Mitte).
  function zoomBy(factor, px, py) {
    const box = panel().querySelector('[data-map-stage]');
    const zoom = state.zoom || { k: 1, x: 0, y: 0 };
    const cx = px ?? box.clientWidth / 2;
    const cy = py ?? box.clientHeight / 2;
    const k = Math.max(0.2, Math.min(8, zoom.k * factor));
    state.zoom = { k, x: cx - (cx - zoom.x) * (k / zoom.k), y: cy - (cy - zoom.y) * (k / zoom.k) };
    applyZoom();
  }

  // ─── Orbit: anhalten, zuruecksetzen, mit der Maus drehen ─────────
  function togglePause() {
    state.paused = !state.paused;
    try { localStorage.setItem(PAUSE_KEY, state.paused ? '1' : '0'); } catch { /* nur Komfort */ }
    draw();
  }

  // Zurueck zum Ausgangsbild: Filter, Auswahl und gewaehlte Mitte weg (die
  // Mitte ist wieder der haeufigste Hashtag), dazu Drehung und Ausschnitt.
  // Zurueck zur Standardansicht ohne Filterung: Uebersicht, gesamter
  // Zeitraum, Familie an, kein Suchtext, keine Chips, keine Auswahl, kein
  // Reindrillen, Drehung und Zoom zurueck. Dauerhaft ausgeblendete Hashtags
  // bleiben ausgeblendet - das ist eine Einstellung, keine Filterung.
  function resetAll() {
    const root = panel();
    state.filter = '';
    state.picked.clear();
    root.querySelector('[data-map-search]').value = '';
    state.selected = '';
    state.focus = '';
    state.drill = [];
    state.orbitTime = 0;
    state.spin = 0;
    state.zoom = null;
    state.range = 'all';
    root.querySelector('[data-map-range]').value = 'all';
    fillDates();
    if (!state.family) {
      state.family = true;
      try { localStorage.setItem(FAMILY_KEY, '1'); } catch { /* nur Komfort */ }
      familyButton();
    }
    state.view = 'overview';
    load();
  }

  function spinBy(angle) {
    state.spin += angle;
    // Laeuft keine Animation (angehalten), zeichnet der Orbit sofort neu.
    if (!state.frame) state.orbitRender?.();
  }

  function orbitControls() {
    const root = panel();
    const orbit = state.view === 'orbit' && Boolean(state.data?.tags.length);
    root.querySelectorAll('[data-map-orbit]').forEach((button) => button.classList.toggle('hidden', !orbit));
    root.querySelector('[data-map-reset]').classList.toggle('hidden', !state.data);
    root.querySelector('[data-map-rings]').classList.toggle('hidden', state.view !== 'brain' || !state.data?.tags.length);
    root.querySelector('[data-map-orbit="pause"]').textContent = state.paused ? 'Weiter' : 'Anhalten';
  }

  function panBy(dx, dy) {
    if (!state.zoom) return;
    state.zoom.x += dx;
    state.zoom.y += dy;
    applyZoom();
  }

  function bindZoom(svg) {
    svg.addEventListener('wheel', (event) => {
      if (!state.data) return;
      event.preventDefault();
      const rect = svg.getBoundingClientRect();
      zoomBy(Math.exp(-event.deltaY * 0.0015), event.clientX - rect.left, event.clientY - rect.top);
    }, { passive: false });
    // Ziehen nur mit Maus/Stift: auf dem Handy bleibt der Finger fuers Scrollen.
    // Im Orbit dreht Ziehen um die Mitte, mit Shift verschiebt es.
    let drag = null;
    const angleAt = (event) => {
      const rect = svg.getBoundingClientRect();
      const zoom = state.zoom || { k: 1, x: 0, y: 0 };
      const center = state.orbitCenter || { cx: rect.width / 2, cy: rect.height / 2 };
      return Math.atan2(event.clientY - rect.top - (zoom.y + center.cy * zoom.k),
        event.clientX - rect.left - (zoom.x + center.cx * zoom.k));
    };
    svg.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'touch' || event.button !== 0) return;
      if (event.target.closest('.map-node, .map-hit')) return;
      const rotate = state.view === 'orbit' && !event.shiftKey;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, rotate, angle: rotate ? angleAt(event) : 0 };
      svg.setPointerCapture(event.pointerId);
      svg.style.cursor = 'grabbing';
    });
    svg.addEventListener('pointermove', (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (drag.rotate) {
        const angle = angleAt(event);
        let delta = angle - drag.angle;
        if (delta > Math.PI) delta -= 2 * Math.PI;
        if (delta < -Math.PI) delta += 2 * Math.PI;
        drag.angle = angle;
        spinBy(delta);
        return;
      }
      panBy(event.clientX - drag.x, event.clientY - drag.y);
      drag.x = event.clientX;
      drag.y = event.clientY;
    });
    const stop = () => { drag = null; svg.style.cursor = ''; };
    svg.addEventListener('pointerup', stop);
    svg.addEventListener('pointercancel', stop);
  }

  document.addEventListener('keydown', (event) => {
    if (!state.open || !state.data || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest?.('input, select, textarea, [contenteditable="true"]')) return;
    const step = 60;
    const turn = event.shiftKey && state.view === 'orbit' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight');
    if (turn) spinBy(event.key === 'ArrowLeft' ? -0.2 : 0.2);
    else if (event.key === ' ' && state.view === 'orbit') togglePause();
    else if (event.key === 'ArrowLeft') panBy(step, 0);
    else if (event.key === 'ArrowRight') panBy(-step, 0);
    else if (event.key === 'ArrowUp') panBy(0, step);
    else if (event.key === 'ArrowDown') panBy(0, -step);
    else if (event.key === '+' || event.key === '=') zoomBy(1.25);
    else if (event.key === '-' || event.key === '_') zoomBy(0.8);
    else if (event.key === '0') resetZoom();
    else if (event.key === 'Escape' && kit.closeMenu?.()) { /* Menue zu */ }
    else if (event.key === 'Escape' && state.selected) { state.selected = ''; card(); } else return;
    event.preventDefault();
  });

  function draw() {
    cancelAnimationFrame(state.frame);
    state.frame = 0;
    const root = panel();
    if (!root) return;
    root.querySelectorAll('[data-map-view]').forEach((button) => {
      const active = button.dataset.mapView === state.view;
      button.classList.toggle('bg-gray-700', active);
      button.classList.toggle('text-white', active);
      button.classList.toggle('text-gray-400', !active);
    });
    const empty = root.querySelector('[data-map-empty]');
    const hasData = Boolean(state.data?.tags.length);
    empty.classList.toggle('hidden', hasData);
    empty.textContent = state.data ? 'Keine Hashtags in diesem Zeitraum.' : '';
    root.querySelector('[data-map-tip]').classList.add('hidden');
    card();
    if (!hasData) { stage(); legend(); orbitControls(); kit.hooks.drawn.forEach((hook) => hook()); return; }
    (kit.views[state.view] || kit.views.orbit)();
    // Was nicht zum Filter passt, tritt zurueck statt zu verschwinden.
    if (filterActive()) {
      root.querySelectorAll('[data-map-viewport] .map-node').forEach((node) => {
        if (node.dataset.name && !matches(node.dataset.name)) node.style.opacity = '0.15';
      });
    }
    if (!state.zoom) resetZoom();
    applyZoom();
    orbitControls();
    legend();
    kit.hooks.drawn.forEach((hook) => hook());
  }

  // Alter in Tagen lesbar machen: 3 T, 2 W, 4 M, 2 J.
  function ageLabel(days) {
    if (days < 14) return `${days} T`;
    if (days < 60) return `${Math.round(days / 7)} W`;
    if (days < 730) return `${Math.round(days / 30)} M`;
    return `${Math.round(days / 365)} J`;
  }

  function legend() {
    const box = panel().querySelector('[data-map-legend]');
    box.textContent = '';
    const scale = state.data?.scale;
    if (!scale || !state.data.tags.length) return;
    const title = document.createElement('span');
    title.textContent = state.view === 'cluster' ? 'Ring = zuletzt genutzt:' : 'Zuletzt genutzt:';
    box.appendChild(title);
    RECENCY_COLORS.forEach((color, index) => {
      const lower = index ? scale.limits[index - 1] : 0;
      const upper = index < scale.limits.length ? scale.limits[index] - 1 : null;
      let text;
      const first = scale.endIsToday ? 'heute' : 'letzter Tag';
      if (upper === null) text = 'älter';
      else if (upper === 0) text = first;
      else if (lower === upper) text = `vor ${ageLabel(lower)}`;
      else text = `${lower ? ageLabel(lower) : first}–${ageLabel(upper)}`;
      const item = document.createElement('span');
      item.className = 'inline-flex items-center gap-1';
      const dot = document.createElement('span');
      dot.className = 'inline-block h-2 w-2 rounded-full';
      dot.style.background = color;
      const label = document.createElement('span');
      label.textContent = text;
      item.append(dot, label);
      box.appendChild(item);
    });
    if (state.view !== 'time') {
      const hint = document.createElement('span');
      hint.className = 'hidden sm:inline text-gray-600';
      const hints = {
        overview: '· Größe = Häufigkeit, Zeigen blendet Verbindungen ein',
        brain: '· innen abstrakt, außen konkret · gestrichelt = Verbindung zwischen Themenfeldern',
      };
      hint.textContent = hints[state.view] || '· Linie = gemeinsame Einträge, Klick zeigt sie';
      box.appendChild(hint);
    }
  }

  function placeTip(event, text) {
    const box = panel().querySelector('[data-map-tip]');
    if (!text) { box.classList.add('hidden'); return; }
    const stageBox = panel().querySelector('[data-map-stage]').getBoundingClientRect();
    box.textContent = text;
    box.style.left = `${Math.max(4, Math.min(stageBox.width - 200, event.clientX - stageBox.left + 12))}px`;
    box.style.top = `${event.clientY - stageBox.top + 12}px`;
    box.classList.remove('hidden');
  }

  function tip(event, tag) {
    if (!tag) { placeTip(null, ''); return; }
    const span = tag.first && tag.last ? `${tag.first} – ${tag.last}` : 'ohne Datum';
    const top = tag.links.slice(0, 3).map((link) => `#${link.name}`).join(', ');
    placeTip(event, `#${tag.name} · ${tag.count} Einträge · ${span}${top ? ` · oft mit ${top}` : ''}`);
  }

  // Die Karte zum gewaehlten Knoten; Namen nur als Text.
  function card() {
    const box = panel().querySelector('[data-map-card]');
    const tag = state.data?.byName.get(state.selected);
    box.classList.toggle('hidden', !tag);
    if (!tag) return;
    box.querySelector('[data-card-title]').textContent = `#${tag.name}`;
    const span = tag.first && tag.last ? (tag.first === tag.last ? tag.first : `${tag.first} – ${tag.last}`) : 'ohne Datum';
    const status = tag.approved === false ? 'Vorschlag' : 'freigegeben';
    box.querySelector('[data-card-meta]').textContent = `${tag.count} Einträge · ${span} · ${status}`;
    // Familien-Vorschlaege gibt nur ein Admin frei - wie in der Verwaltung.
    const canApprove = tag.approved === false && (tag.scope !== 'family' || window.config?.current_user?.admin);
    box.querySelector('[data-card-approve]').classList.toggle('hidden', !canApprove);
  }

  // ─── Auswahl und Sprung in die Eintraege ─────────────────────────
  // Im Hirn bleibt man im Kreis - der Knoten und sein Themenfeld werden
  // hervorgehoben; ueberall sonst fuehrt die Auswahl in den Orbit.
  function select(tag) {
    state.focus = tag.name;
    state.selected = tag.name;
    if (state.view === 'brain') draw();
    else setView('orbit');
  }

  function openEntries(tags) {
    const range = state.data?.range || { start: '', end: '' };
    state.selected = '';
    close({ reload: false });
    if (typeof window.brainShowTagEntries === 'function') window.brainShowTagEntries(tags, range.start, range.end);
    else if (typeof window.loadBrainCurrentMode === 'function') window.loadBrainCurrentMode();
  }

  // Klick waehlt (mit kurzem Warten auf einen Doppelklick, nur mit Maus),
  // Doppelklick und Langdruecken oeffnen das Knotenmenue (Fokussieren,
  // Timeline, Filter, Ausblenden ... - brain-tag-map-actions.js).
  function nodeMenu(tag, x, y) {
    if (typeof kit.openMenu === 'function') kit.openMenu(tag, x, y);
    else openEntries([tag.name]);
  }

  let clickTimer = 0;
  let clickTag = '';
  let pressTimer = 0;
  let pressed = false;
  // Knoten mit der Maus verschieben (Uebersicht, Cluster). ``point`` ist der
  // Layout-Knoten, dessen x/y sich mitbewegt; ``onMove`` zieht Linien nach.
  // Ein echtes Ziehen (ab 4 px) schluckt den folgenden Klick.
  let dragMoved = false;
  function draggable(group, point, onMove, onEnd) {
    group.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'touch' || event.button !== 0) return;
      const start = { x: event.clientX, y: event.clientY };
      let last = start;
      let moving = false;
      const move = (next) => {
        if (!moving && Math.hypot(next.clientX - start.x, next.clientY - start.y) < 4) return;
        if (!moving) { moving = true; placeTip(null, ''); group.style.cursor = 'grabbing'; }
        const k = state.zoom?.k || 1;
        point.x += (next.clientX - last.x) / k;
        point.y += (next.clientY - last.y) / k;
        last = { x: next.clientX, y: next.clientY };
        group.setAttribute('transform', `translate(${point.x.toFixed(1)} ${point.y.toFixed(1)})`);
        onMove?.();
      };
      const up = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        if (!moving) return;
        group.style.cursor = '';
        dragMoved = true;
        clearTimeout(clickTimer);
        setTimeout(() => { dragMoved = false; }, 0);
        onEnd?.();
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', up);
    });
  }

  function bindNode(node, tag) {
    node.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'mouse') return;
      pressed = false;
      const x = event.clientX;
      const y = event.clientY;
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => { pressed = true; nodeMenu(tag, x, y); }, 550);
      const cancel = (move) => {
        if (move && Math.hypot(move.clientX - x, move.clientY - y) < 10) return;
        clearTimeout(pressTimer);
        node.removeEventListener('pointermove', cancel);
      };
      node.addEventListener('pointermove', cancel);
      node.addEventListener('pointerup', () => cancel(), { once: true });
      node.addEventListener('pointercancel', () => cancel(), { once: true });
    });
    node.addEventListener('contextmenu', (event) => event.preventDefault());
    node.addEventListener('click', (event) => {
      if (pressed) { pressed = false; return; }
      if (dragMoved) return;
      if (!hoverDevice) { select(tag); return; }
      if (clickTimer && clickTag === tag.name) {
        clearTimeout(clickTimer);
        clickTimer = 0;
        nodeMenu(tag, event.clientX, event.clientY);
        return;
      }
      clearTimeout(clickTimer);
      clickTag = tag.name;
      clickTimer = setTimeout(() => { clickTimer = 0; select(tag); }, 260);
    });
  }

  // Freigegeben = gefuellt, Vorschlag = blass mit gestrichelter Kontur.
  function nodeGroup(svg, tag, x, y, radius, color, label, ring) {
    const group = el('g', { class: 'map-node', transform: `translate(${x} ${y})` }, svg);
    group.dataset.name = tag.name;
    const proposed = tag.approved === false;
    el('circle', { r: radius + 5, fill: ring || color, opacity: 0.12, 'pointer-events': 'none' }, group);
    const body = el('circle', {
      r: radius, fill: color, 'fill-opacity': proposed ? 0.3 : 0.85,
      stroke: ring || (proposed ? color : '#030712'), 'stroke-width': ring ? 2.5 : 1.5,
      ...(proposed ? { 'stroke-dasharray': '3 2' } : {}),
    }, group);
    if (tag.name === state.selected) el('circle', { r: radius + 4, fill: 'none', stroke: '#f8fafc', 'stroke-width': 1.2, 'stroke-dasharray': '3 3' }, group);
    if (label) {
      const text = el('text', { class: 'map-label', x: radius + 5, y: 4 }, group);
      text.textContent = `#${tag.name}`;
    }
    group.addEventListener('pointerenter', (event) => tip(event, tag));
    group.addEventListener('pointermove', (event) => tip(event, tag));
    group.addEventListener('pointerleave', () => tip(null, null));
    bindNode(group, tag);
    group.journlBody = body;
    group.journlStroke = [body.getAttribute('stroke'), body.getAttribute('stroke-width')];
    return group;
  }

  function markNode(group, on) {
    if (!group) return;
    group.journlBody.setAttribute('stroke', on ? '#f8fafc' : group.journlStroke[0]);
    group.journlBody.setAttribute('stroke-width', on ? 2.5 : group.journlStroke[1]);
  }

  // Unsichtbare, breitere Klickflaeche ueber einer Verbindung: Hover hebt sie
  // und ihre beiden Hashtags hervor, Klick zeigt die gemeinsamen Eintraege.
  function edgeHit(parent, path, a, b, weight, nodes) {
    const width = Number(path.getAttribute('stroke-width')) + 10;
    const hit = el('path', { class: 'map-hit', 'stroke-width': width.toFixed(1) }, parent);
    const base = [path.getAttribute('stroke-opacity'), path.getAttribute('stroke')];
    hit.addEventListener('pointerenter', (event) => {
      path.setAttribute('stroke-opacity', '0.95');
      if (path.getAttribute('stroke') === '#94a3b8') path.setAttribute('stroke', '#e2e8f0');
      markNode(nodes.get(a), true);
      markNode(nodes.get(b), true);
      placeTip(event, `#${a} + #${b} · ${weight} gemeinsame Einträge`);
    });
    hit.addEventListener('pointermove', (event) => placeTip(event, `#${a} + #${b} · ${weight} gemeinsame Einträge`));
    hit.addEventListener('pointerleave', () => {
      path.setAttribute('stroke-opacity', base[0]);
      path.setAttribute('stroke', base[1]);
      markNode(nodes.get(a), false);
      markNode(nodes.get(b), false);
      placeTip(null, '');
    });
    // Klick: kleines Menue (Timeline, Orbit, Fokus - brain-tag-map-actions.js).
    hit.addEventListener('click', (event) => {
      if (typeof kit.openEdgeMenu === 'function') kit.openEdgeMenu(a, b, weight, event.clientX, event.clientY);
      else openEntries([a, b]);
    });
    if (path.getAttribute('d')) hit.setAttribute('d', path.getAttribute('d'));
    return hit;
  }

  // Leicht gebogene Linie - wirkt gezeichnet statt technisch.
  function curve(x1, y1, x2, y2, bend) {
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    return `M${x1.toFixed(1)},${y1.toFixed(1)} Q${(mx - dy * bend).toFixed(1)},${(my + dx * bend).toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`;
  }

  // ─── Oeffnen / Schliessen ────────────────────────────────────────
  function setView(view) {
    if (view !== state.view) state.zoom = null;
    state.view = view;
    draw();
  }

  async function open() {
    const root = panel();
    if (!root) return;
    window.closeBrainChat?.();
    if (typeof window.switchTab === 'function') window.switchTab('brain');
    byId('brain-browser')?.classList.add('hidden');
    byId('brain-toolbar')?.classList.add('hidden');
    root.classList.remove('hidden');
    root.classList.add('flex');
    root.querySelector('[data-map-range]').value = state.range;
    if (state.range !== 'custom') fillDates();
    state.open = true;
    await load();
  }

  function close({ reload = true } = {}) {
    cancelAnimationFrame(state.frame);
    clearTimeout(clickTimer);
    clearTimeout(pressTimer);
    state.open = false;
    const root = byId('brain-tag-map');
    root?.classList.add('hidden');
    root?.classList.remove('flex');
    byId('brain-toolbar')?.classList.remove('hidden');
    byId('brain-browser')?.classList.remove('hidden');
    if (reload && typeof window.loadBrainCurrentMode === 'function') window.loadBrainCurrentMode();
  }

  byId('brain-menu-map')?.addEventListener('click', open);
  // Oeffnet jemand den Chat, macht die Map Platz.
  byId('brain-menu-chat')?.addEventListener('click', () => {
    if (!state.open) return;
    cancelAnimationFrame(state.frame);
    state.open = false;
    byId('brain-tag-map')?.classList.add('hidden');
    byId('brain-tag-map')?.classList.remove('flex');
  });
  // Die Sichten selbst zeichnet brain-tag-map-views.js mit diesen Bausteinen.
  const kit = {
    state, views: {}, hooks: { loaded: [], drawn: [] }, el, stage, nodeGroup, edgeHit, curve, bindNode, placeTip, draggable,
    redraw: () => draw(), applyZoom, openEntries, setView, catalogAction, load,
    recencyColor, clusterColor, radiusFor, matches, filterActive, togglePick, pickMany, clearFilter, select, setFilter, approve, approveMany, hide, panel, CLUSTER_LIMIT, DAY,
  };
  window.journlBrainTagMapKit = kit;
  window.journlBrainTagMap = { open, close };
})();
