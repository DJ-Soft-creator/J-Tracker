// Brain tag filter: three pickers — one per hashtag variant (freigegeben,
// vorgeschlagen, KI-vergeben) — plus two chip rows underneath them.
// AI maintenance contract: picker and chip rendering only; state lives on
// brainState (brain.js) and, for the KI variant, in the ``ki:``/``-ki:`` terms
// of the search field. Requests go through loadBrainTags/loadBrainCurrentMode.
//
// Each picker searches and expands: focus opens the full list of that variant,
// typing narrows it. Picking a hashtag turns it into a chip in the first row.
// The second row ("Auch in Treffern") holds the hashtags that still occur in
// the current hits without being a filter yet — blocks carrying several
// hashtags surface their siblings there, and one click narrows the result down
// to exactly what the chips of the first row say.
//
// The KI variant is an origin filter, not a tag filter: a chip filters every
// occurrence of a hashtag no matter who wrote it, so "only what the model
// assigned" has to travel as a ``ki:`` term. The ◆ on a chip switches between
// the two readings.
//
// The "+" beside the pickers switches them to the full hashtag catalogue
// (brainState.allTags). What one picks there joins brainState.anyTags, the OR
// group: narrowing pickers can only ever offer hashtags that occur beside the
// current filter, so a topic from somewhere else is only reachable by widening.
// The already set hashtags become themes themselves at that moment - "add a
// topic" must not mean "intersect with it", which for foreign topics is empty.

const BRAIN_TAG_MENU_LIMIT = 150;
const BRAIN_TAG_OFFER_LIMIT = 40;
let brainTagOfferExpanded = false;
let brainTagOutsideBound = false;


function brainApplyTagChange() {
  invalidateBrainContext();
  setBrainTags(brainState.tags, null);
  loadBrainTagsUnlessSearchCarriesThem();
  loadBrainCurrentMode();
}


function brainToggleTagInclude(tag) {
  if (brainState.selectedTags.has(tag)) {
    brainState.selectedTags.delete(tag);
  } else {
    brainState.selectedTags.add(tag);
    brainState.excludedTags.delete(tag);
    // Die drei Mengen bleiben disjunkt: derselbe Hashtag kann nicht zugleich
    // Thema, Verfeinerung und Ausschluss sein.
    brainState.anyTags.delete(tag);
  }
  brainApplyTagChange();
}


function brainToggleTagExclude(tag) {
  if (brainState.excludedTags.has(tag)) {
    brainState.excludedTags.delete(tag);
  } else {
    brainState.excludedTags.add(tag);
    brainState.selectedTags.delete(tag);
    brainState.anyTags.delete(tag);
  }
  brainApplyTagChange();
}


/** Ein Thema der ODER-Gruppe wieder abwaehlen. */
function brainRemoveTagTheme(tag) {
  brainState.anyTags.delete(tag);
  brainApplyTagChange();
}


// Auswahl aus einem Picker. Ohne "+"-Modus ist das der bisherige Drilldown; mit
// "+" wird der Hashtag ein weiteres Thema — und die bisherigen UND-Filter
// werden selbst zu Themen, weil "Thema hinzufuegen" sonst Schnittmenge hiesse
// und bei einem fremden Thema nichts uebrig bliebe. Der Nutzer sieht das
// Ergebnis sofort an den Chips ("#a oder #b").
function brainPickTagName(name) {
  if (brainState.anyTags.has(name)) {
    brainRemoveTagTheme(name);
    return;
  }
  if (!brainState.allTagsMode) {
    brainToggleTagInclude(name);
    return;
  }
  brainState.selectedTags.forEach((tag) => brainState.anyTags.add(tag));
  brainState.selectedTags.clear();
  brainState.excludedTags.delete(name);
  brainState.anyTags.add(name);
  brainApplyTagChange();
}


