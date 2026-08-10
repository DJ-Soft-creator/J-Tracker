/* Local undo/redo history for the writing editor.
 *
 * Browser undo is discarded when another feature replaces textarea.value.
 * Keep one small, in-memory history so typing and app-driven replacements
 * (AI answers, hashtag completion and session restores) behave alike. */
(function () {
  const field = document.getElementById('simple-input');
  if (!field) return;

  const LIMIT = 120;
  const undoStack = [];
  const redoStack = [];
  let current = snapshot();
  let beforeInput = null;
  let applying = false;

  function snapshot() {
    return {
      value: field.value,
      start: field.selectionStart ?? field.value.length,
      end: field.selectionEnd ?? field.value.length,
    };
  }

  function same(left, right) {
    return left.value === right.value && left.start === right.start && left.end === right.end;
  }

  function announce() {
    window.dispatchEvent(new CustomEvent('write-history-change', {
      detail: { canUndo: undoStack.length > 0, canRedo: redoStack.length > 0 },
    }));
  }

  function push(stack, state) {
    stack.push(state);
    if (stack.length > LIMIT) stack.shift();
  }

  function remember(previous, next) {
    if (same(previous, next)) return;
    push(undoStack, previous);
    redoStack.length = 0;
    current = next;
    announce();
  }

  function apply(state) {
    applying = true;
    field.value = state.value;
    field.setSelectionRange(state.start, state.end);
    current = snapshot();
    field.dispatchEvent(new Event('input', { bubbles: true }));
    applying = false;
    field.focus({ preventScroll: true });
  }

  function undo() {
    if (!undoStack.length) return false;
    const previous = undoStack.pop();
    push(redoStack, snapshot());
    apply(previous);
    announce();
    return true;
  }

  function redo() {
    if (!redoStack.length) return false;
    const next = redoStack.pop();
    push(undoStack, snapshot());
    apply(next);
    announce();
    return true;
  }

  window.writeEditorReplace = function (value, options = {}) {
    const previous = snapshot();
    const nextValue = String(value ?? '');
    const end = Number.isInteger(options.start) ? options.start : nextValue.length;
    const selectionEnd = Number.isInteger(options.end) ? options.end : end;
    const next = { value: nextValue, start: Math.max(0, Math.min(end, nextValue.length)), end: Math.max(0, Math.min(selectionEnd, nextValue.length)) };
    if (same(previous, next)) return field;
    if (options.history !== false) remember(previous, next);
    else {
      redoStack.length = 0;
      current = next;
      announce();
    }
    applying = true;
    field.value = next.value;
    field.setSelectionRange(next.start, next.end);
    field.dispatchEvent(new Event('input', { bubbles: true }));
    applying = false;
    return field;
  };
  window.writeEditorUndo = undo;
  window.writeEditorRedo = redo;
  window.writeEditorReset = function (value = '') {
    undoStack.length = 0;
    redoStack.length = 0;
    window.writeEditorReplace(value, { history: false });
    announce();
    return field;
  };

  field.addEventListener('beforeinput', () => { if (!applying) beforeInput = snapshot(); });
  field.addEventListener('input', () => {
    if (applying) return;
    const next = snapshot();
    remember(beforeInput || current, next);
    beforeInput = null;
  });
  document.addEventListener('keydown', (event) => {
    if (event.target !== field || !(event.metaKey || event.ctrlKey) || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key !== 'z' && !(event.ctrlKey && !event.metaKey && key === 'y')) return;
    const action = key === 'y' || event.shiftKey ? redo : undo;
    if (action()) event.preventDefault();
  }, true);
  announce();
})();
