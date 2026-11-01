/* Hashtag-Map: was man mit einem Hashtag tun kann, und was die Map sich merkt.
 *
 *   - Knotenmenue (Doppelklick, auf dem Handy Langdruecken): Fokussieren
 *     (reindrillen ins Teil-Hirn), Eintraege in der Timeline, zum Filter der
 *     Map, im Orbit zeigen, freigeben, in der Map ausblenden.
 *   - Linienmenue (Klick auf eine Verbindung): Eintraege mit beiden in der
 *     Timeline, Orbit um einen der beiden, auf einen der beiden fokussieren.
 *   - Brotkrumen ueber dem Hirn: „Alle › #technik › #docker“ zum Zurueckspringen.
 *   - Dezenter Hinweis „N ausgeblendet“ mit Liste zum Wiedereinblenden und
 *     Weg in die Hashtag-Verwaltung (Katalogfeld map_excluded).
 *   - Anordnung speichern/verwerfen fuer Cluster und Uebersicht, pro Nutzer
 *     auf dem Server (/api/brain/tag-map/layout), relativ zur Flaeche.
 *
 * Laedt nach brain-tag-map.js und nutzt dessen kit. Namen nur als Text.
 */
(function () {
  const kit = window.journlBrainTagMapKit;
  if (!kit) return;
  const { state } = kit;
  const LAYOUT_VIEWS = ['cluster', 'overview'];
  let layoutsLoaded = false;

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  function info(message) {
    const box = kit.panel()?.querySelector('[data-map-info]');
    if (box) box.textContent = message;
  }

  // ─── Knotenmenue ─────────────────────────────────────────────────
  function menuBox() {
    const stage = kit.panel().querySelector('[data-map-stage]');
    let box = stage.querySelector('[data-map-menu]');
    if (!box) {
      box = make('div', 'absolute z-30 hidden w-56 rounded-lg border border-gray-700 bg-gray-900/95 p-1 text-xs text-gray-200 shadow-xl');
      box.dataset.mapMenu = '';
      box.setAttribute('role', 'menu');
      stage.appendChild(box);
      // Ein Druck daneben schliesst das Menue.
      document.addEventListener('pointerdown', (event) => {
        if (!box.classList.contains('hidden') && !box.contains(event.target)) closeMenu();
      }, true);
    }
    return { stage, box };
  }

  function closeMenu() {
    const box = kit.panel()?.querySelector('[data-map-menu]');
    if (!box || box.classList.contains('hidden')) return false;
    box.classList.add('hidden');
    return true;
  }

  // Leeres Menue an der Zeigerposition; ``fill(item, note)`` traegt die Zeilen ein.
  function showMenu(title, clientX, clientY, fill) {
    const { stage, box } = menuBox();
    kit.placeTip(null, '');
    box.textContent = '';
    box.appendChild(make('div', 'truncate px-2 py-1 font-semibold text-gray-100', title));
    const item = (label, action, className = 'text-gray-200') => {
      const button = make('button', `block w-full truncate rounded px-2 py-1.5 text-left hover:bg-gray-800 ${className}`, label);
      button.type = 'button';
      button.setAttribute('role', 'menuitem');
      button.addEventListener('click', () => { closeMenu(); action(); });
      box.appendChild(button);
    };
    const note = (text) => box.appendChild(make('div', 'px-2 pb-1 text-[11px] text-gray-500', text));
    fill(item, note);
    box.classList.remove('hidden');
    const rect = stage.getBoundingClientRect();
    const left = Math.max(4, Math.min(rect.width - box.offsetWidth - 4, clientX - rect.left));
    const top = Math.max(4, Math.min(rect.height - box.offsetHeight - 4, clientY - rect.top));
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
    box.querySelector('button')?.focus({ preventScroll: true });
  }

  const orbitAround = (name) => {
    state.focus = name;
    state.selected = name;
    kit.setView('orbit');
  };

  function openEdgeMenu(a, b, weight, clientX, clientY) {
    showMenu(`#${a} + #${b}`, clientX, clientY, (item, note) => {
      note(`${weight} gemeinsame Einträge`);
      item('Einträge mit beiden in der Timeline', () => kit.openEntries([a, b]));
      item(`Orbit um #${a}`, () => orbitAround(a));
      item(`Orbit um #${b}`, () => orbitAround(b));
      item(`Fokussieren auf #${a}`, () => drill(a));
      item(`Fokussieren auf #${b}`, () => drill(b));
    });
  }

  function openMenu(tag, clientX, clientY) {
    showMenu(`#${tag.name}`, clientX, clientY, (item) => fillNodeMenu(tag, item));
  }

  function fillNodeMenu(tag, item) {
    item('Fokussieren (reindrillen)', () => drill(tag.name));
    item('Einträge in der Timeline', () => kit.openEntries([tag.name]));
    item(state.picked.has(tag.name) ? 'Aus dem Filter nehmen' : 'Zum Filter der Map', () => kit.togglePick(tag.name));
    item('Im Orbit zeigen', () => orbitAround(tag.name));
    if (tag.approved === false && (tag.scope !== 'family' || window.config?.current_user?.admin)) {
      item('Hashtag freigeben', () => kit.approve(tag.name, tag.scope), 'text-amber-200');
    }
    item('In der Map ausblenden', () => exclude(tag.name), 'text-gray-400');
  }

  // ─── Reindrillen ─────────────────────────────────────────────────
  function drill(name) {
    if (state.drill[state.drill.length - 1] !== name) state.drill.push(name);
    state.selected = '';
    state.zoom = null;
    kit.setView('brain');
  }

  function crumbs() {
    const stage = kit.panel()?.querySelector('[data-map-stage]');
    if (!stage) return;
    let bar = stage.querySelector('[data-map-crumbs]');
    if (!bar) {
      bar = make('nav', 'absolute left-1/2 top-2 z-10 hidden max-w-[70%] -translate-x-1/2 flex-wrap items-center gap-1 rounded-md border border-gray-800 bg-gray-900/90 px-2 py-1 text-xs text-gray-400');
      bar.dataset.mapCrumbs = '';
      bar.setAttribute('aria-label', 'Fokus');
      stage.appendChild(bar);
    }
    const show = state.view === 'brain' && state.drill.length > 0;
    bar.classList.toggle('hidden', !show);
    bar.classList.toggle('flex', show);
    if (!show) return;
    bar.textContent = '';
    const step = (label, depth) => {
      const button = make('button', 'rounded px-1 text-green-400 hover:bg-gray-800 hover:text-green-300', label);
      button.type = 'button';
      button.addEventListener('click', () => {
        state.drill = state.drill.slice(0, depth);
        state.zoom = null;
        kit.redraw();
      });
      bar.appendChild(button);
    };
    step('Alle', 0);
    state.drill.forEach((name, index) => {
      bar.appendChild(make('span', 'text-gray-600', '›'));
      if (index === state.drill.length - 1) bar.appendChild(make('span', 'px-1 font-semibold text-gray-100', `#${name}`));
      else step(`#${name}`, index + 1);
    });
  }

  // ─── Ausblenden ──────────────────────────────────────────────────
  async function exclude(name) {
    try {
      await kit.catalogAction({ scope: 'personal', action: 'map_exclude', tag: name });
    } catch (error) {
      info(error.message);
      return;
    }
    if (state.selected === name) state.selected = '';
    const index = state.drill.indexOf(name);
    if (index !== -1) state.drill = state.drill.slice(0, index);
    await kit.load();
  }

  async function include(name) {
    try {
      await kit.catalogAction({ scope: 'personal', action: 'map_include', tag: name });
    } catch (error) {
      info(error.message);
      return;
    }
    await kit.load();
  }

  function excludedHint() {
    const root = kit.panel();
    if (!root) return;
    let hint = root.querySelector('[data-map-excluded-hint]');
    if (!hint) {
      hint = make('button', 'hidden text-xs text-gray-600 underline-offset-2 hover:text-gray-400 hover:underline');
      hint.type = 'button';
      hint.dataset.mapExcludedHint = '';
      root.querySelector('[data-map-info]').insertAdjacentElement('afterend', hint);
      hint.addEventListener('click', () => toggleExcludedBox());
    }
    const excluded = state.data?.excluded || [];
    hint.classList.toggle('hidden', !excluded.length);
    hint.textContent = `· ${excluded.length} ausgeblendet`;
    hint.title = excluded.map((name) => `#${name}`).join(' ');
    const box = root.querySelector('[data-map-excluded-box]');
    if (box && !box.classList.contains('hidden')) fillExcludedBox(box);
  }

  function toggleExcludedBox() {
    const stage = kit.panel().querySelector('[data-map-stage]');
    let box = stage.querySelector('[data-map-excluded-box]');
    if (!box) {
      box = make('div', 'absolute right-2 top-2 z-20 hidden w-64 max-w-[calc(100%-1rem)] rounded-lg border border-gray-700 bg-gray-900/95 p-2 text-xs text-gray-300 shadow-lg');
      box.dataset.mapExcludedBox = '';
      stage.appendChild(box);
    }
    box.classList.toggle('hidden');
    if (!box.classList.contains('hidden')) fillExcludedBox(box);
  }

  function fillExcludedBox(box) {
    box.textContent = '';
    const head = make('div', 'mb-1 flex items-center gap-2');
    head.appendChild(make('span', 'font-semibold text-gray-100', 'In der Map ausgeblendet'));
    const close = make('button', 'ml-auto rounded px-1 text-gray-500 hover:bg-gray-800 hover:text-gray-300', 'schließen');
    close.type = 'button';
    close.addEventListener('click', () => box.classList.add('hidden'));
    head.appendChild(close);
    box.appendChild(head);
    const excluded = state.data?.excluded || [];
    if (!excluded.length) box.appendChild(make('p', 'text-gray-500', 'Nichts ausgeblendet.'));
    const list = make('ul', 'max-h-56 space-y-1 overflow-y-auto');
    excluded.forEach((name) => {
      const row = make('li', 'flex items-center gap-2');
      row.appendChild(make('span', 'min-w-0 flex-1 truncate', `#${name}`));
      const button = make('button', 'rounded border border-gray-700 px-1.5 py-0.5 text-green-300 hover:bg-gray-800', 'Einblenden');
      button.type = 'button';
      button.addEventListener('click', () => { button.disabled = true; include(name); });
      row.appendChild(button);
      list.appendChild(row);
    });
    box.appendChild(list);
    const manage = make('button', 'mt-2 text-gray-500 underline-offset-2 hover:text-gray-300 hover:underline', 'In der Hashtag-Verwaltung verwalten');
    manage.type = 'button';
    manage.addEventListener('click', () => {
      window.journlBrainTagMap?.close();
      document.getElementById('brain-tag-catalog')?.click();
    });
    box.appendChild(manage);
  }

  // ─── Anordnung speichern ─────────────────────────────────────────
  function layoutButtons() {
    const root = kit.panel();
    if (!root) return;
    let save = root.querySelector('[data-map-layout="save"]');
    if (!save) {
      const bar = root.querySelector('[data-map-zoom="in"]').parentElement;
      const base = 'hidden h-7 rounded-md border border-gray-800 bg-gray-900/80 px-2 hover:bg-gray-800';
      const drop = make('button', `${base} text-gray-500`, 'Anordnung verwerfen');
      drop.type = 'button';
      drop.dataset.mapLayout = 'drop';
      drop.title = 'Zur berechneten Anordnung zurück';
      save = make('button', `${base} text-gray-300`, 'Anordnung speichern');
      save.type = 'button';
      save.dataset.mapLayout = 'save';
      save.title = 'Verschobene Knoten für alle Geräte merken';
      bar.prepend(drop);
      bar.prepend(save);
      save.addEventListener('click', saveLayout);
      drop.addEventListener('click', dropLayout);
    }
    const movable = LAYOUT_VIEWS.includes(state.view) && Boolean(state.data?.tags.length);
    save.classList.toggle('hidden', !movable);
    root.querySelector('[data-map-layout="drop"]').classList.toggle('hidden', !movable || !state.layouts[state.view]);
  }

  function currentLayout() {
    return state.view === 'cluster' ? state.layout : state.overview;
  }

  async function saveLayout(event) {
    const button = event.currentTarget;
    const layout = currentLayout();
    if (!layout) return;
    const positions = {};
    layout.nodes.slice(0, 600).forEach((node) => {
      positions[node.tag.name] = [node.x / layout.width, node.y / layout.height];
    });
    try {
      const response = await window.apiFetch('/api/brain/tag-map/layout', {
        method: 'PUT', body: JSON.stringify({ view: state.view, positions }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Anordnung konnte nicht gespeichert werden.');
      state.layouts = data.layouts || {};
    } catch (error) {
      info(error.message);
      return;
    }
    button.textContent = 'Gespeichert';
    setTimeout(() => { button.textContent = 'Anordnung speichern'; }, 1500);
    layoutButtons();
  }

  async function dropLayout() {
    const view = state.view;
    try {
      const response = await window.apiFetch(`/api/brain/tag-map/layout?view=${encodeURIComponent(view)}`, { method: 'DELETE' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Anordnung konnte nicht verworfen werden.');
      state.layouts = data.layouts || {};
    } catch (error) {
      info(error.message);
      return;
    }
    if (view === 'cluster') state.layout = null;
    else state.overview = null;
    state.zoom = null;
    kit.redraw();
  }

  // Einmal je Sitzung holen; kommen sie nach dem ersten Bild, neu anordnen.
  async function loadLayouts() {
    if (layoutsLoaded) return;
    layoutsLoaded = true;
    try {
      const response = await window.apiFetch('/api/brain/tag-map/layout', { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) return;
      state.layouts = data.layouts || {};
    } catch {
      layoutsLoaded = false;
      return;
    }
    if (Object.keys(state.layouts).length) {
      state.layout = null;
      state.overview = null;
      state.zoom = null;
      kit.redraw();
    }
  }

  Object.assign(kit, { openMenu, openEdgeMenu, closeMenu, drill });
  kit.hooks.loaded.push(excludedHint, loadLayouts);
  kit.hooks.drawn.push(crumbs, layoutButtons, closeMenu);
})();
