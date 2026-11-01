// AI maintenance contract: classic scripts share one global app shell.
// Preserve template load order; move whole top-level functions, never partial bodies.
    // ─── settings modal ─────────────────────────────────────────────
    const settingsModal = document.getElementById('settings-modal');
    const settingsClose = document.getElementById('settings-close');

    function openSettings() {
      settingsModal.classList.remove('hidden');
      settingsModal.classList.add('flex');
      loadProfile();
      loadDevices();
      loadSettingsTemplates();
      loadSettingsSnippets();
      const writeHintToggle = document.getElementById('settings-write-hint');
      if (writeHintToggle && !writeHintToggle.dataset.bound) {
        writeHintToggle.dataset.bound = 'true';
        writeHintToggle.addEventListener('change', async () => {
          const res = await apiFetch('/api/settings/write-hint', { method: 'POST', body: JSON.stringify({ enabled: writeHintToggle.checked }) });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { writeHintToggle.checked = !writeHintToggle.checked; showToast(data.error || 'Hinweis konnte nicht geändert werden.', true); return; }
          applyWriteHint(data.enabled);
        });
      }
      const hashtagQuickToggle = document.getElementById('settings-hashtag-quick-button');
      if (hashtagQuickToggle && !hashtagQuickToggle.dataset.bound) {
        hashtagQuickToggle.dataset.bound = 'true';
        hashtagQuickToggle.addEventListener('change', async () => {
          const res = await apiFetch('/api/settings/hashtag-quick-button', { method: 'POST', body: JSON.stringify({ enabled: hashtagQuickToggle.checked }) });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { hashtagQuickToggle.checked = !hashtagQuickToggle.checked; showToast(data.error || 'Hashtag-Schnellbutton konnte nicht geändert werden.', true); return; }
          applyHashtagQuickButton(data.enabled);
        });
      }
      const youtubeToggle = document.getElementById('settings-youtube-mode');
      if (youtubeToggle && !youtubeToggle.dataset.bound) {
        youtubeToggle.dataset.bound = 'true';
        youtubeToggle.addEventListener('change', async () => {
          const res = await apiFetch('/api/settings/youtube-mode', { method: 'POST', body: JSON.stringify({ enabled: youtubeToggle.checked }) });
          const data = await res.json().catch(() => ({}));
          if (!res.ok) { youtubeToggle.checked = !youtubeToggle.checked; showToast(data.error || 'YouTube-Mode konnte nicht geändert werden.', true); return; }
          applyYoutubeMode(data.enabled);
        });
      }
    }
    function closeSettings() {
      settingsModal.classList.add('hidden');
      settingsModal.classList.remove('flex');
    }
    window.openSettings = openSettings;
    window.closeSettings = closeSettings;
    settingsClose.addEventListener('click', closeSettings);
    settingsModal.addEventListener('click', (e) => { if (e.target === settingsModal) closeSettings(); });

    async function loadProfile() {
      try {
        const res = await apiFetch('/api/settings/profile');
        if (!res.ok) return;
        const data = await res.json();
        currentUser = data.username || currentUser;
        applyWriteHint(data.show_write_hint !== false);
        applyHashtagQuickButton(data.show_hashtag_quick_button !== false);
        document.getElementById('settings-current-username').textContent = currentUser || '–';
        document.getElementById('settings-new-username').value = '';
        const bu = document.getElementById('burger-username');
        if (bu) bu.textContent = currentUser || 'Bürger';
        const headerUser = document.getElementById('header-username');
        if (headerUser) headerUser.textContent = currentUser || '';
      } catch (e) {
        console.error('load profile failed:', e);
      }
    }

    function applyWriteHint(enabled) {
      appInfoBox?.classList.toggle('hidden', !enabled);
      const toggle = document.getElementById('settings-write-hint');
      if (toggle) toggle.checked = Boolean(enabled);
    }

    function applyHashtagQuickButton(enabled) {
      const toggle = document.getElementById('settings-hashtag-quick-button');
      if (toggle) toggle.checked = Boolean(enabled);
    }

    async function loadDevices() {
      const list = document.getElementById('settings-devices-list');
      list.innerHTML = '<p class="text-gray-500">Lade…</p>';
      try {
        const res = await apiFetch('/api/settings/devices');
        if (!res.ok) { list.innerHTML = '<p class="text-gray-500">Nicht verfügbar.</p>'; return; }
        const data = await res.json();
        const devices = data.devices || [];
        if (!devices.length) { list.innerHTML = '<p class="text-gray-500">Keine vertrauten Geräte.</p>'; return; }
        list.innerHTML = '';
        devices.forEach(d => {
          const fp = d.fingerprint || '';
          const short = fp.slice(0, 12);
          const row = document.createElement('div');
          row.className = 'flex items-center justify-between bg-gray-800 border border-gray-700 rounded-lg px-3 py-2';
          row.innerHTML = `
            <div class="min-w-0">
              <div class="font-mono text-xs text-gray-300 truncate">${escapeHtml(short)}…</div>
              <div class="text-xs text-gray-500">Hinzugefügt: ${escapeHtml(d.added_at || '–')} · Ablauf: ${escapeHtml(d.expires_at || '–')}</div>
            </div>
            <button class="bg-gray-700 hover:bg-red-600 text-white text-xs py-1 px-2 rounded transition ml-2 shrink-0">Widerrufen</button>`;
          row.querySelector('button').addEventListener('click', async () => {
            if (!confirm('Gerät widerrufen?')) return;
            const r = await apiFetch('/api/settings/devices/revoke', { method: 'POST', body: JSON.stringify({ fingerprint: fp }) });
            if (r.ok) { showToast('Gerät widerrufen'); loadDevices(); }
            else { const e = await r.json().catch(() => ({})); showToast('Fehler: ' + (e.error || r.status), true); }
          });
          list.appendChild(row);
        });
      } catch (e) {
        list.innerHTML = '<p class="text-gray-500">Fehler beim Laden.</p>';
      }
    }

    document.getElementById('settings-save-username').addEventListener('click', async () => {
      const val = document.getElementById('settings-new-username').value.trim();
      if (!val) { showToast('Benutzername eingeben', true); return; }
      const res = await apiFetch('/api/settings/profile', { method: 'POST', body: JSON.stringify({ username: val }) });
      if (res.ok) { const data = await res.json(); currentUser = data.username || val; showToast('Benutzername gespeichert'); loadProfile(); }
      else if (res.status === 409) { const e = await res.json().catch(() => ({})); showToast('Vergeben: ' + (e.error || ''), true); }
      else { const e = await res.json().catch(() => ({})); showToast('Fehler: ' + (e.error || res.status), true); }
    });

    document.getElementById('settings-change-password').addEventListener('click', async () => {
      const oldP = document.getElementById('settings-old-password').value;
      const newP = document.getElementById('settings-new-password').value;
      const conf = document.getElementById('settings-confirm-password').value;
      if (!oldP || !newP || !conf) { showToast('Alle Felder ausfüllen', true); return; }
      if (newP !== conf) { showToast('Passwörter stimmen nicht überein', true); return; }
      if (newP.length < 8 || !/[A-Z]/.test(newP) || !/[a-z]/.test(newP) || !/[0-9]/.test(newP) || !/[^A-Za-z0-9]/.test(newP)) {
        showToast('Passwort erfüllt Regeln nicht (min. 8, Groß-/Klein, Ziffer, Sonderzeichen)', true);
        return;
      }
      const res = await apiFetch('/api/settings/password', { method: 'POST', body: JSON.stringify({ old_password: oldP, new_password: newP }) });
      if (res.ok) {
        showToast('Passwort geändert');
        document.getElementById('settings-old-password').value = '';
        document.getElementById('settings-new-password').value = '';
        document.getElementById('settings-confirm-password').value = '';
      } else {
        const e = await res.json().catch(() => ({}));
        showToast('Fehler: ' + (e.error || res.status), true);
      }
    });

    document.getElementById('settings-export').addEventListener('click', async () => {
      try {
        const res = await apiFetch('/api/me/export');
        if (!res.ok) { showToast('Export fehlgeschlagen', true); return; }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'journl-export.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        showToast('Export heruntergeladen');
      } catch (e) {
        showToast('Export fehlgeschlagen', true);
      }
    });

    document.getElementById('settings-delete').addEventListener('click', () => {
      document.getElementById('settings-delete-confirm').classList.remove('hidden');
    });
    document.getElementById('settings-delete-cancel').addEventListener('click', () => {
      document.getElementById('settings-delete-confirm').classList.add('hidden');
      document.getElementById('settings-delete-password').value = '';
    });
    document.getElementById('settings-delete-confirm-btn').addEventListener('click', async () => {
      const pw = document.getElementById('settings-delete-password').value;
      if (!pw) { showToast('Passwort eingeben', true); return; }
      const res = await apiFetch('/api/me/delete', { method: 'POST', body: JSON.stringify({ password: pw }) });
      if (res.ok) {
        showToast('Konto gelöscht');
        if (typeof window.clearBrainState === 'function') window.clearBrainState();
        window.brainAuthenticatedUser = null;
        csrfToken = null;
        currentUser = null;
        closeSettings();
        showLogin();
      } else {
        const e = await res.json().catch(() => ({}));
        showToast('Fehler: ' + (e.error || res.status), true);
      }
    });

    // ─── family tracker ─────────────────────────────────────────────
    // Board, Subtabs, Anheften und Anlegen leben in family-board.js,
    // das beide App-Shells teilen. Hier bleiben nur die Aktionen, die an
    // den geraetespezifischen Modals haengen.

    async function checkFamilyTask(projectId, taskId, checkbox) {
      // Rueckfrage nur beim Abhaken; zuruecksetzen geht ohne (auch spaeter im
      // Archiv unter „Zuletzt erledigt“).
      if (checkbox.checked && !window.confirm('Aufgabe als erledigt abhaken?')) {
        checkbox.checked = false;
        return;
      }
      checkbox.disabled = true;
      try {
        const res = await apiFetch('/api/family/task/check', {
          method: 'POST',
          body: JSON.stringify({ project_id: projectId, task_id: taskId, completed: checkbox.checked })
        });
        if (res.ok) {
          const data = await res.json();
          familyBoardApplyTaskState(taskId, checkbox.checked, data);
          renderFamily();
          if (data.already_completed) showToast('Bereits erledigt');
          else showToast('Erledigt');
          checkFamilyNotifications();
        } else {
          const error = await res.json().catch(() => ({}));
          checkbox.checked = !checkbox.checked;
          checkbox.disabled = false;
          showToast(error.error || 'Fehler beim Abhaken', true);
        }
      } catch {
        checkbox.checked = !checkbox.checked;
        checkbox.disabled = false;
        showToast('Netzwerkfehler', true);
      }
    }

    function setFamilyNotify(blue, red) {
      const el = document.getElementById('family-notify');
      if (!el) return;
      const state = red ? { color: '#ef4444', style: red } : (blue ? { color: '#2563eb', style: blue } : null);
      if (!state) {
        el.classList.add('hidden');
        return;
      }
      el.classList.remove('hidden');
      if (state.style === 'dashed') {
        el.style.backgroundColor = 'transparent';
        // Muster lebt in family-board.js, damit beide Shells nicht auseinanderlaufen.
        el.style.backgroundImage = familyNotifyDashPattern(state.color);
      } else {
        el.style.backgroundColor = state.color;
        el.style.backgroundImage = 'none';
      }
    }

    async function checkFamilyNotifications() {
      try {
        const res = await apiFetch('/api/family/notifications');
        if (!res.ok) return;
        const data = await res.json();
        setFamilyNotify(data.blue, data.red);
      } catch {}
    }

    async function visitFamily() {
      try { await apiFetch('/api/family/visit', { method: 'POST' }); } catch {}
      checkFamilyNotifications();
    }

    // ─── shopping full-screen modal ──────────────────────────────────
    // Die Einkaufsliste hat bewusst einen eigenen Renderpfad; normale
    // Aufgabenlisten laufen ueber family-board.js und bleiben davon unberuehrt.

    // Gleichnamige Ueberschriften werden zusammengezogen, auch wenn sie in der
    // Datei mehrfach vorkommen - sonst liesse sich eine Gruppe nur stueckweise
    // einklappen. Die Reihenfolge des ersten Auftretens bleibt erhalten.
    function shoppingGroups(tasks) {
      const groups = [];
      const byName = new Map();
      for (const task of tasks) {
        const name = task.gruppe || '';
        let group = byName.get(name);
        if (!group) { group = { name, items: [] }; byName.set(name, group); groups.push(group); }
        group.items.push(task);
      }
      return groups;
    }

    function shoppingCollapseKey(projectId) {
      return 'journl:shopping-collapsed:' + projectId;
    }

    // null bedeutet "noch nie etwas gespeichert" - dann entscheidet der
    // Startzustand, nicht eine leere Menge.
    function readShoppingCollapsed(projectId) {
      try {
        const raw = window.localStorage.getItem(shoppingCollapseKey(projectId));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? new Set(parsed) : null;
      } catch { return null; }
    }

    function writeShoppingCollapsed(projectId, names) {
      try {
        window.localStorage.setItem(shoppingCollapseKey(projectId), JSON.stringify(Array.from(names)));
      } catch {}
    }

    function toggleShoppingGroup(button, projectId) {
      const section = button.closest('[data-shopping-group]');
      if (!section) return;
      const body = section.querySelector('[data-shopping-group-body]');
      const chevron = button.querySelector('[data-shopping-chevron]');
      if (!body) return;
      const collapse = !body.classList.contains('hidden');
      body.classList.toggle('hidden', collapse);
      if (chevron) chevron.classList.toggle('-rotate-90', collapse);
      button.setAttribute('aria-expanded', collapse ? 'false' : 'true');
      // Den ganzen Zustand aus dem DOM sichern statt nur die eine Aenderung:
      // so bleibt auch erhalten, was beim Oeffnen automatisch zu war.
      const container = document.getElementById('shopping-container');
      const collapsed = new Set();
      container?.querySelectorAll('[data-shopping-group]').forEach((el) => {
        const el_body = el.querySelector('[data-shopping-group-body]');
        if (el_body && el_body.classList.contains('hidden')) {
          collapsed.add(el.getAttribute('data-shopping-group') || '');
        }
      });
      writeShoppingCollapsed(projectId, collapsed);
    }

    // Welche Liste das Vollbild gerade zeigt - der Sortieren-Knopf und das
    // Neuladen nach dem Agenten brauchen sie.
    let shoppingOpenId = '';

    async function openShopping(projectId) {
      const modal = document.getElementById('shopping-modal');
      const container = document.getElementById('shopping-container');
      const title = document.getElementById('shopping-title');
      shoppingOpenId = projectId;
      modal.classList.remove('hidden');
      modal.classList.add('flex');
      container.innerHTML = '<p class="text-gray-500">Lade…</p>';
      try {
        const res = await apiFetch('/api/family/project/' + projectId);
        if (!res.ok) { container.innerHTML = '<p class="text-red-400">Fehler</p>'; return; }
        const p = await res.json();
        title.textContent = p.title || 'Einkaufsliste';
        const tasks = p.tasks || [];
        if (!tasks.length) { container.innerHTML = '<p class="text-gray-500 text-center mt-8">Noch keine Items.</p>'; return; }
        const collapsedStored = readShoppingCollapsed(projectId);
        let html = '';
        for (const group of shoppingGroups(tasks)) {
          const items = group.items.map((t) => {
            const checked = t.completed ? 'checked' : '';
            const strike = t.completed ? 'line-through text-gray-500' : 'text-gray-100';
            return `<label class="flex items-center gap-3 text-lg ${strike} bg-gray-900 border border-gray-800 rounded-lg px-4 py-3">
            <input type="checkbox" ${checked} onchange="checkShoppingTask('${escapeHtmlAttr(projectId)}','${escapeHtmlAttr(t.id)}',this)" class="w-5 h-5 accent-green-500 disabled:opacity-60" />
            <span class="flex-1">${escapeHtml(t.title || '')}</span>
          </label>`;
          }).join('');
          if (!group.name) { html += items; continue; }
          const open = group.items.filter((t) => !t.completed).length;
          // Ohne gespeicherten Zustand starten fertige Gruppen eingeklappt.
          const isCollapsed = collapsedStored ? collapsedStored.has(group.name) : open === 0;
          const dot = window.journlGroupDot?.(group.name) || '';
          html += `<section data-shopping-group="${escapeHtmlAttr(group.name)}" class="space-y-2">
            <button type="button" onclick="toggleShoppingGroup(this,'${escapeHtmlAttr(projectId)}')"
              aria-expanded="${isCollapsed ? 'false' : 'true'}"
              class="flex w-full items-center gap-2 rounded-lg px-1 pt-2 pb-1 text-left text-sm font-semibold text-gray-400 transition hover:text-gray-200">
              <span data-shopping-chevron aria-hidden="true" class="inline-block w-3 shrink-0 text-xs transition-transform${isCollapsed ? ' -rotate-90' : ''}">▾</span>
              <span class="flex min-w-0 flex-1 items-center gap-2 break-words">${dot}<span class="min-w-0 break-words">${escapeHtml(group.name)}</span></span>
              <span data-shopping-group-count class="shrink-0 text-xs font-normal text-gray-500">${open ? open + ' offen' : 'alle erledigt'}</span>
            </button>
            <div data-shopping-group-body class="space-y-2${isCollapsed ? ' hidden' : ''}">${items}</div>
          </section>`;
        }
        container.innerHTML = html;
      } catch {
        container.innerHTML = '<p class="text-red-400">Netzwerkfehler</p>';
      }
    }

    function closeShopping() {
      const modal = document.getElementById('shopping-modal');
      if (!modal) return;
      const wasOpen = !modal.classList.contains('hidden');
      modal.classList.add('hidden');
      modal.classList.remove('flex');
      if (wasOpen) loadFamily();
    }

    // Der Zaehler in der Gruppenzeile darf nach einem Haken nicht luegen.
    function updateShoppingGroupCount(label) {
      const section = label?.closest?.('[data-shopping-group]');
      const badge = section?.querySelector('[data-shopping-group-count]');
      if (!badge) return;
      const boxes = section.querySelectorAll('[data-shopping-group-body] input[type="checkbox"]');
      const open = Array.from(boxes).filter((box) => !box.checked).length;
      badge.textContent = open ? open + ' offen' : 'alle erledigt';
    }

    async function checkShoppingTask(projectId, taskId, checkbox) {
      const label = checkbox.closest('label');
      checkbox.disabled = true;
      try {
        const res = await apiFetch('/api/family/task/check', {
          method: 'POST',
          body: JSON.stringify({ project_id: projectId, task_id: taskId, completed: checkbox.checked })
        });
        if (res.ok) {
          const data = await res.json();
          label.classList.toggle('line-through', checkbox.checked);
          label.classList.toggle('text-gray-500', checkbox.checked);
          label.classList.toggle('text-gray-100', !checkbox.checked);
          updateShoppingGroupCount(label);
          checkbox.disabled = false;
          if (!data.already_completed) showToast(checkbox.checked ? 'Abgehakt' : 'Wieder geöffnet');
        } else {
          checkbox.checked = !checkbox.checked;
          checkbox.disabled = false;
          showToast('Fehler', true);
        }
      } catch {
        checkbox.checked = !checkbox.checked;
        checkbox.disabled = false;
        showToast('Netzwerkfehler', true);
      }
    }

    async function cleanShoppingList(projectId) {
      const project = familyDocument(projectId);
      const completedCount = (project?.tasks || []).filter(task => task.completed).length;
      const totalCount = (project?.tasks || []).length;
      if (!totalCount) {
        showToast('Die Einkaufsliste ist bereits leer.');
        return;
      }
      const clearAll = completedCount === 0;
      const confirmation = clearAll
        ? `Es gibt keine abgehakten Artikel. Wirklich alle ${totalCount} Artikel endgültig entfernen?`
        : `${completedCount} abgehakte${completedCount === 1 ? 'n Artikel' : ' Artikel'} endgültig entfernen?`;
      if (!window.confirm(confirmation)) return;
      try {
        const res = await apiFetch('/api/family/project/' + projectId + '/shopping-clean', {
          method: 'POST',
          body: JSON.stringify({ clear_all: clearAll })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          showToast('Fehler: ' + (data.error || res.status), true);
          return;
        }
        showToast(data.removed ? `${data.removed} Artikel entfernt` : 'Die Einkaufsliste ist bereits leer.');
        loadFamily();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    // ─── unified editor modal (compact + full) ─────────────────────
    let editorState = { projectId: null, mode: 'full', users: [] };

    async function openEditor(projectId, mode) {
      editorState.projectId = projectId;
      editorState.mode = mode || 'full';
      const modal = document.getElementById('editor-modal');
      const listEl = document.getElementById('editor-list');
      const bulkEl = document.getElementById('editor-bulk');
      const bulkLabel = document.getElementById('editor-bulk-label');
      const titleEl = document.getElementById('editor-title');
      const p = familyDocument(projectId);
      titleEl.textContent = (p ? (p.title || 'Editor') : 'Editor') + ' bearbeiten';
      const titleIcon = document.getElementById('editor-title-icon');
      if (titleIcon) titleIcon.innerHTML = window.journlIcon?.(mode === 'compact' ? 'cart' : 'note', 'h-5 w-5') || '';
      if (mode === 'compact') {
        bulkLabel.textContent = 'Mehrere Artikel hinzufügen (einer pro Zeile):';
      } else {
        bulkLabel.textContent = 'Mehrere Aufgaben – eine pro Zeile. Kurzformen: @Name, morgen, nächsten Mi, 12.10., in 3 Tagen, [x] erledigt. Wiederholung (jeden Fr, täglich, alle 2 Wochen, monatlich) → Planer';
      }
      document.getElementById('editor-bulk-extra').classList.toggle('hidden', mode === 'compact');
      modal.classList.remove('hidden');
      modal.classList.add('flex');
      listEl.innerHTML = '<p class="text-gray-500">Lade…</p>';
      bulkEl.value = '';
      bulkEl.placeholder = mode === 'compact' ? 'Butter\nMehl\nZucker'
        : 'Müll rausbringen @Kim morgen\nSteuer abgeben @Alex 12.10.\n[x] Arzttermin vereinbart';
      window.journlFamilyQuickTasks?.renderPreview();
      document.getElementById('editor-bulk-date').value = '';
      try {
        const res = await apiFetch('/api/family/project/' + projectId + '/editor');
        if (!res.ok) { listEl.innerHTML = '<p class="text-red-400">Fehler</p>'; return; }
        const data = await res.json();
        editorState.users = data.users || [];
        renderEditorItems(data.tasks || [], data.mode || mode);
        renderEditorBulkUserSelect();
        if (mode !== 'compact' && window.journlFamilyQuickTasks?.placeholder) {
          bulkEl.placeholder = window.journlFamilyQuickTasks.placeholder(editorState.users);
        }
      } catch {
        listEl.innerHTML = '<p class="text-red-400">Netzwerkfehler</p>';
      }
    }

    function renderEditorBulkUserSelect() {
      const sel = document.getElementById('editor-bulk-user');
      if (!sel) return;
      let html = '<option value="">—</option>';
      for (const u of editorState.users) {
        html += `<option value="${escapeHtmlAttr(u.id)}">${escapeHtml(u.display)}</option>`;
      }
      sel.innerHTML = html;
    }

    function renderEditorItems(tasks, mode) {
      const listEl = document.getElementById('editor-list');
      if (!tasks.length) {
        listEl.innerHTML = '<p class="text-gray-600 text-sm">Noch keine Einträge.</p>';
        return;
      }
      let html = '';
      for (const it of tasks) {
        const checked = it.completed ? 'checked' : '';
        const strike = it.completed ? 'line-through text-gray-500' : 'text-gray-100';
        if (mode === 'compact') {
          html += `<div class="flex items-center gap-2" data-item-id="${escapeHtmlAttr(it.id || '')}">
            <input type="checkbox" ${checked} class="editor-check w-5 h-5 accent-green-500 shrink-0" />
            <input type="text" value="${escapeHtmlAttr(it.title || '')}" class="editor-input flex-1 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm ${strike} focus:outline-none focus:ring-2 focus:ring-green-500" />
            <button onclick="this.parentElement.remove()" class="text-red-500 hover:text-red-400 text-sm shrink-0" title="Löschen">${window.journlIcon?.('trash') || ''}</button>
          </div>`;
        } else {
          let userOpts = '<option value="">—</option>';
          for (const u of editorState.users) {
            const sel = (u.id === it.user) ? ' selected' : '';
            userOpts += `<option value="${escapeHtmlAttr(u.id)}"${sel}>${escapeHtml(u.display)}</option>`;
          }
          html += `<div class="flex flex-wrap items-center gap-2" data-item-id="${escapeHtmlAttr(it.id || '')}">
            <input type="checkbox" ${checked} class="editor-check w-5 h-5 accent-green-500 shrink-0" />
            <input type="text" value="${escapeHtmlAttr(it.title || '')}" class="editor-input flex-1 min-w-[8rem] rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm ${strike} focus:outline-none focus:ring-2 focus:ring-green-500" />
            <select class="editor-user rounded-lg bg-gray-800 border border-gray-700 px-2 py-2 text-xs text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500">${userOpts}</select>
            <input type="date" value="${escapeHtmlAttr(it.target_date || '')}" class="editor-date rounded-lg bg-gray-800 border border-gray-700 px-2 py-2 text-xs text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500" />
            <button onclick="this.parentElement.remove()" class="text-red-500 hover:text-red-400 text-sm shrink-0" title="Löschen">${window.journlIcon?.('trash') || ''}</button>
          </div>`;
        }
      }
      listEl.innerHTML = html;
      listEl.querySelectorAll('.editor-check').forEach(cb => {
        cb.addEventListener('change', () => {
          const input = cb.parentElement.querySelector('.editor-input');
          if (cb.checked) {
            input.classList.add('line-through', 'text-gray-500');
            input.classList.remove('text-gray-100');
          } else {
            input.classList.remove('line-through', 'text-gray-500');
            input.classList.add('text-gray-100');
          }
        });
      });
    }

    function closeEditor() {
      const modal = document.getElementById('editor-modal');
      if (!modal) return;
      modal.classList.add('hidden');
      modal.classList.remove('flex');
      editorState.projectId = null;
    }

    async function saveEditor() {
      if (!editorState.projectId) return;
      const listEl = document.getElementById('editor-list');
      const bulkEl = document.getElementById('editor-bulk');
      const tasks = [];
      listEl.querySelectorAll('[data-item-id]').forEach(row => {
        const title = row.querySelector('.editor-input').value.trim();
        if (!title) return;
        const task = {
          id: row.getAttribute('data-item-id') || undefined,
          title: title,
          completed: row.querySelector('.editor-check').checked
        };
        if (editorState.mode === 'full') {
          const userSel = row.querySelector('.editor-user');
          const dateInput = row.querySelector('.editor-date');
          task.user = userSel ? userSel.value : '';
          task.target_date = dateInput ? dateInput.value : '';
        }
        tasks.push(task);
      });
      const bulkUser = editorState.mode === 'full' ? (document.getElementById('editor-bulk-user').value || '') : '';
      const bulkDate = editorState.mode === 'full' ? (document.getElementById('editor-bulk-date').value || '') : '';
      // Jede Zeile eine eigene Aufgabe mit eigener Person/Datum/Status
      // (Kurzformen @Name, morgen, fr, 12.10., [x] - family-quick-tasks.js);
      // die Vorgabe darunter gilt nur, wo eine Zeile nichts eigenes sagt.
      const quick = window.journlFamilyQuickTasks?.parse(bulkEl.value, {
        users: editorState.users, defaultUser: bulkUser, defaultDate: bulkDate, full: editorState.mode === 'full',
      }) || bulkEl.value.split('\n').map(line => ({ title: line.trim(), completed: false, user: bulkUser, target_date: bulkDate }));
      // Wiederholungen („jeden freitag“) gehen als Eintrag in den Planer.
      const plans = quick.filter(item => item.recurrence);
      const missing = plans.find(item => item.planError);
      if (missing) { showToast(`Wiederholung „${missing.title}“: ${missing.planError} (@Name oder Vorgabe)`, true); return; }
      for (const item of quick) {
        if (item.recurrence) continue;
        if (!item.title || tasks.some(i => i.title.toLowerCase() === item.title.toLowerCase())) continue;
        const task = { title: item.title, completed: Boolean(item.completed) };
        if (editorState.mode === 'full') {
          task.user = item.user || '';
          task.target_date = item.target_date || '';
        }
        tasks.push(task);
      }
      try {
        const res = await apiFetch('/api/family/project/' + editorState.projectId + '/editor', {
          method: 'PUT',
          body: JSON.stringify({ tasks })
        });
        if (res.ok) {
          const failed = plans.length ? await window.journlFamilyQuickTasks.createPlans(plans) : [];
          if (failed.length) showToast('Nicht im Planer angelegt: ' + failed.join('; '), true);
          else showToast(plans.length ? `Gespeichert, ${plans.length} im Planer` : 'Gespeichert');
          closeEditor();
          loadFamily();
          checkFamilyNotifications();
        } else {
          const e = await res.json().catch(() => ({}));
          showToast('Fehler: ' + (e.error || res.status), true);
        }
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    document.getElementById('editor-close').addEventListener('click', closeEditor);
    document.getElementById('editor-cancel').addEventListener('click', closeEditor);
    document.getElementById('editor-save').addEventListener('click', saveEditor);
    document.getElementById('editor-modal').addEventListener('click', (e) => {
      if (e.target.id === 'editor-modal') closeEditor();
    });
    document.getElementById('shopping-close').addEventListener('click', closeShopping);
    document.getElementById('shopping-sort')?.addEventListener('click', () => {
      if (!shoppingOpenId) return;
      const title = document.getElementById('shopping-title')?.textContent || '';
      window.openShoppingAgent?.(shoppingOpenId, title);
    });
    // Hat der Agent die Liste umgebaut, zeigt das Vollbild darunter sonst den
    // alten Stand bis zum naechsten Oeffnen.
    document.addEventListener('journl:shopping-agent-closed', (event) => {
      const modal = document.getElementById('shopping-modal');
      if (!event.detail?.changed || !shoppingOpenId || modal?.classList.contains('hidden')) return;
      if (event.detail.projectId === shoppingOpenId) openShopping(shoppingOpenId);
    });

    // renderFamily lives in family-board.js, das erst nach diesem Script laedt -
    // ein direkter Verweis wuerde beim Registrieren sofort mit ReferenceError
    // abbrechen, statt beim tatsaechlichen Tippen aufgeloest zu werden.
    document.getElementById('family-search').addEventListener('input', () => renderFamily());
