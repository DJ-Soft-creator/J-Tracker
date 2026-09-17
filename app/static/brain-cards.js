// Brain-Ergebniskarten: Auszeichnung einer Trefferkarte, ihr "⋯"-Menü und das
// Nachladen des Volltexts.
// AI maintenance contract: Rendering und Kartenaktionen liegen hier; geteilter
// Zustand und Anfragen bleiben in brain.js, Datei-/Editoränderungen in
// brain-content.js. Lädt nach brain.js, weil brainState dort entsteht.

function brainSourceClass(source) {
  if (source === 'family') return 'bg-pink-950 text-pink-200 border-pink-800';
  if (source === 'archive') return 'bg-amber-950 text-amber-300 border-amber-800';
  return 'bg-gray-800 text-gray-300 border-gray-700';
}


// `machineTags` are hashtags a model assigned. They live in the technical footer
// and not in the block text, so without a marker they would look exactly like
// something the user typed themselves.
function brainTagBadges(tags, machineTags = []) {
  const fromModel = new Set(machineTags || []);
  return (tags || []).map((tag) => {
    const name = typeof tag === 'string' ? tag : tag.name;
    const type = typeof tag === 'object' ? tag.type : (
      typeof window.hashtagApprovalStatus === 'function' ? window.hashtagApprovalStatus(name) : 'standard'
    );
    const approved = typeof tag === 'object'
      ? Boolean(tag.approved)
      : (typeof window.hashtagApprovalStatus !== 'function' || window.hashtagApprovalStatus(name) === 'approved');
    if (fromModel.has(name)) {
      return `<span class="text-xs text-violet-300" title="Von der KI vergeben – steht nicht im Text">`
        + `#${escapeHtml(name)}<span class="opacity-60">*</span></span>`;
    }
    const color = type === 'ai' ? 'text-sky-200' : (approved ? 'text-green-300' : 'text-orange-300');
    return `<span class="text-xs ${color}">#${escapeHtml(name)}</span>`;
  }).join(' ');
}


function brainEditButton(docId, fingerprint = '') {
  return `<button type="button" data-brain-edit data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" class="text-green-500 hover:text-green-400 transition inline-flex items-center justify-center p-2 -m-2" title="Bearbeiten" aria-label="Bearbeiten">${window.journlIcon?.('pencil', 'h-4 w-4') || ''}</button>`;
}


function brainAssistantButton(docId, fingerprint = '') {
  return `<button type="button" data-brain-assistant data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" class="text-green-500 hover:text-green-400 transition text-xs" title="An Agent senden" aria-label="An Agent senden">${window.journlIcon?.('agent', 'h-4 w-4') || ''}</button>`;
}


// „An Agent senden" startet eine Sitzung auf dem Host; der Schreib-Agent
// dagegen gibt einen Vorschlag zurueck, der im Schreiben-Tab landet. Zwei
// Wege, zwei Eintraege - der abgelegte Treffer bleibt bei beiden unberuehrt.
function brainWriteAgentButton(docId, fingerprint = '') {
  return `<button type="button" data-brain-write-agent data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" class="text-green-500 hover:text-green-400 transition text-xs" title="Schreib-Agent öffnen" aria-label="Schreib-Agent öffnen">${window.journlIcon?.('chat', 'h-4 w-4') || ''}</button>`;
}


// Ein Treffer laesst sich derselben Sammelmappe zuordnen wie ein Eintrag im
// Heute-Tab; adressiert wird er ueber Dokument und Block (buckets.js).
function brainBucketButton(docId, fingerprint) {
  return `<button type="button" data-brain-bucket data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" class="text-green-500 hover:text-green-400 transition text-xs" title="Zu Sammelmappe" aria-label="Zu Sammelmappe">${window.journlIcon?.('folders', 'h-4 w-4') || ''}</button>`;
}


