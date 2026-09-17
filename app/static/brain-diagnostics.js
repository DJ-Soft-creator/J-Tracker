// Journal-Diagnose in den Brain-Einstellungen.
//
// Beantwortet zwei Fragen, die die Oberflaeche bisher nicht stellte: Welche
// Dateien uebergeht der Index stillschweigend, und wie viel des Journals ist
// ueberhaupt adressierbar und verschlagwortet? Beides war bisher nur zu
// erkennen, wenn jemand zufaellig danach suchte.

function brainHealthRow(text, tone) {
  const row = document.createElement('div');
  row.className = tone === 'warn' ? 'text-amber-400' : 'text-gray-400';
  row.textContent = text;
  return row;
}

function renderBrainHealth(panel, data) {
  panel.textContent = '';
  const percent = data.blocks ? Math.round((data.tagged_blocks / data.blocks) * 100) : 0;
  const range = data.range && (data.range.start || data.range.end)
    ? ` (${data.range.start || 'Anfang'} bis ${data.range.end || 'heute'})`
    : '';
  panel.append(brainHealthRow(
    `${data.days} Journaltage${range}, ${data.blocks} adressierbare Bloecke.`,
  ));
  panel.append(brainHealthRow(
    `${data.tagged_blocks} davon mit Hashtags (${percent} %), ${data.ai_blocks} vom Modell vergeben.`,
  ));
  // Ein gestempelter Tag ohne Modell-Hashtags heisst: der Stempel ist aelter
  // als die heutige Blockstruktur. Genau daran erkennt man ihn.
  if (data.stamped_days) {
    panel.append(brainHealthRow(`${data.stamped_days} Tage sind als klassifiziert gestempelt.`));
  }
  if (data.whole_day_days) {
    panel.append(brainHealthRow(
      `${data.whole_day_days} Tage loesen nur als ganzer Tag auf — dort gilt ein Hashtag`
      + ' fuer 24 Stunden statt fuer einen Eintrag.', 'warn',
    ));
  }
  if (data.empty_days) {
    panel.append(brainHealthRow(`${data.empty_days} Tage ergeben keinen einzigen Block.`, 'warn'));
  }
  const ignored = data.ignored || { count: 0, files: [] };
  if (ignored.count) {
    panel.append(brainHealthRow(
      `${ignored.count} Markdown-Dateien im Datenordner werden nicht indexiert —`
      + ' sie tauchen weder in der Suche noch bei den Hashtags auf:', 'warn',
    ));
    const list = document.createElement('ul');
    list.className = 'ml-4 list-disc text-gray-500';
    ignored.files.forEach((file) => {
      const item = document.createElement('li');
      item.textContent = file;
      list.append(item);
    });
    if (ignored.count > ignored.files.length) {
      const rest = document.createElement('li');
      rest.textContent = `… und ${ignored.count - ignored.files.length} weitere`;
      list.append(rest);
    }
    panel.append(list);
  } else {
    panel.append(brainHealthRow('Alle Markdown-Dateien im Datenordner werden indexiert.'));
  }
}

async function loadBrainHealth() {
  const panel = brainElement('brain-health');
  if (!panel) return;
  panel.classList.remove('hidden');
  panel.textContent = 'Wird geprueft…';
  const query = new URLSearchParams();
  const start = brainElement('brain-tag-start')?.value || '';
  const end = brainElement('brain-tag-end')?.value || '';
  if (start) query.set('start', start);
  if (end) query.set('end', end);
  try {
    const response = await apiFetch(`/api/brain/diagnostics?${query.toString()}`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      panel.textContent = data.error || `Diagnose fehlgeschlagen (HTTP ${response.status}).`;
      return;
    }
    renderBrainHealth(panel, data);
  } catch (error) {
    panel.textContent = `Netzwerkfehler bei der Diagnose: ${error?.message || error}`;
  }
}

brainElement('brain-health-check')?.addEventListener('click', loadBrainHealth);
