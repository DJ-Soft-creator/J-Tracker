// AI maintenance contract: classic scripts share one global app shell.
// Preserve template load order; move whole top-level functions, never partial bodies.
    const AI_MODE_ID = '__ai_mode__';
    const ASSISTANT_MODE_ID = '__assistant_mode__';
    let config = null;
    let csrfToken = null;
    let currentUser = null;
    let currentUserId = null;
    let familyNotificationTimer = null;

    function templateVisibleForMe(t) {
      if (!t) return false;
      const au = t.assigned_users || [];
      if (!au.length) return true;
      return currentUserId && au.includes(currentUserId);
    }

    function dedupTemplates(arr) {
      const map = new Map();
      for (const t of arr) {
        if (t && t.id && !map.has(t.id)) map.set(t.id, t);
      }
      return Array.from(map.values());
    }

    async function refreshCsrfToken(timeoutMs = 10000) {
      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timeoutId = controller ? setTimeout(() => controller.abort(), timeoutMs) : null;
      try {
        const res = await fetch('/api/csrf-token', { credentials: 'include', signal: controller?.signal });
        if (res.ok) {
          const data = await res.json();
          csrfToken = data.csrf_token;
        }
      } catch (err) {
        console.error('CSRF fetch failed:', err);
      } finally {
        clearTimeout(timeoutId);
      }
    }

    async function apiFetch(url, opts = {}) {
      const timeoutMs = Number(opts.timeoutMs) || 0;
      opts = { ...opts };
      delete opts.timeoutMs;
      opts.credentials = 'include';
      opts.headers = { ...(opts.headers || {}) };
      let timeoutId = null;
      if (timeoutMs > 0 && !opts.signal && typeof AbortController !== 'undefined') {
        const controller = new AbortController();
        opts.signal = controller.signal;
        timeoutId = setTimeout(() => controller.abort(), timeoutMs);
      }
      if (opts.body && !(opts.body instanceof FormData) && !opts.headers['Content-Type']) {
        opts.headers['Content-Type'] = 'application/json';
      }
      const method = (opts.method || 'GET').toUpperCase();
      if (method !== 'GET' && csrfToken) {
        opts.headers['X-CSRF-Token'] = csrfToken;
      }
      try {
        let res = await fetch(url, opts);
        if (res.status === 403 && method !== 'GET') {
          await refreshCsrfToken();
          if (csrfToken) {
            opts.headers['X-CSRF-Token'] = csrfToken;
            res = await fetch(url, opts);
          }
        }
        return res;
      } finally {
        clearTimeout(timeoutId);
      }
    }

    // ─── auth check on startup ───────────────────────────────────────
    // The CSRF token depends only on the session cookie, never on the health
    // result, so both requests go out together instead of one after the other.
    (async function checkAuth() {
      try {
        const [res, csrfRes] = await Promise.all([
          fetch('/api/health', { credentials: 'include' }),
          fetch('/api/csrf-token', { credentials: 'include' }).catch(() => null),
        ]);
        const data = await res.json();
        if (data.authenticated) {
          currentUser = data.username || null;
          if (csrfRes && csrfRes.ok) {
            csrfToken = (await csrfRes.json()).csrf_token;
          } else {
            await refreshCsrfToken();
          }
          showApp();
        } else {
          showLogin();
        }
      } catch (err) {
        showLogin();
      }
    })();

    if ('serviceWorker' in navigator) {
      // Siehe mobile-session.js: die App-Version als Cache-Buster des Workers.
      window.addEventListener('load', () => {
        const version = document.documentElement.dataset.appVersion;
        const path = version ? `/sw.js?v=${encodeURIComponent(version)}` : '/sw.js';
        navigator.serviceWorker.register(path).catch(() => {});
      });
    }

    function showLogin() {
      clearInterval(familyNotificationTimer);
      familyNotificationTimer = null;
      if (window.brainAuthenticatedUser && typeof window.clearBrainState === 'function') window.clearBrainState();
      window.brainAuthenticatedUser = null;
      document.getElementById('login-overlay').classList.remove('hidden');
      document.getElementById('app').classList.add('hidden');
    }

    function showApp() {
      const brainUserChanged = Boolean(currentUser && currentUser !== window.brainAuthenticatedUser);
      if (brainUserChanged && typeof window.clearBrainState === 'function') window.clearBrainState();
      window.brainAuthenticatedUser = currentUser || null;
      document.getElementById('login-overlay').classList.add('hidden');
      document.getElementById('app').classList.remove('hidden');
      scheduleWritePanelClamp();
      if (currentUser) {
        const bu = document.getElementById('burger-username');
        if (bu) bu.textContent = currentUser;
        const headerUser = document.getElementById('header-username');
        if (headerUser) headerUser.textContent = currentUser;
      }
      loadConfig();
      loadProfile();
      checkFamilyNotifications();
      clearInterval(familyNotificationTimer);
      familyNotificationTimer = setInterval(checkFamilyNotifications, 60000);
    }

    // ─── login form ──────────────────────────────────────────────────
    document.getElementById('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const username = document.getElementById('username').value;
      const password = document.getElementById('password').value;
      const errEl = document.getElementById('login-error');
      errEl.classList.add('hidden');

      const res = await fetch('/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ username, password })
      });

      if (res.ok) {
        const data = await res.json();
        try {
          const hres = await fetch('/api/health', { credentials: 'include' });
          const hdata = await hres.json();
          if (!hres.ok || !hdata.authenticated) throw new Error('Session cookie was not accepted');
          currentUser = hdata.username || username;
          await refreshCsrfToken();
          showApp();
          if (data.notice) showToast(data.notice, false);
        } catch {
          currentUser = null;
          errEl.textContent = 'Anmeldung fehlgeschlagen. Der Browser konnte keine Sitzung aufbauen.';
          errEl.classList.remove('hidden');
        }
      } else if (res.status === 423) {
        const data = await res.json().catch(() => ({}));
        const until = data.locked_until ? new Date(data.locked_until).toLocaleTimeString('de-DE') : '';
        errEl.textContent = 'Konto gesperrt. Bitte später erneut versuchen' + (until ? ' (bis ' + until + ' Uhr)' : '') + '.';
        errEl.classList.remove('hidden');
      } else if (res.status === 429) {
        errEl.textContent = 'Zu viele Versuche. Bitte später erneut versuchen.';
        errEl.classList.remove('hidden');
      } else {
        errEl.textContent = 'Falsche Zugangsdaten.';
        errEl.classList.remove('hidden');
      }
      setTimeout(() => errEl.classList.add('hidden'), 4000);
    });

    // ─── burger menu (logout + settings) ─────────────────────────────
    const burgerBtn = document.getElementById('burger-btn');
    const burgerMenu = document.getElementById('burger-menu');
    burgerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      burgerMenu.classList.toggle('hidden');
    });
    document.addEventListener('click', (e) => {
      if (!burgerMenu.contains(e.target) && !burgerBtn.contains(e.target)) {
        burgerMenu.classList.add('hidden');
      }
    });
    document.getElementById('burger-logout').addEventListener('click', async () => {
      burgerMenu.classList.add('hidden');
      if (typeof window.clearBrainState === 'function') window.clearBrainState();
      window.brainAuthenticatedUser = null;
      try { await apiFetch('/logout', { method: 'POST' }); } catch {}
      csrfToken = null;
      currentUser = null;
      showLogin();
    });

    // ─── load config & build dropdown ────────────────────────────────
    async function loadConfig() {
      try {
        const res = await apiFetch('/api/config');
        if (!res.ok) { showLogin(); return; }
        config = await res.json();
        window.config = config;
        applyYoutubeMode(Boolean(config.presentation?.youtube_mode));
        document.getElementById('settings-app-version').textContent = config.app_version || '–';
        if (config.current_user) currentUserId = config.current_user.id || null;
        config.templates = dedupTemplates(config.templates || []);

        // Build template dropdown
        const select = document.getElementById('template-select');
        for (const t of config.templates) {
          if (!templateVisibleForMe(t)) continue;
          const opt = document.createElement('option');
          opt.value = t.id;
          opt.textContent = t.label;
          if (t.id === 'schnell') opt.selected = true;
          select.appendChild(opt);
        }
        const aiModeOpt = document.createElement('option');
        aiModeOpt.value = AI_MODE_ID;
        aiModeOpt.textContent = 'KI-Modus';
        select.appendChild(aiModeOpt);
        const assistantOpt = document.createElement('option');
        assistantOpt.value = ASSISTANT_MODE_ID;
        assistantOpt.textContent = 'Agent-Projekt';
        select.appendChild(assistantOpt);

        select.addEventListener('change', () => {
          const previous = select.dataset.specialMode || '';
          const val = select.value;
          // Leaving a special mode must run even though the value already changed.
          if (previous === ASSISTANT_MODE_ID && typeof window.leaveAssistantMode === 'function') window.leaveAssistantMode();
          if (previous === AI_MODE_ID && typeof window.leaveWriteAiMode === 'function') window.leaveWriteAiMode();
          select.dataset.specialMode = val;
          if (val === AI_MODE_ID) {
            handleAiMode();
          } else if (val === ASSISTANT_MODE_ID) {
            if (typeof window.handleAssistantMode === 'function') window.handleAssistantMode();
            else {
              select.value = '';
              select.dataset.specialMode = '';
              showToast('Der Assistent ist nicht geladen.', true);
            }
          } else {
            handleTemplateChange(config.templates.find(t => t.id === val));
          }
        });

        // Type-ahead + Enter handling on template-select
        select.addEventListener('keydown', (e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            handleSubmit();
            return;
          }
          if (e.key === 'Enter') {
            e.preventDefault();
            document.getElementById('simple-input').focus();
            return;
          }
          if (e.key.length === 1) {
            const letter = e.key.toLowerCase();
            for (const opt of select.options) {
              const label = opt.textContent.replace(/^[^a-zA-Z0-9äöüÄÖÜß]+/, '').trim().toLowerCase();
              if (label.startsWith(letter)) {
                if (select.value !== opt.value) {
                  select.value = opt.value;
                  select.dispatchEvent(new Event('change'));
                }
                e.preventDefault();
                return;
              }
            }
          }
        });

        // trigger initial state for schnell
        handleTemplateChange(config.templates.find(t => t.id === 'schnell') || config.templates.find(templateVisibleForMe) || config.templates[0]);

        // Build AI provider dropdown
        const providerSelect = document.getElementById('ai-provider-select');
        if (config.ai_providers && config.ai_providers.length > 0) {
          for (const p of config.ai_providers) {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.label;
            providerSelect.appendChild(opt);
          }
        }

        // Historical tagging owns its own connection in the Brain settings
        // (brain-tagging-run.js): endpoint and model are entered there, models
        // are read live from that endpoint. Nothing to build from config here.

        // Build AI function dropdown
        const functionSelect = document.getElementById('ai-function-select');
        if (config.ai_functions && config.ai_functions.length > 0) {
          for (const f of config.ai_functions) {
            const opt = document.createElement('option');
            opt.value = f.id;
            opt.textContent = f.label;
            functionSelect.appendChild(opt);
          }
        }

      } catch (err) {
        console.error('Failed to load config:', err);
      }
    }

    // Bearbeitbare Beschriftung/Platzhalter werden maskiert; Feld-IDs begrenzt journal_templates.py.
    function renderForm(template, container) {
      let html = '';
      for (const field of template.fields) {
        const required = field.required ? ' required' : '', placeholder = escapeHtmlAttr(field.placeholder || '');
        html += `<div class="form-field">`;
        html += `<label for="field-${field.id}">${escapeHtml(field.label || '')}${field.required ? ' <span class="text-green-400">*</span>' : ''}</label>`;

        if (field.type === 'text') {
          html += `<input type="text" id="field-${field.id}" name="${field.id}" placeholder="${placeholder}"${required} />`;
        } else if (field.type === 'date') {
          html += `<input type="date" id="field-${field.id}" name="${field.id}" placeholder="${placeholder}"${required} />`;
        } else if (field.type === 'user') {
          const users = (config && config.users) || [];
          let opts = '<option value="">— wählen —</option>';
          for (const u of users) {
            opts += `<option value="${escapeHtml(u.id)}">${escapeHtml(u.username)}</option>`;
          }
          html += `<select id="field-${field.id}" name="${field.id}"${required}>${opts}</select>`;
        } else if (field.type === 'select') {
          let opts = '<option value="">— wählen —</option>';
          for (const option of (field.options || [])) {
            opts += `<option value="${escapeHtmlAttr(option.value || '')}">${escapeHtml(option.label || option.value || '')}</option>`;
          }
          html += `<select id="field-${field.id}" name="${field.id}"${required}>${opts}</select>`;
        } else if (field.type === 'textarea') {
          html += `<textarea id="field-${field.id}" name="${field.id}" rows="3" placeholder="${placeholder}"${required}></textarea>`;
        } else if (field.type === 'number') {
          const min = field.min !== undefined ? `min="${escapeHtmlAttr(field.min)}"` : '';
          const max = field.max !== undefined ? `max="${escapeHtmlAttr(field.max)}"` : '';
          html += `<input type="number" id="field-${field.id}" name="${field.id}" ${min} ${max} step="1" placeholder="${placeholder}"${required} />`;
        }

        html += `</div>`;
      }
      container.innerHTML = html;
      if (template.id === 'aufgabenplaner') {
        const dateInput = document.getElementById('field-target_date');
        const userInput = document.getElementById('field-user');
        const recurrenceInput = document.getElementById('field-recurrence');
        if (dateInput) dateInput.value = new Date().toLocaleDateString('sv-SE');
        if (userInput && currentUserId) userInput.value = currentUserId;
        if (recurrenceInput) recurrenceInput.value = 'once';
      }
      container.scrollTop = 0;
    }

    // Projektzuordnung und Einkaufslisten-Ziel leben in write-targets.js,
    // damit beide App-Shells eine Implementierung teilen.

    function handleAiMode() {
      const simpleInput = document.getElementById('simple-input');
      const formContainer = document.getElementById('form-container');
      simpleInput.style.display = '';
      formContainer.innerHTML = '';
      formContainer.style.display = 'none';
      // The AI mode is controlled by an #ai-… hashtag in the text itself.
      // Do not expose the generic provider/function controls here: those use
      // the legacy endpoint which may persist a journal entry.
      document.getElementById('ai-controls').classList.add('hidden');
      document.getElementById('submit-btn').textContent = 'KI Senden';
      window.writeTargetsRender?.(null);
      updateAppInfoBox();
    }

    function handleTemplateChange(template) {
      const simpleInput = document.getElementById('simple-input');
      const formContainer = document.getElementById('form-container');

      if (!template || !simpleInput || !formContainer) return;

      document.getElementById('ai-controls').classList.add('hidden');
      document.getElementById('ai-response-area').classList.add('hidden');
      const isShoppingList = template.id === 'einkaufsliste';
      document.getElementById('submit-btn').textContent = isShoppingList ? 'Übernehmen' : 'Senden';
      simpleInput.placeholder = isShoppingList
        ? 'Sortierte Einkaufsliste einfügen – Überschriften mit ##, Artikel je Zeile'
        : 'Notiz eingeben...';

      if (template.type === 'simple') {
        simpleInput.style.display = '';
        formContainer.innerHTML = '';
        formContainer.style.display = 'none';
      } else {
        simpleInput.style.display = 'none';
        formContainer.style.display = '';
        renderForm(template, formContainer);
      }
      window.writeTargetsRender?.(template);
      updateAppInfoBox();
    }

    // ─── init journal before submit ──────────────────────────────────
    async function initJournal() {
      try {
        await apiFetch('/api/dashboard/init', { method: 'POST', timeoutMs: 30000 });
      } catch (err) {
        console.error('Failed to init journal:', err);
      }
    }

    // Browsers may restore textarea values from their page session after a
    // reload. Clear only values that this app already confirmed as saved.
    const normalSubmissionClearKey = 'journl:clear-submitted-write-input';
    function rememberSuccessfulNormalSubmission() {
      try { sessionStorage.setItem(normalSubmissionClearKey, '1'); } catch (_) {}
    }
    const submittedInput = document.getElementById('simple-input');
    function clearRestoredSubmittedInput() {
      try {
        if (sessionStorage.getItem(normalSubmissionClearKey) === '1') {
          if (typeof window.writeEditorReset === 'function') window.writeEditorReset('');
          else {
            submittedInput.value = '';
            submittedInput.dispatchEvent(new Event('input', { bubbles: true }));
          }
        }
      } catch (_) {}
    }
    submittedInput.addEventListener('input', () => {
      if (!submittedInput.value) return;
      try { sessionStorage.removeItem(normalSubmissionClearKey); } catch (_) {}
    });
    window.addEventListener('pageshow', clearRestoredSubmittedInput);

    // ─── submit handler (normal or AI based on template selection) ──────
    async function handleSubmit(options = {}) {
      const isAiMode = document.getElementById('template-select').value === AI_MODE_ID;
      const isAssistantMode = document.getElementById('template-select').value === ASSISTANT_MODE_ID;

      if (isAssistantMode) {
        // agent-projects.js replaces this handler with the Agent-Projekt flow.
        showToast('Bitte kurz warten, bis der Assistent bereit ist.', true);
        return;
      } else if (isAiMode) {
        // write-ai.js replaces this handler with the hashtag-based workflow.
        // This fallback intentionally never calls /api/ai-submit, because that
        // legacy route can write to a journal.
        showToast('Bitte kurz warten, bis der KI-Modus bereit ist.', true);
        return;
      } else {
        // Normal template mode
        const selectedId = document.getElementById('template-select').value;
        if (!selectedId) return;

        const template = config.templates.find(t => t.id === selectedId);
        let payload = {};
        let submittedSimpleValue = null;
        const submittedFormValues = new Map();

        if (template.type === 'simple') {
          submittedSimpleValue = document.getElementById('simple-input').value;
          const content = submittedSimpleValue.trim();
          if (!content && template.id !== 'schnell') {
            showToast('Bitte Text eingeben', true);
            return;
          }
          // For "schnell", allow empty (just time)
          payload = { template_id: selectedId, content };
          if (options.backgroundAgent === true) { payload.background_agent = true; payload.whisper_model = window.journlWhisperModel?.(); }
        } else {
          // form template - collect values
          const values = {};
          for (const field of template.fields) {
            const el = document.getElementById('field-' + field.id);
            if (field.required && (!el || !el.value.trim())) {
              showToast('Bitte „' + field.label + '“ ausfüllen', true);
              if (el) el.focus();
              return;
            }
            if (el) {
              submittedFormValues.set(field.id, el.value);
              if (el.value.trim()) values[field.id] = el.value.trim();
            }
          }
          payload = { form_id: selectedId, values };
        }

        Object.assign(payload, window.writeTargetsPayload?.() || {});

        const recordingBlock = window.writeSessionSubmissionBlockReason?.();
        if (recordingBlock) {
          showToast(recordingBlock, true);
          return;
        }
        const submitBtn = document.getElementById('submit-btn');
        const submitText = submitBtn.textContent;
        const submitToken = window.writeSubmitState?.begin({
          scope: 'normal',
          supersedeScopes: ['ai'],
          readyText: submitText,
          busyText: '…',
          activeMessage: 'Senden läuft bereits – dein Text bleibt erhalten.',
          timeoutMs: 90000,
          timeoutMessage: 'Senden wurde automatisch freigegeben. Dein Text bleibt erhalten; bitte erneut versuchen.',
        });
        if (!submitToken) return;
        try {

        if (template.type === 'simple' && typeof window.writeAiDraftRevisionForSubmit === 'function') {
          const draftRevisionToConsume = await window.writeAiDraftRevisionForSubmit();
          if (draftRevisionToConsume) payload.consume_draft_revision = draftRevisionToConsume;
        }
        if (template.type === 'simple' && typeof window.writeSessionForSubmit === 'function') {
          const writeSessionId = await window.writeSessionForSubmit(submittedSimpleValue);
          if (writeSessionId) payload.write_session_id = writeSessionId;
        }

        if (template.write_to_journal !== false) await initJournal();

          const res = await apiFetch('/api/submit', {
            method: 'POST',
            body: JSON.stringify(payload),
            timeoutMs: 30000
          });
          const responseData = await res.json().catch(() => ({}));

          if (res.ok) {
            if (template.type === 'simple' && typeof window.markWriteSessionSubmitted === 'function') {
              window.markWriteSessionSubmitted(responseData);
            }
            if (typeof window.markWriteAiDraftSubmitted === 'function') window.markWriteAiDraftSubmitted(responseData);
            showToast(responseData.background_error || (responseData.ai_sessions?.length ? 'Gespeichert – KI arbeitet im Hintergrund.' : (template.id === 'einkaufsliste' ? 'Einkaufsliste übernommen' : 'Gespeichert!')), Boolean(responseData.background_error));
            if (typeof window.refreshWriteHashtagCatalog === 'function') window.refreshWriteHashtagCatalog();
            // Die gerade übermittelten Felder anschließend leeren.
            if (template.type === 'simple') {
              const textarea = document.getElementById('simple-input');
              if (textarea.value === submittedSimpleValue) {
                rememberSuccessfulNormalSubmission();
                if (typeof window.writeEditorReset === 'function') window.writeEditorReset('');
                else {
                  textarea.value = '';
                  textarea.dispatchEvent(new Event('input', { bubbles: true }));
                }
                textarea.focus();
              } else {
                showToast('Gespeichert – neuer Text bleibt im Eingabefeld.');
              }
            } else {
              let hasNewInput = false;
              for (const field of template.fields) {
                const el = document.getElementById('field-' + field.id);
                if (el && el.value === submittedFormValues.get(field.id)) {
                  el.value = '';
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                }
                else if (el) hasNewInput = true;
              }
              if (hasNewInput) showToast('Gespeichert – neue Eingaben bleiben erhalten.');
              const firstField = document.getElementById('field-' + template.fields[0].id);
              if (firstField) firstField.focus();
            }
            // Erst nach dem Leeren zurück zu „Schnell“ wechseln. Hashtag-
            // Templates verändern nur ihre verwaltete Kopfzeile; ein vorzeitiger
            // Wechsel würde den gesendeten Wert sonst wie neue Eingabe aussehen lassen.
            if (template.id !== 'schnell') {
              document.getElementById('template-select').value = 'schnell';
              handleTemplateChange(config.templates.find(t => t.id === 'schnell'));
            }
          } else if (res.status === 409) {
            if (responseData.conflict_id) openJournalConflict(responseData.conflict_id);
            else if (responseData.write_session_conflict) showToast((responseData.error || 'Schreib-Session geändert.') + ' Dein Text bleibt erhalten.', true);
            else showToast('Konflikt: ' + responseData.error, true);
          } else {
            showToast(responseData.error || 'Speichern fehlgeschlagen', true);
          }
        } catch (err) {
          showToast(err?.name === 'AbortError' ? 'Senden hat zu lange gedauert. Bitte erneut versuchen.' : (err?.message || 'Netzwerkfehler'), true);
        } finally {
          window.writeSubmitState?.finish(submitToken);
        }
      }
    }

    async function openJournalConflict(conflictId) {
      const res = await apiFetch('/api/journal-conflicts/' + encodeURIComponent(conflictId));
      const data = await res.json();
      if (!res.ok) return showToast(data.error || 'Konflikt konnte nicht geladen werden', true);
      const overlay = document.createElement('div');
      overlay.className = 'fixed inset-0 z-[70] bg-black/70 p-4 flex items-center justify-center';
      const card = document.createElement('div');
      card.className = 'w-full max-w-4xl max-h-[90vh] overflow-auto rounded-xl bg-slate-900 p-4 text-white space-y-3';
      card.innerHTML = '<h2 class="text-lg font-semibold">Journal-Konflikt</h2><p class="text-sm text-slate-300">Wähle bewusst einen Stand. Dein ungesendeter Text bleibt bis dahin im Eingabefeld.</p><div class="grid gap-3 md:grid-cols-2"><label class="text-sm">Externer Stand</label><label class="text-sm">Dein Sendeversuch</label></div>';
      const fields = [data.external, data.jtracker].map(value => { const field = document.createElement('textarea'); field.readOnly = true; field.value = value; field.className = 'w-full h-48 rounded bg-slate-800 p-3 text-sm font-mono'; return field; });
      const grid = document.createElement('div'); grid.className = 'grid gap-3 md:grid-cols-2'; fields.forEach(field => grid.appendChild(field)); card.appendChild(grid);
      const actions = document.createElement('div'); actions.className = 'flex flex-wrap gap-2 justify-end';
      [['external', 'Externen Stand behalten'], ['jtracker', 'Meinen Sendeversuch übernehmen']].forEach(([choice, label]) => { const button = document.createElement('button'); button.textContent = label; button.className = 'rounded bg-green-700 px-3 py-2 text-sm'; button.onclick = async () => { button.disabled = true; const reply = await apiFetch('/api/journal-conflicts/' + encodeURIComponent(conflictId) + '/resolve', {method:'POST', body:JSON.stringify({choice})}); const result = await reply.json(); if (!reply.ok) { button.disabled=false; return showToast(result.error || 'Auflösung fehlgeschlagen', true); } overlay.remove(); showToast('Konflikt aufgelöst.'); }; actions.appendChild(button); });
      const cancel = document.createElement('button'); cancel.textContent = 'Später'; cancel.className = 'rounded bg-slate-700 px-3 py-2 text-sm'; cancel.onclick = () => overlay.remove(); actions.appendChild(cancel); card.appendChild(actions); overlay.appendChild(card); document.body.appendChild(overlay);
    }

    function showToast(msg, isError) {
      const toast = document.getElementById('toast');
      toast.textContent = msg;
      toast.className = `fixed bottom-6 left-1/2 -translate-x-1/2 px-5 py-2.5 rounded-lg shadow-xl text-sm font-medium opacity-0 pointer-events-none transition-opacity duration-300 z-50 ${isError ? 'bg-red-600' : 'bg-green-600'} text-white`;
      toast.classList.remove('opacity-0', 'pointer-events-none');
      // Fehler bleiben laenger stehen und lassen sich wegtippen - nach 2 s
      // war z. B. der Grund einer gescheiterten Nachbearbeitung nicht lesbar.
      const hide = () => toast.classList.add('opacity-0', 'pointer-events-none');
      toast.onclick = hide;
      clearTimeout(showToast.timer);
      showToast.timer = setTimeout(hide, isError ? 8000 : 2000);
    }

    function applyYoutubeMode(enabled) {
      document.documentElement.classList.toggle('youtube-mode', Boolean(enabled));
      document.getElementById('youtube-mode-banner')?.classList.toggle('hidden', !enabled);
      const toggle = document.getElementById('settings-youtube-mode');
      if (toggle) toggle.checked = Boolean(enabled);
    }

    // ─── tab switching ───────────────────────────────────────────────
    function switchTab(tabId) {
      document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
      document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active', 'border-green-400', 'text-green-400');
        btn.classList.add('border-transparent', 'text-gray-500');
      });

      const tab = document.getElementById('tab-' + tabId);
      tab.classList.remove('hidden');

      const btn = document.getElementById('tab-btn-' + tabId);
      btn.classList.add('active', 'border-green-400', 'text-green-400');
      btn.classList.remove('border-transparent', 'text-gray-500');

      if (tabId === 'dashboard') loadDashboard();
      if (tabId === 'family') { loadFamily(); visitFamily(); }
      if (tabId === 'brain' && typeof window.openBrainTab === 'function') window.openBrainTab();
    }

    // ─── dashboard ───────────────────────────────────────────────────
    let dashboardJournalDate = null;
    async function loadDashboard() {
      const container = document.getElementById('dashboard-container');
      container.innerHTML = '<p class="text-gray-500 text-center mt-8">Lade Einträge…</p>';

      try {
        // Der Hashtag-Katalog haengt an nichts, was initJournal tut: beide
        // parallel, sonst kostet der erste Tabwechsel zwei Umlaeufe statt einem.
        // Der Katalog muss nur vor dem Rendern da sein, nicht vor initJournal.
        await Promise.all([
          typeof window.loadFamilyHashtags === 'function' ? window.loadFamilyHashtags() : Promise.resolve(),
          // Initialize journal file first (ensures fresh state from disk)
          initJournal(),
        ]);

        const res = await apiFetch('/api/dashboard/refresh' + (dashboardJournalDate ? '?date=' + encodeURIComponent(dashboardJournalDate) : ''));
        if (res.status === 401) { showLogin(); return; }
        const entries = await res.json();

        const today = new Date().toLocaleDateString('sv-SE');
        const selectedDashboardDate = dashboardJournalDate || today;
        let html = `<div class="flex items-center justify-between mb-4">
          <h2 class="text-lg font-semibold text-gray-300">${dashboardJournalDate || 'Heutige'} Einträge</h2>
          <div class="flex items-center gap-3">
          <button type="button" data-journal-calendar="dashboard" data-date="${escapeHtmlAttr(selectedDashboardDate)}" data-max="${escapeHtmlAttr(today)}" class="relative h-8 w-8 shrink-0 rounded text-gray-400 hover:bg-gray-800 hover:text-gray-200" title="Journaldatum auswählen" aria-label="Journaldatum auswählen"><span class="pointer-events-none absolute inset-0 inline-flex items-center justify-center" aria-hidden="true"><svg viewBox="0 0 24 24" class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg></span></button>
          <button onclick="openManagedJournalHistory()" class="text-gray-400 hover:text-gray-200 transition text-sm" title="Versionen dieses Tages" aria-label="Versionen dieses Tages öffnen">Versionen</button>
          <button onclick="loadDashboard()" class="text-green-500 hover:text-green-400 transition flex items-center gap-1.5 text-sm">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            Aktualisieren
          </button>
          </div>
        </div>`;

        html += `<div id="dashboard-today" class="space-y-3" data-journal-date="${escapeHtmlAttr(selectedDashboardDate)}">`
          + dashboardDayHtml(entries, selectedDashboardDate, 'Keine Einträge für heute gefunden.')
          + '</div>';
        container.innerHTML = html;
        window.journlDashboardDays?.mount(selectedDashboardDate);
      } catch (err) {
        container.innerHTML += `<p class="text-red-400 text-center mt-8">Fehler beim Laden: ${err.message}</p>`;
      }
    }

    // Rendering one day of cards. Appending earlier days below lives in
    // dashboard-days.js, which calls back into this function.
    function dashboardDayHtml(entries, journalDate, emptyText) {
      if (!entries.length) return `<p class="text-gray-500 text-center mt-8">${escapeHtml(emptyText)}</p>`;
      let html = '';
      entries.forEach((entry, idx) => {
        const editId = simpleHash(entry.content) + '-' + journalDate + '-' + idx;
        const contentAttr = escapeHtmlAttr(entry.content);
        html += `
          <div class="bg-gray-900 rounded-xl p-4 border border-gray-800" id="entry-${editId}" data-content="${contentAttr}" data-filename="${escapeHtmlAttr(entry.file)}" data-journal-date="${escapeHtmlAttr(journalDate)}">
            <div class="text-xs text-gray-600 mb-2 font-mono flex items-center justify-between">
              <span>${entry.file}</span>
              ${(window.actionMenu || ((parts) => parts.join('')))([
                `<button type="button" data-menu-label="Bearbeiten" onclick="openDashboardWriteEditor('${editId}')" class="text-green-500 hover:text-green-400 transition" title="Im Schreiben-Tab bearbeiten" aria-label="Im Schreiben-Tab bearbeiten">${window.journlIcon?.('pencil') || ''}</button>`,
                `<button type="button" onclick="window.openWriteAgentFromDashboard('${editId}')" class="text-green-500 hover:text-green-400 transition text-xs" title="Schreib-Agent öffnen" aria-label="Schreib-Agent öffnen">${window.journlIcon?.('chat') || ''}</button>`,
                `<button type="button" onclick="window.openAssistantFromDashboard('${editId}')" class="text-green-500 hover:text-green-400 transition text-xs" title="An Agent senden" aria-label="An Agent senden">${window.journlIcon?.('agent') || ''}</button>`,
                `<button type="button" onclick="window.journlBuckets?.assign('${editId}')" class="text-green-500 hover:text-green-400 transition text-xs" title="Zu Sammelmappe" aria-label="Zu Sammelmappe">${window.journlIcon?.('folders') || ''}</button>`,
                `<button type="button" data-menu-variant="danger" onclick="deleteEntry('${editId}')" class="text-red-500 hover:text-red-400 transition" title="Eintrag löschen" aria-label="Eintrag löschen">${window.journlIcon?.('trash') || ''}</button>`,
              ], { label: 'Eintrag-Aktionen' })}
            </div>
            <div class="text-gray-200 whitespace-pre-wrap text-sm leading-relaxed">${typeof window.renderJournalText === 'function' ? window.renderJournalText(entry.content) : escapeHtml(entry.content)}</div>
            ${typeof window.renderJournalMedia === 'function' ? window.renderJournalMedia(entry.media, { retranscribe: true }) : ''}
          </div>`;
      });
      return html;
    }

    window.dashboardDayHtml = dashboardDayHtml;

    function chooseDashboardJournalDate(date) { const today = new Date().toLocaleDateString('sv-SE'); if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return; dashboardJournalDate = date === today ? null : date; loadDashboard(); }

    function escapeHtml(text) {
      if (typeof text !== 'string') return String(text);
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    function escapeHtmlAttr(text) {
      if (typeof text !== 'string') return String(text);
      return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    function simpleHash(str) {
      let h = 0;
      for (let i = 0; i < str.length; i++) {
        const char = str.charCodeAt(i);
        h = ((h << 5) - h) + char;
        h = h & h; // Convert to 32-bit integer
      }
      return Math.abs(h).toString(16);
    }

    async function openManagedJournalHistory() {
      const date = dashboardJournalDate || new Date().toLocaleDateString('sv-SE');
      const res = await apiFetch('/api/journals/' + date + '/history');
      const data = await res.json().catch(() => ({}));
      if (res.status === 404) return showToast('Für den ' + date.split('-').reverse().join('.') + ' gibt es noch keine verwaltete Versionshistorie.', true);
      if (!res.ok) return showToast(data.error || 'Versionen konnten nicht geladen werden.', true);
      const overlay = document.createElement('div');
      overlay.className = 'fixed inset-0 z-[70] bg-black/70 p-4 flex items-center justify-center';
      const card = document.createElement('div');
      card.className = 'w-full max-w-2xl max-h-[90vh] overflow-auto rounded-xl bg-slate-900 p-4 text-white space-y-3';
      card.innerHTML = '<h2 class="text-lg font-semibold">Versionen vom ' + escapeHtml(date) + '</h2><p class="text-sm text-slate-300">Eine Wiederherstellung ersetzt den Stand erst nach deiner Bestätigung und erzeugt selbst eine neue Version.</p>';
      const list = document.createElement('div'); list.className = 'space-y-2';
      for (const revision of data.revisions) {
        const row = document.createElement('div'); row.className = 'flex items-center justify-between gap-3 rounded bg-slate-800 p-3 text-sm';
        const time = new Date(revision.created_at).toLocaleString('de-DE', {dateStyle:'short', timeStyle:'medium'});
        const mediaLabel = revision.media_count ? ' · ' + revision.media_count + (revision.media_count === 1 ? ' Medium' : ' Medien') + ' ' + (window.journlIcon?.(revision.media_integrity === 'verified' ? 'check' : 'warning', 'h-3 w-3 inline align-[-0.1em]') || '') : '';
        row.innerHTML = '<div><div>' + time + ' · ' + escapeHtml(revision.summary || revision.reason) + '</div><div class="text-xs text-slate-400">' + escapeHtml(revision.origin) + ' · ' + escapeHtml(revision.id.slice(0, 8)) + mediaLabel + '</div></div>';
        const view = document.createElement('button'); view.type = 'button'; view.textContent = 'Ansehen'; view.className = 'cursor-pointer touch-manipulation rounded bg-slate-700 px-3 py-1.5 text-xs';
        view.onclick = async () => { const reply = await apiFetch('/api/journals/' + date + '/history/' + revision.id); const version = await reply.json().catch(() => ({})); if (!reply.ok) return showToast(version.error || 'Version nicht verfügbar.', true); const preview = document.createElement('div'); preview.className = 'mt-3 space-y-3'; const field = document.createElement('textarea'); field.readOnly = true; field.value = version.content; field.className = 'h-64 w-full rounded bg-slate-950 p-3 font-mono text-xs'; preview.appendChild(field); if (typeof window.renderJournalMedia === 'function') { const media = document.createElement('div'); media.innerHTML = window.renderJournalMedia(version.media); preview.appendChild(media); } card.querySelector('[data-version-preview]')?.remove(); preview.dataset.versionPreview = ''; card.insertBefore(preview, list); preview.scrollIntoView({block:'nearest'}); };
        const actions = document.createElement('div'); actions.className = 'flex gap-2 shrink-0'; actions.appendChild(view);
        if (revision.id !== data.current_revision_id) {
          const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Wiederherstellen'; restore.className = 'cursor-pointer touch-manipulation rounded bg-amber-700 px-3 py-1.5 text-xs';
          restore.onclick = async () => { if (!window.confirm('Diesen alten Stand als neue aktuelle Version wiederherstellen?')) return; restore.disabled = true; const reply = await apiFetch('/api/journals/' + date + '/history/' + revision.id + '/restore', {method:'POST', body:JSON.stringify({})}); const result = await reply.json().catch(() => ({})); if (!reply.ok) { restore.disabled = false; return showToast(result.error || 'Wiederherstellung fehlgeschlagen.', true); } overlay.remove(); showToast('Version wiederhergestellt.'); loadDashboard(); };
          actions.appendChild(restore);
        }
        row.appendChild(actions); list.appendChild(row);
      }
      if (!data.revisions.length) list.textContent = 'Noch keine Versionen vorhanden.';
      const close = document.createElement('button'); close.textContent = 'Schließen'; close.className = 'rounded bg-slate-700 px-3 py-2 text-sm'; close.onclick = () => overlay.remove(); card.appendChild(list); card.appendChild(close); overlay.appendChild(card); document.body.appendChild(overlay);
    }

    async function deleteEntry(editId) {
      const entry = document.getElementById('entry-' + editId);
      const content = entry.dataset.content;
      const filename = entry.dataset.filename;
      // Appended earlier days live in the same list, so the entry itself has to
      // say which journal it belongs to - the selected date no longer does.
      const journalDate = entry.dataset.journalDate || dashboardJournalDate;
      if (!content || !confirm('Eintrag wirklich löschen?')) return;

      try {
        const res = await apiFetch('/api/dashboard/delete', {
          method: 'POST',
          body: JSON.stringify({ content, filename, journal_date: journalDate })
        });

        if (res.ok) {
          const entry = document.getElementById('entry-' + editId);
          entry.classList.add('wipe-out');
          setTimeout(() => {
            const day = entry.parentElement;
            entry.remove();
            // Only the day that just lost its last entry reports empty; other
            // days stay untouched below it.
            if (day && !day.querySelector('[data-content]')) {
              day.insertAdjacentHTML('beforeend', '<p class="text-gray-500 text-center mt-8">Keine Einträge für diesen Tag gefunden.</p>');
            }
          }, 350);
          showToast('Eintrag gelöscht');
        } else if (res.status === 409) {
          const err = await res.json();
          showToast('Eintrag wurde extern geändert. Seite neu laden: ' + JSON.stringify(err), true);
          setTimeout(() => location.reload(), 2000);
        } else {
          const err = await res.json();
          showToast('Fehler: ' + JSON.stringify(err), true);
        }
      } catch (err) {
        showToast('Netzwerkfehler', true);
      }
    }

    // Shared reference for the desktop resize logic below.
    const inputArea = document.getElementById('input-area');

    // ─── keyboard shortcut: Cmd/Ctrl+Enter to submit ────────────────
    document.getElementById('simple-input').addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSubmit();
      }
    });
    document.getElementById('form-container').addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        handleSubmit();
      }
    });

    // start on write tab
    switchTab('write');
    clearRestoredSubmittedInput();

    // ─── init app info box ────────────────────────────────────────
    const appInfoBox = document.getElementById('app-info-box');
    function updateAppInfoBox() {
      if (!appInfoBox) return;
      const isAiMode = document.getElementById('template-select').value === AI_MODE_ID;
      if (isAiMode) {
        appInfoBox.textContent = 'KI-Modus aktiv: Text mit #ai-Hashtag eingeben und „KI Senden“ klicken. KI-Modus: ⌥⌘K (Windows/Linux: Ctrl+Alt+K). Rückgängig: ⌘/Ctrl+Z · Wiederholen: ⇧⌘/Ctrl+Z (Windows auch Ctrl+Y).\nHashtag-Vorschläge: ↑/↓ auswählen, Enter/Tab übernehmen, Esc schließen. Tab rückt ohne offenes Vorschlagsmenü ein.';
      } else {
        appInfoBox.textContent = 'Text eingeben und „Senden“ klicken oder ⌘/Ctrl+Enter drücken. KI-Modus: ⌥⌘K (Windows/Linux: Ctrl+Alt+K). Rückgängig: ⌘/Ctrl+Z · Wiederholen: ⇧⌘/Ctrl+Z (Windows auch Ctrl+Y).\nHashtag-Vorschläge: ↑/↓ auswählen, Enter/Tab übernehmen, Esc schließen. Tab rückt ohne offenes Vorschlagsmenü ein.';
      }
    }
    updateAppInfoBox();
