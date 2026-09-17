/* Config-driven hashtag templates for the shared writing textarea. */
(function () {
  const field = document.getElementById('simple-input');
  const select = document.getElementById('template-select');
  if (!field || !select || typeof window.handleTemplateChange !== 'function') return;

  let activeTemplate = null;
  let managedTags = [];

  function configuredTags(template) {
    if (!template || template.type !== 'simple' || template.hashtag_template !== true || !Array.isArray(template.hashtags)) return [];
    return [...new Set(template.hashtags
      .filter(tag => typeof tag === 'string')
      .map(tag => tag.trim())
      .filter(tag => /^#[\p{L}\p{N}_-]+$/u.test(tag)))];
  }

  function tagPattern(tag) {
    return new RegExp(`(^|\\s)${tag.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?=\\s|$)`, 'giu');
  }

  function stripManagedTags(value) {
    if (!managedTags.length) return String(value || '');
    const text = String(value || '');
    const newline = text.indexOf('\n');
    const firstLine = newline === -1 ? text : text.slice(0, newline);
    let remaining = firstLine;
    for (const tag of managedTags) remaining = remaining.replace(tagPattern(tag), '$1');
    remaining = remaining.replace(/[ \t]+/g, ' ').trim();
    if (remaining) return remaining + (newline === -1 ? '' : text.slice(newline));
    if (newline === -1) return '';
    const rest = text.slice(newline + 1);
    return rest.startsWith('\n') ? rest.slice(1) : rest;
  }

  function replaceEditor(value, cursor) {
    if (typeof window.writeEditorReplace === 'function') {
      window.writeEditorReplace(value, { start: cursor, end: cursor });
    } else {
      field.value = value;
      field.setSelectionRange(cursor, cursor);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function applyTemplateTags(template) {
    const nextTags = configuredTags(template);
    const body = stripManagedTags(field.value);
    managedTags = nextTags;
    activeTemplate = nextTags.length ? template : null;
    const header = nextTags.join(' ');
    const value = header ? `${header}\n\n${body}` : body;
    if (value !== field.value) replaceEditor(value, header ? header.length + 2 : 0);
  }

  function hasManagedHeader() {
    if (!managedTags.length) return false;
    const firstLine = field.value.split('\n', 1)[0];
    return managedTags.every(tag => tagPattern(tag).test(firstLine));
  }

  function askAiHandoff() {
    return new Promise(resolve => {
      const overlay = document.createElement('div');
      overlay.className = 'fixed inset-0 z-[80] flex items-center justify-center bg-black/70 p-4';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      overlay.setAttribute('aria-label', 'Hashtags in den KI-Modus übernehmen');
      overlay.innerHTML = '<div class="w-full max-w-md rounded-xl border border-gray-700 bg-gray-900 p-4 shadow-2xl"><h2 class="text-base font-semibold text-gray-100">Hashtags übernehmen?</h2><p class="mt-2 text-sm text-gray-300">Sollen die Hashtags dieses Templates in den KI-Modus übernommen werden?</p><div class="mt-4 flex flex-col gap-2 sm:flex-row sm:justify-end"><button type="button" data-choice="cancel" class="rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300">Abbrechen</button><button type="button" data-choice="remove" class="rounded-lg bg-gray-700 px-3 py-2 text-sm text-white">Ohne Hashtags</button><button type="button" data-choice="keep" class="rounded-lg bg-green-700 px-3 py-2 text-sm text-white">Hashtags übernehmen</button></div></div>';
      const finish = choice => { overlay.remove(); resolve(choice); };
      overlay.addEventListener('click', event => {
        const button = event.target.closest('[data-choice]');
        if (button) finish(button.dataset.choice);
        else if (event.target === overlay) finish('cancel');
      });
      overlay.addEventListener('keydown', event => {
        if (event.key === 'Escape') finish('cancel');
      });
      document.body.appendChild(overlay);
      overlay.querySelector('[data-choice="keep"]').focus();
    });
  }

  const oldTemplateChange = window.handleTemplateChange;
  window.handleTemplateChange = function (template) {
    applyTemplateTags(template);
    return oldTemplateChange(template);
  };

  window.prepareTemplateHashtagsForAiMode = async function () {
    if (!activeTemplate || !hasManagedHeader()) {
      activeTemplate = null;
      managedTags = [];
      return true;
    }
    const choice = await askAiHandoff();
    if (choice === 'cancel') {
      select.value = activeTemplate.id;
      return false;
    }
    if (choice === 'remove') {
      const body = stripManagedTags(field.value);
      managedTags = [];
      activeTemplate = null;
      if (body !== field.value) replaceEditor(body, 0);
    }
    return true;
  };

  window.isBackgroundAgentTemplate = function (template) {
    return Boolean(template && template.type === 'simple' && (template.id === 'schnell' || template.hashtag_template === true));
  };
})();
