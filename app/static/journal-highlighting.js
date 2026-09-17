/* Shared, safe journal rendering for Family hashtags and AI-generated text. */
(function () {
  const HASHTAG_RE = /(?<![\w#])#([\p{L}\p{N}_-]+)/gu;
  let familyTags = new Set();
  let approvedTags = new Set();
  let proposedTags = new Set();
  let aiTags = new Set();
  let catalogRequest = null;
  let catalogLoaded = false;
  let catalogGeneration = 0;

  function normaliseTag(value) {
    return (value || '')
      .normalize('NFKD')
      .replace(/[\u0300-\u036f]/g, '')
      .trim()
      .replace(/^#/, '')
      .toLocaleLowerCase('de-DE');
  }

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value;
    return element.innerHTML;
  }

  function setFamilyTags(catalog) {
    const personal = catalog?.personal || {};
    const family = catalog?.family || {};
    familyTags = new Set([
      ...(family.canonical || []),
      ...(family.proposals || []),
      ...Object.keys(family.aliases || {}),
      ...Object.values(family.aliases || {}),
    ].map(normaliseTag).filter(Boolean));
    approvedTags = new Set([
      ...(personal.canonical || []),
      ...(family.canonical || []),
      ...Object.keys(personal.aliases || {}),
      ...Object.values(personal.aliases || {}),
      ...Object.keys(family.aliases || {}),
      ...Object.values(family.aliases || {}),
    ].map(normaliseTag).filter(Boolean));
    proposedTags = new Set([
      ...(personal.proposals || []),
      ...(family.proposals || []),
    ].map(normaliseTag).filter(Boolean));
    aiTags = new Set(Object.keys(catalog?.ai || {}).map(normaliseTag).filter(Boolean));
    catalogLoaded = true;
  }

  function loadFamilyHashtags(force = false) {
    if (force) {
      catalogGeneration += 1;
      catalogRequest = null;
      catalogLoaded = false;
    }
    if (catalogLoaded && !force) return Promise.resolve(familyTags);
    if (catalogRequest) return catalogRequest;
    const request = typeof window.apiFetch === 'function'
      ? window.apiFetch('/api/brain/tag-catalog')
      : fetch('/api/brain/tag-catalog', { credentials: 'include' });
    const requestGeneration = catalogGeneration;
    const pending = request
      .then(async (response) => {
        if (!response.ok) throw new Error('Hashtag-Katalog konnte nicht geladen werden.');
        const data = await response.json();
        if (requestGeneration === catalogGeneration) setFamilyTags(data.catalog || {});
        return familyTags;
      })
      .catch(() => familyTags)
      .finally(() => {
        if (catalogRequest === pending) catalogRequest = null;
      });
    catalogRequest = pending;
    return catalogRequest;
  }

  function clearHashtagCatalog() {
    catalogGeneration += 1;
    catalogRequest = null;
    catalogLoaded = false;
    familyTags = new Set();
    approvedTags = new Set();
    proposedTags = new Set();
    aiTags = new Set();
  }

  // Markdown is rendered line by line and stays inline (tables are the one
  // block element), so the cards keep the line breaks their
  // `whitespace-pre-wrap` container already provides.
  const MD_FENCE_RE = /^\s*(?:```|~~~)/;
  const MD_RULE_RE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const MD_HEADING_RE = /^(#{1,6})\s+(.+)$/;
  const MD_QUOTE_RE = /^\s*>\s?(.*)$/;
  const MD_TASK_RE = /^(\s*)[-*+]\s+\[([ xX])\]\s*(.*)$/;
  const MD_BULLET_RE = /^(\s*)[-*+]\s+(.+)$/;
  const MD_ORDERED_RE = /^(\s*)(\d{1,3})[.)]\s+(.+)$/;
  const MD_TABLE_ROW_RE = /^\s*\|/;
  const MD_BULLET_MARKERS = ['•', '◦', '▪', '‣'];
  const MD_LINK_TARGET_RE = /^(?:https?:\/\/|mailto:|\/)/i;
  // The date belongs to the journal file, not to the card preview. Keep the
  // time (also for completed quick entries) to retain the chronology.
  const QUICK_TIMESTAMP_RE = /^\s*(?:-\s+|~~)(?:\d{2}\.\d{2}\.\s+)?(\d{2}:\d{2}:\d{2})(?:~~)?\s*$/;

  // Sentinels that cannot occur in journal text, used to park code spans and
  // finished links so later passes never rewrite their markup.
  const CODE_MARK = '\u0000';
  const CODE_PLACEHOLDER_RE = /\u0000(\d+)\u0000/g;
  const LINK_MARK = '\u0001';
  const LINK_PLACEHOLDER_RE = /\u0001(\d+)\u0001/g;
  // Bare addresses people paste into notes: no markdown syntax, still a link.
  // The lookbehind keeps the target of a `[Text](URL)` link out of this pass —
  // that one is turned into an anchor by the markdown rule below.
  const BARE_URL_RE = /(?<!\]\()(?:https?:\/\/|www\.)[^\s<]+/g;
  // Trailing sentence punctuation belongs to the sentence, not to the URL.
  const URL_TAIL_RE = /(?:[.,;:!?]|&quot;|&#39;|&amp;)+$/;

  function anchor(target, label) {
    return `<a class="journal-md-link" href="${target.replace(/"/g, '&quot;')}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  }

  // Balanced trailing parens stay in the URL (Wikipedia links), a lone one
  // closes the sentence around it.
  function trimUrlTail(url) {
    let target = url;
    for (;;) {
      const trimmed = target.replace(URL_TAIL_RE, '');
      const unbalanced = trimmed.endsWith(')')
        && (trimmed.match(/\(/g) || []).length < (trimmed.match(/\)/g) || []).length;
      const next = unbalanced ? trimmed.slice(0, -1) : trimmed;
      if (next === target) return target;
      target = next;
    }
  }

  function renderInline(value) {
    // Code spans are parked first so emphasis inside them stays literal.
    const codeSpans = [];
    const links = [];
    const parkLink = (html) => {
      links.push(html);
      return `${LINK_MARK}${links.length - 1}${LINK_MARK}`;
    };
    let html = escapeHtml(value).replace(/`([^`\n]+)`/g, (match, code) => {
      codeSpans.push(code);
      return `${CODE_MARK}${codeSpans.length - 1}${CODE_MARK}`;
    });
    // Bare URLs are parked before the emphasis rules run, otherwise the
    // underscores inside an address would be read as italics.
    html = html.replace(BARE_URL_RE, (match) => {
      const target = trimUrlTail(match);
      if (!target) return match;
      const href = target.startsWith('www.') ? `https://${target}` : target;
      return parkLink(anchor(href, target)) + match.slice(target.length);
    });
    html = html
      .replace(/\*\*\*([^*\n]+)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/(?<![\w_])___([^_\n]+)___(?![\w_])/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(?<![\w_])__([^_\n]+)__(?![\w_])/g, '<strong>$1</strong>')
      .replace(/(?<![\w*])\*([^*\n]+)\*(?!\w)/g, '<em>$1</em>')
      .replace(/(?<![\w_])_([^_\n]+)_(?![\w_])/g, '<em>$1</em>')
      .replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
      .replace(/\[([^\]\n]*)\]\(([^)\s]+)\)/g, (match, text, target) => (MD_LINK_TARGET_RE.test(target)
        ? anchor(target, text || target)
        : match));
    html = html.replace(LINK_PLACEHOLDER_RE, (match, index) => links[index]);
    return html.replace(CODE_PLACEHOLDER_RE, (match, index) => `<code class="journal-md-code">${codeSpans[index]}</code>`);
  }

  function renderLine(line, className) {
    let rendered = '';
    let lastIndex = 0;
    HASHTAG_RE.lastIndex = 0;
    for (const match of line.matchAll(HASHTAG_RE)) {
      const prefix = renderInline(line.slice(lastIndex, match.index));
      const tag = escapeHtml(match[0]);
      const text = className ? `<span class="${className}">${prefix}</span>` : prefix;
      const normalised = normaliseTag(match[1]);
      const tagClass = aiTags.has(normalised)
        ? 'journal-ai-tag'
        : approvedTags.has(normalised)
        ? 'journal-approved-tag'
        : (normalised ? 'journal-proposed-tag' : className);
      rendered += `${text}<span class="${tagClass || ''}">${tag}</span>`;
      lastIndex = match.index + match[0].length;
    }
    const suffix = renderInline(line.slice(lastIndex));
    return rendered + (className ? `<span class="${className}">${suffix}</span>` : suffix);
  }

  function splitTableRow(line) {
    const cells = [];
    let current = '';
    const inner = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    for (let index = 0; index < inner.length; index += 1) {
      if (inner[index] === '\\' && inner[index + 1] === '|') { current += '|'; index += 1; continue; }
      if (inner[index] === '|') { cells.push(current.trim()); current = ''; continue; }
      current += inner[index];
    }
    cells.push(current.trim());
    return cells;
  }

  function tableAlignments(line) {
    const cells = splitTableRow(line);
    if (!cells.length || !cells.every(cell => /^:?-{1,}:?$/.test(cell))) return null;
    return cells.map(cell => (cell.startsWith(':') && cell.endsWith(':')
      ? 'center'
      : cell.endsWith(':') ? 'right' : cell.startsWith(':') ? 'left' : ''));
  }

  // A GFM table needs a header row plus its delimiter row; everything else
  // stays plain text so lone pipes in prose are not swallowed.
  function tableStartsAt(lines, index) {
    if (lines[index].fenced || !MD_TABLE_ROW_RE.test(lines[index].text)) return false;
    const next = lines[index + 1];
    return Boolean(next) && !next.fenced && MD_TABLE_ROW_RE.test(next.text) && tableAlignments(next.text) !== null;
  }

  function renderTable(lines, start, className) {
    const alignments = tableAlignments(lines[start + 1].text);
    const header = splitTableRow(lines[start].text);
    const columns = header.length;
    let end = start + 2;
    while (end < lines.length && !lines[end].fenced && MD_TABLE_ROW_RE.test(lines[end].text)) end += 1;

    const cell = (tag, value, column) => {
      const align = alignments[column] || '';
      return `<${tag}${align ? ` style="text-align:${align}"` : ''}>${renderLine(value || '', className)}</${tag}>`;
    };
    const row = (values, tag) => `<tr>${Array.from({ length: columns }, (_, column) => cell(tag, values[column], column)).join('')}</tr>`;
    const body = lines.slice(start + 2, end).map(line => row(splitTableRow(line.text), 'td')).join('');
    return {
      html: `<div class="journal-md-table-wrap"><table class="journal-md-table"><thead>${row(header, 'th')}</thead><tbody>${body}</tbody></table></div>`,
      block: true,
      next: end,
    };
  }

  // List markers follow the nesting level, which only the indentation of the
  // surrounding list reveals — two-space and four-space lists both work.
  function assignBulletDepths(lines) {
    const indents = [];
    for (const line of lines) {
      if (line.fenced) { indents.length = 0; continue; }
      const bullet = !QUICK_TIMESTAMP_RE.test(line.text) && MD_BULLET_RE.exec(line.text);
      if (!bullet) {
        if (line.text.trim()) indents.length = 0;
        continue;
      }
      const indent = bullet[1].replace(/\t/g, '  ').length;
      while (indents.length && indents[indents.length - 1] > indent) indents.pop();
      if (!indents.length || indents[indents.length - 1] < indent) indents.push(indent);
      line.depth = indents.length - 1;
    }
  }

  // `block` marks a rendering that already fills its own line, so the caller
  // must not add a line break around it.
  function renderMarkdownLine(line, className, depth = 0) {
    const timestamp = line.match(QUICK_TIMESTAMP_RE);
    if (timestamp) return { html: `<span class="text-gray-500">${timestamp[1]}</span>` };
    if (MD_RULE_RE.test(line)) return { html: '<span class="journal-md-rule" aria-hidden="true"></span>', block: true };
    const heading = line.match(MD_HEADING_RE);
    if (heading) {
      return { html: `<span class="journal-md-heading journal-md-h${Math.min(heading[1].length, 3)}">${renderLine(heading[2], className)}</span>` };
    }
    const quote = line.match(MD_QUOTE_RE);
    if (quote) return { html: `<span class="journal-md-quote">${renderLine(quote[1], className)}</span>` };
    const task = line.match(MD_TASK_RE);
    if (task) {
      const done = task[2].toLowerCase() === 'x';
      const text = renderLine(task[3], className);
      return { html: `${task[1]}<span class="journal-md-marker">${window.journlIcon?.(done ? 'checkbox' : 'square', 'h-3.5 w-3.5 inline align-[-0.15em]') || ''}</span> ${done ? `<span class="journal-md-done">${text}</span>` : text}` };
    }
    const bullet = line.match(MD_BULLET_RE);
    if (bullet) {
      const marker = MD_BULLET_MARKERS[Math.min(depth, MD_BULLET_MARKERS.length - 1)];
      return { html: `${bullet[1]}<span class="journal-md-marker">${marker}</span> ${renderLine(bullet[2], className)}` };
    }
    const ordered = line.match(MD_ORDERED_RE);
    if (ordered) return { html: `${ordered[1]}<span class="journal-md-marker">${ordered[2]}.</span> ${renderLine(ordered[3], className)}` };
    return { html: renderLine(line, className) };
  }

  function renderJournalText(value) {
    const lines = [];
    let insideFence = false;
    for (const raw of String(value || '').split('\n')) {
      if (/^<!--\s*(?:jt:(?:media|transcript)|journl-share-job)\b/.test(raw.trim())) continue;
      if (MD_FENCE_RE.test(raw)) { insideFence = !insideFence; continue; }
      lines.push({ text: raw, fenced: insideFence });
    }
    assignBulletDepths(lines);
    const aiHeadingIndex = lines.findIndex((line) => /^##\s+KI(?:-Antwort)?\b/i.test(line.text));
    const hasAiEntry = aiHeadingIndex !== -1;
    const responseDividerIndex = hasAiEntry
      ? lines.findIndex((line, index) => index > aiHeadingIndex && line.text.trim() === '---')
      : -1;

    const parts = [];
    let previousWasBlock = false;
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      const isAiResponse = hasAiEntry && (
        index === aiHeadingIndex
        || (responseDividerIndex === -1 && index > aiHeadingIndex)
        || (responseDividerIndex !== -1 && index >= responseDividerIndex)
      );
      const className = isAiResponse ? 'journal-ai-text' : '';
      const rendered = line.fenced
        ? { html: `<span class="journal-md-code">${escapeHtml(line.text)}</span>` }
        : tableStartsAt(lines, index)
        ? renderTable(lines, index, className)
        : renderMarkdownLine(line.text, className, line.depth || 0);
      if (index > 0 && !previousWasBlock && !rendered.block) parts.push('\n');
      parts.push(rendered.html);
      previousWasBlock = Boolean(rendered.block);
      index = rendered.next || index + 1;
    }
    return parts.join('');
  }

  window.loadFamilyHashtags = loadFamilyHashtags;
  window.setHashtagCatalog = setFamilyTags;
  window.clearHashtagCatalog = clearHashtagCatalog;
  window.hashtagApprovalStatus = (tag) => {
    const normalised = normaliseTag(tag);
    if (aiTags.has(normalised)) return 'ai';
    return approvedTags.has(normalised) ? 'approved' : 'proposed';
  };
  window.renderJournalText = renderJournalText;
}());