function brainManageButton(docId, label) {
  return `<button type="button" data-brain-manage data-doc-id="${escapeHtmlAttr(docId)}" class="inline-flex items-center gap-1 px-1 py-0.5 text-[11px] text-gray-600 hover:text-gray-300" title="${escapeHtmlAttr(label)} verwalten">${window.journlIcon?.('gear', 'h-3.5 w-3.5') || ''}<span>${escapeHtml(label)} verwalten</span></button>`;
}


// Kartenaktionen liegen hinter dem gemeinsamen "⋯"-Menü; eine einzelne Aktion
// bleibt als Icon stehen (siehe action-menu.js).
function brainActionMenu(parts, label = 'Aktionen') {
  return (window.actionMenu || ((items) => items.filter(Boolean).join('')))(parts, { label });
}


function brainExpandButton(docId, fingerprint, expanded) {
  const label = expanded ? 'Wieder einklappen' : 'Ganz anzeigen';
  return `<button type="button" data-brain-expand data-doc-id="${escapeHtmlAttr(docId)}" data-fingerprint="${escapeHtmlAttr(fingerprint)}" data-menu-label="${escapeHtmlAttr(label)}" class="text-gray-400 hover:text-white transition text-xs" title="${escapeHtmlAttr(label)}" aria-label="${escapeHtmlAttr(label)}">${expanded ? '⤡' : '⤢'}</button>`;
}


// Der Menüeintrag wird beim Öffnen aus diesem versteckten Button gebaut, also
// muss die Beschriftung dort umgestellt werden, nicht an der Menüzeile.
function setBrainExpandLabel(button, expanded) {
  const label = expanded ? 'Wieder einklappen' : 'Ganz anzeigen';
  button.dataset.menuLabel = label;
  button.title = label;
  button.setAttribute('aria-label', label);
  button.textContent = expanded ? '⤡' : '⤢';
}


function brainCardBody(item) {
  const text = brainState.expanded.get(item.fingerprint) ?? item.snippet;
  const rendered = typeof window.renderJournalText === 'function'
    ? window.renderJournalText(text) : escapeHtml(text);
  const media = typeof window.renderJournalMedia === 'function' ? window.renderJournalMedia(item.media) : '';
  return rendered + media;
}