function brainNormaliseTagQuery(value) {
  return (value || '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .trim()
    .replace(/^#/, '')
    .toLocaleLowerCase('de-DE');
}


// Same ranking as the write tab: exact, prefix, word-part, then substring.
function brainTagMatchScore(name, query) {
  if (!query) return 0;
  const normalised = brainNormaliseTagQuery(name);
  if (normalised === query) return 0;
  if (normalised.startsWith(query)) return 1;
  if (normalised.split('-').some((part) => part.startsWith(query))) return 2;
  const position = normalised.indexOf(query);
  return position === -1 ? null : 3 + (position / Math.max(normalised.length, 1));
}


function brainTagStatus(name) {
  return typeof window.hashtagApprovalStatus === 'function'
    ? window.hashtagApprovalStatus(name) : 'approved';
}


function brainQueryTerms() {
  return (brainElement('brain-query')?.value || '').split(/\s+/).filter(Boolean);
}


/** The hashtags the ``ki:``/``-ki:`` terms of the search field currently pin. */
function brainAiTermSets() {
  const included = new Set();
  const excluded = new Set();
  brainQueryTerms().forEach((term) => {
    const match = /^(-?)(?:ki|ai):#?(.+)$/i.exec(term);
    if (!match || !match[2]) return;
    (match[1] ? excluded : included).add(match[2].toLocaleLowerCase('de-DE'));
  });
  return { included, excluded };
}


/** Toggle one ``ki:``/``-ki:`` term in the search field and rerun the search. */
function brainToggleQueryTerm(term, opposite) {
  const input = brainElement('brain-query');
  if (!input) return;
  const terms = brainQueryTerms().filter((item) => item !== opposite);
  const index = terms.indexOf(term);
  if (index === -1) terms.push(term);
  else terms.splice(index, 1);
  input.value = terms.join(' ');
  invalidateBrainContext();
  setBrainTags(brainState.tags, null);
  loadBrainForQuery();
  loadBrainTagsUnlessSearchCarriesThem();
}


/** Drop every ``ki:``/``-ki:`` term without touching the rest of the query. */
function brainClearAiQueryTerms() {
  const input = brainElement('brain-query');
  if (!input) return false;
  const kept = brainQueryTerms().filter((term) => !/^-?(?:ki|ai):/i.test(term));
  const next = kept.join(' ');
  if (next === input.value.trim()) return false;
  input.value = next;
  return true;
}


// The three variants. ``matches`` splits the facet list, the rest says what a
// row of that list does — for KI that is a query term, for the other two an
// entry in brainState.
const BRAIN_TAG_VARIANTS = {
  approved: {
    headline: 'Freigegebene Hashtags',
    emptyText: 'Kein passender freigegebener Hashtag',
    matches: (tag) => brainTagStatus(tag.name) === 'approved',
    count: (tag) => tag.count,
    isActive: (tag) => brainState.selectedTags.has(tag.name) || brainState.anyTags.has(tag.name),
    isExcluded: (tag) => brainState.excludedTags.has(tag.name),
    include: (tag) => brainPickTagName(tag.name),
    exclude: (tag) => brainToggleTagExclude(tag.name),
    includeTitle: () => 'Auf diesen Hashtag einschränken',
    excludeTitle: () => 'Ausschließen',
    countTitle: (tag) => (tag.ai_count ? `${tag.count} Treffer, ${tag.ai_count} davon von der KI vergeben` : `${tag.count} Treffer`),
  },
  proposed: {
    headline: 'Vorgeschlagene Hashtags',
    emptyText: 'Kein passender Vorschlag',
    matches: (tag) => brainTagStatus(tag.name) === 'proposed',
    count: (tag) => tag.count,
    isActive: (tag) => brainState.selectedTags.has(tag.name) || brainState.anyTags.has(tag.name),
    isExcluded: (tag) => brainState.excludedTags.has(tag.name),
    include: (tag) => brainPickTagName(tag.name),
    exclude: (tag) => brainToggleTagExclude(tag.name),
    includeTitle: () => 'Auf diesen Vorschlag einschränken',
    excludeTitle: () => 'Ausschließen',
    countTitle: (tag) => `${tag.count} Treffer`,
  },
  ai: {
    headline: 'Von der KI vergeben',
    emptyText: 'Kein passender KI-Hashtag',
    matches: (tag) => tag.ai_count > 0 || brainTagStatus(tag.name) === 'ai',
    count: (tag) => tag.ai_count || 0,
    isActive: (tag) => brainAiTermSets().included.has(tag.name),
    isExcluded: (tag) => brainAiTermSets().excluded.has(tag.name),
    include: (tag) => brainToggleQueryTerm(`ki:${tag.name}`, `-ki:${tag.name}`),
    exclude: (tag) => brainToggleQueryTerm(`-ki:${tag.name}`, `ki:${tag.name}`),
    includeTitle: (tag) => `Nur die ${tag.ai_count} Vergaben des Modells zeigen (ki:${tag.name})`,
    excludeTitle: (tag) => `Vom Modell vergebene ${tag.name}-Treffer ausblenden (-ki:${tag.name})`,
    countTitle: (tag) => `${tag.ai_count} von ${tag.count} Treffern hat ein Modell vergeben`,
  },
};


function brainTagRowTone(tag, selected) {
  const status = brainTagStatus(tag.name);
  const tone = tag.scope === 'family'
    ? 'text-pink-300'
    : status === 'ai'
    ? 'text-sky-200'
    : status === 'approved'
    ? 'text-gray-200'
    : 'text-orange-300';
  return `${tone} ${selected ? 'bg-gray-700/70' : 'hover:bg-gray-800'}`;
}


// ---------------------------------------------------------------- pickers ---

function brainTagPickerElements(picker) {
  return {
    input: picker.querySelector('[data-brain-tag-search]'),
    menu: picker.querySelector('[data-brain-tag-menu]'),
    count: picker.querySelector('[data-brain-tag-count]'),
    toggle: picker.querySelector('[data-brain-tag-open]'),
  };
}


/** Woraus die Picker ihre Zeilen nehmen: Facetten der Treffer oder alles. */
function brainTagPickerSource() {
  if (brainState.allTagsMode && brainState.allTags) return brainState.allTags;
  return brainState.tags || [];
}


/** The facet rows of one variant, ranked by the typed query and by frequency. */
function brainTagPickerRows(picker) {
  const variant = BRAIN_TAG_VARIANTS[picker.getAttribute('data-brain-tag-picker')];
  if (!variant) return { variant: null, rows: [], total: 0 };
  const { input } = brainTagPickerElements(picker);
  const query = brainNormaliseTagQuery(input?.value);
  const all = brainTagPickerSource().filter(variant.matches);
  const rows = all
    .map((tag) => ({ tag, score: brainTagMatchScore(tag.name, query) }))
    .filter((row) => row.score !== null)
    .sort((left, right) => left.score - right.score
      || variant.count(right.tag) - variant.count(left.tag)
      || left.tag.name.localeCompare(right.tag.name, 'de-DE'));
  return { variant, rows, total: all.length };
}


// Das Menu haengt am Viewport, nicht am Filterbereich: der ist auf Mobil nur
// ein paar Zeilen hoch und beschnitte die Liste nach drei Eintraegen. Fixed
// positioniert reicht sie bis knapp ueber die Tastatur — visualViewport meldet,
// wo die anfaengt, layout-viewport-relativ und damit im selben Koordinaten-
// system wie `position: fixed`.
function brainPositionTagMenu(picker) {
  const { menu } = brainTagPickerElements(picker);
  if (!menu || picker.dataset.open !== '1') return;
  const viewport = window.visualViewport;
  const viewLeft = viewport ? viewport.offsetLeft : 0;
  const viewTop = viewport ? viewport.offsetTop : 0;
  const viewWidth = viewport ? viewport.width : window.innerWidth;
  const viewHeight = viewport ? viewport.height : window.innerHeight;
  const anchor = picker.getBoundingClientRect();
  const width = Math.min(288, viewWidth - 16);
  const left = Math.min(
    Math.max(anchor.left, viewLeft + 8),
    viewLeft + viewWidth - width - 8,
  );
  const top = anchor.bottom + 4;
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
  menu.style.width = `${Math.round(width)}px`;
  menu.style.maxHeight = `${Math.max(120, Math.round(viewTop + viewHeight - top - 8))}px`;
}


function brainCloseTagMenu(picker) {
  const { menu, input } = brainTagPickerElements(picker);
  picker.dataset.open = '0';
  picker.dataset.index = '-1';
  if (menu) {
    menu.classList.add('hidden');
    menu.replaceChildren();
    menu.removeAttribute('style');
  }
  if (input) {
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }
}


function brainCloseAllTagMenus(except) {
  document.querySelectorAll('[data-brain-tag-picker]').forEach((picker) => {
    if (picker !== except) brainCloseTagMenu(picker);
  });
}


function brainSetTagMenuIndex(picker, index) {
  const { menu, input } = brainTagPickerElements(picker);
  if (!menu) return;
  picker.dataset.index = String(index);
  const key = picker.getAttribute('data-brain-tag-picker');
  menu.querySelectorAll('[data-brain-tag-row]').forEach((row, position) => {
    const active = position === index;
    row.classList.toggle('bg-gray-700/70', active);
    row.setAttribute('aria-selected', active ? 'true' : 'false');
    if (active) row.scrollIntoView({ block: 'nearest' });
  });
  if (input) {
    if (index >= 0) input.setAttribute('aria-activedescendant', `brain-tag-row-${key}-${index}`);
    else input.removeAttribute('aria-activedescendant');
  }
}


function brainRenderTagMenu(picker) {
  const { menu, input } = brainTagPickerElements(picker);
  if (!menu || picker.dataset.open !== '1') return;
  const key = picker.getAttribute('data-brain-tag-picker');
  const { variant, rows } = brainTagPickerRows(picker);
  if (!variant) return;
  menu.classList.remove('hidden');
  brainPositionTagMenu(picker);
  if (input) input.setAttribute('aria-expanded', 'true');
  // Auf dem Handy ist ein sichtbares Kreuz der verlaessliche Weg zu; das
  // erneute Antippen des Feldes tut dasselbe (siehe bindBrainTagPickers).
  // Im "+"-Modus steht in der Liste der ganze Bestand, nicht nur was neben den
  // gesetzten Filtern vorkommt — der Kopf sagt, was ein Klick dort bedeutet.
  const themeMode = brainState.allTagsMode && key !== 'ai';
  const headline = brainState.allTagsMode ? `${variant.headline} · alle` : variant.headline;
  const head = `<div class="flex items-center justify-between gap-2 px-1 pb-1">
      <span class="text-[11px] text-gray-500">${escapeHtml(headline)}</span>
      <button type="button" data-brain-tag-close aria-label="Liste schliessen" title="Schliessen"
        class="shrink-0 rounded px-1.5 text-sm leading-none text-gray-500 hover:text-gray-200">&times;</button>
    </div>`;
  if (!rows.length) {
    menu.innerHTML = head + `<p class="px-2 py-1.5 text-xs text-gray-500">${escapeHtml(variant.emptyText)}</p>`;
    picker.dataset.index = '-1';
    menu.querySelector('[data-brain-tag-close]')?.addEventListener('click', () => brainCloseTagMenu(picker));
    return;
  }
  const shown = rows.slice(0, BRAIN_TAG_MENU_LIMIT);
  const truncated = rows.length - shown.length;
  menu.innerHTML = head + shown.map((row, index) => {
    const tag = row.tag;
    const active = variant.isActive(tag);
    const excluded = variant.isExcluded(tag);
    return `
    <div id="brain-tag-row-${key}-${index}" role="option" aria-selected="false" data-brain-tag-row="${index}"
      class="flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs cursor-pointer ${brainTagRowTone(tag, active)}">
      <span class="w-3 shrink-0 text-green-400">${active ? (window.journlIcon?.('check', 'h-3 w-3') || '') : ''}</span>
      <span class="flex-1 truncate ${excluded ? 'line-through decoration-red-500/70' : ''}"
        title="${escapeHtmlAttr(themeMode ? 'Als weiteres Thema aufnehmen (ODER)' : variant.includeTitle(tag))}">#${escapeHtml(tag.name)}</span>
      <span class="text-gray-500" title="${escapeHtmlAttr(variant.countTitle(tag))}">${variant.count(tag)}</span>
      <button type="button" data-brain-tag-row-exclude="${index}" title="${escapeHtmlAttr(variant.excludeTitle(tag))}"
        aria-label="Ausschließen"
        class="shrink-0 rounded px-1 ${excluded ? 'text-red-300' : 'text-gray-500 hover:text-red-300'}">⊘</button>
    </div>`;
  }).join('') + (truncated > 0
    ? `<p class="px-2 py-1.5 text-[11px] text-gray-600">… ${truncated} weitere – zum Eingrenzen tippen</p>`
    : '');
  menu.querySelector('[data-brain-tag-close]')?.addEventListener('click', () => brainCloseTagMenu(picker));
  menu.querySelectorAll('[data-brain-tag-row]').forEach((row) => {
    const index = Number(row.getAttribute('data-brain-tag-row'));
    row.addEventListener('mousedown', (event) => {
      // mousedown, not click: the menu is re-rendered before a click would land.
      if (event.target.closest('[data-brain-tag-row-exclude]')) return;
      event.preventDefault();
      brainPickTagRow(picker, index, false);
    });
    row.addEventListener('mousemove', () => {
      if (Number(picker.dataset.index) !== index) brainSetTagMenuIndex(picker, index);
    });
  });
  menu.querySelectorAll('[data-brain-tag-row-exclude]').forEach((button) => {
    button.addEventListener('mousedown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      brainPickTagRow(picker, Number(button.getAttribute('data-brain-tag-row-exclude')), true);
    });
  });
  const current = Number(picker.dataset.index);
  brainSetTagMenuIndex(picker, current >= 0 && current < shown.length ? current : 0);
}


function brainPickTagRow(picker, index, exclude) {
  const { variant, rows } = brainTagPickerRows(picker);
  const row = rows[index];
  if (!variant || !row) return;
  // The menu stays open: picking several hashtags in a row is the normal case.
  if (exclude) variant.exclude(row.tag);
  else variant.include(row.tag);
}


function brainOpenTagMenu(picker) {
  brainCloseAllTagMenus(picker);
  picker.dataset.open = '1';
  brainRenderTagMenu(picker);
}


function brainRefreshTagPickers() {
  document.querySelectorAll('[data-brain-tag-picker]').forEach((picker) => {
    const { count } = brainTagPickerElements(picker);
    const { variant, total } = brainTagPickerRows(picker);
    if (count && variant) count.textContent = String(total);
    if (picker.dataset.open === '1') brainRenderTagMenu(picker);
  });
}


function brainOpenTagPicker() {
  return document.querySelector('[data-brain-tag-picker][data-open="1"]');
}


function brainUpdateAllTagsButton() {
  const button = brainElement('brain-tag-all');
  if (!button) return;
  const active = Boolean(brainState.allTagsMode);
  button.setAttribute('aria-pressed', active ? 'true' : 'false');
  button.classList.toggle('border-green-500', active);
  button.classList.toggle('text-green-300', active);
  button.classList.toggle('border-gray-700', !active);
  button.classList.toggle('text-gray-400', !active);
}


/** Zwischen "nur was zu den Treffern passt" und dem vollen Bestand umschalten. */
async function brainToggleAllTagsMode() {
  brainState.allTagsMode = !brainState.allTagsMode;
  brainUpdateAllTagsButton();
  if (!brainState.allTagsMode) {
    brainCloseAllTagMenus(null);
    brainRefreshTagPickers();
    return;
  }
  await loadBrainAllTags();
  if (!brainState.allTagsMode) return;
  brainRefreshTagPickers();
  // Ein Klick soll reichen: der Knopf oeffnet die Liste, die er gerade
  // vollstaendig gemacht hat.
  const picker = document.querySelector('[data-brain-tag-picker="approved"]');
  if (!picker) return;
  brainOpenTagMenu(picker);
  brainTagPickerElements(picker).input?.focus();
}


function bindBrainTagPickers() {
  if (!brainTagOutsideBound) {
    brainTagOutsideBound = true;
    document.addEventListener('pointerdown', (event) => {
      if (!event.target.closest('[data-brain-tag-picker]')) brainCloseAllTagMenus(null);
    });
    // Die Tastatur schiebt den sichtbaren Bereich zusammen, ohne dass sich am
    // Layout etwas aendert — ohne diese Ereignisse bliebe das Menu darunter.
    const reposition = () => {
      const picker = brainOpenTagPicker();
      if (picker) brainPositionTagMenu(picker);
    };
    window.visualViewport?.addEventListener('resize', reposition);
    window.visualViewport?.addEventListener('scroll', reposition);
    window.addEventListener('resize', reposition);
    window.addEventListener('scroll', reposition, true);
  }
  const allButton = brainElement('brain-tag-all');
  if (allButton && allButton.dataset.bound !== '1') {
    allButton.dataset.bound = '1';
    const icon = window.journlIcon?.('plus', 'h-3.5 w-3.5') || '';
    if (icon) allButton.insertAdjacentHTML('afterbegin', icon);
    allButton.addEventListener('click', () => { brainToggleAllTagsMode(); });
    brainUpdateAllTagsButton();
  }
  document.querySelectorAll('[data-brain-tag-picker]').forEach((picker) => {
    if (picker.dataset.bound === '1') return;
    picker.dataset.bound = '1';
    picker.dataset.open = '0';
    picker.dataset.index = '-1';
    const { input, toggle } = brainTagPickerElements(picker);
    if (toggle) {
      toggle.addEventListener('click', () => {
        if (picker.dataset.open === '1') brainCloseTagMenu(picker);
        else {
          brainOpenTagMenu(picker);
          input?.focus();
        }
      });
    }
    if (!input) return;
    // Zweiter Tipp aufs Feld klappt wieder ein. Der Zeigerdruck kommt vor dem
    // Fokus: ohne diese Notiz wuerde `focus` sofort wieder aufklappen.
    input.addEventListener('pointerdown', () => {
      const open = picker.dataset.open === '1';
      picker.dataset.toggling = '1';
      if (open) brainCloseTagMenu(picker);
      else brainOpenTagMenu(picker);
    });
    input.addEventListener('focus', () => {
      if (picker.dataset.toggling === '1') {
        picker.dataset.toggling = '0';
        return;
      }
      brainOpenTagMenu(picker);
    });
    input.addEventListener('input', () => {
      picker.dataset.index = '-1';
      brainOpenTagMenu(picker);
    });
    input.addEventListener('keydown', (event) => {
      const { rows } = brainTagPickerRows(picker);
      const length = Math.min(rows.length, BRAIN_TAG_MENU_LIMIT);
      if (event.key === 'Escape') {
        event.preventDefault();
        input.value = '';
        brainCloseTagMenu(picker);
        return;
      }
      if (!length) return;
      const index = Number(picker.dataset.index);
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (picker.dataset.open !== '1') brainOpenTagMenu(picker);
        brainSetTagMenuIndex(picker, (index + 1) % length);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        brainSetTagMenuIndex(picker, (index - 1 + length) % length);
      } else if (event.key === 'Enter') {
        event.preventDefault();
        // Shift+Enter excludes, mirroring the ⊘ button in the row.
        brainPickTagRow(picker, index < 0 ? 0 : index, event.shiftKey);
      }
    });
  });
}


