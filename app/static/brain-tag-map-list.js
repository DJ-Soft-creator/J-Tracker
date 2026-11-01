/* Hashtag-Map: Hashtag-Liste und Filter-Chips - wie die Tag-Filter der
 * Timeline-Suche im Brain-Tab.
 *
 * Ein Panel „Hashtags“ mit zwei Reitern:
 *   - Freigegeben: alle freigegebenen Hashtags des Zeitraums (Server: names),
 *     mit Suchfeld; ankreuzen nimmt sie als Chip in den Filter der Map.
 *   - Vorschlaege: noch nicht freigegebene mit Grund (brain_tag_map.suggest);
 *     ankreuzen wie oben, dazu „Freigeben“ (dieselbe Katalog-Aktion wie die
 *     Hashtag-Verwaltung) und „Ausblenden“ (nur aus dieser Liste, map_hidden) -
 *     einzeln oder fuer alle angekreuzten auf einmal.
 * Beide Listen: Suchfeld, wahlweise Top 10/20/50 oder alle. Die gewaehlten
 * Hashtags stehen als Chips unter der Filterzeile; die Map hebt alle Treffer
 * hervor (kit.matches), der Rest tritt zurueck.
 *
 * Laedt nach brain-tag-map.js und nutzt dessen kit. Namen nur als Text.
 */
