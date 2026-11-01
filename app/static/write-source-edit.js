/* Edit existing Today and Brain sources inside the shared writing context. */
(function () {
  const field = document.getElementById('simple-input');
  const select = document.getElementById('template-select');
  const submitButton = document.getElementById('submit-btn');
  const cancelButton = document.getElementById('write-edit-cancel');
  if (!field || !select || !submitButton || !cancelButton) return;

  let edit = null;
  let changingTemplate = false;

  // Persisted attachments go into the shared media strip that `write-session.js`
  // owns: raw marker JSON is unreadable in a textarea, and a second container
  // below the editor would fight the mobile shell for height.
  function renderSourceMedia(items) {
    window.setWriteSourceMedia?.(items || []);
  }

  function replaceEditor(value, reset = false) {
    if (reset && typeof window.writeEditorReset === 'function') {
      window.writeEditorReset(value);
    } else if (typeof window.writeEditorReplace === 'function') {
      window.writeEditorReplace(value);
    } else {
      field.value = value;
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function selectedTemplate() {
    return window.config?.templates?.find((item) => item.id === select.value) || null;
  }

  function captureWriteState() {
    const template = selectedTemplate();
    const values = {};
    for (const item of template?.fields || []) {
      const input = document.getElementById(`field-${item.id}`);
      if (input) values[item.id] = input.value;
    }
    return { templateId: select.value, content: field.value, values };
  }

  function setTemplate(templateId) {
    changingTemplate = true;
    select.value = templateId;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    changingTemplate = false;
  }

  function restoreWriteState(snapshot) {
    if (snapshot.templateId === '__ai_mode__' && typeof window.resumeWriteAiSession === 'function') {
      window.resumeWriteAiSession(snapshot.content);
      return;
    }
    setTemplate(snapshot.templateId || 'schnell');
    replaceEditor(snapshot.content, true);
    for (const [id, value] of Object.entries(snapshot.values || {})) {
      const input = document.getElementById(`field-${id}`);
      if (input) {
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }
  }

  function framedBlock(content) {
    // Family documents carry a YAML frontmatter block (id, title, type, ...)
    // that isn't meant for hand-editing; it rides along unchanged like the
    // journal quick-entry timestamp below
    // (including the optional travel "Ortszeit" line after its end stamp).
    const frontmatter = String(content || '').match(/^(---\n[\s\S]*?\n---\n\n?)([\s\S]*)$/);
    if (frontmatter) return { body: frontmatter[2], prefix: frontmatter[1], suffix: '' };
    const quick = String(content || '').match(/^(\s*-\s*(?:\d{2}\.\d{2}\.\s+)?\d{2}:\d{2}:\d{2}\s*\n)([\s\S]*?)(\n\s*~~(?:\d{2}\.\d{2}\.\s+)?\d{2}:\d{2}:\d{2}~~(?:\n\*Ortszeit: [^\n]*\*)?\s*)$/);
    if (quick) return { body: quick[2], prefix: quick[1], suffix: quick[3] };
    const headed = String(content || '').match(/^(\s*#{1,6}\s+.*(?:Datum\s*&\s*Uhrzeit|Datum und Uhrzeit).*\n)([\s\S]*)$/i);
    if (headed) return { body: headed[2], prefix: headed[1], suffix: '' };
    return { body: String(content || ''), prefix: '', suffix: '' };
  }

  // ── Family markdown editing: hide the task/comment machine fields ──────
  // family_markdown.py stores tasks/comments as pipe-delimited lines
  // ("- [ ] id: … | title: … | user: … | target-date: …"); typing that by
  // hand is exactly the noise this editor exists to avoid. These mirror just
  // enough of that format to find section boundaries and a title/text - the
  // server stays the source of truth for parsing.
  // Nur eine Zeile mit Kaestchen ist eine Aufgabe. Alles andere unter
  // "## Aufgaben" ist Text, der unter der Liste steht - frueher wurde daraus
  // beim Speichern eine Aufgabe, und wer eine Notiz schrieb, bekam seinen Text
  // als Checkliste zurueck.
  const FAMILY_TASK_LINE_RE = /^-\s*\[[ xX]\]/;

  function familySplitSections(body) {
    const lines = String(body || '').split('\n');
    let section = null;
    const notes = [];
    const taskLines = [];
    const tailLines = [];
    const commentLines = [];
    for (const line of lines) {
      const stripped = line.trim();
      if (stripped.startsWith('## ')) {
        const header = stripped.slice(3).trim().toLowerCase();
        // Nur diese beiden Ueberschriften sind Maschinenformat. Jede andere
        // ist eine ganz normale Zwischenueberschrift im Notiztext und darf
        // nicht verschwinden.
        if (header.includes('aufgabe')) { section = 'tasks'; continue; }
        if (header.includes('kommentar')) { section = 'comments'; continue; }
      }
      if (section === 'tasks') {
        if (FAMILY_TASK_LINE_RE.test(stripped)) taskLines.push(stripped);
        else tailLines.push(line);
        continue;
      }
      if (section === 'comments') { if (stripped) commentLines.push(stripped); continue; }
      notes.push(line);
    }
    const trim = (value) => value.join('\n').replace(/^\n+|\n+$/g, '');
    return { notes: trim(notes), taskLines, tail: trim(tailLines), commentLines };
  }

  function familyPipeField(body, field) {
    const parts = body.split('|');
    if (parts.length < 4) return null;
    for (const part of parts) {
      const idx = part.indexOf(':');
      if (idx === -1) return null;
      if (part.slice(0, idx).trim().toLowerCase() === field) return part.slice(idx + 1).trim();
    }
    return null;
  }

  function familyTaskChecked(line) {
    return /^-\s*\[[xX]\]/.test(line);
  }

  function familyTaskTitle(line) {
    const m = line.match(/^-\s*\[[ xX]\]\s*(.*)$/);
    if (!m) return line;
    return familyPipeField(m[1], 'title') ?? m[1];
  }

  function familyCommentText(line) {
    const m = line.match(/^-\s*(.*)$/);
    const body = m ? m[1] : line;
    return familyPipeField(body, 'text') ?? body;
  }

  // Server body -> what the textarea shows: machine fields ride along
  // invisibly, keyed by the current title/text, and are restored on save.
  //
  // Die Ueberschriften "## Aufgaben" und "## Kommentare" bleiben dabei aussen
  // vor. Sie sind Ablagefaecher des Dateiformats, keine Gliederung des Textes -
  // sichtbar sorgten sie dafuer, dass jede unten angehaengte Zeile als
  // Kommentar gespeichert wurde und auf der Karte nie auftauchte. Der Editor
  // zeigt stattdessen einen durchgehenden Text; eine Aufgabe ist schlicht eine
  // Zeile mit Kaestchen.
  function familySimplifyBody(body) {
    const { notes, taskLines, tail, commentLines } = familySplitSections(body);
    const tasks = taskLines.map((raw) => ({ raw, title: familyTaskTitle(raw) }));
    const comments = commentLines.map((raw) => ({ raw, text: familyCommentText(raw) }));
    const out = [];
    if (notes) out.push(notes, '');
    for (const t of tasks) out.push(`- [${familyTaskChecked(t.raw) ? 'x' : ' '}] ${t.title}`);
    // Bestehende Kommentare erscheinen als der Text, der sie fuer den Leser
    // immer schon waren, und werden beim Speichern zu Schlusstext.
    const trailing = [tail, comments.map((c) => c.text).join('\n')].filter(Boolean).join('\n');
    if (trailing) {
      if (out.length) out.push('');
      out.push(trailing);
    }
    return { display: `${out.join('\n').replace(/\n+$/, '')}\n`, tasks, comments };
  }

  // Reverse: eine Zeile mit Kaestchen ist eine Aufgabe, alles andere ist Text.
  // Text vor der ersten Aufgabe wird zur Einleitung, Text danach zum
  // Schlusstext - auch Zeilen zwischen zwei Aufgaben, die dabei unter die Liste
  // rutschen. Eine Aufgabe, deren Titel unveraendert blieb, bekommt ihre
  // vollstaendige Maschinenzeile zurueck (nur der Haken wird neu gesetzt);
  // alles Neue oder Umbenannte geht als schlichte Zeile an den Reparaturmodus
  // des Servers (family_markdown.py, repair_context).
  function familyReinflateBody(editedBody, originalTasks) {
    const remainingTasks = [...(originalTasks || [])];
    const before = [];
    const after = [];
    const outTasks = [];
    for (const line of String(editedBody || '').split('\n')) {
      const stripped = line.trim();
      if (FAMILY_TASK_LINE_RE.test(stripped)) {
        const checked = familyTaskChecked(stripped);
        const title = familyTaskTitle(stripped);
        const idx = remainingTasks.findIndex((t) => t.title === title);
        if (idx === -1) outTasks.push(`- [${checked ? 'x' : ' '}] ${title}`);
        else {
          const [orig] = remainingTasks.splice(idx, 1);
          outTasks.push(orig.raw.replace(/^-\s*\[[ xX]\]/, `- [${checked ? 'x' : ' '}]`));
        }
        continue;
      }
      (outTasks.length ? after : before).push(line);
    }
    const trim = (value) => value.join('\n').replace(/^\n+|\n+$/g, '');
    const notes = trim(before);
    const tail = trim(after);
    const parts = [];
    if (notes) parts.push(notes, '');
    parts.push('## Aufgaben', ...outTasks);
    if (tail) parts.push('', tail);
    parts.push('', '## Kommentare', '');
    return parts.join('\n');
  }

  // The media region is carried through the edit verbatim; only the text above
  // it is editable, so no marker, hash or transcript can be damaged by typing.
  function editableBlock(content, source) {
    const framed = framedBlock(content);
    if (source === 'family') {
      const simplified = familySimplifyBody(framed.body);
      return { ...framed, body: simplified.display, mediaTail: '', mediaItems: [],
        familyTasks: simplified.tasks, familyComments: simplified.comments };
    }
    const split = window.journlJournalMedia?.splitMediaTail(framed.body)
      || { head: framed.body, tail: '', items: [] };
    return { ...framed, body: split.head, mediaTail: split.tail, mediaItems: split.items };
  }

  // „Bearbeiten abbrechen“ steht zusaetzlich ganz oben im „⋯“-Menue. Auf dem
  // Desktop ist es der einzige Weg: dort bleibt die Leiste bei Senden plus
  // einem Element fuer alles Weitere, der rote Knopf ist per CSS aus.
  const menu = window.journlWriteMediaMenu;
  const menuCancel = document.createElement('button');
  menuCancel.type = 'button';
  menuCancel.setAttribute('data-edit-cancel', '');
  menuCancel.className = 'hidden block w-full px-3 py-2 text-left text-sm text-red-300 hover:bg-red-950/50';
  menuCancel.textContent = 'Bearbeiten abbrechen';
  if (menu?.element) menu.element.prepend(menuCancel);
  menuCancel.addEventListener('click', () => {
    menu?.close?.();
    cancelEdit(true);
  });

  function setEditUi(active) {
    document.getElementById('tab-write')?.classList.toggle('write-source-editing', active);
    cancelButton.classList.toggle('hidden', !active);
    menuCancel.classList.toggle('hidden', !active);
    submitButton.textContent = active ? 'Speichern' : 'Senden';
    for (const option of select.options) {
      const template = window.config?.templates?.find((item) => item.id === option.value);
      option.disabled = active && (option.value === '__ai_mode__' || template?.write_to_journal === false);
    }
  }

  async function startEdit(descriptor) {
    // Declining to drop unsaved attachments must keep the running edit alive.
    if (edit && !await cancelEdit(false)) return;
    const writeState = captureWriteState();
    await window.beginWriteSourceEdit?.();
    const block = editableBlock(descriptor.editorContent, descriptor.source);
    edit = { ...descriptor, writeState, block, templateApplied: false, saving: false };
    setTemplate('schnell');
    replaceEditor(block.body, true);
    renderSourceMedia(block.mediaItems);
    setEditUi(true);
    window.switchTab?.('write');
    field.focus({ preventScroll: true });
  }

  async function openDashboardWriteEditor(editId) {
    const entry = document.getElementById(`entry-${editId}`);
    if (!entry) return;
    await startEdit({
      source: 'dashboard',
      returnTab: 'dashboard',
      editorContent: entry.dataset.content || '',
      oldContent: entry.dataset.content || '',
      filename: entry.dataset.filename || '',
      // The Heute tab may show earlier days below today, so the card carries its
      // own date; the selected date is only the fallback for older markup.
      journalDate: entry.dataset.journalDate
        || (typeof dashboardJournalDate === 'string' ? dashboardJournalDate : null),
    });
  }

  /* After the worker rewrote the transcript, only the media region of the entry
     changed. Re-reading it keeps the edit alive: the typed text stays as it is,
     and the save is matched against the entry the file now actually holds. */
  async function rebaseDashboardEdit(mediaId) {
    if (!edit || edit.source !== 'dashboard' || edit.saving) return false;
    try {
      const query = edit.journalDate ? `?date=${encodeURIComponent(edit.journalDate)}` : '';
      const response = await window.apiFetch('/api/dashboard/refresh' + query, { cache: 'no-store' });
      const entries = await response.json().catch(() => null);
      if (!response.ok || !Array.isArray(entries)) return false;
      const fresh = entries.find((item) => item.file === edit.filename
        && String(item.content || '').includes(`"id":"${mediaId}"`));
      if (!fresh || fresh.content === edit.oldContent) return Boolean(fresh);
      const block = editableBlock(fresh.content);
      edit.oldContent = fresh.content;
      edit.block = { ...edit.block, mediaTail: block.mediaTail, mediaItems: block.mediaItems };
      renderSourceMedia(block.mediaItems);
      return true;
    } catch (_) {
      return false;
    }
  }

  async function openFamilyWriteEditor(projectId) {
    try {
      const response = await window.apiFetch(`/api/family/project/${encodeURIComponent(projectId)}/raw`, { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Datei konnte nicht geöffnet werden.');
      await startEdit({
        source: 'family',
        returnTab: 'family',
        editorContent: data.content || '',
        familyProjectId: projectId,
      });
    } catch (error) {
      window.showToast?.(error.message || 'Netzwerkfehler beim Öffnen.', true);
    }
  }

  async function openBrainWriteEditor(docId, fingerprint = '') {
    try {
      const params = new URLSearchParams({ doc_id: docId, fingerprint });
      const response = await window.apiFetch(`/api/brain/document?${params.toString()}`, { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Datei konnte nicht geöffnet werden.');
      if (data.read_only) throw new Error('Diese Quelle kann nur gelesen werden.');
      const editBlock = data.source !== 'family' && data.block_start_line && data.block_end_line;
      await startEdit({
        source: 'brain',
        returnTab: 'brain',
        editorContent: editBlock ? data.block_content : data.content,
        brain: data,
        editBlock: Boolean(editBlock),
      });
    } catch (error) {
      window.showToast?.(error.message || 'Netzwerkfehler beim Öffnen.', true);
    }
  }

  function formValues(template) {
    const values = {};
    for (const item of template.fields || []) {
      const input = document.getElementById(`field-${item.id}`);
      if (input) values[item.id] = input.value;
    }
    return values;
  }

  async function editedBlockContent(mediaSessionId) {
    // The server only knows the final media paths after it has staged them, so
    // the editor just marks the spot where the attachments belong.
    const placeholder = mediaSessionId ? '\n\n<!-- jt:media-placeholder -->' : '';
    const tail = edit.block.mediaTail ? '\n\n' + edit.block.mediaTail : '';
    if (!edit.templateApplied) {
      const body = edit.source === 'family'
        ? familyReinflateBody(field.value, edit.block.familyTasks)
        : field.value;
      return edit.block.prefix + body + tail + placeholder + edit.block.suffix;
    }
    const template = selectedTemplate();
    if (!template) throw new Error('Bitte ein gültiges Template auswählen.');
    const payload = template.type === 'simple'
      ? { template_id: template.id, content: field.value }
      : { template_id: template.id, values: formValues(template) };
    const response = await window.apiFetch('/api/write/template-preview', {
      method: 'POST', body: JSON.stringify(payload),
      timeoutMs: 30000,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Template konnte nicht angewendet werden.');
    return data.content + tail + placeholder;
  }

  function replaceBrainBlock(content, startLine, endLine, replacement) {
    const lines = String(content || '').split('\n');
    lines.splice(startLine - 1, endLine - startLine + 1, ...String(replacement).split('\n'));
    return lines.join('\n');
  }

  async function saveEdit() {
    if (!edit) return false;
    // A running or unfinished voice message blocks the save inside begin(),
    // exactly as it blocks a normal submission.
    const submitToken = window.writeSubmitState?.begin({
      scope: 'edit',
      supersedeScopes: ['ai'],
      readyText: 'Speichern',
      busyText: '…',
      activeMessage: 'Speichern läuft bereits – dein Text bleibt erhalten.',
      timeoutMs: 45000,
      timeoutMessage: 'Speichern wurde automatisch freigegeben. Dein Text bleibt erhalten; bitte erneut versuchen.',
      onTimeout: () => { if (edit) edit.saving = false; },
    });
    if (!submitToken) return false;
    edit.saving = true;
    try {
      const mediaSessionId = window.writeSourceEditMedia?.().id || null;
      const content = await editedBlockContent(mediaSessionId);
      let response;
      if (edit.source === 'dashboard') {
        response = await window.apiFetch('/api/dashboard/edit', {
          method: 'POST',
          timeoutMs: 60000,
          body: JSON.stringify({
            content: edit.oldContent,
            new_content: content,
            filename: edit.filename,
            journal_date: edit.journalDate,
            write_session_id: mediaSessionId,
          }),
        });
      } else if (edit.source === 'family') {
        response = await window.apiFetch(`/api/family/project/${encodeURIComponent(edit.familyProjectId)}/raw`, {
          method: 'PUT',
          timeoutMs: 60000,
          body: JSON.stringify({ content }),
        });
      } else {
        const brain = edit.brain;
        const documentContent = edit.editBlock
          ? replaceBrainBlock(brain.content, brain.block_start_line, brain.block_end_line, content)
          : content;
        response = await window.apiFetch('/api/brain/document', {
          method: 'PUT',
          timeoutMs: 60000,
          body: JSON.stringify({
            doc_id: brain.doc_id,
            content: documentContent,
            content_hash: brain.content_hash,
            confirm_family_edit: brain.source === 'family',
            write_session_id: mediaSessionId,
          }),
        });
      }
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(response.status === 409 && !result.write_session_conflict
        ? 'Die Quelle wurde zwischenzeitlich geändert. Dein Text bleibt zur Prüfung geöffnet.'
        : (result.error || 'Speichern fehlgeschlagen.'));
      // The save consumed the carrier session; only then may the edit end
      // without discarding what was just written into the source.
      if (mediaSessionId) window.markWriteSourceEditMediaSubmitted?.();
      const returnTab = edit.returnTab;
      await finishEdit(Boolean(mediaSessionId));
      window.showToast?.('Eintrag aktualisiert');
      window.switchTab?.(returnTab);
      return true;
    } catch (error) {
      window.showToast?.(error.message || 'Netzwerkfehler beim Speichern.', true);
      return false;
    } finally {
      if (edit) {
        edit.saving = false;
      }
      window.writeSubmitState?.finish(submitToken);
    }
  }

  async function finishEdit(keepMedia = false) {
    const snapshot = edit?.writeState;
    edit = null;
    renderSourceMedia([]);
    setEditUi(false);
    if (snapshot) restoreWriteState(snapshot);
    await window.endWriteSourceEdit?.({ keepMedia });
  }

  function mediaDiscardConfirmed() {
    const count = window.writeSourceEditMedia?.().count || 0;
    if (!count) return true;
    return window.confirm(count === 1
      ? 'Der Anhang wurde noch nicht gespeichert und wird verworfen. Bearbeiten wirklich abbrechen?'
      : `Die ${count} Anhänge wurden noch nicht gespeichert und werden verworfen. Bearbeiten wirklich abbrechen?`);
  }

  async function cancelEdit(returnToSource = true) {
    if (!edit || edit.saving) return false;
    if (!mediaDiscardConfirmed()) return false;
    const returnTab = edit.returnTab;
    await finishEdit();
    if (returnToSource) window.switchTab?.(returnTab);
    return true;
  }

  select.addEventListener('change', () => {
    if (!edit || changingTemplate) return;
    edit.templateApplied = true;
    setTimeout(() => setEditUi(true), 0);
  });
  cancelButton.addEventListener('click', () => cancelEdit(true));

  const normalSubmit = window.handleSubmit;
  window.handleSubmit = function (options = {}) {
    return edit ? saveEdit() : normalSubmit(options);
  };
  window.isWriteSourceEdit = () => Boolean(edit);
  // Attachments are stored below the private user root, so a shared Family
  // source would reference media nobody else could open.
  window.writeSourceEditAllowsMedia = () => Boolean(edit)
    && (edit.source === 'dashboard' || (edit.source === 'brain' && edit.brain?.source === 'personal'));
  window.rebaseWriteSourceEdit = rebaseDashboardEdit;
  window.openDashboardWriteEditor = openDashboardWriteEditor;
  window.openBrainWriteEditor = openBrainWriteEditor;
  window.openFamilyWriteEditor = openFamilyWriteEditor;
})();