// ------------------------------------------------------------------ chips ---

function brainChipTone(chip) {
  const status = brainTagStatus(chip.name);
  const active = chip.state === 'active';
  if (chip.state === 'excluded') {
    return 'border-red-800 bg-red-500/10 text-red-300';
  }
  if (chip.kind === 'ki') {
    return active
      ? 'border-sky-400 bg-sky-500/15 text-sky-200'
      : 'border-sky-900 text-sky-200 hover:text-sky-100';
  }
  if (chip.scope === 'family') {
    return active
      ? 'border-pink-400 bg-pink-500/15 text-pink-200'
      : 'border-pink-900 text-pink-300 hover:text-pink-200';
  }
  if (status === 'proposed') {
    return active
      ? 'border-orange-500 bg-orange-500/15 text-orange-300'
      : 'border-orange-900 text-orange-300 hover:text-orange-100';
  }
  if (status === 'ai') {
    return active
      ? 'border-sky-400 bg-sky-500/15 text-sky-200'
      : 'border-sky-900 text-sky-200 hover:text-sky-100';
  }
  return active
    ? 'border-green-500 bg-green-500/15 text-green-300'
    : 'border-gray-700 text-gray-400 hover:text-white';
}


function brainChipTitle(chip) {
  if (chip.state === 'excluded') return 'Ausgeschlossen – Klick zum Zurücksetzen';
  if (chip.kind === 'any') return 'Thema der ODER-Auswahl – Klick nimmt es wieder heraus';
  if (chip.state === 'active') {
    return chip.kind === 'ki'
      ? `Nur vom Modell vergeben (ki:${chip.name}) – Klick entfernt den Filter`
      : 'Aktiver Filter – Klick entfernt ihn';
  }
  return 'Klick: zusätzlich einschränken · Rechtsklick/gedrückt halten: ausschließen';
}