(function () {
  const kit = window.journlBrainTagMapKit;
  if (!kit) return;
  const { state } = kit;
  const OPEN_KEY = 'journl.tagMap.suggest';
  const LIMIT_KEY = 'journl.tagMap.suggestLimit';
  const TAB_KEY = 'journl.tagMap.listTab';
  const LIMITS = [['10', 'Top 10'], ['20', 'Top 20'], ['50', 'Top 50'], ['all', 'Alle']];
  let open = read(OPEN_KEY, '0') === '1';
  let limit = read(LIMIT_KEY, '20');
  let tab = read(TAB_KEY, 'approved');
  let query = '';

  function read(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch { /* nur Komfort */ }
  }
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };
  const button = (label, className, onClick) => {
    const node = make('button', className, label);
    node.type = 'button';
    node.addEventListener('click', onClick);
    return node;
  };

  function parts() {
    const root = kit.panel();
    if (!root) return null;
    let box = root.querySelector('[data-map-suggest]');
    if (box) return { root, box, toggle: root.querySelector('[data-map-suggest-toggle]') };
    const toggle = button('Hashtags', 'rounded-full border border-amber-800/70 px-2.5 py-1 text-amber-200 hover:bg-amber-950/40', () => setOpen(!open));
    toggle.dataset.mapSuggestToggle = '';
    root.querySelector('[data-map-family]').insertAdjacentElement('afterend', toggle);
    // Chips der gewaehlten Hashtags: eigene Zeile unter der Filterzeile.
    const chips = make('div', 'hidden flex-wrap items-center gap-1 shrink-0 pb-2 text-xs');
    chips.dataset.mapChips = '';
    root.querySelector('[data-map-info]').parentElement.insertAdjacentElement('afterend', chips);
    box = make('div', 'absolute left-2 top-2 bottom-12 z-10 hidden w-72 max-w-[calc(100%-1rem)] flex-col rounded-lg border border-gray-700 bg-gray-900/95 text-xs text-gray-300 shadow-lg');
    box.dataset.mapSuggest = '';
    box.innerHTML = `
      <div class="flex items-center gap-2 border-b border-gray-800 px-2.5 py-2">
        <div class="flex rounded-full border border-gray-800 p-0.5" role="tablist">
          <button type="button" data-list-tab="approved" class="whitespace-nowrap rounded-full px-2 py-0.5"></button>
          <button type="button" data-list-tab="proposed" class="whitespace-nowrap rounded-full px-2 py-0.5"></button>
        </div>
        <button type="button" data-suggest-close class="ml-auto rounded p-0.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300" aria-label="Liste schließen">
          <svg viewBox="0 0 24 24" class="h-3.5 w-3.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="M6 6l12 12"/></svg>
        </button>
      </div>
      <div class="flex items-center gap-2 px-2.5 pt-2">
        <input data-list-search placeholder="Hashtag suchen …" class="min-w-0 flex-1 rounded-md border border-gray-800 bg-gray-950 px-2 py-1 text-gray-200 placeholder:text-gray-600">
        <select data-suggest-limit class="rounded border border-gray-800 bg-gray-900 px-1 py-0.5 text-gray-300"></select>
      </div>
      <p data-list-hint class="px-2.5 pt-1.5 text-[11px] text-gray-500"></p>
      <ul data-suggest-list class="min-h-0 flex-1 space-y-1 overflow-y-auto p-2"></ul>`;
    root.querySelector('[data-map-stage]').appendChild(box);
    box.querySelector('[data-suggest-limit]').innerHTML = LIMITS.map(([value, label]) =>
      `<option value="${value}">${label}</option>`).join('');
    box.querySelector('[data-suggest-limit]').value = limit;
    box.querySelector('[data-suggest-limit]').addEventListener('change', (event) => {
      limit = event.target.value;
      write(LIMIT_KEY, limit);
      render();
    });
    box.querySelector('[data-list-search]').addEventListener('input', (event) => {
      query = event.target.value.trim().replace(/^#/, '').toLowerCase();
      render();
    });
    box.querySelectorAll('[data-list-tab]').forEach((item) => item.addEventListener('click', () => {
      tab = item.dataset.listTab;
      write(TAB_KEY, tab);
      render();
    }));
    box.querySelector('[data-suggest-close]').addEventListener('click', () => setOpen(false));
    return { root, box, toggle };
  }

  function setOpen(value) {
    open = value;
    write(OPEN_KEY, open ? '1' : '0');
    render();
  }

  function reasonText(item) {
    return item.reasons.map((reason) => {
      if (reason.kind === 'partner') return `oft mit #${reason.tag} (${reason.weight}×)`;
      if (reason.kind === 'growing') return `wächst (${reason.older} → ${reason.recent})`;
      return 'ohne Gruppe';
    }).join(' · ');
  }

  // Kaestchen + Name: ankreuzen = als Chip in den Filter der Map.
  function pickRow(item, tone) {
    const head = make('div', 'flex items-center gap-2');
    const picked = state.picked.has(item.name);
    const box = button('', `grid h-4 w-4 shrink-0 place-items-center rounded border ${picked ? 'border-green-500 bg-green-600/30 text-green-200' : 'border-gray-600'}`,
      () => kit.togglePick(item.name));
    if (picked) box.innerHTML = '<svg viewBox="0 0 24 24" class="h-3 w-3" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 12 5 5 9-10"/></svg>';
    box.setAttribute('aria-pressed', String(picked));
    box.title = picked ? 'Aus dem Filter nehmen' : 'Zum Filter hinzufügen';
    const name = button(`#${item.name}`, `min-w-0 truncate text-left font-medium ${tone}`, () => {
      const tag = state.data?.byName.get(item.name);
      if (tag) kit.select(tag);
      else kit.togglePick(item.name);
    });
    name.title = 'In der Map zeigen';
    head.append(box, name, make('span', 'ml-auto shrink-0 text-gray-500', `${item.count}×`));
    return head;
  }

  // Mehrere auf einmal: was angekreuzt ist (die Filter-Chips), wird gemeinsam
  // freigegeben oder ausgeblendet. Familien-Vorschlaege nur mit Admin-Rechten.
  function bulkBar(shown, suggestions) {
    const admin = Boolean(window.config?.current_user?.admin);
    const picked = suggestions.filter((item) => state.picked.has(item.name));
    const allowed = picked.filter((item) => item.scope !== 'family' || admin);
    const bar = make('li', 'flex flex-wrap items-center gap-1 px-1 pb-1');
    const unpicked = shown.filter((item) => !state.picked.has(item.name));
    if (unpicked.length) {
      bar.appendChild(button(`Alle ${shown.length} ankreuzen`, 'rounded border border-gray-700 px-1.5 py-0.5 text-gray-300 hover:bg-gray-800',
        () => kit.pickMany(unpicked.map((item) => item.name))));
    }
    if (picked.length) {
      const approve = button(`${allowed.length} freigeben`, 'rounded border border-green-800/70 px-1.5 py-0.5 text-green-300 hover:bg-green-950/40', async (event) => {
        event.currentTarget.disabled = true;
        await kit.approveMany(allowed);
      });
      approve.disabled = !allowed.length;
      approve.title = allowed.length < picked.length ? 'Familien-Vorschläge gibt nur ein Admin frei' : 'Alle angekreuzten Vorschläge freigeben';
      const hideAll = button(`${picked.length} ausblenden`, 'rounded border border-gray-700 px-1.5 py-0.5 text-gray-400 hover:bg-gray-800', async (event) => {
        event.currentTarget.disabled = true;
        for (const item of picked) {
          try { await kit.hide(item.name); } catch { break; }
          state.picked.delete(item.name);
        }
        kit.redraw();
      });
      bar.append(approve, hideAll);
    }
    return bar;
  }

  function renderChips(root) {
    const chips = root.querySelector('[data-map-chips]');
    chips.textContent = '';
    const picked = [...state.picked];
    chips.classList.toggle('hidden', !picked.length);
    chips.classList.toggle('flex', picked.length > 0);
    const approved = new Set((state.data?.names || []).filter((item) => item.approved).map((item) => item.name));
    picked.forEach((name) => {
      const tone = approved.has(name) ? 'border-green-800/70 text-green-200' : 'border-dashed border-amber-700/70 text-amber-200';
      const chip = make('span', `inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${tone}`);
      chip.appendChild(make('span', '', `#${name}`));
      const remove = button('×', 'text-gray-500 hover:text-gray-200', () => kit.togglePick(name));
      remove.setAttribute('aria-label', `#${name} aus dem Filter nehmen`);
      chip.appendChild(remove);
      chips.appendChild(chip);
    });
    if (picked.length > 1) chips.appendChild(button('alle entfernen', 'px-1 text-gray-500 hover:text-gray-300', () => kit.clearFilter()));
  }

  function render() {
    const found = parts();
    if (!found) return;
    const { root, box, toggle } = found;
    renderChips(root);
    const names = state.data?.names || [];
    const approved = names.filter((item) => item.approved);
    const suggestions = state.data?.suggestions || [];
    toggle.textContent = suggestions.length ? `Hashtags · ${suggestions.length} Vorschläge` : 'Hashtags';
    toggle.setAttribute('aria-expanded', String(open));
    box.classList.toggle('hidden', !open);
    box.classList.toggle('flex', open);
    if (!open) return;
    box.querySelectorAll('[data-list-tab]').forEach((item) => {
      const active = item.dataset.listTab === tab;
      item.textContent = item.dataset.listTab === 'approved' ? `Freigegeben (${approved.length})` : `Vorschläge (${suggestions.length})`;
      item.classList.toggle('bg-gray-700', active);
      item.classList.toggle('text-white', active);
      item.classList.toggle('text-gray-400', !active);
    });
    box.querySelector('[data-list-hint]').textContent = tab === 'approved'
      ? 'Ankreuzen nimmt den Hashtag in den Filter der Map – mehrere möglich.'
      : 'Noch nicht freigegeben, mit Grund. Freigeben wirkt wie in der Hashtag-Verwaltung.';
    const list = box.querySelector('[data-suggest-list]');
    // Ankreuzen zeichnet neu - die Liste soll dabei nicht nach oben springen.
    const scroll = list.scrollTop;
    requestAnimationFrame(() => { list.scrollTop = scroll; });
    list.textContent = '';
    const source = (tab === 'approved' ? approved : suggestions).filter((item) => !query || item.name.includes(query));
    const shown = limit === 'all' ? source : source.slice(0, Number(limit));
    if (!shown.length) {
      list.appendChild(make('li', 'px-1 py-2 text-gray-500', query ? 'Kein Treffer.' : 'Keine Hashtags im Zeitraum.'));
      return;
    }
    if (tab === 'proposed') list.appendChild(bulkBar(shown, suggestions));
    if (shown.length < source.length) {
      const more = make('li', 'px-1 pb-1 text-[11px] text-gray-600', `${shown.length} von ${source.length} – „Alle“ zeigt den Rest.`);
      list.appendChild(more);
    }
    const admin = Boolean(window.config?.current_user?.admin);
    shown.forEach((item) => {
      const row = make('li', 'rounded-md border border-gray-800 bg-gray-950/40 px-2 py-1.5');
      row.appendChild(pickRow(item, tab === 'approved' ? 'text-green-200 hover:text-green-100' : 'text-amber-200 hover:text-amber-100'));
      if (tab === 'proposed') {
        const why = make('div', 'mt-0.5 pl-6 text-[11px] text-gray-500', reasonText(item) + (item.scope === 'family' ? ' · Familie' : ''));
        const actions = make('div', 'mt-1 flex gap-1 pl-6');
        if (item.scope !== 'family' || admin) {
          actions.appendChild(button('Freigeben', 'rounded border border-green-800/70 px-1.5 py-0.5 text-green-300 hover:bg-green-950/40', async (event) => {
            event.currentTarget.disabled = true;
            await kit.approve(item.name, item.scope);
          }));
        }
        actions.appendChild(button('Ausblenden', 'rounded border border-gray-700 px-1.5 py-0.5 text-gray-400 hover:bg-gray-800', async (event) => {
          event.currentTarget.disabled = true;
          try {
            await kit.hide(item.name);
          } catch (error) {
            why.textContent = error.message;
            return;
          }
          render();
        }));
        row.append(why, actions);
      }
      list.appendChild(row);
    });
  }

  kit.hooks.loaded.push(render);
  kit.hooks.drawn.push(render);
})();
