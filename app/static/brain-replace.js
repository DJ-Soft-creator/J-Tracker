// Explicit, revision-safe hashtag replacement workflow for personal journals.
(function () {
  const state = { preview: null, applying: false };

  function el(id) {
    return document.getElementById(id);
  }

  function tagValue(id) {
    return (el(id)?.value || '').trim().replace(/^#/, '');
  }

  function modalMarkup() {
    return `<div id="brain-replace-modal" class="fixed inset-0 z-[70] hidden items-center justify-center bg-black/70 px-3 py-5" role="dialog" aria-modal="true" aria-labelledby="brain-replace-title">
      <div class="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-gray-700 bg-gray-950 shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-800 px-4 py-3 sm:px-5">
          <div><h2 id="brain-replace-title" class="text-base font-semibold text-gray-100">Hashtags in Journalen ersetzen</h2><p class="mt-1 text-xs text-gray-500">Nur persönliche Journale. Archiv und Familie bleiben unverändert.</p></div>
          <button id="brain-replace-close" type="button" class="text-xl leading-none text-gray-500 hover:text-white" aria-label="Schließen">×</button>
        </div>
        <div class="flex-1 overflow-y-auto px-4 py-4 sm:px-5">
          <div class="grid gap-3 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-end">
            <label class="block text-xs text-gray-400">Altes Hashtag<input id="brain-replace-old" type="text" placeholder="#alt" autocomplete="off" class="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-base text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500"></label>
            <span class="hidden pb-2 text-gray-600 sm:block" aria-hidden="true">→</span>
            <label class="block text-xs text-gray-400">Neues Hashtag<input id="brain-replace-new" type="text" placeholder="#neu" autocomplete="off" class="mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-base text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500"></label>
            <button id="brain-replace-preview" type="button" class="rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-200 hover:bg-gray-700">Treffer prüfen</button>
          </div>
          <p id="brain-replace-message" class="mt-3 min-h-5 text-sm text-gray-400" aria-live="polite"></p>
          <div id="brain-replace-results" class="mt-2 space-y-2"></div>
        </div>
        <div id="brain-replace-actions" class="hidden border-t border-gray-800 px-4 py-3 sm:px-5"></div>
      </div>
    </div>`;
  }

  function ensureModal() {
    if (el('brain-replace-modal')) return;
    document.body.insertAdjacentHTML('beforeend', modalMarkup());
    el('brain-replace-close').addEventListener('click', closeModal);
    el('brain-replace-preview').addEventListener('click', loadPreview);
    el('brain-replace-modal').addEventListener('click', (event) => {
      if (event.target === el('brain-replace-modal')) closeModal();
    });
    [el('brain-replace-old'), el('brain-replace-new')].forEach((input) => {
      input.addEventListener('input', () => {
        if (!state.preview) return;
        state.preview = null;
        el('brain-replace-results').innerHTML = '';
        el('brain-replace-actions').classList.add('hidden');
        el('brain-replace-message').textContent = 'Eingabe geändert. Bitte die Treffer erneut prüfen.';
        el('brain-replace-message').className = 'mt-3 min-h-5 text-sm text-gray-400';
      });
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') loadPreview();
      });
    });
  }

  function openModal() {
    ensureModal();
    state.preview = null;
    el('brain-replace-old').value = brainState.selectedTags.size === 1
      ? '#' + Array.from(brainState.selectedTags)[0] : '';
    el('brain-replace-new').value = '';
    el('brain-replace-message').textContent = '';
    el('brain-replace-results').innerHTML = '';
    el('brain-replace-actions').classList.add('hidden');
    el('brain-replace-modal').classList.remove('hidden');
    el('brain-replace-modal').classList.add('flex');
    (el('brain-replace-old').value ? el('brain-replace-new') : el('brain-replace-old')).focus();
  }

  function closeModal() {
    if (state.applying || !el('brain-replace-modal')) return;
    el('brain-replace-modal').classList.add('hidden');
    el('brain-replace-modal').classList.remove('flex');
  }

  function selectedDocuments() {
    if (!state.preview) return [];
    const selected = new Set(Array.from(document.querySelectorAll('[data-brain-replace-doc]:checked')).map((input) => input.value));
    return state.preview.journals.filter((journal) => selected.has(journal.doc_id));
  }

  function updateSelectionSummary() {
    const selected = selectedDocuments();
    const count = selected.reduce((sum, item) => sum + item.occurrences, 0);
    const summary = el('brain-replace-selection-summary');
    const continueButton = el('brain-replace-continue');
    if (summary) summary.textContent = `${selected.length} Journal${selected.length === 1 ? '' : 'e'} · ${count} Ersetzung${count === 1 ? '' : 'en'}`;
    if (continueButton) continueButton.disabled = selected.length === 0;
  }

  function renderPreview(data, selectedDocIds = null) {
    state.preview = data;
    const results = el('brain-replace-results');
    const actions = el('brain-replace-actions');
    if (!data.journals.length) {
      results.innerHTML = '<p class="rounded-lg border border-gray-800 bg-gray-900/60 p-4 text-sm text-gray-500">Keine persönlichen Journale mit diesem exakten Hashtag gefunden.</p>';
      actions.classList.add('hidden');
      return;
    }
    const isSelected = (journal) => !selectedDocIds || selectedDocIds.has(journal.doc_id);
    const allSelected = data.journals.every(isSelected);
    results.innerHTML = `<div class="mb-2 flex items-center justify-between gap-3 text-xs"><label class="inline-flex items-center gap-2 text-gray-300"><input id="brain-replace-all" type="checkbox"${allSelected ? ' checked' : ''} class="h-4 w-4 accent-green-500">Alle auswählen</label><span>${data.journal_count} Journale · ${data.occurrence_count} Treffer</span></div>`
      + data.journals.map((journal) => `<label class="block cursor-pointer rounded-xl border border-gray-800 bg-gray-900/60 p-3 hover:border-gray-700">
        <div class="flex items-start gap-3"><input type="checkbox"${isSelected(journal) ? ' checked' : ''} value="${escapeHtmlAttr(journal.doc_id)}" data-brain-replace-doc class="mt-1 h-4 w-4 shrink-0 accent-green-500"><div class="min-w-0 flex-1"><div class="flex flex-wrap items-center justify-between gap-2"><span class="break-all text-sm text-gray-200">${escapeHtml(journal.path)}</span><span class="text-xs text-gray-500">${journal.occurrences}×</span></div>${(journal.previews || []).map((line) => `<p class="mt-1 break-words text-xs text-gray-500">${escapeHtml(line)}</p>`).join('')}</div></div>
      </label>`).join('');
    actions.classList.remove('hidden');
    actions.innerHTML = `<div class="flex flex-wrap items-center justify-between gap-3"><span id="brain-replace-selection-summary" class="text-sm text-gray-400"></span><button id="brain-replace-continue" type="button" class="rounded-lg bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-600 disabled:cursor-not-allowed disabled:opacity-40">Auswahl prüfen</button></div>`;
    el('brain-replace-all').addEventListener('change', (event) => {
      document.querySelectorAll('[data-brain-replace-doc]').forEach((input) => { input.checked = event.target.checked; });
      updateSelectionSummary();
    });
    document.querySelectorAll('[data-brain-replace-doc]').forEach((input) => input.addEventListener('change', () => {
      const choices = Array.from(document.querySelectorAll('[data-brain-replace-doc]'));
      el('brain-replace-all').checked = choices.every((choice) => choice.checked);
      el('brain-replace-all').indeterminate = choices.some((choice) => choice.checked) && !choices.every((choice) => choice.checked);
      updateSelectionSummary();
    }));
    const choices = Array.from(document.querySelectorAll('[data-brain-replace-doc]'));
    el('brain-replace-all').indeterminate = choices.some((choice) => choice.checked) && !choices.every((choice) => choice.checked);
    el('brain-replace-continue').addEventListener('click', showConfirmation);
    updateSelectionSummary();
  }

  async function loadPreview() {
    const oldTag = tagValue('brain-replace-old');
    const newTag = tagValue('brain-replace-new');
    const message = el('brain-replace-message');
    if (!oldTag || !newTag) {
      message.textContent = 'Bitte altes und neues Hashtag eingeben.';
      message.className = 'mt-3 min-h-5 text-sm text-red-400';
      return;
    }
    if (oldTag === newTag) {
      message.textContent = 'Altes und neues Hashtag sind identisch.';
      message.className = 'mt-3 min-h-5 text-sm text-red-400';
      return;
    }
    const button = el('brain-replace-preview');
    button.disabled = true;
    message.textContent = 'Journale werden geprüft…';
    message.className = 'mt-3 min-h-5 text-sm text-gray-400';
    try {
      const response = await apiFetch('/api/brain/hashtag-replacement?' + new URLSearchParams({ old_tag: oldTag }), { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Treffer konnten nicht geladen werden.');
      if (tagValue('brain-replace-old') !== oldTag || tagValue('brain-replace-new') !== newTag) {
        message.textContent = 'Eingabe geändert. Bitte die Treffer erneut prüfen.';
        return;
      }
      message.textContent = `Vorschau für #${data.old_tag}. Noch wurde nichts geändert.`;
      renderPreview(data);
    } catch (error) {
      state.preview = null;
      el('brain-replace-results').innerHTML = '';
      el('brain-replace-actions').classList.add('hidden');
      message.textContent = error.message;
      message.className = 'mt-3 min-h-5 text-sm text-red-400';
    } finally {
      button.disabled = false;
    }
  }

  function showConfirmation() {
    const selected = selectedDocuments();
    if (!selected.length) return;
    const oldTag = tagValue('brain-replace-old');
    const newTag = tagValue('brain-replace-new');
    const occurrences = selected.reduce((sum, item) => sum + item.occurrences, 0);
    el('brain-replace-results').innerHTML = `<section class="rounded-xl border border-amber-800/70 bg-amber-950/25 p-4"><h3 class="font-medium text-amber-200">Änderung wirklich durchführen?</h3><p class="mt-2 text-sm text-gray-300"><strong>#${escapeHtml(oldTag)}</strong> wird in <strong>${selected.length} Journal${selected.length === 1 ? '' : 'en'}</strong> durch <strong>#${escapeHtml(newTag)}</strong> ersetzt. Betroffen sind ${occurrences} exakte Vorkommen.</p><p class="mt-2 text-xs text-amber-300/80">Dadurch werden die ausgewählten Journaldateien dauerhaft neu geschrieben. Zwischenzeitlich geänderte Journale werden zum Schutz abgelehnt.</p></section>`;
    el('brain-replace-actions').innerHTML = `<div class="flex flex-wrap justify-end gap-2"><button id="brain-replace-back" type="button" class="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-white">Zurück zur Auswahl</button><button id="brain-replace-apply" type="button" class="rounded-lg bg-amber-700 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600">Ja, ${selected.length} Journal${selected.length === 1 ? '' : 'e'} jetzt ändern</button></div>`;
    el('brain-replace-back').addEventListener('click', () => renderPreview(
      state.preview, new Set(selected.map((item) => item.doc_id)),
    ));
    el('brain-replace-apply').addEventListener('click', () => applyReplacement(selected));
  }

  async function applyReplacement(selected) {
    state.applying = true;
    const button = el('brain-replace-apply');
    button.disabled = true;
    button.textContent = 'Journale werden geändert…';
    try {
      const response = await apiFetch('/api/brain/hashtag-replacement', {
        method: 'POST',
        body: JSON.stringify({
          old_tag: tagValue('brain-replace-old'),
          new_tag: tagValue('brain-replace-new'),
          confirmed: true,
          documents: selected.map((item) => ({ doc_id: item.doc_id, content_hash: item.content_hash })),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok && !data.changed?.length) throw new Error(data.error || 'Ersetzung fehlgeschlagen.');
      const partial = !response.ok;
      el('brain-replace-results').innerHTML = `<section class="rounded-xl border ${partial ? 'border-amber-800 bg-amber-950/25' : 'border-green-800 bg-green-950/25'} p-4"><h3 class="font-medium ${partial ? 'text-amber-200' : 'text-green-300'}">${partial ? 'Teilweise abgeschlossen' : 'Ersetzung abgeschlossen'}</h3><p class="mt-2 text-sm text-gray-300">${data.journal_count || 0} Journale und ${data.occurrence_count || 0} Vorkommen wurden geändert. Das neue Hashtag ist jetzt als Brain-Filter verfügbar.${partial ? ` ${data.conflicts.length} Journale blieben wegen zwischenzeitlicher Änderungen unverändert.` : ''}</p></section>`;
      el('brain-replace-actions').innerHTML = '<div class="flex justify-end"><button id="brain-replace-done" type="button" class="rounded-lg bg-gray-800 px-4 py-2 text-sm text-gray-200 hover:bg-gray-700">Schließen</button></div>';
      el('brain-replace-done').addEventListener('click', closeModal);
      invalidateBrainContext();
      brainState.supportLoaded = false;
      loadBrain();
    } catch (error) {
      el('brain-replace-message').textContent = error.message;
      el('brain-replace-message').className = 'mt-3 min-h-5 text-sm text-red-400';
      button.disabled = false;
      button.textContent = 'Erneut versuchen';
    } finally {
      state.applying = false;
    }
  }

  document.querySelectorAll('[data-brain-hashtag-replace]').forEach((button) => button.addEventListener('click', openModal));
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !el('brain-replace-modal')?.classList.contains('hidden')) closeModal();
  });
})();