/** Click on a chip: set it, or take it back off. */
function brainChipToggle(chip) {
  if (chip.kind === 'any') {
    brainRemoveTagTheme(chip.name);
    return;
  }
  if (chip.kind === 'ki') {
    if (chip.state === 'excluded') brainToggleQueryTerm(`-ki:${chip.name}`, `ki:${chip.name}`);
    else brainToggleQueryTerm(`ki:${chip.name}`, `-ki:${chip.name}`);
    return;
  }
  if (chip.state === 'excluded') brainToggleTagExclude(chip.name);
  else brainToggleTagInclude(chip.name);
}


function brainChipExclude(chip) {
  if (chip.kind === 'ki') brainToggleQueryTerm(`-ki:${chip.name}`, `ki:${chip.name}`);
  else brainToggleTagExclude(chip.name);
}


// ◆ switches between the two readings of the same hashtag: every occurrence
// versus only the ones a model assigned.
function brainChipAiSwitch(chip) {
  if (chip.kind === 'ki') {
    const terms = brainQueryTerms().filter((term) => term !== `ki:${chip.name}` && term !== `-ki:${chip.name}`);
    const input = brainElement('brain-query');
    if (input) input.value = terms.join(' ');
    brainState.excludedTags.delete(chip.name);
    brainState.selectedTags.add(chip.name);
    invalidateBrainContext();
    setBrainTags(brainState.tags, null);
    loadBrainTagsUnlessSearchCarriesThem();
    loadBrainCurrentMode();
    return;
  }
  brainState.selectedTags.delete(chip.name);
  brainState.excludedTags.delete(chip.name);
  brainToggleQueryTerm(`ki:${chip.name}`, `-ki:${chip.name}`);
}


