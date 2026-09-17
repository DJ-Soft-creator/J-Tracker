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
  let lastSaveError = null;
  let recorder = null;
  let recordingStream = null;
  let recordingMedia = null;
  let chunkIndex = 0;
  let chunkChain = Promise.resolve();
  let wakeLock = null;
  let recordingStartedAt = null;
  let recordingClock = null;
  let recordingStopped = Promise.resolve();
  let resolveRecordingStopped = null;
  let suppressInput = false;
  let uploadingImage = false;
  let uploadingDocument = false;
  let sourceEditMode = false;
  // While an existing source is edited, attachments collect in their own
  // carrier session so the open draft session stays untouched.
  let editSession = null;

  function currentSession() { return sourceEditMode ? editSession : active; }
  function setCurrentSession(data) {
    if (sourceEditMode) editSession = data; else active = data;
  }

  const panel = document.createElement('div');
  panel.id = 'write-session-panel';
  panel.className = 'hidden fixed inset-0 z-50 bg-black/60 px-4 items-center justify-center';
  panel.innerHTML = '<div class="w-full max-w-md max-h-[80vh] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-4"><div class="flex items-center justify-between gap-3"><h2 class="text-sm font-semibold text-gray-200">Offene Sessions</h2><div class="flex items-center gap-1"><button type="button" data-refresh class="rounded px-2 py-1 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200" aria-label="Sessions aktualisieren" title="Sessions aktualisieren">Aktualisieren</button><button type="button" data-close class="text-gray-400 px-2">×</button></div></div><div data-list class="mt-3 space-y-2"></div><div class="mt-3 grid gap-2"><button type="button" data-new class="w-full rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-300 hover:border-gray-600 hover:text-white">Neue leere Session</button><button type="button" data-delete-all class="hidden w-full rounded-lg px-3 py-2 text-xs text-red-300/80 hover:bg-red-950/50 hover:text-red-200 disabled:cursor-wait disabled:opacity-50">Alle offenen Sessions löschen</button></div></div>';
  document.body.appendChild(panel);

  const mediaButton = document.createElement('button');
  mediaButton.type = 'button';
  mediaButton.id = 'write-media-button';
  mediaButton.className = 'write-session-action h-10 w-10 rounded-lg border border-gray-700/50 text-gray-300 hover:text-white shrink-0';
  mediaButton.textContent = '⋯';
  mediaButton.setAttribute('aria-label', 'Weitere Aktionen und Uploads');
  mediaButton.title = 'Weitere Aktionen und Uploads';
  const recordingIdleMarkup = '<svg aria-hidden="true" viewBox="0 0 24 24" class="mx-auto h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8.5" y="3" width="7" height="12" rx="3.5"></rect><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M9 21h6"></path></svg>';
  const recordingStopMarkup = '<svg aria-hidden="true" viewBox="0 0 24 24" class="mx-auto h-4 w-4" fill="currentColor"><rect x="5.5" y="5.5" width="13" height="13" rx="2.5"></rect></svg>';
  const recordingButton = document.createElement('button');
  recordingButton.type = 'button';
  recordingButton.id = 'write-recording-button';
  recordingButton.className = 'write-session-action h-10 w-10 rounded-lg border border-gray-700/50 text-gray-300 hover:border-gray-600 hover:text-white shrink-0';
  recordingButton.innerHTML = recordingIdleMarkup;
  recordingButton.setAttribute('aria-label', 'Sprachnachricht aufnehmen');
  recordingButton.title = 'Sprachnachricht aufnehmen';
  // Timer-Pille schwebt dezent ueber dem Button; der Button zeigt waehrend der
  // Aufnahme ein zentriertes Stop-Symbol auf rotem Grund.
  const recordingWrap = document.createElement('div');
  recordingWrap.id = 'write-recording-wrap';
  recordingWrap.className = 'relative shrink-0';
  const recordingTimer = document.createElement('span');
  recordingTimer.id = 'write-recording-timer';
  recordingTimer.className = 'pointer-events-none absolute left-1/2 -translate-x-1/2 hidden items-center gap-1 whitespace-nowrap rounded-full border border-red-700/60 bg-red-950/95 px-2.5 py-0.5 text-xs font-medium tabular-nums tracking-wide text-red-300 shadow-lg shadow-red-950/40';
  recordingTimer.style.bottom = 'calc(100% - 0.5rem)';
  recordingTimer.innerHTML = '<span class="h-1.5 w-1.5 rounded-full bg-red-500 animate-pulse"></span><span data-time>00:00:00</span>';
  recordingWrap.append(recordingTimer, recordingButton);
  const sessionActions = document.createElement('div');
  sessionActions.className = 'write-session-actions';
  sessionButton.parentNode.insertBefore(sessionActions, sessionButton);
  sessionActions.appendChild(sessionButton);
  sessionActions.append(recordingWrap, mediaButton);

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

  // Huelle, Gruppen und Ausrichtung des "..."-Menues stehen in
  // write-media-menu.js; hier haengen nur die Eintraege und ihre Handler.
  const menu = window.journlWriteMediaMenu;
  const mediaMenu = menu.element;

  const editorFontMinimum = 2;
  const editorFontMaximum = 40;
  const editorFontStep = 2;
  const editorFontDefault = 16;
  const editorFontStorageKey = 'journl.write.editor-font-size';
  function readEditorFontSize() {
    try {
      const value = window.localStorage.getItem(editorFontStorageKey);
      const legacySizes = { small: 14, normal: 16, large: 18 };
      const parsed = Object.prototype.hasOwnProperty.call(legacySizes, value)
        ? legacySizes[value]
        : Number.parseInt(value, 10);
      return Number.isFinite(parsed) ? parsed : editorFontDefault;
    } catch (_) { return editorFontDefault; }
  }
  function applyEditorFontSize(size, announce = false) {
    const selected = Math.min(
      editorFontMaximum,
      Math.max(editorFontMinimum, Number(size) || editorFontDefault),
    );
    // Both page templates protect mobile inputs with `font-size: 16px !important`.
    // Match that priority so the user's explicit editor preference wins.
    field.style.setProperty('font-size', `${selected}px`, 'important');
    mediaMenu.querySelector('[data-font-size-label]').textContent = `${selected} px`;
    mediaMenu.querySelectorAll('[data-font-step]').forEach((button) => {
      const next = selected + Number(button.dataset.fontStep) * editorFontStep;
      button.disabled = next < editorFontMinimum || next > editorFontMaximum;
    });
    try { window.localStorage.setItem(editorFontStorageKey, String(selected)); } catch (_) { /* private mode */ }
    // The hashtag mirror measures the editor's computed typography. Give it a
    // deterministic refresh after changing the size, including on old browsers.
    field.dispatchEvent(new Event('journl:editor-metrics-change'));
    window.dispatchEvent(new Event('resize'));
    if (announce) window.showToast?.(`Schriftgröße: ${selected} px`);
    return selected;
  }
  let editorFontSize = applyEditorFontSize(readEditorFontSize());

  // Anhaenge liegen in einer faltbaren Huelle, die `write-media-strip.js`
  // besitzt; hier bleibt nur, welche Anhaenge sie zeigen soll.
  const mediaStrip = window.journlWriteMediaStrip?.mount(document.getElementById('input-area'));
  if (!mediaStrip) return;
  const strip = mediaStrip.strip;

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
    const requestOptions = { timeoutMs: 15000, ...options };
    let response;
    try {
      response = await window.apiFetch(url, requestOptions);
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error('Session-Anfrage hat zu lange gedauert. Bitte erneut versuchen.');
      }
      throw error;
    }
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
    if (sourceEditMode) {
      sessionButton.classList.remove('hidden', 'text-amber-400', 'text-green-400');
      sessionButton.classList.add('text-gray-300');
      sessionButton.textContent = '⋯';
      sessionButton.title = 'Bearbeitungsaktionen aufklappen';
      sessionButton.setAttribute('aria-label', 'Bearbeitungsaktionen aufklappen');
      return;
    }
    const open = sessions.filter(item => item.status === 'active');
    const expired = open.some(item => item.expired);
    // The session control is also how a person starts a deliberately empty session.
    sessionButton.classList.remove('hidden');
    sessionButton.textContent = String(open.length);
    sessionButton.title = open.length ? `${open.length} offene Session${open.length === 1 ? '' : 's'}` : 'Sessions';
    sessionButton.setAttribute('aria-label', 'Sessions');
    sessionButton.classList.toggle('text-amber-400', expired);
    sessionButton.classList.toggle('text-gray-500', !expired);
    sessionButton.classList.remove('text-green-400');
  }

  function setUndoAvailability(canUndo) {
    mediaMenu.querySelector('[data-undo]').disabled = !canUndo;
  }
  setUndoAvailability(false);
  window.addEventListener('write-history-change', (event) => setUndoAvailability(Boolean(event.detail?.canUndo)));

  // Attachments an edited entry already carries; they share the strip with new
  // ones so editing an entry looks exactly like writing one.
  let sourceMedia = [];
  const transcribingMedia = new Set();

  function renderMedia() {
    mediaStrip.render({
      sessionMedia: currentSession()?.media || [],
      sourceMedia,
      uploadingLabel: uploadingImage ? 'Foto' : uploadingDocument ? 'Dokument' : '',
      sourceEditMode,
      transcribing: transcribingMedia,
    });
  }

  function renderPanel() {
    const list = panel.querySelector('[data-list]');
    const deleteAllButton = panel.querySelector('[data-delete-all]');
    const open = sessions.filter(item => item.status === 'active');
    deleteAllButton.classList.toggle('hidden', open.length === 0);
    deleteAllButton.textContent = `Alle offenen Sessions löschen (${open.length})`;
    if (!open.length) {
      list.innerHTML = '<p class="py-4 text-center text-sm text-gray-500">Keine offene Session</p>';
      return;
    }
    list.innerHTML = open.map(item => {
      const expired = item.expired;
      const decisions = expired ? '<div class="mt-2 grid grid-cols-3 gap-1"><button data-action="extend" class="rounded bg-gray-800 px-2 py-1 text-xs">+1 Tag</button><button data-action="archive" class="rounded bg-gray-800 px-2 py-1 text-xs">Archivieren</button><button data-action="discard" class="rounded bg-red-950 px-2 py-1 text-xs text-red-300">Verwerfen</button></div>' : '';
      const preview = item.preview ? `<div class="mt-0.5 max-h-8 overflow-hidden text-xs leading-4 text-gray-500">${escapeHtml(item.preview)}</div>` : '';
      return `<div class="rounded-lg border ${expired ? 'border-amber-700/70' : 'border-gray-800'} p-3" data-session="${item.id}"><button type="button" data-open class="w-full text-left" ${expired ? 'disabled' : ''}><div class="text-xs ${expired ? 'text-amber-400' : 'text-gray-500'}">${expired ? 'Entscheidung erforderlich · ' : ''}${escapeHtml(formatDate(item.created_at))}</div><div class="mt-1 truncate text-sm text-gray-200">${escapeHtml(item.title || 'Ungesendeter Gedanke')}</div>${preview}<div class="text-xs text-gray-600">${item.media_count || 0} Medien</div></button>${decisions}<div class="mt-2 flex justify-end"><button type="button" data-delete-session class="rounded px-2 py-1 text-xs text-red-300 hover:bg-red-950">Löschen</button></div></div>`;
    }).join('');
  }

  async function refreshSessions({ reconcileActive = false } = {}) {
    try {
      const data = await jsonRequest('/api/write-sessions', { cache: 'no-store' });
      sessions = data.sessions || [];
      const activeStillOpen = active && sessions.some(item => item.id === active.id && item.status === 'active');
      // The draft session is not on screen during an edit; reconciling it would
      // judge the edited text as if it belonged to that session.
      if (reconcileActive && !sourceEditMode && active && !activeStillOpen) {
        const stale = active;
        clearTimeout(saveTimer);
        active = null;
        renderMedia();
        if (field.value === String(stale.content || '')) {
          resetEditor('');
        } else if (typeof window.showToast === 'function') {
          window.showToast('Session auf einem anderen Gerät abgeschlossen – deine neue Eingabe bleibt erhalten.');
        }
      }
      renderSignal();
      renderPanel();
    } catch (_) { /* Writing remains available while offline. */ }
  }

  function currentTemplate() {
    return document.getElementById('template-select')?.value || 'schnell';
  }

  async function createSession() {
    // A carrier session must stay empty: the edited text belongs to its source
    // file, never to a draft that could later be sent a second time.
    const created = await jsonRequest('/api/write-sessions', {
      method: 'POST', body: JSON.stringify(sourceEditMode
        ? { content: '', template_id: 'schnell' }
        : { content: field.value, template_id: currentTemplate() })
    });
    setCurrentSession(created);
    lastSaveError = null;
    await refreshSessions();
    renderMedia();
    return created;
  }

  async function ensureSession() {
    return currentSession() || createSession();
  }

  function persist() {
    const snapshot = field.value;
    const templateId = currentTemplate();
    saveChain = saveChain.catch(() => {}).then(async () => {
      if (!active) await createSession();
      const data = await jsonRequest(`/api/write-sessions/${encodeURIComponent(active.id)}`, {
        method: 'PUT', body: JSON.stringify({
          content: snapshot, template_id: templateId, revision: active.revision, discard_if_empty: true
        })
      });
      lastSaveError = null;
      if (data.status !== 'active') {
        active = null;
        sessions = sessions.filter(item => item.id !== data.id);
        renderSignal();
        renderPanel();
        renderMedia();
        if (field.value !== snapshot) queueSave();
        return null;
      }
      active = data;
      sessions = sessions.map(item => item.id === data.id ? data : item);
      renderSignal();
      renderPanel();
      if (field.value !== snapshot) queueSave();
      return active;
    }).catch(async (error) => {
      lastSaveError = error;
      if (error.status === 409 && error.data?.revision) {
        sessions = sessions.map(item => item.id === error.data.id ? error.data : item);
        sessionButton.classList.add('text-red-400');
      } else if (error.status === 404 && active) {
        const stale = active;
        active = null;
        sessions = sessions.filter(item => item.id !== stale.id);
        renderSignal();
        renderPanel();
        renderMedia();
        if (field.value === snapshot && !snapshot) {
          lastSaveError = null;
          resetEditor('');
        } else {
          try {
            await createSession();
            window.showToast?.('Session auf einem anderen Gerät geschlossen – deine Eingabe wurde als neue Session gesichert.');
          } catch (recreateError) {
            lastSaveError = recreateError;
            return null;
          }
        }
      }
      return active;
    });
    return saveChain;
  }

  function queueSave() {
    clearTimeout(saveTimer);
    if (sourceEditMode) return;
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
    if (!suppressInput && !sourceEditMode) queueSave();
  });

  sessionButton.addEventListener('click', async () => {
    if (sourceEditMode) {
      sessionActions.classList.toggle('is-expanded');
      return;
    }
    await refreshSessions({ reconcileActive: true });
    panel.classList.remove('hidden');
    panel.classList.add('flex');
  });
  panel.querySelector('[data-close]').addEventListener('click', () => {
    panel.classList.add('hidden'); panel.classList.remove('flex');
  });
  panel.querySelector('[data-refresh]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = 'Lädt…';
    await refreshSessions({ reconcileActive: true });
    button.textContent = 'Aktualisieren';
    button.disabled = false;
  });
  panel.querySelector('[data-new]').addEventListener('click', newBlankSession);
  panel.querySelector('[data-delete-all]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    const count = sessions.filter(item => item.status === 'active').length;
    if (!count) return;
    const message = `Alle ${count} offenen Sessions dieses Benutzers auf allen Geräten löschen?\n\nTexte, ungesendete Anhänge und laufende Aufnahmen können nicht wiederhergestellt werden. Archivierte Sessions bleiben erhalten.`;
    if (!window.confirm(message)) return;
    const editorSnapshot = field.value;
    clearTimeout(saveTimer);
    button.disabled = true;
    button.textContent = 'Sessions werden gelöscht…';
    try {
      await saveChain.catch(() => {});
      if (recorder && recorder.state !== 'inactive') await stopRecording();
      const result = await jsonRequest('/api/write-sessions', { method: 'DELETE' });
      active = null;
      sessions = sessions.filter(item => item.status !== 'active');
      renderSignal();
      renderPanel();
      renderMedia();
      if (field.value === editorSnapshot) resetEditor('');
      else queueSave();
      window.showToast?.(`${result.deleted} offene Session${result.deleted === 1 ? '' : 's'} auf allen Geräten gelöscht.`);
    } catch (error) {
      window.showToast?.(error.message || 'Sessions konnten nicht gelöscht werden.', true);
      await refreshSessions({ reconcileActive: true });
    } finally {
      button.disabled = false;
    }
  });
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

  function closeMediaMenu() { menu.close(); }
  mediaButton.addEventListener('click', () => menu.toggle(mediaButton));
  function sourceEditAiUnavailable() {
    if (!sourceEditMode) return false;
    closeMediaMenu();
    window.showToast?.('Die Hintergrund-KI ist beim Bearbeiten eines bestehenden Eintrags nicht verfügbar.', true);
    return true;
  }
  function sourceEditMediaBlocked() {
    if (!sourceEditMode || window.writeSourceEditAllowsMedia?.() !== false) return false;
    closeMediaMenu();
    window.showToast?.('Anhänge sind nur in privaten Quellen möglich.', true);
    return true;
  }
  mediaMenu.querySelector('[data-photo]').addEventListener('click', () => {
    if (sourceEditMediaBlocked()) return;
    mediaMenu.classList.add('hidden'); imageLibraryInput.click();
  });
  const backgroundAgentButton = mediaMenu.querySelector('[data-background-agent]');
  backgroundAgentButton.addEventListener('click', async () => {
    if (sourceEditAiUnavailable()) return;
    closeMediaMenu();
    const selectedId = document.getElementById('template-select')?.value;
    const selectedTemplate = window.config?.templates?.find(template => template.id === selectedId);
    const allowed = typeof window.isBackgroundAgentTemplate === 'function'
      ? window.isBackgroundAgentTemplate(selectedTemplate)
      : selectedId === 'schnell';
    if (!allowed) {
      window.showToast?.('Hintergrund-KI ist für „Schnell“ und Hashtag-Templates verfügbar.', true);
      return;
    }
    if (typeof window.handleSubmit !== 'function') {
      window.showToast?.('KI-Funktion ist noch nicht bereit. Bitte Seite neu laden.', true);
      return;
    }
    backgroundAgentButton.disabled = true;
    window.showToast?.('KI-Auftrag wird vorbereitet …');
    try {
      await window.handleSubmit({ backgroundAgent: true });
    } finally {
      backgroundAgentButton.disabled = false;
    }
  });
  mediaMenu.querySelector('[data-camera]').addEventListener('click', () => {
    if (sourceEditMediaBlocked()) return;
    mediaMenu.classList.add('hidden'); imageInput.click();
  });

  mediaMenu.querySelector('[data-document]').addEventListener('click', () => {
    if (sourceEditMediaBlocked()) return;
    closeMediaMenu(); documentInput.click();
  });
  mediaMenu.querySelector('[data-undo]').addEventListener('click', () => {
    closeMediaMenu(); window.writeEditorUndo?.();
  });
  mediaMenu.querySelectorAll('[data-font-step]').forEach((button) => {
    button.addEventListener('click', () => {
      editorFontSize = applyEditorFontSize(
        editorFontSize + Number(button.dataset.fontStep) * editorFontStep,
        true,
      );
    });
  });
  document.addEventListener('pointerdown', (event) => {
    if (!mediaMenu.contains(event.target) && !mediaButton.contains(event.target)) closeMediaMenu();
    if (!sessionActions.contains(event.target)) sessionActions.classList.remove('is-expanded');
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
      await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/images`, {
        method: 'POST', body: form, timeoutMs: 120000
      });
      setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' }));
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
      await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/documents`, {
        method: 'POST', body: form, timeoutMs: 120000
      });
      setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' }));
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
  // Wird nur noch auf ausdrueckliche Anforderung genutzt ("Text einfuegen" an
  // einer abgelegten Sprachnachricht), nicht mehr automatisch nach dem
  // Transkribieren - sonst stuende der Text zweimal im Eintrag.
  function appendTranscriptToEditor(text) {
    const block = `## Transkription:\n${text.trim()}`;
    const current = field.value;
    const separator = !current ? '' : current.endsWith('\n\n') ? '' : current.endsWith('\n') ? '\n' : '\n\n';
    replaceEditor(current + separator + block);
  }
  async function transcribeMedia(mediaId) {
    if (transcribingMedia.has(mediaId)) return;
    transcribingMedia.add(mediaId);
    renderMedia();
    try {
      const updated = await jsonRequest(`/api/write-sessions/media/${encodeURIComponent(mediaId)}/transcribe`, {
        method: 'POST', body: JSON.stringify({ model: window.journlWhisperModel?.() }), timeoutMs: 300000
      });
      const session = currentSession();
      if (session) setCurrentSession({ ...session, media: (session.media || []).map(entry => entry.id === mediaId ? updated : entry) });
      // Der Text wird bewusst nicht in den Editor kopiert: beim Senden schreibt
      // der Server ihn als eigenen, markierten Block unter die Sprachnachricht.
      // Beides zusammen hatte jeden sofort transkribierten Eintrag verdoppelt.
      // Zu lesen ist er trotzdem sofort - in der Lesefassung unter der Leiste.
      if (updated.transcription_status === 'error') window.showToast?.('Transkription fehlgeschlagen.', true);
      else if (updated.transcript_text) mediaStrip.showTranscript?.(mediaId);
    } catch (error) {
      window.showToast?.(error.message || 'Transkription fehlgeschlagen.', true);
    } finally {
      transcribingMedia.delete(mediaId);
      renderMedia();
    }
  }
  strip.addEventListener('click', async (event) => {
    const transcribeButton = event.target.closest('[data-transcribe-media]');
    if (transcribeButton) {
      transcribeMedia(transcribeButton.dataset.transcribeMedia);
      return;
    }
    const button = event.target.closest('[data-remove-media]');
    const session = currentSession();
    if (!button || !session) return;
    const item = session.media?.find(candidate => candidate.id === button.dataset.removeMedia);
    const label = item?.type === 'image' ? 'Foto' : item?.type === 'audio' ? 'Sprachnachricht' : 'Dokument';
    if (!window.confirm(`${label} wirklich entfernen? Der Anhang wird verworfen und kann nicht wiederhergestellt werden.`)) return;
    try {
      setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/media/${encodeURIComponent(item.id)}`, { method: 'DELETE' }));
      await refreshSessions();
      renderMedia();
    } catch (error) {
      if (typeof window.showToast === 'function') window.showToast(error.message || 'Anhang konnte nicht entfernt werden.', true);
    }
  });
  async function saveMediaTitle(input) {
    const session = currentSession();
    if (!session || input.disabled) return;
    const item = session.media?.find(candidate => candidate.id === input.dataset.mediaTitle);
    if (!item || !['image', 'audio', 'document'].includes(item.type)) return;
    const title = input.value.trim();
    if (title === String(item.title || '')) return;
    input.disabled = true;
    try {
      setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}/media/${encodeURIComponent(item.id)}`, {
        method: 'PUT', body: JSON.stringify({ title })
      }));
      renderMedia();
      await refreshSessions();
    } catch (error) {
      input.disabled = false;
      input.value = item.title || '';
      window.showToast?.(error.message || 'Beschreibung konnte nicht gespeichert werden.', true);
    }
  }
  strip.addEventListener('change', (event) => {
    const input = event.target.closest('[data-media-title]');
    if (input) saveMediaTitle(input);
  });
  strip.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.target.closest('[data-media-title]')) return;
    event.preventDefault();
    event.target.blur();
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
          method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: blob, timeoutMs: 30000
        });
        if (response.ok) return;
        error = new Error('Audio-Chunk konnte nicht gesichert werden');
      } catch (caught) { error = caught; }
      await new Promise(resolve => setTimeout(resolve, 500 * (attempt + 1)));
    }
    throw error || new Error('Audio-Chunk konnte nicht gesichert werden');
  }

  function recordingLabel() {
    const total = Math.max(0, Math.floor((Date.now() - recordingStartedAt) / 1000));
    const hours = String(Math.floor(total / 3600)).padStart(2, '0');
    const minutes = String(Math.floor((total % 3600) / 60)).padStart(2, '0');
    const seconds = String(total % 60).padStart(2, '0');
    return `${hours}:${minutes}:${seconds}`;
  }

  function setRecordingButton(recording) {
    recordingButton.classList.toggle('is-recording', recording);
    recordingButton.classList.toggle('bg-red-600/90', recording);
    recordingButton.classList.toggle('text-white', recording);
    recordingButton.classList.toggle('border-red-500', recording);
    recordingButton.classList.toggle('hover:bg-red-500', recording);
    recordingButton.classList.toggle('hover:border-red-400', recording);
    recordingButton.classList.toggle('border-gray-700/50', !recording);
    recordingButton.classList.toggle('text-gray-300', !recording);
    recordingButton.classList.toggle('hover:border-gray-600', !recording);
    recordingButton.classList.toggle('hover:text-white', !recording);
    recordingTimer.classList.toggle('hidden', !recording);
    recordingTimer.classList.toggle('flex', recording);
    recordingButton.setAttribute('aria-label', recording
      ? 'Sprachnachricht stoppen'
      : 'Sprachnachricht aufnehmen');
    recordingButton.title = recording ? 'Sprachnachricht stoppen' : 'Sprachnachricht aufnehmen';
    submitButton.disabled = recording;
    submitButton.classList.toggle('opacity-60', recording);
    submitButton.classList.toggle('cursor-not-allowed', recording);
    submitButton.title = recording ? 'Senden ist während einer laufenden Sprachnachricht gesperrt' : '';
    mediaMenu.querySelector('[data-audio]').textContent = recording
      ? 'Sprachnachricht stoppen'
      : 'Sprachnachricht aufnehmen';
    recordingButton.innerHTML = recording ? recordingStopMarkup : recordingIdleMarkup;
    window.writeSubmitState?.refresh();
  }

  async function stopRecording() {
    if (!recorder || recorder.state === 'inactive') return;
    const stopped = recordingStopped;
    recorder.stop();
    await stopped;
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
    setCurrentSession({ ...currentSession(), media: [...(currentSession()?.media || []), recordingMedia] });
    renderMedia();
    chunkIndex = 0;
    chunkChain = Promise.resolve();
    let chunkUploadFailed = false;
    recordingStartedAt = Date.now();
    recordingStopped = new Promise((resolve) => { resolveRecordingStopped = resolve; });
    setRecordingButton(true);
    recordingTimer.querySelector('[data-time]').textContent = recordingLabel();
    recordingClock = setInterval(() => {
      recordingTimer.querySelector('[data-time]').textContent = recordingLabel();
    }, 1000);
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
        setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' }));
      } catch (error) {
        setCurrentSession(await jsonRequest(`/api/write-sessions/${encodeURIComponent(session.id)}`, { cache: 'no-store' }).catch(() => currentSession()));
        if (typeof window.showToast === 'function') window.showToast(chunkUploadFailed ? 'Audio-Upload fehlgeschlagen. Bitte Aufnahme entfernen und neu aufnehmen.' : (error.message || 'Audioaufnahme ist unvollständig.'), true);
      }
      if (wakeLock) await wakeLock.release().catch(() => {});
      wakeLock = null; recorder = null; recordingMedia = null;
      setRecordingButton(false);
      resolveRecordingStopped?.();
      resolveRecordingStopped = null;
      renderMedia(); await refreshSessions();
    }, { once: true });
    recorder.start(10000);
  }

  async function toggleRecording() {
    if (sourceEditMediaBlocked()) return;
    closeMediaMenu();
    try { await startRecording(); } catch (_) {
      recordingStream?.getTracks().forEach(track => track.stop());
      recorder = null;
      recordingMedia = null;
      setRecordingButton(false);
    }
  }
  recordingButton.addEventListener('click', toggleRecording);
  mediaMenu.querySelector('[data-audio]').addEventListener('click', toggleRecording);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    if (recorder && recorder.state !== 'inactive' && (!wakeLock || wakeLock.released)) acquireWakeLock();
    refreshSessions({ reconcileActive: true });
  });
  window.addEventListener('pageshow', () => refreshSessions({ reconcileActive: true }));

  window.beginWriteSourceEdit = async function () {
    clearTimeout(saveTimer);
    // A recording started before the edit belongs to the draft session and has
    // to land there before the carrier session takes over.
    if (recorder && recorder.state !== 'inactive') await stopRecording();
    if (active || field.value) await flushCurrent();
    sourceEditMode = true;
    editSession = null;
    sessionActions.classList.add('is-source-edit');
    sessionActions.classList.remove('is-expanded');
    panel.classList.add('hidden');
    panel.classList.remove('flex');
    renderSignal();
    renderMedia();
  };
  window.endWriteSourceEdit = async function ({ keepMedia = false } = {}) {
    // A running recording still writes into the carrier session, so it has to
    // finish before the session may be discarded.
    if (!keepMedia && recorder && recorder.state !== 'inactive') {
      await stopRecording().catch(() => {});
    }
    const discarded = keepMedia ? null : editSession;
    sourceEditMode = false;
    editSession = null;
    sessionActions.classList.remove('is-source-edit', 'is-expanded');
    closeMediaMenu();
    renderSignal();
    renderMedia();
    if (discarded) {
      try {
        await jsonRequest(`/api/write-sessions/${encodeURIComponent(discarded.id)}`, { method: 'DELETE' });
      } catch (_) { /* The session list stays the way to clean this up. */ }
    }
    await refreshSessions();
  };
  window.setWriteSourceMedia = function (items) {
    sourceMedia = Array.isArray(items) ? items : [];
    window.journlJournalMedia?.setStripped(sourceMedia);
    renderMedia();
  };
  window.appendWriteTranscript = appendTranscriptToEditor;
  window.writeSourceEditMedia = function () {
    return { id: editSession?.id || null, count: editSession?.media?.length || 0 };
  };
  window.markWriteSourceEditMediaSubmitted = function () {
    // The server has consumed the carrier session together with the save.
    editSession = null;
    window.dispatchEvent(new Event('journl:screenshot-inbox-change'));
  };

  window.writeSessionForSubmit = async function (submittedValue) {
    clearTimeout(saveTimer);
    if (!active && !String(submittedValue || '').trim()) return null;
    if (!active) await createSession();
    if (active.content !== submittedValue) await persist();
    await saveChain.catch(() => {});
    if (lastSaveError) throw lastSaveError;
    return active?.id || null;
  };
  window.writeSessionSubmissionBlockReason = function () {
    return recordingMedia ? 'Eine Sprachnachricht läuft oder wird noch gesichert.' : '';
  };
  window.markWriteSessionSubmitted = async function () {
    clearTimeout(saveTimer);
    active = null;
    renderMedia();
    await refreshSessions();
    window.dispatchEvent(new Event('journl:screenshot-inbox-change'));
  };
  window.journlWriteSession = {
    // Der Bildschirmschoner zeigt den Aufnahmestand in seiner Infozeile, ohne
    // die Aufnahme-Bedienelemente anzufassen.
    isRecording: () => Boolean(recorder && recorder.state !== 'inactive'),
    recordingLabel: () => (recorder && recorder.state !== 'inactive' ? recordingLabel() : ''),
    recordingSeconds: () => (recorder && recorder.state !== 'inactive'
      ? Math.max(0, Math.floor((Date.now() - recordingStartedAt) / 1000)) : null),
    attachInboxItem: async function (uploadId) {
      if (sourceEditMediaBlocked()) throw new Error('Anhänge sind nur in privaten Quellen möglich.');
      const session = await ensureSession();
      setCurrentSession(await jsonRequest(`/api/screenshot-inbox/items/${encodeURIComponent(uploadId)}/attach`, {
        method: 'POST', body: JSON.stringify({ session_id: session.id })
      }));
      await refreshSessions();
      renderMedia();
      field.focus({ preventScroll: true });
      return currentSession();
    }
  };
  const transcribeButton = document.getElementById('settings-transcribe-now');
  const transcribeStatus = document.getElementById('settings-transcribe-status');
  let transcriptionPollTimer = null;
  function transcriptionTime(value) {
    if (!value) return '';
    try { return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date(value)); }
    catch (_) { return ''; }
  }
  function renderTranscriptionStatus(data) {
    if (!transcribeStatus) return;
    const time = transcriptionTime(data.completed_at || data.started_at || data.requested_at);
    const suffix = time ? ` · ${time}` : '';
    if (data.status === 'queued') transcribeStatus.textContent = `Initiiert${suffix}`;
    else if (data.status === 'running') transcribeStatus.textContent = `Transkription läuft${suffix}`;
    else if (data.status === 'completed' && data.errors) transcribeStatus.textContent = `Beendet mit ${data.errors} Fehler${data.errors === 1 ? '' : 'n'}${suffix}`;
    else if (data.status === 'completed' && data.completed) transcribeStatus.textContent = `${data.completed} Transkription${data.completed === 1 ? '' : 'en'} abgeschlossen${suffix}`;
    else if (data.status === 'completed') transcribeStatus.textContent = `Keine offene Aufnahme${suffix}`;
    else transcribeStatus.textContent = '';
  }
  async function refreshTranscriptionStatus() {
    clearTimeout(transcriptionPollTimer);
    try {
      const data = await jsonRequest('/api/write-sessions/transcriptions/status', { cache: 'no-store' });
      renderTranscriptionStatus(data);
      if (data.status === 'queued' || data.status === 'running') {
        transcriptionPollTimer = setTimeout(refreshTranscriptionStatus, 5000);
      }
    } catch (_) { /* The settings remain usable when the worker status is unavailable. */ }
  }
  if (transcribeButton) transcribeButton.addEventListener('click', async () => {
    transcribeButton.disabled = true;
    if (transcribeStatus) transcribeStatus.textContent = 'Wird initiiert…';
    try {
      const data = await jsonRequest('/api/write-sessions/transcriptions/run', { method: 'POST', body: JSON.stringify({ model: window.journlWhisperModel?.() }) });
      renderTranscriptionStatus(data);
      transcriptionPollTimer = setTimeout(refreshTranscriptionStatus, 5000);
    } catch (_) {
      if (transcribeStatus) transcribeStatus.textContent = 'nicht verfügbar';
    } finally {
      transcribeButton.disabled = false;
    }
  });

  window.addEventListener('pagehide', () => {
    clearTimeout(saveTimer);
    clearTimeout(transcriptionPollTimer);
    if (!sourceEditMode && (field.value || active?.media?.length)) persist();
    if (recorder && recorder.state === 'recording') recorder.requestData();
  });
  refreshTranscriptionStatus();
  refreshSessions();
})();
