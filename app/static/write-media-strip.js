/* Die faltbare Anhang-Leiste im Schreiben-Tab.

   Anhaenge kosten vor allem mobil viel Hoehe. Sie liegen darum in einer
   faltbaren Huelle: eingeklappt bleibt nur eine kleine Pille mit der
   Zusammenfassung, ausgeklappt die seitlich scrollbare Flaeche. Die Leiste
   rendert nur - wem ein Anhang gehoert und was beim Klick passiert, bleibt in
   `write-session.js`.

   Unter der Leiste haengt die Lesefassung eines Transkripts: im seitlich
   scrollenden Chip ist der Text nur abgeschnitten zu sehen, gelesen und
   geprueft werden will er aber ganz - und zwar schon vor dem Senden. */
(function () {
  const collapsedKey = 'journl.write.attachments-collapsed';

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value || '');
    return element.innerHTML;
  }

  function mediaUrl(id, variant) {
    return `/api/write-sessions/media/${encodeURIComponent(id)}/${variant}`;
  }

  // Eine Zeile, die auch eingeklappt sagt, was mitgeht.
  function summaryText(items, uploadingLabel) {
    const counts = { image: 0, audio: 0, document: 0 };
    items.forEach((item) => { counts[item.type in counts ? item.type : 'document'] += 1; });
    const parts = [];
    if (counts.image) parts.push(`${counts.image} ${counts.image === 1 ? 'Foto' : 'Fotos'}`);
    if (counts.audio) parts.push(`${counts.audio} ${counts.audio === 1 ? 'Sprachnachricht' : 'Sprachnachrichten'}`);
    if (counts.document) parts.push(`${counts.document} ${counts.document === 1 ? 'Dokument' : 'Dokumente'}`);
    if (uploadingLabel) parts.push(`${uploadingLabel} wird angehängt…`);
    return parts.join(' · ') || 'Anhänge';
  }

  // Fuehrt zur Lesefassung unter der Leiste statt zu einem breiteren Chip.
  function transcriptButton(item, openTranscript) {
    const open = openTranscript === item.id;
    return `<button type="button" data-transcript-view="${escapeHtml(item.id)}" class="mt-1 block ${open ? 'text-gray-300' : 'text-sky-300'} hover:underline">`
      + `${open ? 'Transkript schließen' : 'Transkript lesen'}</button>`;
  }

  function transcribeBlock(item, { transcribing, openTranscript }) {
    if (item.status !== 'ready') return '';
    if (transcribing.has(item.id)) return '<span class="mt-1 block text-gray-500">Wird transkribiert…</span>';
    if (item.transcription_status === 'completed' && item.transcript_text) {
      return `<span class="mt-1 block max-w-44 truncate text-gray-400" title="${escapeHtml(item.transcript_text)}">„${escapeHtml(item.transcript_text)}“</span>`
        + transcriptButton(item, openTranscript);
    }
    const failed = item.transcription_status === 'error';
    const label = failed ? 'Transkription fehlgeschlagen – erneut versuchen' : 'Transkribieren';
    return '<select data-whisper-model aria-label="Modell für die Transkription" class="mt-1 block w-44 max-w-[50vw] rounded border border-gray-700 bg-gray-800 px-1 py-0.5 text-xs text-gray-300"></select>'
      + `<button type="button" data-transcribe-media="${escapeHtml(item.id)}" class="mt-1 block ${failed ? 'text-amber-400' : 'text-green-400'} hover:underline">${label}</button>`;
  }

  function chip(item, { sourceEditMode, transcribing, openTranscript }) {
    const status = item.status === 'ready'
      ? (sourceEditMode ? 'Wird beim Speichern übernommen' : 'Wird beim Senden übernommen')
      : item.status === 'failed' ? 'Upload fehlgeschlagen – bitte entfernen und neu aufnehmen' : 'Wird noch gesichert';
    const remove = `<button type="button" data-remove-media="${escapeHtml(item.id)}" class="self-start rounded px-1 text-gray-500 hover:bg-red-950 hover:text-red-300" aria-label="Anhang entfernen">×</button>`;
    if (item.type === 'image') {
      // Thumbnail left, title and status right: the chip stays as flat as the
      // preview itself so the editor above keeps its height.
      return `<div class="flex shrink-0 items-center gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-1.5 pr-1"><button type="button" data-journal-image-viewer="${mediaUrl(item.id, 'original')}" data-journal-image-alt="Angehängtes Foto" class="shrink-0 cursor-zoom-in rounded" aria-label="Foto angehängt – in der App öffnen"><img class="h-12 w-12 rounded object-cover" src="${mediaUrl(item.id, 'preview')}" alt="Angehängtes Foto"></button><span class="min-w-0"><label class="block"><span class="sr-only">Titel oder Hashtag des Fotos</span><input type="text" data-media-title="${escapeHtml(item.id)}" value="${escapeHtml(item.title || '')}" maxlength="160" placeholder="Foto benennen oder #hashtag" class="w-64 max-w-[58vw] rounded bg-transparent px-1 py-0.5 text-sm font-medium text-gray-200 placeholder:text-gray-500 hover:bg-gray-800 focus:bg-gray-800 focus:outline-none focus:ring-1 focus:ring-green-500" aria-label="Titel oder Hashtag des Fotos"></label><span class="block px-1 text-gray-500">${escapeHtml(status)}</span></span>${remove}</div>`;
    }
    if (item.type === 'audio') {
      const player = item.status === 'ready'
        ? `<audio controls preload="metadata" class="mt-1 h-7 max-w-48" src="${mediaUrl(item.id, 'original')}"></audio>` : '';
      return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><span><label class="block"><span class="sr-only">Titel der Sprachnachricht</span><input type="text" data-media-title="${escapeHtml(item.id)}" value="${escapeHtml(item.title || '')}" maxlength="160" placeholder="Sprachnachricht benennen" class="w-44 max-w-[50vw] rounded bg-transparent px-1 py-0.5 text-sm font-medium text-gray-200 placeholder:text-gray-500 hover:bg-gray-800 focus:bg-gray-800 focus:outline-none focus:ring-1 focus:ring-green-500" aria-label="Titel der Sprachnachricht"></label><span class="block text-gray-500">${escapeHtml(status)}</span>${player}${transcribeBlock(item, { transcribing, openTranscript })}</span>${remove}</div>`;
    }
    return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><a href="${mediaUrl(item.id, 'original')}" target="_blank"><strong class="block font-medium text-gray-300">Dokument</strong><span class="block max-w-48 truncate text-gray-500">${escapeHtml(item.original_filename || 'Datei öffnen')}</span><span class="block text-gray-500">${escapeHtml(status)}</span></a>${remove}</div>`;
  }

  /** Haengt Huelle und Leiste hinter `anchor` und liefert ihr Render-Handle. */
  function mount(anchor) {
    if (!anchor) return null;
    const shell = document.createElement('div');
    shell.id = 'write-media-shell';
    // Barely any padding below: the shell should sit close to the action row so
    // the editor keeps the space instead of an empty gap above the send button.
    shell.className = 'hidden flex-none w-full max-w-5xl mx-auto px-4 pt-1 pb-0';
    shell.setAttribute('role', 'status');
    shell.setAttribute('aria-live', 'polite');
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.id = 'write-media-toggle';
    toggle.className = 'inline-flex max-w-full items-center gap-1.5 rounded-full border border-gray-700/50 bg-gray-900/60 py-1 pl-2.5 pr-2 text-xs text-gray-400 hover:border-gray-600 hover:text-gray-200';
    const strip = document.createElement('div');
    strip.id = 'write-media-strip';
    strip.className = 'hidden flex flex-none flex-nowrap w-full gap-2 overflow-x-auto pt-1 pb-0 text-xs text-gray-400';
    toggle.setAttribute('aria-controls', strip.id);
    const reader = document.createElement('div');
    reader.id = 'write-media-transcript';
    reader.className = 'hidden mt-1 rounded-lg border border-gray-700 bg-gray-900/60 px-2 py-1.5';
    shell.append(toggle, strip, reader);
    anchor.insertAdjacentElement('afterend', shell);

    let collapsed = false;
    try { collapsed = window.localStorage.getItem(collapsedKey) === '1'; } catch (_) { collapsed = false; }
    let renderedCount = 0;
    let state = {
      sessionMedia: [], sourceMedia: [], uploadingLabel: '', sourceEditMode: false,
      transcribing: new Set(), openTranscript: null,
    };

    function renderToggle(items) {
      const failed = items.filter(item => item.status === 'failed').length;
      const securing = items.some(item => item.status && item.status !== 'ready' && item.status !== 'failed');
      const summary = summaryText(items, state.uploadingLabel);
      // Auch eingeklappt muss sichtbar bleiben, wenn ein Upload haengt.
      const hint = failed ? ` · ${failed} fehlgeschlagen` : securing ? ' · wird gesichert' : '';
      const chevron = `<svg viewBox="0 0 24 24" class="h-3.5 w-3.5 shrink-0 transition-transform${collapsed ? '' : ' rotate-180'}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`;
      toggle.innerHTML = `${window.journlIcon?.('paperclip', 'h-3.5 w-3.5') || ''}<span class="truncate">${escapeHtml(summary + hint)}</span>${chevron}`;
      toggle.classList.toggle('text-amber-300', Boolean(failed));
      toggle.classList.toggle('border-amber-800/70', Boolean(failed));
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      const action = collapsed ? 'ausklappen' : 'einklappen';
      toggle.setAttribute('aria-label', `Anhänge ${action} – ${summary}${hint}`);
      toggle.title = `Anhänge ${action}`;
    }

    /* Die Lesefassung des gerade geoeffneten Transkripts: vollstaendig,
       umgebrochen und bei Bedarf scrollbar, damit der Editor darueber nicht
       zusammenschrumpft. Sie bleibt Anzeige - in den Text kopiert das
       Transkript nur „Text einfuegen“ an einer abgelegten Aufnahme, sonst
       stuende es zweimal im Eintrag. */
    function renderReader(persisted, media) {
      const id = state.openTranscript;
      const item = id ? persisted.concat(media).find((candidate) => candidate.id === id) : null;
      const text = item && typeof item.transcript_text === 'string' ? item.transcript_text.trim() : '';
      if (id && !text) state.openTranscript = null;
      const visible = Boolean(text) && !collapsed;
      reader.classList.toggle('hidden', !visible);
      if (!visible) { reader.innerHTML = ''; return; }
      const label = String(item.title || '').trim() || 'Sprachnachricht';
      const stored = persisted.some((candidate) => candidate.id === id);
      reader.innerHTML = '<div class="flex items-start gap-2">'
        + `<strong class="min-w-0 flex-1 truncate text-sm font-medium text-gray-200">Transkript: ${escapeHtml(label)}</strong>`
        + '<button type="button" data-transcript-close class="shrink-0 rounded px-1 text-gray-500 hover:bg-gray-800 hover:text-gray-200" aria-label="Transkript schließen">×</button>'
        + '</div>'
        + `<div data-transcript-text class="mt-1 max-h-[30vh] overflow-y-auto whitespace-pre-wrap break-words text-sm leading-relaxed text-gray-200">${escapeHtml(text)}</div>`
        + `<span class="mt-1 block text-gray-500">${stored ? 'Steht im Eintrag unter der Aufnahme.' : 'Wird beim Senden unter die Aufnahme geschrieben.'}</span>`;
    }

    function render(next = {}) {
      state = { ...state, ...next };
      const media = state.sessionMedia || [];
      const persisted = state.sourceMedia || [];
      const items = persisted.concat(media);
      const visible = items.length > 0 || Boolean(state.uploadingLabel);
      shell.classList.toggle('hidden', !visible);
      strip.classList.toggle('hidden', !visible || collapsed);
      renderToggle(items);
      if (!visible || collapsed) {
        // Eingeklappt bleibt die Flaeche leer: so laedt das Handy keine
        // Vorschauen und keine Audiodaten fuer unsichtbare Anhaenge.
        strip.innerHTML = '';
        renderReader(persisted, media);
        renderedCount = media.length;
        return;
      }
      const pending = state.uploadingLabel
        ? `<div class="shrink-0 rounded-lg border border-gray-700 bg-gray-900/60 px-3 py-2">${escapeHtml(state.uploadingLabel)} wird angehängt…</div>`
        : '';
      strip.innerHTML = persisted.map(item => window.journlJournalMedia?.chip(item, { openTranscript: state.openTranscript }) || '').join('')
        + pending
        + media.map(item => chip(item, {
          sourceEditMode: state.sourceEditMode,
          transcribing: state.transcribing || new Set(),
          openTranscript: state.openTranscript,
        })).join('');
      renderReader(persisted, media);
      // A new attachment lands at the end of the horizontally scrolling strip;
      // only a growing list scrolls, so renaming keeps the current position.
      if (media.length > renderedCount) strip.scrollLeft = strip.scrollWidth;
      renderedCount = media.length;
      window.journlWhisperModels?.fill(strip);
    }

    // Der Lesebutton sitzt in beiden Chip-Sorten, die Lesefassung gehoert aber
    // der Huelle: darum hoert sie hier zu und nicht in `write-session.js`.
    shell.addEventListener('click', (event) => {
      if (event.target.closest('[data-transcript-close]')) {
        state.openTranscript = null;
        render();
        return;
      }
      const view = event.target.closest('[data-transcript-view]');
      if (!view) return;
      const id = view.dataset.transcriptView;
      state.openTranscript = state.openTranscript === id ? null : id;
      render();
    });

    toggle.addEventListener('click', () => {
      collapsed = !collapsed;
      try { window.localStorage.setItem(collapsedKey, collapsed ? '1' : '0'); } catch (_) { /* private mode */ }
      render();
      if (!collapsed) strip.scrollLeft = 0;
    });

    /* Nach einer fertigen Transkription oeffnet der Schreiben-Tab die
       Lesefassung von selbst - wer gerade transkribiert hat, will den Text
       sehen, nicht erst danach suchen. */
    function showTranscript(mediaId) {
      if (!mediaId) return;
      state.openTranscript = mediaId;
      // Aufklappen gilt nur fuer diesen Moment: gemerkt wird weiterhin nur, was
      // die Pille selbst umschaltet. Sonst haette ein einziges Transkript die
      // Leiste dauerhaft ausgeklappt - auf dem Handy bleibt dann selbst nach
      // einem Neustart der App kaum noch Platz zum Schreiben.
      collapsed = false;
      render();
    }

    return { shell, strip, render, showTranscript };
  }

  window.journlWriteMediaStrip = { mount };
}());
