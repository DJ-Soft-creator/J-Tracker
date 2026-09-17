// Brain-Auswahl: Bloecke aus dem aktuellen Ergebnis markieren und als Markdown
// weitergeben — in die Zwischenablage oder als .md-Datei.
// AI maintenance contract: Auswahlzustand und Export liegen hier; das Ankreuz-
// feld auf der Karte rendert brain-cards.js, die Menueeintraege stehen im
// Template. Laedt nach brain-cards.js.
//
// Die Auswahl haelt Fingerprints, keine Kopien: der Volltext eines Blocks wird
// erst beim Export geholt. Eine Trefferkarte zeigt nur einen Ausschnitt
// (SNIPPET_LIMIT), und ein Export, der mitten im Satz "..." schreibt, waere
// wertlos.

const BRAIN_EXPORT_SEPARATOR = '\n\n---\n\n';


function brainSelectionCount() {
  return brainState.selected.size;
}


function brainSelectedItems() {
  // Reihenfolge der Trefferliste, nicht die des Anklickens: der Export soll
  // lesen wie das, was auf dem Schirm stand.
  return [...brainState.results.values()].filter((item) => brainState.selected.has(item.fingerprint));
}


function brainToggleSelectMode(active) {
  brainState.selectMode = active === undefined ? !brainState.selectMode : Boolean(active);
  if (!brainState.selectMode) brainState.selected.clear();
  brainRefreshSelectUi();
  // Die Ankreuzfelder haengen im Kartenmarkup, also neu zeichnen.
  if (brainState.results.size) renderBrainResults([...brainState.results.values()], brainState.pagination || {});
}


function brainToggleSelected(fingerprint, selected) {
  if (selected) brainState.selected.add(fingerprint);
  else brainState.selected.delete(fingerprint);
  brainRefreshSelectUi();
  const card = document.querySelector(`[data-brain-card][data-fingerprint="${CSS.escape(fingerprint)}"]`);
  card?.classList.toggle('border-green-600', Boolean(selected));
}


function brainSelectAllLoaded() {
  brainState.results.forEach((_, fingerprint) => brainState.selected.add(fingerprint));
  brainRefreshSelectUi();
  document.querySelectorAll('[data-brain-select]').forEach((box) => {
    box.checked = true;
    box.closest('[data-brain-card]')?.classList.add('border-green-600');
  });
}


function brainClearSelection() {
  brainState.selected.clear();
  brainRefreshSelectUi();
  document.querySelectorAll('[data-brain-select]').forEach((box) => {
    box.checked = false;
    box.closest('[data-brain-card]')?.classList.remove('border-green-600');
  });
}


/** Beschriftungen und Sichtbarkeit der Menueeintraege an den Zustand angleichen. */
function brainRefreshSelectUi() {
  const count = brainSelectionCount();
  const loaded = brainState.results.size;
  const entries = {
    'brain-menu-select': {
      label: brainState.selectMode ? 'Auswahl beenden' : 'Auswählen',
      show: true,
    },
    'brain-menu-select-all': { label: `Alle auswählen (${loaded})`, show: brainState.selectMode },
    'brain-menu-select-none': { label: 'Auswahl aufheben', show: brainState.selectMode && count > 0 },
    'brain-menu-copy': { label: `Auswahl kopieren (${count})`, show: brainState.selectMode && count > 0 },
    'brain-menu-download': { label: `Als .md herunterladen (${count})`, show: brainState.selectMode && count > 0 },
  };
  Object.entries(entries).forEach(([id, entry]) => {
    const button = brainElement(id);
    if (!button) return;
    button.dataset.menuLabel = entry.label;
    button.classList.toggle('hidden', !entry.show);
  });
  const badge = brainElement('brain-select-status');
  if (badge) {
    badge.textContent = brainState.selectMode ? `${count} von ${loaded} ausgewählt` : '';
    badge.classList.toggle('hidden', !brainState.selectMode);
  }
}


function brainExportDateLabel(value) {
  if (!value) return '';
  const [year, month, day] = String(value).slice(0, 10).split('-');
  return year && month && day ? `${day}.${month}.${year}` : String(value);
}


function brainExportTimeLabel(item) {
  const raw = String(item.sort_at || '');
  const match = /T(\d{2}:\d{2})/.exec(raw);
  return match ? match[1] : '';
}