function brainChipAiTitle(chip) {
  return chip.kind === 'ki'
    ? `Auf alle ${chip.allCount} Vorkommen ausweiten, nicht nur die des Modells`
    : `${chip.aiCount} davon von der KI vergeben – nur diese zeigen (ki:${chip.name})`;
}


// Die Chipreihe liest sich als Satz: "#a oder #b und #c". Ohne die beiden
// Woerter waere aus einer Reihe gleich aussehender Chips nicht zu erkennen,
// welche davon die ODER-Gruppe bilden.
function brainChipSeparator(previous, chip) {
  if (!previous || previous.kind !== 'any') return '';
  return `<span class="self-center text-[11px] text-gray-500">${chip.kind === 'any' ? 'oder' : 'und'}</span>`;
}


function brainRenderChips(container, chips) {
  container.innerHTML = chips.map((chip, index) => {
    const separator = brainChipSeparator(chips[index - 1], chip);
    const label = chip.kind === 'ki' ? `ki:${chip.name}` : chip.name;
    const strike = chip.state === 'excluded' ? ' line-through decoration-red-500/70' : '';
    // The marker only shows up where switching makes sense: a chip without any
    // machine-assigned occurrence has nothing to narrow to.
    const showAi = chip.state !== 'excluded' && chip.kind !== 'any'
      && (chip.kind === 'ki' || chip.aiCount > 0);
    const marker = showAi
      ? `<button type="button" data-brain-chip-ai="${index}" title="${escapeHtmlAttr(brainChipAiTitle(chip))}"
          aria-label="${escapeHtmlAttr(brainChipAiTitle(chip))}"
          class="shrink-0 rounded text-sky-400/80 hover:text-sky-200">◆${chip.kind === 'ki' ? '' : chip.aiCount}</button>`
      : '';
    return `${separator}<span class="inline-flex items-center gap-1 text-xs rounded-full border px-2 py-1 ${brainChipTone(chip)}">
      <button type="button" data-brain-chip="${index}" title="${escapeHtmlAttr(brainChipTitle(chip))}"
        class="inline-flex items-center gap-1${strike}">${escapeHtml(label)} <span class="text-gray-500">${chip.count}</span></button>${marker}
    </span>`;
  }).join('');
  container.querySelectorAll('[data-brain-chip]').forEach((button) => {
    const chip = chips[Number(button.getAttribute('data-brain-chip'))];
    let longPressTimer = null;
    let longPressFired = false;
    let touchStart = null;
    const cancelLongPress = () => {
      if (longPressTimer) {
        clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    };
    button.addEventListener('click', () => {
      if (longPressFired) {
        longPressFired = false;
        return;
      }
      brainChipToggle(chip);
    });
    button.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      brainChipExclude(chip);
    });
    button.addEventListener('touchstart', (event) => {
      if (event.touches.length !== 1) return;
      touchStart = { x: event.touches[0].clientX, y: event.touches[0].clientY };
      longPressFired = false;
      longPressTimer = setTimeout(() => {
        longPressFired = true;
        longPressTimer = null;
        brainChipExclude(chip);
      }, 550);
    }, { passive: true });
    button.addEventListener('touchmove', (event) => {
      if (!touchStart || !event.touches.length) return;
      const dx = event.touches[0].clientX - touchStart.x;
      const dy = event.touches[0].clientY - touchStart.y;
      if (Math.hypot(dx, dy) > 10) cancelLongPress();
    }, { passive: true });
    button.addEventListener('touchend', (event) => {
      if (longPressFired) event.preventDefault();
      cancelLongPress();
    });
    button.addEventListener('touchcancel', cancelLongPress);
  });
  container.querySelectorAll('[data-brain-chip-ai]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.stopPropagation();
      brainChipAiSwitch(chips[Number(button.getAttribute('data-brain-chip-ai'))]);
    });
  });
}


