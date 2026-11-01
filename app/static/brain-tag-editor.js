// Brain: alle Hashtags eines Treffers an einer Stelle bearbeiten.
// AI maintenance contract: Dialog und Speichern liegen hier. Eigene Hashtags
// gehen wie bisher als Brain-Metadaten an /api/brain/metadata; KI-Hashtags
// stehen nur in der technischen Fussnote des Journals und werden ueber
// /api/brain/ai-tags/reject zurueckgenommen - dort merkt sich die Fussnote sie
// als abgelehnt, damit der naechste Tagging-Lauf sie nicht wieder vergibt.
// Hashtags aus dem Text bleiben fest: sie aendern sich nur im Text selbst.
// Laedt nach brain-content.js (brainState, brainTagBadges, loadBrainTags).

(function () {
  const state = { item: null, removed: new Set(), added: [], saving: false };

  function el(id) {
    return document.getElementById(id);
  }

  function normaliseTag(value) {
    return brainNormaliseTagQuery(value).replace(/\s+/g, '-');
  }

  function validTag(tag) {
    return /^[\p{L}\p{N}_-]+$/u.test(tag);
  }

  function modalMarkup() {
    return `<div id="brain-tag-editor-modal" class="fixed inset-0 z-[70] hidden items-center justify-center bg-black/70 px-3 py-5" role="dialog" aria-modal="true" aria-labelledby="brain-tag-editor-title">
      <div class="flex max-h-full w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-gray-700 bg-gray-950 shadow-2xl">
        <div class="flex items-start justify-between gap-4 border-b border-gray-800 px-4 py-3 sm:px-5">
          <div class="min-w-0"><h2 id="brain-tag-editor-title" class="text-base font-semibold text-gray-100">Hashtags bearbeiten</h2><p id="brain-tag-editor-source" class="mt-1 truncate font-mono text-xs text-gray-500"></p></div>
          <button id="brain-tag-editor-close" type="button" class="text-gray-500 hover:text-white" aria-label="Schließen">${window.journlIcon?.('close', 'h-5 w-5') || '×'}</button>
        </div>
        <div class="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-5">
          <div id="brain-tag-editor-chips" class="flex flex-wrap gap-1.5" aria-label="Hashtags des Eintrags"></div>
          <div class="relative">
            <div class="flex gap-2">
              <input id="brain-tag-editor-input" type="text" placeholder="#neues-hashtag" autocomplete="off" aria-label="Hashtag hinzufügen"
                class="min-w-0 flex-1 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-base text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500">
              <button id="brain-tag-editor-add" type="button" class="inline-flex items-center gap-1 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-200 hover:bg-gray-700">${window.journlIcon?.('plus', 'h-4 w-4') || ''}<span>Hinzufügen</span></button>
            </div>
            <div id="brain-tag-editor-suggestions" class="mt-2 flex flex-wrap gap-1.5"></div>
          </div>
          <p class="text-[11px] leading-relaxed text-gray-500"><span class="text-violet-300">KI</span> vergeben – beim Entfernen merkt sich das Journal das Hashtag als abgelehnt, ein späterer Tagging-Lauf vergibt es nicht wieder. <span class="text-gray-300">Text</span> steht im Eintrag selbst und lässt sich nur im Editor ändern.</p>
          <p id="brain-tag-editor-message" class="min-h-5 text-sm text-gray-400" aria-live="polite"></p>
        </div>
        <div class="flex items-center justify-end gap-2 border-t border-gray-800 px-4 py-3 sm:px-5">
          <button id="brain-tag-editor-cancel" type="button" class="rounded-lg px-3 py-2 text-sm text-gray-400 hover:text-white">Abbrechen</button>
          <button id="brain-tag-editor-save" type="button" class="rounded-lg bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-600 disabled:cursor-not-allowed disabled:opacity-40">Speichern</button>
        </div>
      </div>
    </div>`;
  }

  function ensureModal() {
    if (el('brain-tag-editor-modal')) return;
    document.body.insertAdjacentHTML('beforeend', modalMarkup());
    el('brain-tag-editor-close').addEventListener('click', closeModal);
    el('brain-tag-editor-cancel').addEventListener('click', closeModal);
    el('brain-tag-editor-save').addEventListener('click', save);
    el('brain-tag-editor-add').addEventListener('click', () => addTag(el('brain-tag-editor-input').value));
    el('brain-tag-editor-modal').addEventListener('click', (event) => {
      if (event.target === el('brain-tag-editor-modal')) closeModal();
    });
    el('brain-tag-editor-modal').addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeModal();
    });
    const input = el('brain-tag-editor-input');
    input.addEventListener('input', renderSuggestions);
    input.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addTag(input.value);
    });
  }

  // Ein Hashtag kann mehrfach begruendet sein (z. B. im Text und als eigenes);
  // die Herkunft mit dem engsten Recht entscheidet, was sich entfernen laesst.
  function entries() {
    const item = state.item;
    const text = new Set(item.text_tags || []);
    const ai = new Set(item.ai_tags || []);
    const manual = new Set(item.manual_tags || []);
    const names = new Set([...(item.tags || []), ...text, ...ai, ...manual, ...state.added]);
    return Array.from(names).sort((left, right) => left.localeCompare(right, 'de-DE')).map((name) => {
      if (ai.has(name)) return { name, kind: 'ai' };
      if (manual.has(name) || state.added.includes(name)) return { name, kind: 'manual' };
      return { name, kind: 'text' };
    });
  }

  const KIND_LABEL = { ai: 'KI', manual: 'eigenes', text: 'Text' };
  const KIND_TONE = {
    ai: 'border-violet-800 bg-violet-500/10 text-violet-200',
    manual: 'border-green-800 bg-green-500/10 text-green-200',
    text: 'border-gray-700 bg-gray-900 text-gray-300',
  };

  function chipMarkup(entry) {
    const removed = state.removed.has(entry.name);
    const label = `<span class="${removed ? 'line-through decoration-red-500/70 opacity-60' : ''}">#${escapeHtml(entry.name)}</span>`
      + `<span class="text-[10px] uppercase tracking-wide opacity-60">${KIND_LABEL[entry.kind]}</span>`;
    if (entry.kind === 'text') {
      return `<span class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${KIND_TONE.text}" title="Steht im Text – nur im Editor änderbar">${label}${window.journlIcon?.('lock', 'h-3 w-3 opacity-60') || ''}</span>`;
    }
    const action = removed ? 'Wiederherstellen' : 'Entfernen';
    return `<span class="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs ${removed ? 'border-red-900 bg-red-500/5 text-gray-400' : KIND_TONE[entry.kind]}">${label}
      <button type="button" data-brain-tag-editor-toggle="${escapeHtmlAttr(entry.name)}" title="#${escapeHtmlAttr(entry.name)} ${action.toLowerCase()}" aria-label="#${escapeHtmlAttr(entry.name)} ${action.toLowerCase()}"
        class="-mr-1 rounded-full p-0.5 ${removed ? 'text-gray-400 hover:text-green-300' : 'opacity-70 hover:text-red-300 hover:opacity-100'}">${window.journlIcon?.(removed ? 'plus' : 'close', 'h-3 w-3') || (removed ? '+' : '×')}</button>
    </span>`;
  }

  function renderChips() {
    const list = entries();
    const chips = el('brain-tag-editor-chips');
    chips.innerHTML = list.length
      ? list.map(chipMarkup).join('')
      : '<p class="text-sm text-gray-500">Noch keine Hashtags.</p>';
    chips.querySelectorAll('[data-brain-tag-editor-toggle]').forEach((button) => {
      button.addEventListener('click', () => toggleRemoved(button.dataset.brainTagEditorToggle));
    });
    el('brain-tag-editor-save').disabled = state.saving || !hasChanges();
  }

  function toggleRemoved(name) {
    // Ein eben erst hinzugefuegtes Hashtag verschwindet einfach wieder.
    if (state.added.includes(name)) {
      state.added = state.added.filter((tag) => tag !== name);
    } else if (state.removed.has(name)) {
      state.removed.delete(name);
    } else {
      state.removed.add(name);
    }
    renderChips();
    renderSuggestions();
  }

  function suggestionSource() {
    return (brainState.allTags && brainState.allTags.length ? brainState.allTags : brainState.tags) || [];
  }

  function renderSuggestions() {
    const box = el('brain-tag-editor-suggestions');
    const query = brainNormaliseTagQuery(el('brain-tag-editor-input').value);
    if (!query) {
      box.innerHTML = '';
      return;
    }
    const present = new Set(entries().map((entry) => entry.name));
    const rows = suggestionSource()
      .filter((tag) => tag && tag.name && !present.has(tag.name))
      .map((tag) => ({ tag, score: brainTagMatchScore(tag.name, query) }))
      .filter((row) => row.score !== null)
      .sort((left, right) => left.score - right.score || (right.tag.count || 0) - (left.tag.count || 0))
      .slice(0, 8);
    box.innerHTML = rows.map(({ tag }) => `<button type="button" data-brain-tag-editor-suggest="${escapeHtmlAttr(tag.name)}"
      class="inline-flex items-center gap-1 rounded-full border border-gray-700 px-2 py-1 text-xs ${brainTagRowTone(tag, false)}">#${escapeHtml(tag.name)}${tag.count ? ` <span class="text-gray-500">${tag.count}</span>` : ''}</button>`).join('');
    box.querySelectorAll('[data-brain-tag-editor-suggest]').forEach((button) => {
      button.addEventListener('click', () => addTag(button.dataset.brainTagEditorSuggest));
    });
  }

  function setMessage(text, isError = false) {
    const message = el('brain-tag-editor-message');
    message.textContent = text;
    message.className = `min-h-5 text-sm ${isError ? 'text-red-400' : 'text-gray-400'}`;
  }

  function addTag(raw) {
    const tag = normaliseTag(raw);
    const input = el('brain-tag-editor-input');
    if (!tag) return;
    if (!validTag(tag)) {
      setMessage('Hashtags bestehen nur aus Buchstaben, Ziffern, - und _.', true);
      return;
    }
    setMessage('');
    if (state.removed.has(tag)) state.removed.delete(tag);
    else if (!entries().some((entry) => entry.name === tag)) state.added.push(tag);
    input.value = '';
    renderChips();
    renderSuggestions();
    input.focus();
  }

  function hasChanges() {
    return state.added.length > 0 || state.removed.size > 0;
  }

  function open(docId, fingerprint) {
    const item = brainState.results.get(fingerprint);
    if (!item || item.doc_id !== docId) return;
    ensureModal();
    state.item = item;
    state.removed = new Set();
    state.added = [];
    state.saving = false;
    el('brain-tag-editor-source').textContent = String(item.path || '').split('/').pop() || item.path || '';
    el('brain-tag-editor-input').value = '';
    setMessage('');
    renderChips();
    renderSuggestions();
    el('brain-tag-editor-modal').classList.remove('hidden');
    el('brain-tag-editor-modal').classList.add('flex');
    el('brain-tag-editor-input').focus();
    // Die Vorschlaege nehmen den vollen Bestand, nicht nur die Facetten der
    // aktuellen Treffer - die kennen gerade das gesuchte Hashtag oft nicht.
    if (typeof loadBrainAllTags === 'function') loadBrainAllTags().then(renderSuggestions);
  }

  function closeModal() {
    if (state.saving || !el('brain-tag-editor-modal')) return;
    el('brain-tag-editor-modal').classList.add('hidden');
    el('brain-tag-editor-modal').classList.remove('flex');
    state.item = null;
  }

  async function postJson(url, payload) {
    const response = await apiFetch(url, { method: 'POST', body: JSON.stringify(payload) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen.');
    return data;
  }

  async function save() {
    const item = state.item;
    if (!item || state.saving || !hasChanges()) return;
    // Ein Hashtag, das KI und Nutzer zugleich vergeben haben, verliert beim
    // Entfernen beide Herkuenfte - sonst stuende es nach dem Speichern noch da.
    const rejectAi = (item.ai_tags || []).filter((tag) => state.removed.has(tag));
    const manual = Array.from(new Set([...(item.manual_tags || []), ...state.added]))
      .filter((tag) => !state.removed.has(tag))
      .sort();
    const manualChanged = manual.length !== (item.manual_tags || []).length
      || manual.some((tag) => !(item.manual_tags || []).includes(tag));
    state.saving = true;
    el('brain-tag-editor-save').disabled = true;
    setMessage('Speichere …');
    try {
      let aiTags = item.ai_tags || [];
      if (rejectAi.length) {
        const result = await postJson('/api/brain/ai-tags/reject', {
          doc_id: item.doc_id, fingerprint: item.fingerprint, tags: rejectAi,
        });
        const removed = new Set(result.removed || []);
        aiTags = aiTags.filter((tag) => !removed.has(tag));
      }
      let manualTags = item.manual_tags || [];
      if (manualChanged) {
        const result = await postJson('/api/brain/metadata', {
          doc_id: item.doc_id, reference_type: 'block', fingerprint: item.fingerprint, tags: manual,
        });
        manualTags = result.metadata?.tags || manual;
      }
      applyToCard(item, aiTags, manualTags);
      state.saving = false;
      closeModal();
      showToast('Hashtags gespeichert.');
      // Die Zaehler des vollen Bestands stimmen nicht mehr; die Facetten der
      // Filterleiste kommen frisch vom Server.
      brainState.allTags = null;
      if (typeof loadBrainTags === 'function') loadBrainTags();
    } catch (error) {
      state.saving = false;
      setMessage(error.message || 'Netzwerkfehler beim Speichern.', true);
      renderChips();
    }
  }

  // Die Karte wird an Ort und Stelle nachgezogen statt die Trefferliste neu zu
  // laden: sonst springt die Liste nach oben, mitten im Aufraeumen.
  function applyToCard(item, aiTags, manualTags) {
    item.ai_tags = aiTags;
    item.manual_tags = manualTags;
    item.tags = Array.from(new Set([...(item.text_tags || []), ...aiTags, ...manualTags]))
      .sort((left, right) => left.localeCompare(right, 'de-DE'));
    const card = Array.from(document.querySelectorAll('[data-brain-card]'))
      .find((node) => node.dataset.fingerprint === item.fingerprint);
    const target = card?.querySelector('[data-brain-card-tags]');
    if (target) target.innerHTML = brainTagBadges(item.tags, item.ai_tags);
  }

  window.openBrainTagEditor = open;
}());