/** Kopfblock: woher der Export stammt, damit er spaeter noch einzuordnen ist. */
function brainExportHeader(items) {
  const query = (brainElement('brain-query')?.value || '').trim();
  const chips = [
    ...[...brainState.selectedTags].map((tag) => `#${tag}`),
    ...[...brainState.excludedTags].map((tag) => `-#${tag}`),
  ].sort((left, right) => left.localeCompare(right, 'de-DE'));
  // Die Themengruppe ist eine ODER-Auswahl; als Komma-Liste zwischen den
  // UND-Filtern waere im Export nicht mehr zu erkennen, was zusammengehoerte.
  const themes = [...brainState.anyTags].sort((left, right) => left.localeCompare(right, 'de-DE'));
  const start = brainElement('brain-range-start')?.value || '';
  const end = brainElement('brain-range-end')?.value || '';
  const today = new Date().toLocaleDateString('de-DE');
  const lines = [`# Brain-Export ${today}`];
  const context = [];
  if (query) context.push(`Suche: "${query}"`);
  if (themes.length) context.push(`Themen: ${themes.map((tag) => `#${tag}`).join(' oder ')}`);
  if (chips.length) context.push(`Filter: ${chips.join(', ')}`);
  if (!query && !chips.length && !themes.length) context.push('Ohne Filter');
  lines.push(context.join(' · '));
  const scope = [];
  if (start || end) {
    scope.push(`Zeitraum: ${brainExportDateLabel(start) || '…'}–${brainExportDateLabel(end) || '…'}`);
  }
  scope.push(`${items.length} ${items.length === 1 ? 'Block' : 'Blöcke'}`);
  lines.push(scope.join(' · '));
  return lines.join('\n');
}


function brainExportBlock(item, text) {
  const filename = String(item.path || '').split('/').pop() || item.path || '';
  const when = [brainExportDateLabel(item.date), brainExportTimeLabel(item)].filter(Boolean).join(' ');
  const heading = [filename, when].filter(Boolean).join(' · ');
  const tags = (item.tags || []).map((tag) => `#${typeof tag === 'string' ? tag : tag.name}`).join(' ');
  return [`## ${heading}`, tags, '', String(text || '').trim()].filter((line, index) => line !== '' || index === 2).join('\n');
}


// Der Volltext kommt ueber dieselbe Route wie "Ganz anzeigen", damit die
// Sichtbarkeitspruefung an einer Stelle bleibt. Nur gekuerzte Bloecke, deren
// Text nicht schon ausgeklappt vorliegt, kosten eine Anfrage.
async function brainFullText(item) {
  const cached = brainState.expanded.get(item.fingerprint);
  if (cached) return cached;
  if (!item.truncated || item.db_journal) return item.snippet || '';
  try {
    const params = new URLSearchParams({ doc_id: item.doc_id, fingerprint: item.fingerprint });
    const response = await apiFetch(`/api/brain/document?${params.toString()}`, { cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) return item.snippet || '';
    const full = data.block_content ?? data.content ?? '';
    if (full) brainState.expanded.set(item.fingerprint, full);
    return full || item.snippet || '';
  } catch {
    return item.snippet || '';
  }
}


async function brainSelectionMarkdown() {
  const items = brainSelectedItems();
  if (!items.length) return '';
  const texts = [];
  for (const item of items) texts.push(await brainFullText(item));
  return [brainExportHeader(items), ...items.map((item, index) => brainExportBlock(item, texts[index]))]
    .join(BRAIN_EXPORT_SEPARATOR) + '\n';
}


// Ohne HTTPS gibt es keine Zwischenablage-API. Das versteckte Textfeld ist der
// Weg, der auch auf einer selbst gehosteten Instanz ohne Zertifikat traegt.
async function brainCopyText(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // faellt auf das Textfeld zurueck
  }
  const field = document.createElement('textarea');
  field.value = text;
  field.setAttribute('readonly', '');
  field.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
  document.body.appendChild(field);
  field.select();
  field.setSelectionRange(0, text.length);
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch {
    copied = false;
  }
  field.remove();
  return copied;
}


async function brainCopySelection() {
  const markdown = await brainSelectionMarkdown();
  if (!markdown) {
    showToast('Nichts ausgewählt.', true);
    return;
  }
  const copied = await brainCopyText(markdown);
  showToast(
    copied ? `${brainSelectionCount()} Blöcke in der Zwischenablage.` : 'Kopieren wurde vom Browser abgelehnt.',
    !copied,
  );
}


async function brainDownloadSelection() {
  const markdown = await brainSelectionMarkdown();
  if (!markdown) {
    showToast('Nichts ausgewählt.', true);
    return;
  }
  const stamp = new Date().toISOString().slice(0, 10);
  const link = document.createElement('a');
  const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
  link.href = url;
  link.download = `brain-export-${stamp}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}


function bindBrainSelectMenu() {
  brainElement('brain-menu-select')?.addEventListener('click', () => brainToggleSelectMode());
  brainElement('brain-menu-select-all')?.addEventListener('click', brainSelectAllLoaded);
  brainElement('brain-menu-select-none')?.addEventListener('click', brainClearSelection);
  brainElement('brain-menu-copy')?.addEventListener('click', brainCopySelection);
  brainElement('brain-menu-download')?.addEventListener('click', brainDownloadSelection);
  brainRefreshSelectUi();
}

bindBrainSelectMenu();
