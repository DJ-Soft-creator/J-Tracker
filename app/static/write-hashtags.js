/* Inline hashtag completion for all writable text fields in the write tab. */
(function () {
  const MAX_SUGGESTIONS = 8;
  const boundFields = new WeakSet();
  const fieldStates = new WeakMap();
  let activeState = null;
  let catalog = null;
  let catalogRequest = null;
  const mirrorStyleProperties = [
    'boxSizing', 'width', 'height', 'overflowX', 'overflowY', 'borderTopWidth', 'borderRightWidth',
    'borderBottomWidth', 'borderLeftWidth', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
    'fontFamily', 'fontSize', 'fontWeight', 'fontStyle', 'fontVariant', 'letterSpacing', 'lineHeight',
    'textAlign', 'textTransform', 'textIndent', 'textDecoration', 'wordSpacing', 'tabSize', 'whiteSpace',
    'wordBreak', 'overflowWrap', 'direction',
  ];
  const caretMirror = document.createElement('div');

  const menu = document.createElement('div');
  menu.id = 'write-hashtag-menu';
  menu.className = 'fixed z-50 hidden overflow-y-auto rounded-lg border border-gray-500/60 bg-gray-900/85 p-1 shadow-xl backdrop-blur-sm';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', 'Hashtag-Vorschlaege');
  document.body.appendChild(menu);

  function normalisePartial(value) {
    return (value || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('de-DE');
  }

  function catalogCandidates() {
    if (!catalog) return [];
    const candidates = new Map();
    for (const tag of Object.keys(catalog.ai || {})) {
      candidates.set(tag, { name: tag, status: 'ai', family: false });
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
    if (activeState) activeState.input.setAttribute('aria-expanded', 'false');
  }

  function caretPosition(input) {
    const cursor = input.selectionStart || 0;
    const style = window.getComputedStyle(input);
    caretMirror.style.cssText = 'position: absolute; visibility: hidden; top: 0; left: -9999px; overflow: hidden; white-space: pre-wrap; overflow-wrap: break-word;';
    mirrorStyleProperties.forEach((property) => {
      caretMirror.style[property] = style[property];
    });
    caretMirror.style.width = `${input.offsetWidth}px`;
    caretMirror.textContent = input.value.slice(0, cursor);
    const marker = document.createElement('span');
    marker.textContent = input.value.slice(cursor) || '\u200b';
    caretMirror.appendChild(marker);
    document.body.appendChild(caretMirror);
    const mirrorRect = caretMirror.getBoundingClientRect();
    const markerRect = marker.getBoundingClientRect();
    caretMirror.remove();

    const inputRect = input.getBoundingClientRect();
    const lineHeight = Number.parseFloat(style.lineHeight) || Number.parseFloat(style.fontSize) * 1.2;
    return {
      left: inputRect.left + markerRect.left - mirrorRect.left - input.scrollLeft,
      top: inputRect.top + markerRect.top - mirrorRect.top - input.scrollTop,
      lineHeight,
    };
  }

  function positionMenu(input) {
    const rect = input.getBoundingClientRect();
    const caret = caretPosition(input);
    const width = Math.min(360, Math.max(1, rect.width - 8));
    const maxTop = rect.bottom - 36;
    const top = Math.max(rect.top + 4, Math.min(caret.top + caret.lineHeight + 2, maxTop));
    const maxHeight = Math.max(32, Math.min(180, rect.bottom - top - 4));
    const left = Math.max(rect.left + 4, Math.min(caret.left, rect.right - width - 4));
    menu.style.top = `${top}px`;
    menu.style.left = `${left}px`;
    menu.style.width = `${width}px`;
    menu.style.maxHeight = `${maxHeight}px`;
  }

  function selectSuggestion(state, suggestion) {
    const input = state.input;
    const cursor = input.selectionStart;
    const before = input.value.slice(0, state.token.start);
    const after = input.value.slice(cursor);
    const separator = after ? '' : ' ';
    input.value = `${before}#${suggestion.name}${separator}${after}`;
    const nextCursor = before.length + suggestion.name.length + 1 + separator.length;
    input.setSelectionRange(nextCursor, nextCursor);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus({ preventScroll: true });
    closeMenu();
  }

  function renderMenu(state) {
    menu.replaceChildren();
    state.suggestions.forEach((suggestion, index) => {
      const button = document.createElement('button');
      const selected = index === state.selectedIndex;
      button.type = 'button';
      button.className = `flex w-full items-center justify-between gap-3 rounded px-3 py-2 text-left text-sm transition ${selected
        ? (suggestion.status === 'ai' ? 'bg-sky-500/15 text-sky-200' : (suggestion.family ? 'bg-pink-500/15 text-pink-200' : (suggestion.status === 'approved' ? 'bg-green-500/15 text-green-200' : 'bg-amber-500/15 text-amber-200')))
        : (suggestion.status === 'ai' ? 'text-sky-200 hover:bg-sky-500/10' : (suggestion.family ? 'text-pink-200 hover:bg-pink-500/10' : 'text-gray-200 hover:bg-gray-800'))}`;
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(selected));
      button.textContent = `#${suggestion.name}`;

      const status = document.createElement('span');
      status.className = suggestion.status === 'ai' ? 'text-xs text-sky-300' : (suggestion.family ? 'text-xs text-pink-300' : (suggestion.status === 'approved' ? 'text-xs text-green-400' : 'text-xs text-amber-400'));
      status.textContent = suggestion.status === 'ai' ? 'AI-Workflow' : (suggestion.family ? 'Familie' : (suggestion.status === 'approved' ? 'Freigegeben' : 'Vorschlag'));
      button.appendChild(status);
      button.addEventListener('mousedown', (event) => event.preventDefault());
      button.addEventListener('click', () => selectSuggestion(state, suggestion));
      menu.appendChild(button);
    });
    positionMenu(state.input);
    menu.classList.remove('hidden');
    state.input.setAttribute('aria-expanded', 'true');
  }

  function updateMenu(state) {
    if (activeState !== state || document.activeElement !== state.input) return;
    const token = activeToken(state.input);
    if (!token) {
      closeMenu();
      return;
    }
    state.token = token;
    state.suggestions = catalogCandidates()
      .filter((candidate) => normalisePartial(candidate.name).startsWith(token.query))
      .slice(0, MAX_SUGGESTIONS);
    if (!state.suggestions.length) {
      closeMenu();
      return;
    }
    state.selectedIndex = Math.min(state.selectedIndex, state.suggestions.length - 1);
    renderMenu(state);
  }

  function activateField(state) {
    activeState = state;
    state.selectedIndex = 0;
    loadCatalog().then(() => updateMenu(state));
  }

  function bindField(input) {
    if (boundFields.has(input)) return fieldStates.get(input);
    boundFields.add(input);
    input.setAttribute('aria-haspopup', 'listbox');
    input.setAttribute('aria-expanded', 'false');
    const state = { input, token: null, suggestions: [], selectedIndex: 0 };
    fieldStates.set(input, state);

    input.addEventListener('input', () => {
      state.selectedIndex = 0;
      updateMenu(state);
    });
    input.addEventListener('click', () => updateMenu(state));
    input.addEventListener('keyup', (event) => {
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) {
        updateMenu(state);
      }
    });
    input.addEventListener('blur', () => {
      window.setTimeout(() => {
        if (activeState === state && document.activeElement !== input) closeMenu();
      }, 0);
    });
    input.addEventListener('keydown', (event) => {
      if (activeState !== state) return;
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
        state.selectedIndex = (state.selectedIndex + direction + state.suggestions.length) % state.suggestions.length;
        renderMenu(state);
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
  document.addEventListener('mousedown', (event) => {
    if (!menu.contains(event.target) && activeState && event.target !== activeState.input) closeMenu();
  });
  window.addEventListener('resize', () => {
    if (activeState && !menu.classList.contains('hidden')) positionMenu(activeState.input);
  });
  window.addEventListener('scroll', () => {
    if (activeState && !menu.classList.contains('hidden')) positionMenu(activeState.input);
  }, true);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      if (activeState && !menu.classList.contains('hidden')) positionMenu(activeState.input);
    });
  }

  bindWriteFields(document);
  window.refreshWriteHashtagCatalog = async function refreshWriteHashtagCatalog() {
    catalog = null;
    await loadCatalog();
    if (activeState) updateMenu(activeState);
  };
}());
