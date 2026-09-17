/* Inline hashtag completion for all writable text fields in the write tab. */
(function () {
  const MAX_SUGGESTIONS = 40;
  const MAX_MENU_HEIGHT = 216;
  const MIN_COMFORTABLE_MENU_HEIGHT = 88;
  const MENU_GAP = 6;
  const VIEWPORT_MARGIN = 8;
  const boundFields = new WeakSet();
  const fieldStates = new WeakMap();
  let activeState = null;
  let catalog = null;
  let catalogRequest = null;
  const textMetricProperties = [
    'boxSizing', 'borderTopWidth', 'borderRightWidth',
    'borderBottomWidth', 'borderLeftWidth', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariant', 'fontStretch', 'fontKerning',
    'fontFeatureSettings', 'fontVariationSettings', 'fontSynthesis', 'letterSpacing', 'lineHeight',
    'textAlign', 'textTransform', 'textIndent', 'textDecoration', 'wordSpacing', 'tabSize', 'whiteSpace',
    'wordBreak', 'overflowWrap', 'direction', 'textRendering', 'WebkitTextSizeAdjust',
  ];
  const caretMirror = document.createElement('div');
  const highlightStates = new WeakMap();
  let menuPositionFrame = 0;
  const HASHTAG_RE = /(?<![\w#])#([\p{L}\p{N}_-]+)/gu;

  const highlightClasses = {
    ai: 'write-hashtag-ai',
    knowledge: 'write-hashtag-knowledge',
    write_target: 'write-hashtag-write-target',
    family: 'write-hashtag-family',
    approved: 'write-hashtag-approved',
    proposal: 'write-hashtag-proposal',
  };

  const menu = document.createElement('div');
  menu.id = 'write-hashtag-menu';
  menu.className = 'fixed z-50 hidden overflow-y-auto rounded-xl border border-gray-700/80 bg-gray-900/95 p-1.5 shadow-xl backdrop-blur-md';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', 'Hashtag-Vorschläge');
  document.body.appendChild(menu);

  function normalisePartial(value) {
    return (value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('de-DE');
  }

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value;
    return element.innerHTML;
  }

  function suggestionForTag(rawTag) {
    const normalised = normalisePartial(rawTag);
    return catalogCandidates().find((candidate) => normalisePartial(candidate.name) === normalised) || null;
  }

  // Offset of a collapsed caret that stands inside a hashtag, otherwise null:
  // only there does the opaque overlay swallow the caret the native control
  // draws one layer below it.
  function maskedCaret(input) {
    if (document.activeElement !== input) return null;
    const start = input.selectionStart;
    if (start === null || start !== input.selectionEnd) return null;
    const value = String(input.value || '');
    HASHTAG_RE.lastIndex = 0;
    for (const match of value.matchAll(HASHTAG_RE)) {
      if (start >= match.index && start <= match.index + match[0].length) return start;
    }
    return null;
  }

  function highlightMarkup(value, caret) {
    let rendered = '';
    let lastIndex = 0;
    HASHTAG_RE.lastIndex = 0;
    for (const match of String(value || '').matchAll(HASHTAG_RE)) {
      rendered += escapeHtml(value.slice(lastIndex, match.index));
      const suggestion = suggestionForTag(match[1]);
      const className = suggestion && highlightClasses[suggestion.status === 'proposal'
        ? 'proposal' : suggestion.status === 'ai'
          ? 'ai' : suggestion.status === 'knowledge'
            ? 'knowledge' : suggestion.status === 'write_target'
              ? 'write_target' : suggestion.family ? 'family' : 'approved'];
      // The mask hides the native caret, so a caret inside this tag is redrawn
      // by the overlay at the very same offset.  The marker is a plain inline
      // box holding a word joiner: it keeps the font metrics the caret bar is
      // sized from without opening a line-break opportunity inside the tag.
      const caretOffset = caret == null || caret < match.index || caret > match.index + match[0].length
        ? null : caret - match.index;
      const tag = caretOffset === null ? escapeHtml(match[0])
        : `${escapeHtml(match[0].slice(0, caretOffset))}<i class="write-hashtag-caret">\u2060</i>${escapeHtml(match[0].slice(caretOffset))}`;
      // Even a new, not-yet-catalogued hashtag is a hashtag rather than a
      // spelling error.  Give it a neutral opaque mask until it is classified.
      rendered += `<span class="${className || 'write-hashtag-unrecognised'}">${tag}</span>`;
      lastIndex = match.index + match[0].length;
    }
    return rendered + escapeHtml(String(value || '').slice(lastIndex)) + '\u200b';
  }

  function syncHighlight(state) {
    if (!state?.content || state.composing) return;
    const input = state.input;
    syncHighlightMetrics(state);
    // Ordinary text is drawn only by the native control. The overlay paints
    // opaque hashtag spans, so mobile composition/autocorrection can never
    // produce a second visible word beside the caret - and because those spans
    // also cover the native caret, the overlay redraws it inside a tag.
    state.caret = maskedCaret(input);
    state.content.innerHTML = highlightMarkup(input.value, state.caret);
    syncHighlightPosition(state);
  }

  function syncHighlightPosition(state) {
    if (!state?.content) return;
    const pixelRatio = window.devicePixelRatio || 1;
    const scrollLeft = Math.round(state.input.scrollLeft * pixelRatio) / pixelRatio;
    const scrollTop = Math.round(state.input.scrollTop * pixelRatio) / pixelRatio;
    state.content.style.transform = `translate3d(${-scrollLeft}px, ${-scrollTop}px, 0)`;
  }

  function numericStyle(style, property) {
    return Number.parseFloat(style[property]) || 0;
  }

  function verticalScrollbarWidth(input, style) {
    if (!(input instanceof HTMLTextAreaElement)) return 0;
    const borders = numericStyle(style, 'borderLeftWidth') + numericStyle(style, 'borderRightWidth');
    return Math.max(0, input.offsetWidth - input.clientWidth - borders);
  }

  function syncHighlightMetrics(state) {
    const style = window.getComputedStyle(state.input);
    textMetricProperties.forEach((property) => {
      state.highlight.style[property] = style[property];
    });
    state.highlight.style.borderStyle = 'solid';
    state.highlight.style.borderColor = 'transparent';
    state.highlight.style.whiteSpace = state.input instanceof HTMLTextAreaElement ? 'pre-wrap' : 'pre';
    const rightPadding = numericStyle(style, 'paddingRight') + verticalScrollbarWidth(state.input, style);
    state.highlight.style.paddingRight = `${rightPadding}px`;
  }

  function refreshMaskedCaret(state) {
    if (!state || state.composing) return;
    if (maskedCaret(state.input) === state.caret) return;
    syncHighlight(state);
  }

  function syncSelectionPresentation(state) {
    const input = state.input;
    const hasSelection = document.activeElement === input
      && input.selectionStart !== null
      && input.selectionStart !== input.selectionEnd;
    state.wrapper.classList.toggle('has-selection', hasSelection);
  }

  function bindHighlight(input) {
    if (highlightStates.has(input)) return highlightStates.get(input);
    const wrapper = document.createElement('div');
    wrapper.className = `write-hashtag-editor${input instanceof HTMLTextAreaElement ? ' write-hashtag-textarea' : ''}${input.id === 'simple-input' ? ' write-primary-editor' : ''}`;
    const inputStyle = window.getComputedStyle(input);
    wrapper.style.backgroundColor = inputStyle.backgroundColor;
    wrapper.style.backgroundImage = inputStyle.backgroundImage;
    wrapper.style.borderRadius = inputStyle.borderRadius;
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);
    const highlight = document.createElement('div');
    highlight.className = 'write-hashtag-highlight';
    highlight.setAttribute('aria-hidden', 'true');
    const content = document.createElement('div');
    content.className = 'write-hashtag-highlight-content';
    highlight.appendChild(content);
    wrapper.insertBefore(highlight, input);
    input.classList.add('write-hashtag-input');
    const state = { input, wrapper, highlight, content, composing: false, caret: null };
    highlightStates.set(input, state);
    input.addEventListener('scroll', () => syncHighlightPosition(state));
    input.addEventListener('journl:editor-metrics-change', () => syncHighlight(state));
    input.addEventListener('input', (event) => {
      if (event.isComposing || state.composing) {
        if (event.isComposing && !state.composing) {
          state.composing = true;
          wrapper.classList.add('is-composing');
        }
        closeMenu();
        return;
      }
      syncHighlight(state);
      syncSelectionPresentation(state);
    });
    input.addEventListener('focus', () => {
      syncHighlight(state);
      syncSelectionPresentation(state);
    });
    input.addEventListener('blur', () => {
      wrapper.classList.remove('has-selection');
      refreshMaskedCaret(state);
    });
    input.addEventListener('compositionstart', () => {
      state.composing = true;
      wrapper.classList.add('is-composing');
      closeMenu();
    });
    input.addEventListener('compositionend', () => {
      state.composing = false;
      wrapper.classList.remove('is-composing');
      syncHighlight(state);
      const fieldState = fieldStates.get(input);
      if (fieldState) updateMenu(fieldState);
    });
    input.addEventListener('select', () => syncSelectionPresentation(state));
    if (typeof ResizeObserver === 'function') {
      state.resizeObserver = new ResizeObserver(() => {
        syncHighlightMetrics(state);
        syncHighlightPosition(state);
        scheduleMenuPosition();
      });
      state.resizeObserver.observe(input);
    }
    syncHighlight(state);
    return state;
  }

  function syncInputHighlight(input) {
    const state = highlightStates.get(input);
    if (state) syncHighlight(state);
  }

  function candidateMatchScore(name, query) {
    if (!query) return 0;
    const normalisedName = normalisePartial(name);
    if (normalisedName === query) return 0;
    if (normalisedName.startsWith(query)) return 1;
    if (normalisedName.split('-').some((part) => part.startsWith(query))) return 2;
    const position = normalisedName.indexOf(query);
    return position === -1 ? null : 3 + (position / Math.max(normalisedName.length, 1));
  }

  function catalogCandidates() {
    if (!catalog) return [];
    const candidates = new Map();
    for (const tag of Object.keys(catalog.ai || {})) {
      candidates.set(tag, { name: tag, status: 'ai', family: false });
    }
    // Knowledge tags are explicit opt-in context sources.  They need to be
    // selectable like the other tags, but their special visual status must not
    // imply that ordinary approved/proposed hashtags become AI context.
    for (const scope of ['personal', 'family', 'host']) {
      const sources = catalog.knowledge?.[scope] || {};
      for (const tag of Object.keys(sources)) {
        candidates.set(tag, { name: tag, status: 'knowledge', family: scope === 'family' });
      }
    }
    // Write targets are routing-only permissions, distinct from Knowledge
    // context. They must still be selectable in the writing tab so users do
    // not have to remember their exact #schreibziel- spelling.
    for (const tag of Object.keys(catalog.write_targets?.personal || {})) {
      candidates.set(tag, { name: tag, status: 'write_target', family: false });
    }
    for (const tag of Object.keys(catalog.write_targets?.family || {})) {
      candidates.set(tag, { name: tag, status: 'write_target', family: true });
    }
    for (const tag of Object.keys(catalog.write_targets?.host || {})) {
      candidates.set(tag, { name: tag, status: 'write_target', family: false });
    }
    for (const scope of ['personal', 'family']) {
      const section = catalog[scope] || {};
      for (const tag of section.canonical || []) {
        const candidate = candidates.get(tag) || { name: tag, status: 'approved', family: false };
        candidate.family ||= scope === 'family';
        candidates.set(tag, candidate);
      }
    }
    for (const scope of ['personal', 'family']) {
      const section = catalog[scope] || {};
      for (const tag of section.proposals || []) {
        const candidate = candidates.get(tag) || { name: tag, status: 'proposal', family: false };
        candidate.family ||= scope === 'family';
        candidates.set(tag, candidate);
      }
    }
    return [...candidates.values()];
  }

  async function loadCatalog() {
    if (catalog) return catalog;
    if (!catalogRequest) {
      const request = typeof window.apiFetch === 'function'
        ? window.apiFetch('/api/brain/tag-catalog')
        : fetch('/api/brain/tag-catalog', { credentials: 'include' });
      catalogRequest = request
        .then(async (response) => {
          if (!response.ok) throw new Error('Hashtag-Katalog konnte nicht geladen werden.');
          const data = await response.json();
          catalog = data.catalog || {};
          document.querySelectorAll('#tab-write textarea, #tab-write input[type="text"]')
            .forEach(syncInputHighlight);
          return catalog;
        })
        .catch(() => {
          catalog = {};
          return catalog;
        })
        .finally(() => { catalogRequest = null; });
    }
    return catalogRequest;
  }

  function activeToken(input) {
    const cursor = input.selectionStart;
    if (cursor === null || cursor !== input.selectionEnd) return null;
    const before = input.value.slice(0, cursor);
    const after = input.value.slice(cursor);
    const match = before.match(/(?:^|[^\p{L}\p{N}_#])#([\p{L}\p{N}_-]*)$/u);
    if (!match || /^[\p{L}\p{N}_-]/u.test(after)) return null;
    return {
      start: cursor - match[1].length - 1,
      query: normalisePartial(match[1]),
    };
  }

  function closeMenu() {
    menu.classList.add('hidden');
    menu.replaceChildren();
    menu.removeAttribute('style');
    menu.removeAttribute('data-placement');
    if (activeState) {
      activeState.input.setAttribute('aria-expanded', 'false');
      activeState.input.removeAttribute('aria-activedescendant');
    }
  }

  function caretPosition(input) {
    const cursor = input.selectionStart || 0;
    const style = window.getComputedStyle(input);
    const inputRect = input.getBoundingClientRect();
    caretMirror.style.cssText = 'position: fixed; visibility: hidden; pointer-events: none; top: 0; left: -10000px; overflow: hidden;';
    textMetricProperties.forEach((property) => {
      caretMirror.style[property] = style[property];
    });
    caretMirror.style.borderStyle = 'solid';
    caretMirror.style.borderColor = 'transparent';
    caretMirror.style.whiteSpace = input instanceof HTMLTextAreaElement ? 'pre-wrap' : 'pre';
    caretMirror.style.width = `${Math.max(1, inputRect.width - verticalScrollbarWidth(input, style))}px`;
    caretMirror.textContent = input.value.slice(0, cursor);
    const marker = document.createElement('span');
    marker.textContent = '\u200b';
    caretMirror.appendChild(marker);
    document.body.appendChild(caretMirror);
    const mirrorRect = caretMirror.getBoundingClientRect();
    const markerRect = marker.getBoundingClientRect();
    caretMirror.remove();

    const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.2;
    return {
      left: inputRect.left + markerRect.left - mirrorRect.left - input.scrollLeft,
      top: inputRect.top + markerRect.top - mirrorRect.top - input.scrollTop,
      lineHeight,
    };
  }

  function visibleViewportBounds() {
    const viewport = window.visualViewport;
    const rootStyle = window.getComputedStyle(document.documentElement);
    const safeTop = Number.parseFloat(rootStyle.getPropertyValue('--safe-top')) || 0;
    const safeRight = Number.parseFloat(rootStyle.getPropertyValue('--safe-right')) || 0;
    const safeBottom = Number.parseFloat(rootStyle.getPropertyValue('--safe-bottom')) || 0;
    const safeLeft = Number.parseFloat(rootStyle.getPropertyValue('--safe-left')) || 0;
    const top = viewport ? viewport.offsetTop : 0;
    const left = viewport ? viewport.offsetLeft : 0;
    const width = viewport ? viewport.width : (document.documentElement.clientWidth || window.innerWidth);
    const height = viewport ? viewport.height : window.innerHeight;
    return {
      top: top + Math.max(VIEWPORT_MARGIN, safeTop),
      right: left + width - Math.max(VIEWPORT_MARGIN, safeRight),
      bottom: top + height - Math.max(VIEWPORT_MARGIN, safeBottom),
      left: left + Math.max(VIEWPORT_MARGIN, safeLeft),
    };
  }

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(value, maximum));
  }

  function positionMenu(input) {
    const previousScrollTop = menu.scrollTop;
    const rect = input.getBoundingClientRect();
    const caret = caretPosition(input);
    const viewport = visibleViewportBounds();
    const viewportWidth = Math.max(1, viewport.right - viewport.left);
    const preferredWidth = Math.max(240, Math.min(360, rect.width - 8));
    const width = Math.min(viewportWidth, preferredWidth);
    const left = clamp(caret.left, viewport.left, Math.max(viewport.left, viewport.right - width));

    menu.style.width = `${width}px`;
    menu.style.maxHeight = 'none';
    const desiredHeight = Math.min(MAX_MENU_HEIGHT, Math.max(40, menu.scrollHeight + 2));
    const caretBottom = caret.top + caret.lineHeight;
    let topBoundary = Math.max(viewport.top, rect.top + 4);
    let bottomBoundary = Math.min(viewport.bottom, rect.bottom - 4);
    let belowSpace = bottomBoundary - caretBottom - MENU_GAP;
    let aboveSpace = caret.top - MENU_GAP - topBoundary;
    const comfortableHeight = Math.min(desiredHeight, MIN_COMFORTABLE_MENU_HEIGHT);

    // Keep the picker visually inside a roomy editor. Small form controls may
    // use the surrounding viewport so their picker is not reduced to one row.
    if (Math.max(belowSpace, aboveSpace) < comfortableHeight) {
      topBoundary = viewport.top;
      bottomBoundary = viewport.bottom;
      belowSpace = bottomBoundary - caretBottom - MENU_GAP;
      aboveSpace = caret.top - MENU_GAP - topBoundary;
    }

    const placeBelow = belowSpace >= desiredHeight
      || (aboveSpace < desiredHeight && belowSpace >= aboveSpace);
    const availableHeight = Math.max(1, placeBelow ? belowSpace : aboveSpace);
    const maxHeight = Math.min(MAX_MENU_HEIGHT, availableHeight);
    const renderedHeight = Math.min(desiredHeight, maxHeight);
    const top = placeBelow
      ? clamp(caretBottom + MENU_GAP, topBoundary, Math.max(topBoundary, bottomBoundary - renderedHeight))
      : clamp(caret.top - MENU_GAP - renderedHeight, topBoundary, Math.max(topBoundary, bottomBoundary - renderedHeight));

    menu.style.top = `${top}px`;
    menu.style.left = `${left}px`;
    menu.style.maxHeight = `${maxHeight}px`;
    menu.dataset.placement = placeBelow ? 'below' : 'above';
    menu.scrollTop = Math.min(previousScrollTop, Math.max(0, menu.scrollHeight - menu.clientHeight));
  }

  function scheduleMenuPosition() {
    if (menuPositionFrame || !activeState || menu.classList.contains('hidden')) return;
    menuPositionFrame = window.requestAnimationFrame(() => {
      menuPositionFrame = 0;
      if (activeState && !menu.classList.contains('hidden')) positionMenu(activeState.input);
    });
  }

  function keepSelectedOptionVisible(option) {
    if (!option || menu.clientHeight <= 0) return;
    const optionTop = option.offsetTop;
    const optionBottom = optionTop + option.offsetHeight;
    if (optionTop < menu.scrollTop) menu.scrollTop = optionTop;
    else if (optionBottom > menu.scrollTop + menu.clientHeight) {
      menu.scrollTop = optionBottom - menu.clientHeight;
    }
  }

  function selectSuggestion(state, suggestion) {
    const input = state.input;
    const cursor = input.selectionStart;
    const before = input.value.slice(0, state.token.start);
    const after = input.value.slice(cursor);
    const separator = after ? '' : ' ';
    const nextValue = `${before}#${suggestion.name}${separator}${after}`;
    const nextCursor = before.length + suggestion.name.length + 1 + separator.length;
    if (input.id === 'simple-input' && typeof window.writeEditorReplace === 'function') {
      window.writeEditorReplace(nextValue, { start: nextCursor, end: nextCursor });
    } else {
      input.value = nextValue;
      input.setSelectionRange(nextCursor, nextCursor);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    syncInputHighlight(input);
    input.focus({ preventScroll: true });
    closeMenu();
  }

  function suggestionButtonClass(suggestion, selected) {
    const selectedClass = suggestion.status === 'ai'
      ? 'bg-sky-500/15 text-sky-200'
      : suggestion.status === 'knowledge'
        ? 'bg-violet-500/15 text-violet-200'
        : suggestion.status === 'write_target'
          ? 'bg-amber-500/15 text-amber-200'
          : suggestion.family
            ? 'bg-pink-500/15 text-pink-200'
            : suggestion.status === 'approved'
              ? 'bg-green-500/15 text-green-200'
              : 'bg-amber-500/15 text-amber-200';
    const idleClass = suggestion.status === 'ai'
      ? 'text-sky-200 hover:bg-sky-500/10'
      : suggestion.status === 'knowledge'
        ? 'text-violet-200 hover:bg-violet-500/10'
        : suggestion.status === 'write_target'
          ? 'text-amber-200 hover:bg-amber-500/10'
          : suggestion.family
            ? 'text-pink-200 hover:bg-pink-500/10'
            : 'text-gray-200 hover:bg-gray-800';
    return `flex min-h-10 w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors ${selected ? selectedClass : idleClass}`;
  }

  function setSelectedIndex(state, index) {
    if (!state.suggestions.length) return;
    const nextIndex = (index + state.suggestions.length) % state.suggestions.length;
    const previousOption = menu.querySelector('[aria-selected="true"]');
    if (previousOption) {
      const previousIndex = Number(previousOption.dataset.index);
      previousOption.className = suggestionButtonClass(state.suggestions[previousIndex], false);
      previousOption.setAttribute('aria-selected', 'false');
    }
    state.selectedIndex = nextIndex;
    const selectedOption = menu.querySelector(`[data-index="${nextIndex}"]`);
    if (!selectedOption) return;
    selectedOption.className = suggestionButtonClass(state.suggestions[nextIndex], true);
    selectedOption.setAttribute('aria-selected', 'true');
    state.input.setAttribute('aria-activedescendant', selectedOption.id);
    keepSelectedOptionVisible(selectedOption);
  }

  function renderMenu(state) {
    menu.replaceChildren();
    menu.scrollTop = 0;
    let selectedOption = null;
    state.suggestions.forEach((suggestion, index) => {
      const button = document.createElement('button');
      const selected = index === state.selectedIndex;
      button.type = 'button';
      button.className = suggestionButtonClass(suggestion, selected);
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(selected));
      button.id = `write-hashtag-option-${index}`;
      button.dataset.index = String(index);
      const label = document.createElement('span');
      label.className = 'min-w-0 truncate font-medium';
      label.textContent = `#${suggestion.name}`;
      button.appendChild(label);

      const status = document.createElement('span');
      status.className = suggestion.status === 'ai' ? 'text-xs text-sky-300' : (suggestion.status === 'knowledge' ? 'text-xs text-violet-300' : (suggestion.status === 'write_target' ? 'text-xs text-amber-300' : (suggestion.family ? 'text-xs text-pink-300' : (suggestion.status === 'approved' ? 'text-xs text-green-400' : 'text-xs text-amber-400'))));
      status.textContent = suggestion.status === 'ai' ? 'AI-Workflow' : (suggestion.status === 'knowledge' ? `Knowledge-Quelle${suggestion.family ? ' · Familie' : ''}` : (suggestion.status === 'write_target' ? 'Schreibziel' : (suggestion.family ? 'Familie' : (suggestion.status === 'approved' ? 'Freigegeben' : 'Vorschlag'))));
      status.classList.add('shrink-0');
      button.appendChild(status);
      button.addEventListener('mousedown', (event) => event.preventDefault());
      button.addEventListener('pointermove', (event) => {
        if (event.pointerType === 'mouse' && state.selectedIndex !== index) setSelectedIndex(state, index);
      });
      button.addEventListener('click', () => selectSuggestion(state, suggestion));
      menu.appendChild(button);
      if (selected) selectedOption = button;
    });
    menu.style.visibility = 'hidden';
    menu.classList.remove('hidden');
    positionMenu(state.input);
    keepSelectedOptionVisible(selectedOption);
    menu.style.removeProperty('visibility');
    state.input.setAttribute('aria-expanded', 'true');
    if (selectedOption) state.input.setAttribute('aria-activedescendant', selectedOption.id);
  }

  function updateMenu(state) {
    if (activeState !== state || document.activeElement !== state.input) return;
    if (highlightStates.get(state.input)?.composing) {
      closeMenu();
      return;
    }
    const token = activeToken(state.input);
    if (!token) {
      closeMenu();
      return;
    }
    state.token = token;
    state.suggestions = catalogCandidates()
      .map((candidate) => ({ candidate, score: candidateMatchScore(candidate.name, token.query) }))
      .filter((entry) => entry.score !== null)
      .sort((left, right) => left.score - right.score || left.candidate.name.localeCompare(right.candidate.name, 'de'))
      .map((entry) => entry.candidate)
      .slice(0, MAX_SUGGESTIONS);
    if (!state.suggestions.length) {
      closeMenu();
      return;
    }
    state.selectedIndex = Math.min(state.selectedIndex, state.suggestions.length - 1);
    renderMenu(state);
  }

  function activateField(state) {
    if (activeState && activeState !== state) closeMenu();
    activeState = state;
    state.selectedIndex = 0;
    loadCatalog().then(() => updateMenu(state));
  }

  function bindField(input) {
    if (boundFields.has(input)) return fieldStates.get(input);
    boundFields.add(input);
    input.setAttribute('aria-haspopup', 'listbox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', menu.id);
    input.setAttribute('aria-expanded', 'false');
    const state = { input, token: null, suggestions: [], selectedIndex: 0 };
    fieldStates.set(input, state);
    bindHighlight(input);

    input.addEventListener('input', () => {
      state.selectedIndex = 0;
      updateMenu(state);
    });
    input.addEventListener('click', () => updateMenu(state));
    input.addEventListener('select', () => updateMenu(state));
    input.addEventListener('keyup', (event) => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
        updateMenu(state);
      }
    });
    input.addEventListener('blur', () => {
      window.setTimeout(() => {
        if (activeState === state && document.activeElement !== input && !menu.contains(document.activeElement)) closeMenu();
      }, 0);
    });
    input.addEventListener('keydown', (event) => {
      if (activeState !== state) return;
      if (event.isComposing || event.keyCode === 229) return;
      if (event.key === 'Escape') {
        if (!menu.classList.contains('hidden')) event.preventDefault();
        closeMenu();
        return;
      }
      if (menu.classList.contains('hidden')) return;
      if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const direction = event.key === 'ArrowDown' ? 1 : -1;
        setSelectedIndex(state, state.selectedIndex + direction);
      } else if (event.key === 'Tab' || event.key === 'Enter') {
        const suggestion = state.suggestions[state.selectedIndex];
        if (!suggestion) return;
        event.preventDefault();
        selectSuggestion(state, suggestion);
      }
    });
    return state;
  }

  function isWriteTextField(target) {
    if (!(target instanceof HTMLTextAreaElement || target instanceof HTMLInputElement)) return false;
    if (target instanceof HTMLInputElement && target.type !== 'text') return false;
    return Boolean(target.closest('#tab-write'));
  }

  function bindWriteFields(root) {
    root.querySelectorAll('#tab-write textarea, #tab-write input[type="text"]').forEach(bindField);
  }

  document.addEventListener('focusin', (event) => {
    if (isWriteTextField(event.target)) activateField(bindField(event.target));
  });
  document.addEventListener('selectionchange', () => {
    if (!activeState || document.activeElement !== activeState.input) return;
    const highlightState = highlightStates.get(activeState.input);
    if (!highlightState) return;
    syncSelectionPresentation(highlightState);
    refreshMaskedCaret(highlightState);
  });
  document.addEventListener('mousedown', (event) => {
    if (!menu.contains(event.target) && activeState && event.target !== activeState.input) closeMenu();
  });
  window.addEventListener('resize', scheduleMenuPosition);
  window.addEventListener('scroll', (event) => {
    if (event.target === menu || (event.target instanceof Node && menu.contains(event.target))) return;
    scheduleMenuPosition();
  }, true);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', scheduleMenuPosition);
    window.visualViewport.addEventListener('scroll', scheduleMenuPosition);
  }

  bindWriteFields(document);
  window.refreshWriteHashtagCatalog = async function refreshWriteHashtagCatalog() {
    catalog = null;
    await loadCatalog();
    document.querySelectorAll('#tab-write textarea, #tab-write input[type="text"]')
      .forEach(syncInputHighlight);
    if (activeState) updateMenu(activeState);
  };
}());
