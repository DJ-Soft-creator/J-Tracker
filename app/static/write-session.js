/* Server-backed writing sessions and private media capture. */
(function () {
  const field = document.getElementById('simple-input');
  const sessionButton = document.getElementById('resume-write-session');
  const submitButton = document.getElementById('submit-btn');
  if (!field || !sessionButton || !submitButton) return;

  let sessions = [];
  let active = null;
  let saveTimer = null;
  let saveChain = Promise.resolve();
  let recorder = null;
  let recordingStream = null;
  let recordingMedia = null;
  let chunkIndex = 0;
  let chunkChain = Promise.resolve();
  let wakeLock = null;
  let recordingStartedAt = null;
  let recordingClock = null;
  let suppressInput = false;
  let uploadingImage = false;
  let uploadingDocument = false;

  const panel = document.createElement('div');
  panel.id = 'write-session-panel';
  panel.className = 'hidden fixed inset-0 z-50 bg-black/60 px-4 items-center justify-center';
  panel.innerHTML = '<div class="w-full max-w-md max-h-[80vh] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-4"><div class="flex items-center justify-between"><h2 class="text-sm font-semibold text-gray-200">Offene Sessions</h2><button type="button" data-close class="text-gray-400 px-2">×</button></div><div data-list class="mt-3 space-y-2"></div><button type="button" data-new class="mt-3 w-full rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300">Neue leere Session</button></div>';
  document.body.appendChild(panel);

  const mediaButton = document.createElement('button');
  mediaButton.type = 'button';
  mediaButton.id = 'write-media-button';
  mediaButton.className = 'write-session-action h-10 w-10 rounded-lg border border-gray-700/50 text-gray-300 hover:text-white shrink-0';
  mediaButton.textContent = '⋯';
  mediaButton.setAttribute('aria-label', 'Foto, Sprachnachricht oder Dokument hinzufügen');
  mediaButton.title = 'Weitere Aktionen und Uploads';
  const sessionActions = document.createElement('div');
  sessionActions.className = 'write-session-actions';
  sessionButton.parentNode.insertBefore(sessionActions, sessionButton);
  sessionActions.appendChild(sessionButton);
  const undoButton = document.createElement('button');
  undoButton.type = 'button';
  undoButton.id = 'write-undo-button';
  undoButton.className = 'write-session-action h-10 w-10 rounded-lg border border-gray-700/50 text-gray-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-35 shrink-0';
  undoButton.textContent = '↶';
  undoButton.setAttribute('aria-label', 'Rückgängig');
  undoButton.title = 'Rückgängig (⌘/Ctrl + Z)';
  sessionActions.append(undoButton, mediaButton);

  const imageInput = document.createElement('input');
  imageInput.type = 'file';
  imageInput.accept = 'image/*';
  imageInput.setAttribute('capture', 'environment');
  imageInput.className = 'hidden';
  document.body.appendChild(imageInput);
  const imageLibraryInput = document.createElement('input');
  imageLibraryInput.type = 'file';
  imageLibraryInput.accept = 'image/*';
  imageLibraryInput.className = 'hidden';
  document.body.appendChild(imageLibraryInput);
  const documentInput = document.createElement('input');
  documentInput.type = 'file';
  documentInput.accept = '.pdf,.txt,.md,.csv,.doc,.docx,.odt,.xls,.xlsx';
  documentInput.className = 'hidden';
  document.body.appendChild(documentInput);

  const mediaMenu = document.createElement('div');
  mediaMenu.className = 'hidden fixed z-50 rounded-lg border border-gray-700 bg-gray-900 p-1 shadow-xl';
  mediaMenu.innerHTML = '<button type="button" data-undo class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35">↶ Rückgängig</button><div class="my-1 border-t border-gray-700" role="separator"></div><button type="button" data-camera class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Foto aufnehmen</button><button type="button" data-photo class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Bild auswählen</button><button type="button" data-audio class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Sprachnachricht aufnehmen</button><button type="button" data-document class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Dokument anhängen</button>';
  document.body.appendChild(mediaMenu);

  const strip = document.createElement('div');
  strip.id = 'write-media-strip';
  strip.className = 'hidden flex flex-none flex-nowrap w-full max-w-5xl mx-auto gap-2 overflow-x-auto px-4 py-2 text-xs text-gray-400';
  strip.setAttribute('role', 'status');
  strip.setAttribute('aria-live', 'polite');
  document.getElementById('input-area').insertAdjacentElement('afterend', strip);

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value || '');
    return element.innerHTML;
  }

  function dispatchEditorInput() {
    suppressInput = true;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    suppressInput = false;
  }

  function replaceEditor(value, { history = true } = {}) {
    if (typeof window.writeEditorReplace === 'function') {
      window.writeEditorReplace(value, { history });
      return;
    }
    field.value = value;
    dispatchEditorInput();
  }

  function resetEditor(value) {
    // A session boundary must never make one session's content undoable into
    // another one; keeping them isolated also avoids accidental draft merges.
    if (typeof window.writeEditorReset === 'function') {
      window.writeEditorReset(value);
      return;
    }
    replaceEditor(value, { history: false });
  }

  function formatDate(value) {
    try { return new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)); }
    catch (_) { return value || ''; }
  }

  async function jsonRequest(url, options = {}) {
    const response = await window.apiFetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || 'Session-Aktion fehlgeschlagen');
      error.status = response.status;
      error.data = data;
      throw error;
    }
    return data;
  }

  function renderSignal() {
    const open = sessions.filter(item => item.status === 'active');
    const expired = open.some(item => item.expired);
    // The session control is also how a person starts a deliberately empty session.
    sessionButton.classList.remove('hidden');
    sessionButton.textContent = String(open.length);
    sessionButton.title = open.length ? `${open.length} offene Session${open.length === 1 ? '' : 's'}` : 'Sessions';
    sessionButton.classList.toggle('text-amber-400', expired);
    sessionButton.classList.toggle('text-gray-500', !expired);
    sessionButton.classList.remove('text-green-400');
  }

  function setUndoAvailability(canUndo) {
    undoButton.disabled = !canUndo;
    mediaMenu.querySelector('[data-undo]').disabled = !canUndo;
  }
  setUndoAvailability(false);
  window.addEventListener('write-history-change', (event) => setUndoAvailability(Boolean(event.detail?.canUndo)));
  undoButton.addEventListener('click', () => window.writeEditorUndo?.());

  function renderMedia() {
    const media = active?.media || [];
    strip.classList.toggle('hidden', media.length === 0 && !uploadingImage && !uploadingDocument);
    const pending = uploadingImage || uploadingDocument
      ? `<div class="shrink-0 rounded-lg border border-gray-700 bg-gray-900/60 px-3 py-2">${uploadingImage ? 'Foto' : 'Dokument'} wird angehängt…</div>`
      : '';
    strip.innerHTML = pending + media.map(item => {
      const status = item.status === 'ready' ? 'Wird beim Senden übernommen' : item.status === 'failed' ? 'Upload fehlgeschlagen – bitte entfernen und neu aufnehmen' : 'Wird noch gesichert';
      const remove = `<button type="button" data-remove-media="${escapeHtml(item.id)}" class="self-start rounded px-1 text-gray-500 hover:bg-red-950 hover:text-red-300" aria-label="Anhang entfernen">×</button>`;
      if (item.type === 'image') {
        const mediaId = encodeURIComponent(item.id);
        return `<div class="flex shrink-0 items-center gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-1.5 pr-1"><a class="flex items-center gap-2" href="/api/write-sessions/media/${mediaId}/original" target="_blank"><img class="h-12 w-12 rounded object-cover" src="/api/write-sessions/media/${mediaId}/preview" alt="Angehängtes Foto"><span><strong class="block font-medium text-gray-300">Foto angehängt</strong><span class="text-gray-500">${escapeHtml(status)}</span></span></a>${remove}</div>`;
      }
      if (item.type === 'audio') return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><span><strong class="block font-medium text-gray-300">Sprachnachricht</strong><span class="block text-gray-500">${escapeHtml(status)}</span>${item.status === 'ready' ? `<audio controls preload="metadata" class="mt-1 h-7 max-w-48" src="/api/write-sessions/media/${encodeURIComponent(item.id)}/original"></audio>` : ''}</span>${remove}</div>`;
      return `<div class="flex shrink-0 items-start gap-2 rounded-lg border border-gray-700 bg-gray-900/60 p-2"><a href="/api/write-sessions/media/${encodeURIComponent(item.id)}/original" target="_blank"><strong class="block font-medium text-gray-300">Dokument</strong><span class="block max-w-48 truncate text-gray-500">${escapeHtml(item.original_filename || 'Datei öffnen')}</span><span class="block text-gray-500">${escapeHtml(status)}</span></a>${remove}</div>`;
    }).join('');
  }

  function renderPanel() {
    const list = panel.querySelector('[data-list]');
    const open = sessions.filter(item => item.status === 'active');
    if (!open.length) {
      list.innerHTML = '<p class="py-4 text-center text-sm text-gray-500">Keine offene Session</p>';
      return;
    }
    list.innerHTML = open.map(item => {
      const expired = item.expired;
      const decisions = expired ? '<div class="mt-2 grid grid-cols-3 gap-1"><button data-action="extend" class="rounded bg-gray-800 px-2 py-1 text-xs">+1 Tag</button><button data-action="archive" class="rounded bg-gray-800 px-2 py-1 text-xs">Archivieren</button><button data-action="discard" class="rounded bg-red-950 px-2 py-1 text-xs text-red-300">Verwerfen</button></div>' : '';
      return `<div class="rounded-lg border ${expired ? 'border-amber-700/70' : 'border-gray-800'} p-3" data-session="${item.id}"><button type="button" data-open class="w-full text-left" ${expired ? 'disabled' : ''}><div class="text-xs ${expired ? 'text-amber-400' : 'text-gray-500'}">${expired ? 'Entscheidung erforderlich · ' : ''}${escapeHtml(formatDate(item.created_at))}</div><div class="mt-1 truncate text-sm text-gray-200">${escapeHtml(item.title || 'Ungesendeter Gedanke')}</div><div class="text-xs text-gray-600">${item.media_count || 0} Medien</div></button>${decisions}<div class="mt-2 flex justify-end"><button type="button" data-delete-session class="rounded px-2 py-1 text-xs text-red-300 hover:bg-red-950">Löschen</button></div></div>`;
    }).join('');
  }

  async function refreshSessions() {
    try {
      const data = await jsonRequest('/api/write-sessions', { cache: 'no-store' });
      sessions = data.sessions || [];
      renderSignal();
      renderPanel();
    } catch (_) { /* Writing remains available while offline. */ }
  }

  function currentTemplate() {
    return document.getElementById('template-select')?.value || 'schnell';
  }

  async function createSession() {
    active = await jsonRequest('/api/write-sessions', {
      method: 'POST', body: JSON.stringify({ content: field.value, template_id: currentTemplate() })
    });
    await refreshSessions();
    renderMedia();
    return active;
  }

  async function ensureSession() {
    return active || createSession();
  }

  function persist() {
    const snapshot = field.value;
    const templateId = currentTemplate();
    saveChain = saveChain.catch(() => {}).then(async () => {
      if (!active) await createSession();
      const data = await jsonRequest(`/api/write-sessions/${encodeURIComponent(active.id)}`, {
        method: 'PUT', body: JSON.stringify({ content: snapshot, template_id: templateId, revision: active.revision })
      });
      active = data;
      sessions = sessions.map(item => item.id === data.id ? data : item);
      renderSignal();
      renderPanel();
      if (field.value !== snapshot) queueSave();
      return active;
    }).catch((error) => {
      if (error.status === 409 && error.data?.revision) {
        sessions = sessions.map(item => item.id === error.data.id ? error.data : item);
        sessionButton.classList.add('text-red-400');
      }
      return null;
    });
    return saveChain;
  }

  function queueSave() {
    clearTimeout(saveTimer);
    if (!active && !field.value) return;
    saveTimer = setTimeout(persist, 350);
  }

  async function flushCurrent() {
    clearTimeout(saveTimer);
    if (active || field.value) await persist();
    await saveChain.catch(() => {});
  }

  async function openSession(id) {
    await flushCurrent();
    const data = await jsonRequest(`/api/write-sessions/${encodeURIComponent(id)}`, { cache: 'no-store' });
    if (data.expired) return;
    active = data;
    const select = document.getElementById('template-select');
    if (data.template_id === '__ai_mode__' && typeof window.resumeWriteAiSession === 'function') {
      resetEditor(data.content || '');
      window.resumeWriteAiSession(data.content || '');
    } else {
      if (select && Array.from(select.options).some(option => option.value === data.template_id)) {
        select.value = data.template_id;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      resetEditor(data.content || '');
      field.focus();
    }
    renderMedia();
    panel.classList.add('hidden');
    panel.classList.remove('flex');
  }

  async function newBlankSession() {
    await flushCurrent();
    active = null;
    resetEditor('');
    renderMedia();
    panel.classList.add('hidden');
    panel.classList.remove('flex');
    field.focus();
  }

  resetEditor('');
  field.addEventListener('input', () => {
    if (!suppressInput) queueSave();
  });

  sessionButton.addEventListener('click', () => {
    renderPanel();
    panel.classList.remove('hidden');
    panel.classList.add('flex');
  });
  panel.querySelector('[data-close]').addEventListener('click', () => {
    panel.classList.add('hidden'); panel.classList.remove('flex');
  });
  panel.querySelector('[data-new]').addEventListener('click', newBlankSession);
  panel.querySelector('[data-list]').addEventListener('click', async (event) => {
    const row = event.target.closest('[data-session]');
    if (!row) return;
    const action = event.target.closest('[data-action]')?.dataset.action;
    try {
      if (event.target.closest('[data-delete-session]')) {
        if (!window.confirm('Diese Session wirklich löschen? Text und ungesendete Anhänge können nicht wiederhergestellt werden.')) return;
        const deletingActive = active?.id === row.dataset.session;
        if (deletingActive) await flushCurrent();
        await jsonRequest(`/api/write-sessions/${encodeURIComponent(row.dataset.session)}`, { method: 'DELETE' });
        if (deletingActive) {
          active = null;
          resetEditor('');
          renderMedia();
          field.focus();
        }
        await refreshSessions();
      } else if (action) {
        await jsonRequest(`/api/write-sessions/${row.dataset.session}/decision`, { method: 'POST', body: JSON.stringify({ action }) });
        await refreshSessions();
      } else if (event.target.closest('[data-open]')) {
        await openSession(row.dataset.session);
      }
    } catch (_) {}
  });

  function closeMediaMenu() { mediaMenu.classList.add('hidden'); }
  function positionMediaMenu() {
    const rect = mediaButton.getBoundingClientRect();
    mediaMenu.style.left = `${Math.max(8, Math.min(window.innerWidth - 260, rect.left))}px`;
    mediaMenu.style.top = `${Math.max(8, rect.top - 164)}px`;
  }
  mediaButton.addEventListener('click', (event) => {
    if (recorder && recorder.state !== 'inactive') {
      event.preventDefault();
      closeMediaMenu();
      stopRecording();
      return;
    }
    positionMediaMenu();
    mediaMenu.classList.toggle('hidden');
  });
  mediaMenu.querySelector('[data-photo]').addEventListener('click', () => {
    mediaMenu.classList.add('hidden'); imageLibraryInput.click();
  });
  mediaMenu.querySelector('[data-camera]').addEventListener('click', () => {
    mediaMenu.classList.add('hidden'); imageInput.click();
  });

  mediaMenu.querySelector('[data-document]').addEventListener('click', () => {
    closeMediaMenu(); documentInput.click();
  });
  mediaMenu.querySelector('[data-undo]').addEventListener('click', () => {
    closeMediaMenu(); window.writeEditorUndo?.();
  });
  document.addEventListener('pointerdown', (event) => {
    if (!mediaMenu.contains(event.target) && !mediaButton.contains(event.target)) closeMediaMenu();
  });
  field.addEventListener('focus', closeMediaMenu);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') closeMediaMenu();
  });
  window.addEventListener('blur', closeMediaMenu);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMediaMenu(); });
  async function uploadSelectedImage(input) {
    const file = input.files?.[0];
    if (!file) return;
    uploadingImage = true;
    renderMedia();
    try {
      const session = await ensureSession();
      const form = new FormData();
      form.append('file', file, file.name || 'foto');
      form.append('captured_at', new Date().toISOString());
      await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/images`, { method: 'POST', body: form });
      active = await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' });
      await refreshSessions();
      field.focus({ preventScroll: true });
    } catch (_) {
      if (typeof window.showToast === 'function') window.showToast('Foto konnte nicht angehängt werden.', true);
    } finally {
      uploadingImage = false;
      renderMedia();
    }
    input.value = '';
  }
  imageInput.addEventListener('change', () => uploadSelectedImage(imageInput));
  imageLibraryInput.addEventListener('change', () => uploadSelectedImage(imageLibraryInput));

  async function uploadSelectedDocument() {
    const file = documentInput.files?.[0];
    if (!file) return;
    uploadingDocument = true;
    renderMedia();
    try {
      const session = await ensureSession();
      const form = new FormData();
      form.append('file', file, file.name || 'dokument');
      form.append('captured_at', new Date().toISOString());
      await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/documents`, { method: 'POST', body: form });
      active = await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' });
      await refreshSessions();
      field.focus({ preventScroll: true });
    } catch (error) {
      if (typeof window.showToast === 'function') window.showToast(error.message || 'Dokument konnte nicht angehängt werden.', true);
    } finally {
      uploadingDocument = false;
      renderMedia();
      documentInput.value = '';
    }
  }
  documentInput.addEventListener('change', uploadSelectedDocument);
  strip.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-remove-media]');
    if (!button || !active) return;
    const item = active.media?.find(candidate => candidate.id === button.dataset.removeMedia);
    const label = item?.type === 'image' ? 'Foto' : item?.type === 'audio' ? 'Sprachnachricht' : 'Dokument';
    if (!window.confirm(`${label} wirklich entfernen? Der Anhang wird verworfen und kann nicht wiederhergestellt werden.`)) return;
    try {
      active = await jsonRequest(`/api/write-sessions/${encodeURIComponent(active.id)}/media/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
      await refreshSessions();
      renderMedia();
    } catch (error) {
      if (typeof window.showToast === 'function') window.showToast(error.message || 'Anhang konnte nicht entfernt werden.', true);
    }
  });
  async function acquireWakeLock() {
    if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
    try { wakeLock = await navigator.wakeLock.request('screen'); } catch (_) { wakeLock = null; }
  }

  async function uploadChunk(blob, index) {
    let error;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        const response = await window.apiFetch(`/api/write-sessions/media/${encodeURIComponent(recordingMedia.id)}/chunks/${index}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: blob
        });
        if (response.ok) return;
        error = new Error('Audio-Chunk konnte nicht gesichert werden');
      } catch (caught) { error = caught; }
      await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
    throw error || new Error('Audio-Chunk konnte nicht gesichert werden');
  }

  function recordingLabel() {
    const seconds = Math.max(0, Math.floor((Date.now() - recordingStartedAt) / 1000));
    return `■ ${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
  }

  async function stopRecording() {
    if (!recorder || recorder.state === 'inactive') return;
    recorder.stop();
  }

  async function startRecording() {
    if (recorder && recorder.state !== 'inactive') return stopRecording();
    const session = await ensureSession();
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const candidates = ['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
    const mimeType = candidates.find(type => window.MediaRecorder?.isTypeSupported(type)) || '';
    recorder = mimeType ? new MediaRecorder(recordingStream, { mimeType }) : new MediaRecorder(recordingStream);
    const baseMime = (recorder.mimeType || mimeType || 'audio/webm').split(';', 1)[0];
    recordingMedia = await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/audio`, {
      method: 'POST', body: JSON.stringify({ mime_type: baseMime, captured_at: new Date().toISOString() })
    });
    chunkIndex = 0;
    chunkChain = Promise.resolve();
    let chunkUploadFailed = false;
    recordingStartedAt = Date.now();
    mediaButton.classList.add('text-red-400', 'border-red-700');
    mediaButton.textContent = recordingLabel();
    mediaButton.style.width = 'auto';
    recordingClock = setInterval(() => { mediaButton.textContent = recordingLabel(); }, 1000);
    await acquireWakeLock();
    recorder.addEventListener('dataavailable', (event) => {
      if (!event.data?.size) return;
      const index = chunkIndex++;
      chunkChain = chunkChain.catch(() => {}).then(() => uploadChunk(event.data, index)).catch(() => { chunkUploadFailed = true; });
    });
    recorder.addEventListener('stop', async () => {
      clearInterval(recordingClock);
      recordingStream?.getTracks().forEach(track => track.stop());
      await chunkChain.catch(() => {});
      try {
        await jsonRequest(`/api/write-sessions/media/${encodeURIComponent(recordingMedia.id)}/complete`, { method: 'POST', body: JSON.stringify({ chunk_count: chunkIndex }) });
        active = await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' });
      } catch (error) {
        active = await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' }).catch(() => active);
        if (typeof window.showToast === 'function') window.showToast(chunkUploadFailed ? 'Audio-Upload fehlgeschlagen. Bitte Aufnahme entfernen und neu aufnehmen.' : (error.message || 'Audioaufnahme ist unvollständig.'), true);
      }
      if (wakeLock) await wakeLock.release().catch(() => {});
      wakeLock = null; recorder = null; recordingMedia = null;
      mediaButton.textContent = '⋯'; mediaButton.style.width = '';
      mediaButton.classList.remove('text-red-400', 'border-red-700');
      renderMedia(); await refreshSessions();
    }, { once: true });
    recorder.start(10000);
  }

  mediaMenu.querySelector('[data-audio]').addEventListener('click', async () => {
    mediaMenu.classList.add('hidden');
    try { await startRecording(); } catch (_) {
      recordingStream?.getTracks().forEach(track => track.stop());
      mediaButton.textContent = '⋯'; mediaButton.style.width = '';
    }
  });
  document.addEventListener('visibilitychange', () => {
    if (recorder && recorder.state !== 'inactive' && document.visibilityState === 'visible' && (!wakeLock || wakeLock.released)) acquireWakeLock();
  });

  window.writeSessionForSubmit = async function (submittedValue) {
    clearTimeout(saveTimer);
    if (!active && !String(submittedValue || '').trim()) return null;
    if (!active) await createSession();
    if (active.content !== submittedValue) await persist();
    await saveChain.catch(() => {});
    return active?.id || null;
  };
  window.markWriteSessionSubmitted = async function () {
    clearTimeout(saveTimer);
    active = null;
    renderMedia();
    await refreshSessions();
  };
  window.renderJournalMedia = function (media) {
    if (!Array.isArray(media) || !media.length) return '';
    const mediaUrl = (item, variant = 'original') => item.media_path
      ? `/api/write-sessions/media/final?path=${encodeURIComponent(item.media_path)}`
      : `/api/write-sessions/media/${encodeURIComponent(item.id)}/${variant}`;
    return `<div class="mt-3 grid gap-2">${media.map(item => item.type === 'image'
      ? `<a href="${mediaUrl(item)}" target="_blank"><img loading="lazy" class="max-h-80 rounded-lg border border-gray-800 object-contain" src="${mediaUrl(item, 'preview')}" alt="Foto ${escapeHtml(formatDate(item.captured_at))}"></a>`
      : item.type === 'audio' ? `<audio controls preload="metadata" class="w-full" src="${mediaUrl(item)}"></audio>`
      : `<a class="rounded-lg border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-sky-300 hover:text-sky-200" href="${mediaUrl(item)}" target="_blank">Dokument: ${escapeHtml(item.original_filename || 'Datei öffnen')}</a>`).join('')}</div>`;
  };

  const transcribeButton = document.getElementById('settings-transcribe-now');
  const transcribeStatus = document.getElementById('settings-transcribe-status');
  if (transcribeButton) transcribeButton.addEventListener('click', async () => {
    transcribeButton.disabled = true;
    if (transcribeStatus) transcribeStatus.textContent = 'wird eingeplant…';
    try {
      await jsonRequest('/api/write-sessions/transcriptions/run', { method: 'POST', body: '{}' });
      if (transcribeStatus) transcribeStatus.textContent = 'eingeplant';
    } catch (_) {
      if (transcribeStatus) transcribeStatus.textContent = 'nicht verfügbar';
    } finally {
      transcribeButton.disabled = false;
    }
  });

  window.addEventListener('pagehide', () => {
    clearTimeout(saveTimer);
    if (field.value || active?.media?.length) persist();
    if (recorder && recorder.state === 'recording') recorder.requestData();
  });
  refreshSessions();
})();