/** Anything that narrows the current hits — the offer row only makes sense then.
 *
 * The query counts only where loadBrainTags asks for block facets: the file
 * views measure whole documents and would answer a text query with every tag
 * of the catalogue instead of the ones actually on screen.
 */
function brainFiltersActive() {
  if (brainState.selectedTags.size > 0 || brainState.excludedTags.size > 0
    || brainState.anyTags.size > 0) return true;
  return ['search', 'journals', 'db-journals'].includes(brainState.mode)
    && brainQueryTerms().length > 0;
}


function brainRenderTagOffers(items, chipNames) {
  const wrap = brainElement('brain-tag-offer-wrap');
  const list = brainElement('brain-tag-offer');
  const more = brainElement('brain-tag-offer-more');
  if (!wrap || !list) return;
  const offers = (items || [])
    .filter((tag) => tag.count > 0 && !chipNames.has(tag.name))
    .sort((left, right) => right.count - left.count
      || left.name.localeCompare(right.name, 'de-DE'));
  const show = brainFiltersActive() && offers.length > 0;
  wrap.classList.toggle('hidden', !show);
  if (!show) {
    list.replaceChildren();
    brainTagOfferExpanded = false;
    if (more) more.classList.add('hidden');
    return;
  }
  const visible = brainTagOfferExpanded ? offers : offers.slice(0, BRAIN_TAG_OFFER_LIMIT);
  brainRenderChips(list, visible.map((tag) => ({
    name: tag.name,
    kind: 'tag',
    state: 'offer',
    scope: tag.scope,
    count: tag.count,
    allCount: tag.count,
    aiCount: tag.ai_count || 0,
  })));
  if (more) {
    const rest = offers.length - visible.length;
    more.classList.toggle('hidden', offers.length <= BRAIN_TAG_OFFER_LIMIT);
    more.textContent = brainTagOfferExpanded ? 'weniger zeigen' : `+${rest} weitere`;
    if (more.dataset.bound !== '1') {
      more.dataset.bound = '1';
      more.addEventListener('click', () => {
        brainTagOfferExpanded = !brainTagOfferExpanded;
        setBrainTags(brainState.tags, null);
      });
    }
  }
}


