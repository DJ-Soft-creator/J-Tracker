/* Trigger-based text snippets ("Textbausteine"): type a short trigger (e.g.
   "-" or "aufgabe"), tap a suggestion chip to insert the full phrase
   (e.g. "- [ ] "). Deliberately lighter than write-hashtags.js's caret-
   anchored menu: on mobile, reaching special characters like "-" and "[ ]"
   is the actual pain point, so the suggestion strip sits in a fixed spot
   below the textarea instead of tracking the caret pixel-for-pixel. */
(function () {
  const field = document.getElementById('simple-input');
  const bar = document.getElementById('write-snippet-bar');
  if (!field || !bar) return;

  // Read fresh on every use, not cached: window.config only arrives after an
  // async /api/config call that finishes well after this script runs.
  function currentSnippets() {
    return Array.isArray(window.config?.text_snippets) ? window.config.text_snippets : [];
  }

  function escapeHtml(value) {
    const el = document.createElement('div');
    el.textContent = value;
    return el.innerHTML;
  }

  // The run of non-whitespace characters ending at the caret - "-" alone
  // counts too, which is exactly what lets a bare "-" trigger "- [ ] ".
  function currentToken() {
    if (document.activeElement !== field) return '';
    const pos = field.selectionStart;
    if (pos === null || pos !== field.selectionEnd) return '';
    const value = field.value;
    let start = pos;
    while (start > 0 && !/\s/.test(value[start - 1])) start -= 1;
    return { text: value.slice(start, pos), start, end: pos };
  }

  function matches(token) {
    if (!token) return [];
    const needle = token.toLocaleLowerCase('de-DE');
    return currentSnippets().filter((s) => (s.trigger || '').toLocaleLowerCase('de-DE').startsWith(needle));
  }

  function render(list) {
    if (!list.length) { bar.classList.add('hidden'); bar.innerHTML = ''; return; }
    bar.innerHTML = list.map((s, index) => `
      <button type="button" data-snippet-index="${index}"
        class="inline-flex items-center gap-1.5 rounded-full border border-gray-700 bg-gray-800/60 px-2.5 py-1 text-xs text-gray-200 hover:border-green-500 hover:text-green-300"
        title="${escapeHtmlAttr(s.trigger)} → ${escapeHtmlAttr(s.expansion)}">
        <span class="text-gray-500">${escapeHtml(s.trigger)}</span>
        <span class="font-mono text-green-300">${escapeHtml(s.expansion) || '(leer)'}</span>
      </button>
    `).join('');
    bar.classList.remove('hidden');
  }

  function escapeHtmlAttr(value) {
    return escapeHtml(value).replace(/"/g, '&quot;');
  }

  function refresh() {
    const token = currentToken();
    render(token ? matches(token.text) : []);
  }

  function applySnippet(snippet) {
    const token = currentToken();
    if (!token) return;
    const before = field.value.slice(0, token.start);
    const after = field.value.slice(token.end);
    const insertion = snippet.expansion || '';
    const caret = before.length + insertion.length;
    if (typeof window.writeEditorReplace === 'function') {
      window.writeEditorReplace(before + insertion + after, { start: caret, end: caret });
    } else {
      field.value = before + insertion + after;
      field.setSelectionRange(caret, caret);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
    field.focus();
    bar.classList.add('hidden');
    bar.innerHTML = '';
  }

  bar.addEventListener('mousedown', (event) => event.preventDefault());
  bar.addEventListener('click', (event) => {
    const button = event.target.closest('[data-snippet-index]');
    if (!button) return;
    const token = currentToken();
    const list = token ? matches(token.text) : [];
    const snippet = list[Number(button.dataset.snippetIndex)];
    if (snippet) applySnippet(snippet);
  });

  field.addEventListener('input', refresh);
  field.addEventListener('click', refresh);
  field.addEventListener('keyup', (event) => {
    if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) refresh();
  });
  // Tab confirms the top suggestion instead of leaving the field, exactly
  // like the chip tap - only while a suggestion is actually showing, so a
  // normal Tab (no trigger typed) still moves focus as expected.
  field.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab' || event.shiftKey || bar.classList.contains('hidden')) return;
    const token = currentToken();
    const top = token && matches(token.text)[0];
    if (!top) return;
    event.preventDefault();
    applySnippet(top);
  });
  field.addEventListener('blur', () => {
    // A chip click's mousedown already ran (and is prevented from blurring
    // the field), so a real blur means focus genuinely moved elsewhere.
    setTimeout(() => { if (document.activeElement !== field) { bar.classList.add('hidden'); bar.innerHTML = ''; } }, 0);
  });

})();
