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

  function renderLine(line, className) {
    let rendered = '';
    let lastIndex = 0;
    HASHTAG_RE.lastIndex = 0;
    for (const match of line.matchAll(HASHTAG_RE)) {
      const prefix = escapeHtml(line.slice(lastIndex, match.index));
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
    const suffix = escapeHtml(line.slice(lastIndex));
    return rendered + (className ? `<span class="${className}">${suffix}</span>` : suffix);
  }

  function renderJournalText(value) {
    const lines = String(value || '').split('\n').filter(line => !/^<!--\s*jt:(?:media|transcript)\b/.test(line.trim()));
    const aiHeadingIndex = lines.findIndex((line) => /^##\s+KI(?:-Antwort)?\b/i.test(line));
    const hasAiEntry = aiHeadingIndex !== -1;
    const responseDividerIndex = hasAiEntry
      ? lines.findIndex((line, index) => index > aiHeadingIndex && line.trim() === '---')
      : -1;

    // The date belongs to the journal file, not to the card preview. Keep
    // the time (also for completed quick entries) to retain the chronology.
    const quickTimestamp = /^\s*(?:-\s+|~~)(?:\d{2}\.\d{2}\.\s+)?(\d{2}:\d{2}:\d{2})(?:~~)?\s*$/;
    return lines.map((line, index) => {
      const timestamp = line.match(quickTimestamp);
      if (timestamp) return `<span class="text-gray-500">${timestamp[1]}</span>`;
      const isAiHeading = index === aiHeadingIndex;
      const isAiResponse = hasAiEntry && (
        isAiHeading
        || (responseDividerIndex === -1 && index > aiHeadingIndex)
        || (responseDividerIndex !== -1 && index >= responseDividerIndex)
      );
      return renderLine(line, isAiResponse ? 'journal-ai-text' : '');
    }).join('\n');
  }

  window.loadFamilyHashtags = loadFamilyHashtags;
  window.setHashtagCatalog = setFamilyTags;
  window.clearHashtagCatalog = clearHashtagCatalog;
  window.isFamilyHashtag = (tag) => familyTags.has(normaliseTag(tag));
  window.hashtagApprovalStatus = (tag) => {
    const normalised = normaliseTag(tag);
    if (aiTags.has(normalised)) return 'ai';
    return approvedTags.has(normalised) ? 'approved' : 'proposed';
  };
  window.renderJournalText = renderJournalText;
}());