function setBrainTags(items, indexPending) {
  const tags = brainElement('brain-tags');
  if (!tags) return;
  if (indexPending !== null) setBrainIndexStatus(indexPending);
  brainState.tags = items || [];
  bindBrainTagPickers();
  // Active filters are never pruned against the facet list. The facets are
  // narrowed by the very filters they would be measured against, so dropping
  // an excluded tag here would silently undo the filter just set.
  const known = new Map(brainState.tags.map((tag) => [tag.name, tag]));
  const ai = brainAiTermSets();
  const chips = [];
  const seen = new Set();
  const addChip = (name, kind, state) => {
    const key = `${kind}:${name}`;
    if (seen.has(key)) return;
    seen.add(key);
    const tag = known.get(name) || { name, count: 0, ai_count: 0, scope: 'personal' };
    chips.push({
      name,
      kind,
      state,
      scope: tag.scope,
      count: kind === 'ki' ? (tag.ai_count || 0) : tag.count,
      allCount: tag.count,
      aiCount: tag.ai_count || 0,
    });
  };
  brainState.anyTags.forEach((name) => addChip(name, 'any', 'active'));
  brainState.selectedTags.forEach((name) => addChip(name, 'tag', 'active'));
  brainState.excludedTags.forEach((name) => addChip(name, 'tag', 'excluded'));
  ai.included.forEach((name) => addChip(name, 'ki', 'active'));
  ai.excluded.forEach((name) => addChip(name, 'ki', 'excluded'));
  // Die ODER-Gruppe steht vorn und bleibt zusammen, sonst stuende das "und"
  // der Trennworte zwischen zwei Themen.
  chips.sort((left, right) => (left.kind === 'any' ? 0 : 1) - (right.kind === 'any' ? 0 : 1)
    || left.name.localeCompare(right.name, 'de-DE'));

  brainRenderChips(tags, chips);
  brainElement('brain-tag-active-wrap')?.classList.toggle('hidden', chips.length === 0);
  brainUpdateAllTagsButton();
  brainRenderTagOffers(brainState.tags, new Set(chips.map((chip) => chip.name)));
  brainRefreshTagPickers();
  updateBrainFiltersClearVisibility();
}
