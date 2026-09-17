/* Persisted journal media: cards for Today/Brain plus the re-transcription control.

   A submitted recording no longer belongs to a write session; its audio lives in
   the dated media tree and its transcript is a block in the Markdown. This file
   renders those from the entry itself and owns the "transcribe again with a
   better model" round trip, which the sidecar worker performs asynchronously. */
(function () {
  const MEDIA_MARKER_RE = /^<!--\s*jt:media\s+(\{.*\})\s*-->$/;
  const TRANSCRIPT_MARKER_RE = /^<!--\s*jt:transcript\s+\{"media_id":"([^"]+)"\}\s*-->$/;
  const HEADING_RE = /^#{1,6}\s+Transkript\b/;
  // Anything that belongs to the entry again rather than to the transcript.
  const TEXT_END_RE = /^(?:<!--|~~.*~~$|_{3,}$|#{1,6}\s)/;
  const LABEL_RE = /^(?:Foto|Sprachnachricht|Dokument|Anhang)\b.*\bAufnahmezeit:/;

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value == null ? '' : value);
    return element.innerHTML;
  }

  function formatDate(value) {
    try { return new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)); }
    catch (_) { return value || ''; }
  }

  function endsText(line) {
    // In the packed layout the media label sits *behind* the transcript text,
    // so it terminates the block just like a blank line does.
    const stripped = line.trim();
    return !stripped || TEXT_END_RE.test(stripped) || LABEL_RE.test(stripped);
  }

  // Mirrors app/transcript_blocks.py: the two historic layouts (blank-line
  // separated and tightly packed) both have to be recognised.
  function transcriptRange(lines, mediaId) {
    for (let index = 0; index < lines.length; index += 1) {
      const match = lines[index].trim().match(TRANSCRIPT_MARKER_RE);
      if (!match || match[1] !== mediaId) continue;
      let cursor = index + 1;
      const afterBlank = cursor < lines.length && !lines[cursor].trim() ? cursor + 1 : cursor;
      if (afterBlank < lines.length && HEADING_RE.test(lines[afterBlank].trim())) {
        cursor = afterBlank + 1;
        if (cursor < lines.length && !lines[cursor].trim()) cursor += 1;
        while (cursor < lines.length && !endsText(lines[cursor])) cursor += 1;
      }
      return [index, cursor];
    }
    return null;
  }

  function transcriptText(lines, mediaId) {
    const range = transcriptRange(lines, mediaId);
    if (!range) return '';
    return lines.slice(range[0] + 1, range[1])
      .filter((line) => !HEADING_RE.test(line.trim()))
      .join('\n').trim();
  }

  /* Split one journal entry into its editable text and its trailing media
     region. Markers are always appended behind the text, so the tail can be
     carried through an edit verbatim instead of being retyped. */
  function splitMediaTail(text) {
    const lines = String(text || '').split('\n');
    const first = lines.findIndex((line) => MEDIA_MARKER_RE.test(line.trim()));
    if (first === -1) return { head: String(text || ''), tail: '', items: [] };
    const tailLines = lines.slice(first);
    const items = [];
    for (const line of tailLines) {
      const match = line.trim().match(MEDIA_MARKER_RE);
      if (!match) continue;
      try {
        const marker = JSON.parse(match[1]);
        if (marker && marker.id) items.push({ ...marker, transcript_text: transcriptText(tailLines, marker.id) });
      } catch (_) { /* A damaged marker stays untouched in the tail. */ }
    }
    return { head: lines.slice(0, first).join('\n').replace(/\s+$/, ''), tail: tailLines.join('\n'), items };
  }

  function mediaUrl(item, variant = 'original') {
    if (item.media_url) return item.media_url;
    if (item.media_path) return `/api/write-sessions/media/final?path=${encodeURIComponent(item.media_path)}`;
    return `/api/write-sessions/media/${encodeURIComponent(item.id)}/${variant}`;
  }

  /* The re-transcription control, laid out for whichever surface asks for it:
     `block` stacks inside a narrow strip chip, otherwise it sits in one row. */
  function retranscribeControls(item, layout) {
    const id = escapeHtml(item.id);
    const stacked = layout === 'block';
    const select = `<select data-whisper-model aria-label="Modell für die Transkription" class="${stacked ? 'mt-1 block w-44 max-w-[50vw] ' : ''}rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-xs text-gray-300"></select>`;
    const button = `<button type="button" data-retranscribe-media="${id}" class="${stacked ? 'mt-1 block text-green-400 hover:underline' : 'rounded border border-gray-700 px-2 py-0.5 text-green-400 hover:bg-gray-800'} text-xs">Neu transkribieren</button>`;
    const status = `<span data-retranscribe-status="${id}" class="${stacked ? 'block ' : ''}text-xs text-gray-500" role="status"></span>`;
    return stacked ? select + button + status
      : `<div class="mt-1 flex flex-wrap items-center gap-2">${select}${button}${status}</div>`;
  }

  function audioCard(item, options) {
    // The entry text already carries the transcript, so the card only needs a
    // player — and a modest one, not the full column width.
    const player = `<audio controls preload="metadata" class="h-9 w-full" src="${mediaUrl(item)}"></audio>`;
    return `<div class="w-full max-w-sm">${player}${options.retranscribe ? retranscribeControls(item, 'row') : ''}</div>`;
  }

  /* Compact strip chip for the editor — deliberately the same shape as the
     chips a new recording gets in `write-session.js`, so editing an entry looks
     like writing one. The chip only previews the transcript; "Transkript lesen"
     opens it in full below the strip, and pulling it into the text stays an
     explicit decision, exactly as it is during creation. */
  function chip(item, options = {}) {
    const label = { image: 'Foto', audio: 'Sprachnachricht', document: 'Dokument' }[item.type] || 'Anhang';
    const title = escapeHtml(item.title || item.original_filename || label);
    const head = `<span class="block max-w-48 truncate text-sm font-medium text-gray-200" title="${title}">${title}</span>`
      + '<span class="block text-gray-500">Im Eintrag gespeichert</span>';
    if (item.type === 'image') {
      return `<div class="flex shrink-0 items-center gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-1.5"><button type="button" data-journal-image-viewer="${escapeHtml(mediaUrl(item))}" data-journal-image-alt="${title}" class="shrink-0 cursor-zoom-in rounded" aria-label="Foto in der App öffnen"><img class="h-12 w-12 rounded object-cover" src="${mediaUrl(item, 'preview')}" alt="${title}"></button><span class="min-w-0">${head}</span></div>`;
    }
    if (item.type !== 'audio') {
      return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><a href="${mediaUrl(item)}" target="_blank" class="block">${head}</a></div>`;
    }
    const text = typeof item.transcript_text === 'string' ? item.transcript_text : '';
    const open = options.openTranscript === item.id;
    const preview = text
      ? `<span class="mt-1 block max-w-48 truncate text-gray-400" title="${escapeHtml(text)}">„${escapeHtml(text)}“</span>`
        + `<button type="button" data-transcript-view="${escapeHtml(item.id)}" class="mt-1 block text-xs ${open ? 'text-gray-300' : 'text-sky-300'} hover:underline">${open ? 'Transkript schließen' : 'Transkript lesen'}</button>`
        + `<button type="button" data-insert-transcript="${escapeHtml(item.id)}" class="mt-1 block text-xs text-sky-300 hover:underline">Text einfügen</button>`
      : '<span class="mt-1 block text-gray-500">Noch kein Transkript</span>';
    return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><span>${head}`
      + `<audio controls preload="metadata" class="mt-1 h-7 max-w-48" src="${mediaUrl(item)}"></audio>`
      + `${preview}${retranscribeControls(item, 'block')}</span></div>`;
  }

  /* Attachments below an entry stay deliberately small: the entry text is what
     the Today tab is for. A photo opens full size in the viewer on tap. */
  window.renderJournalMedia = function (media, options = {}) {
    if (!Array.isArray(media) || !media.length) return '';
    const html = `<div class="mt-3 flex flex-wrap items-start gap-2">${media.map((item) => item.integrity_status === 'unverified'
      ? '<div class="rounded-lg border border-amber-800 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">Medienanhang ist nicht verifizierbar.</div>'
      : item.type === 'image'
      ? `<button type="button" data-journal-image-viewer="${escapeHtml(mediaUrl(item))}" data-journal-image-alt="Foto ${escapeHtml(formatDate(item.captured_at))}" class="shrink-0 cursor-zoom-in" aria-label="Foto in der App öffnen"><img loading="lazy" class="h-28 w-28 rounded-lg border border-gray-800 object-cover" src="${mediaUrl(item, 'preview')}" alt="Foto ${escapeHtml(formatDate(item.captured_at))}"></button>`
      : item.type === 'audio' ? audioCard(item, options)
      : `<a class="shrink-0 rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-sky-300 hover:text-sky-200" href="${mediaUrl(item)}" target="_blank">Dokument: ${escapeHtml(item.original_filename || 'Datei öffnen')}</a>`).join('')}</div>`;
    // The caller inserts this string synchronously, so the selects exist by the
    // time the catalogue arrives.
    if (options.retranscribe) {
      Promise.resolve(window.journlWhisperModels?.load()).then(() => window.journlWhisperModels?.fill()).catch(() => {});
    }
    return html;
  };

  function setStatus(mediaId, text) {
    document.querySelectorAll(`[data-retranscribe-status="${CSS.escape(mediaId)}"]`)
      .forEach((element) => { element.textContent = text; });
  }

  let strippedMedia = [];
  let pollTimer = null;
  function pollUntilDone(mediaId) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const response = await window.apiFetch('/api/write-sessions/transcriptions/status', { cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (data.status === 'queued' || data.status === 'running') {
          setStatus(mediaId, data.status === 'running' ? 'läuft…' : 'eingereiht…');
          pollUntilDone(mediaId);
          return;
        }
        setStatus(mediaId, data.errors ? 'fehlgeschlagen' : 'fertig');
        if (data.errors) return;
        // The worker rewrote the journal, so anything showing that entry is
        // stale. An open edit is rebased in place instead of being thrown away.
        await window.rebaseWriteSourceEdit?.(mediaId);
        window.loadDashboard?.();
      } catch (_) { setStatus(mediaId, ''); }
    }, 5000);
  }

  document.addEventListener('click', (event) => {
    const insert = event.target.closest('[data-insert-transcript]');
    if (!insert) return;
    const item = strippedMedia.find((candidate) => candidate.id === insert.dataset.insertTranscript);
    if (!item?.transcript_text) return;
    event.preventDefault();
    window.appendWriteTranscript?.(item.transcript_text);
  });

  document.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-retranscribe-media]');
    if (!button || button.disabled) return;
    event.preventDefault();
    const mediaId = button.dataset.retranscribeMedia;
    const editing = Boolean(window.isWriteSourceEdit?.());
    button.disabled = true;
    try {
      const response = await window.apiFetch(`/api/write-sessions/media/${encodeURIComponent(mediaId)}/retranscribe`, {
        method: 'POST', body: JSON.stringify({ model: window.journlWhisperModel?.() }), timeoutMs: 20000,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Transkription konnte nicht gestartet werden.');
      setStatus(mediaId, 'eingereiht…');
      window.showToast?.(editing
        ? 'Transkription läuft – dein Text bleibt offen, das Transkript wird nachgezogen.'
        : 'Transkription läuft – der Eintrag wird aktualisiert, sobald sie fertig ist.');
      pollUntilDone(mediaId);
    } catch (error) {
      window.showToast?.(error.message || 'Netzwerkfehler beim Starten der Transkription.', true);
    } finally {
      button.disabled = false;
    }
  });

  window.journlJournalMedia = {
    splitMediaTail, transcriptText, transcriptRange, chip,
    // The editor hands over what it stripped out of the textarea; the insert
    // button reads the transcript from here instead of from the DOM.
    setStripped: (items) => { strippedMedia = Array.isArray(items) ? items : []; },
  };
}());