// Suchergebnisse liefern nur einen Ausschnitt (siehe SNIPPET_LIMIT). Der
// Volltext eines Blocks kommt über dieselbe Route wie der Inline-Editor, damit
// die Sichtbarkeitsprüfung an einer Stelle bleibt.
async function toggleBrainCardExpansion(button) {
  const card = button.closest('[data-brain-card]');
  const display = card?.querySelector('[data-brain-display]');
  const fingerprint = button.dataset.fingerprint || '';
  const item = brainState.results.get(fingerprint);
  if (!display || !item) return;
  if (brainState.expanded.has(fingerprint)) {
    brainState.expanded.delete(fingerprint);
    display.innerHTML = brainCardBody(item);
    setBrainExpandLabel(button, false);
    return;
  }
  if (card.dataset.brainExpanding === '1') return;
  card.dataset.brainExpanding = '1';
  try {
    const params = new URLSearchParams({ doc_id: button.dataset.docId, fingerprint });
    const response = await apiFetch(`/api/brain/document?${params.toString()}`, { cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Der vollständige Eintrag konnte nicht geladen werden.');
    const full = data.block_content ?? data.content ?? '';
    if (!full) throw new Error('Zu diesem Eintrag gibt es keinen vollständigen Text.');
    brainState.expanded.set(fingerprint, full);
    display.innerHTML = brainCardBody(item);
    setBrainExpandLabel(button, true);
  } catch (error) {
    showToast(error.message || 'Netzwerkfehler beim Ausklappen.', true);
  } finally {
    delete card.dataset.brainExpanding;
  }
}


function bindBrainCardActions(container) {
  container.querySelectorAll('[data-brain-expand]').forEach((button) => {
    button.addEventListener('click', () => toggleBrainCardExpansion(button));
  });
  container.querySelectorAll('[data-brain-edit]').forEach((button) => {
    button.addEventListener('click', () => {
      const openEditor = window.openBrainWriteEditor || window.openBrainDocument;
      openEditor(
        button.dataset.docId,
        button.dataset.fingerprint || '',
        button.closest('[data-brain-card]'),
      );
    });
  });
  container.querySelectorAll('[data-brain-assistant]').forEach((button) => {
    button.addEventListener('click', () => {
      if (typeof window.openAssistantFromBrain === 'function') {
        window.openAssistantFromBrain(button.dataset.docId, button.dataset.fingerprint || '');
      } else {
        showToast('Der Assistent ist nicht geladen.', true);
      }
    });
  });
  container.querySelectorAll('[data-brain-write-agent]').forEach((button) => {
    button.addEventListener('click', () => {
      if (typeof window.openWriteAgentFromBrain === 'function') {
        window.openWriteAgentFromBrain(button.dataset.docId, button.dataset.fingerprint || '');
      } else {
        showToast('Der Schreib-Agent ist nicht geladen.', true);
      }
    });
  });
  container.querySelectorAll('[data-brain-bucket]').forEach((button) => {
    button.addEventListener('click', () => {
      const fingerprint = button.dataset.fingerprint || '';
      const item = brainState.results.get(fingerprint);
      // Der ausgeklappte Volltext ist die bessere Beschriftung als der Ausschnitt.
      const text = brainState.expanded.get(fingerprint) ?? item?.snippet ?? '';
      if (!window.journlBuckets) {
        showToast('Die Sammelmappen sind nicht geladen.', true);
        return;
      }
      window.journlBuckets.assignBrain(button.dataset.docId, fingerprint, item?.path || '', text);
    });
  });
  container.querySelectorAll('[data-brain-manage]').forEach((button) => {
    button.addEventListener('click', () => openBrainAccessManagerForDocument(button.dataset.docId));
  });
}


// Wurde nichts gefunden und ein aehnlich geschriebenes Wort aus den eigenen
// Notizen eingesetzt, muss das ueber den Treffern stehen. Ergebnisse zu etwas
// anderem als dem Getippten kommentarlos anzuzeigen waere eine stille Luege.
function brainCorrectionNotice(corrections) {
  if (!Array.isArray(corrections) || !corrections.length) return '';
  const pairs = corrections
    .map((item) => `„${escapeHtml(item.term)}“ &rarr; „${escapeHtml(item.replacement)}“`)
    .join(', ');
  return `<p class="rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-xs text-amber-300">
    Keine Treffer wie getippt &ndash; gesucht wurde stattdessen nach ${pairs}.
  </p>`;
}


function renderBrainResults(results, pagination = {}) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  brainState.results = new Map(results.map((item) => [item.fingerprint, item]));
  brainState.pagination = pagination;
  brainState.expanded.forEach((_, fingerprint) => {
    if (!brainState.results.has(fingerprint)) brainState.expanded.delete(fingerprint);
  });
  brainState.selected.forEach((fingerprint) => {
    if (!brainState.results.has(fingerprint)) brainState.selected.delete(fingerprint);
  });
  if (!results.length) {
    container.innerHTML = '<p class="text-gray-500 text-center mt-8">Keine sichtbaren Markdown-Eintraege gefunden.</p>';
    if (typeof brainRefreshSelectUi === 'function') brainRefreshSelectUi();
    return;
  }
  const corrections = brainCorrectionNotice(pagination.corrections);
  const more = pagination.has_more
    ? `<div class="flex justify-center pt-2"><button type="button" data-brain-load-more class="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:border-gray-500 hover:text-white">Mehr laden (${results.length} von ${pagination.total})</button></div>`
    : '';
  container.innerHTML = results.map((item) => {
    const tags = brainTagBadges(item.tags, item.ai_tags);
    const filename = String(item.path || '').split('/').pop() || item.path;
    const selected = brainState.selected.has(item.fingerprint);
    // Die Trefferflaeche ist bewusst groesser als das Kaestchen: auf dem Handy
    // trifft man 16 Pixel sonst nicht zuverlaessig.
    const checkbox = brainState.selectMode
      ? `<label class="-m-1 flex shrink-0 cursor-pointer items-start p-1">
          <input type="checkbox" data-brain-select="${escapeHtmlAttr(item.fingerprint)}" ${selected ? 'checked' : ''}
            aria-label="Block auswaehlen" class="mt-0.5 h-4 w-4 shrink-0 accent-green-500" />
        </label>`
      : '';
    return `<article data-brain-card data-doc-id="${escapeHtmlAttr(item.doc_id)}" data-fingerprint="${escapeHtmlAttr(item.fingerprint)}" class="min-w-0 max-w-full overflow-hidden bg-gray-900 border ${selected ? 'border-green-600' : 'border-gray-800'} rounded-xl p-4 ${item.source === 'family' ? 'brain-family-entry' : ''}">
      <div class="flex items-start justify-between gap-2 mb-2">
        ${checkbox}
        <div class="min-w-0 flex flex-1 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass(item.source)}">${escapeHtml(item.source_label)}</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(filename)}</span>
          ${tags}
          ${item.read_only ? '<span class="text-amber-400">nur lesen</span>' : ''}
        </div>
        <div class="flex shrink-0 items-center justify-end">
          ${brainActionMenu([
            item.management?.can_manage ? brainManageButton(item.doc_id, item.path.startsWith('projects/') ? 'Projekt' : 'Notiz') : '',
            item.db_journal ? '' : `<button type="button" data-brain-metadata data-reference-type="block" data-doc-id="${escapeHtmlAttr(item.doc_id)}" data-fingerprint="${escapeHtmlAttr(item.fingerprint)}" class="text-xs text-gray-500 hover:text-white">Tags bearbeiten</button>`,
            item.truncated && !item.db_journal
              ? brainExpandButton(item.doc_id, item.fingerprint, brainState.expanded.has(item.fingerprint)) : '',
            item.read_only ? '' : brainEditButton(item.doc_id, item.fingerprint),
            brainAssistantButton(item.doc_id, item.fingerprint),
            brainWriteAgentButton(item.doc_id, item.fingerprint),
            item.db_journal ? '' : brainBucketButton(item.doc_id, item.fingerprint),
          ])}
        </div>
      </div>
      <div data-brain-display class="min-w-0 max-w-full w-full break-words whitespace-pre-wrap text-sm leading-relaxed text-gray-200">${brainCardBody(item)}</div>
    </article>`;
  }).join('') + more;
  container.insertAdjacentHTML('afterbegin', corrections);
  bindBrainCardActions(container);
  container.querySelectorAll('[data-brain-metadata]').forEach((button) => {
    button.addEventListener('click', () => editBrainMetadata(
      button.dataset.referenceType, button.dataset.docId, button.dataset.fingerprint,
    ));
  });
  container.querySelector('[data-brain-load-more]')?.addEventListener('click', loadMoreBrainResults);
  container.querySelectorAll('[data-brain-select]').forEach((box) => {
    box.addEventListener('change', () => brainToggleSelected(box.dataset.brainSelect, box.checked));
  });
  if (typeof brainRefreshSelectUi === 'function') brainRefreshSelectUi();
}


function mapDbJournalResults(items) {
  return (items || []).map((item) => ({
    fingerprint: `db-journal:${item.revision_id}`,
    doc_id: `db-journal:${item.revision_id}`,
    source: 'journal-db',
    source_label: 'Journal DB',
    path: item.relative_path,
    snippet: String(item.excerpt || '').replace(/<\/?mark>/g, ''),
    tags: [],
    media: [],
    read_only: true,
    db_journal: true,
  }));
}
