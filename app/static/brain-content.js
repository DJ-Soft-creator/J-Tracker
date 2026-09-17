// Brain file, session and inline-editor interactions.
// AI maintenance contract: brain.js owns shared state/search/catalogs and must
// load first. This file owns file lists, editor mutations and metadata actions.
function formatBrainModified(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('de-DE', {
    dateStyle: 'medium',
    timeStyle: 'short',
  });
}


function renderAiSession(file) {
  const session = file.ai_session;
  const events = (session.events || []).map((event) => {
    const isAgent = !['user-request', 'user-reply', 'permission-decision'].includes(event.type);
    const permissionActions = event.type === 'permission-request' && !session.archived && event.permission_id
      ? `<div class="mt-2 flex gap-2"><button type="button" data-ai-session-permission="allow" data-session-id="${escapeHtmlAttr(session.session_id)}" data-permission-id="${escapeHtmlAttr(event.permission_id)}" class="rounded bg-green-700 px-2 py-1 text-xs text-white hover:bg-green-600">Erlauben</button><button type="button" data-ai-session-permission="deny" data-session-id="${escapeHtmlAttr(session.session_id)}" data-permission-id="${escapeHtmlAttr(event.permission_id)}" class="rounded bg-red-800 px-2 py-1 text-xs text-white hover:bg-red-700">Ablehnen</button></div>` : '';
    return `<section class="rounded-lg border border-gray-800 bg-gray-950/50 p-3"><div class="mb-1 flex flex-wrap items-center gap-2 text-xs text-gray-500"><span>${escapeHtml(event.type || 'event')}</span><span>${escapeHtml(formatBrainModified(event.at || ''))}</span></div><div class="whitespace-pre-wrap text-sm leading-relaxed ${isAgent ? 'journal-ai-text' : 'text-gray-200'}">${typeof window.renderJournalText === 'function' ? window.renderJournalText(event.body || '') : escapeHtml(event.body || '')}</div>${permissionActions}</section>`;
  }).join('') || '<p class="text-sm text-gray-600">Noch keine Session-Ereignisse.</p>';
  const interaction = session.kind === 'agent-project'
    ? `<div class="mt-3 text-xs text-gray-500">Agent-Projekt · Ordner <span class="font-mono">${escapeHtml((session.project_root_id || '') + ':' + (session.project_subpath || '.'))}</span> · Folgeaufträge über „Projekt fortsetzen".</div>`
    : session.read_only
    ? `<div class="mt-3 text-xs text-gray-500">DB-Hintergrundsession · ${escapeHtml(session.latest_job_status || session.status || '')}</div>`
    : session.archived ? '<span class="text-xs text-amber-400">Archiviert</span>' : `<div class="mt-3 flex gap-2"><textarea data-ai-session-reply rows="2" placeholder="Antwort an den Agenten..." class="min-w-0 flex-1 rounded bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100"></textarea><button type="button" data-ai-session-send data-session-id="${escapeHtmlAttr(session.session_id)}" class="self-end rounded bg-sky-700 px-3 py-2 text-sm font-semibold text-white hover:bg-sky-600">Senden</button></div>`;
  return `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-sky-900/60 rounded-xl p-4">
    <div class="mb-3 flex flex-wrap items-start justify-between gap-2"><div><div class="flex flex-wrap items-center gap-2 text-xs"><span class="rounded-full border border-sky-800 bg-sky-950/50 px-2 py-0.5 text-sky-200">AI-Session</span>${session.db_native ? '<span class="rounded-full border border-violet-800 px-2 py-0.5 text-violet-200">DB</span>' : ''}<span class="text-sky-200">${session.kind === 'agent-project' ? escapeHtml(session.project_label || session.workflow_tag) : '#' + escapeHtml(session.workflow_tag)}</span>${session.kind === 'agent-project' ? `<span class="rounded-full border border-green-800 px-2 py-0.5 text-green-300">${escapeHtml(session.project_mode || '')}</span>` : ''}<span class="text-gray-500">${escapeHtml(session.agent)} · ${escapeHtml(session.model)}</span></div><div class="mt-1 break-all font-mono text-xs text-gray-600">${escapeHtml(file.path)}</div></div><div class="flex flex-wrap items-center gap-3">${session.kind === 'agent-project' ? `<button type="button" data-agent-project-open data-session-id="${escapeHtmlAttr(session.session_id)}" class="text-xs text-green-400 hover:text-green-300" title="Dieses Agent-Projekt im Schreiben-Tab weiterbearbeiten">&#9654; Projekt fortsetzen</button>` : ''}${session.archived || session.read_only ? '' : `<button type="button" data-ai-session-archive data-session-id="${escapeHtmlAttr(session.session_id)}" class="text-xs text-gray-400 hover:text-white">Abschließen &amp; archivieren</button>`}</div></div>
    <div class="space-y-2">${events}</div>${interaction}
  </article>`;
}


function bindAiSessionActions(container) {
  container.querySelectorAll('[data-agent-project-open]').forEach((button) => button.addEventListener('click', () => {
    if (typeof window.openAgentProjectSession !== 'function') return;
    if (typeof window.switchTab === 'function') window.switchTab('write');
    window.openAgentProjectSession(button.dataset.sessionId);
  }));
  container.querySelectorAll('[data-ai-session-send]').forEach((button) => button.addEventListener('click', async () => {
    const input = button.parentElement.querySelector('[data-ai-session-reply]');
    const text = input.value.trim();
    if (!text) return;
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/reply`, { method: 'POST', body: JSON.stringify({ text }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Antwort konnte nicht gespeichert werden.', true); button.disabled = false; return; }
    showToast('Folgeauftrag an Pi eingeplant.');
    await loadBrainFiles('agent');
    pollAiSessionJob(data.job_id);
  }));
  container.querySelectorAll('[data-ai-session-permission]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/permission`, { method: 'POST', body: JSON.stringify({ permission_id: button.dataset.permissionId, decision: button.dataset.aiSessionPermission }) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Entscheidung konnte nicht gespeichert werden.', true); button.disabled = false; return; }
    showToast('Permission-Entscheidung gespeichert.');
    await loadBrainFiles('agent');
  }));
  container.querySelectorAll('[data-ai-session-archive]').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm('Session abschließen und archivieren?')) return;
    button.disabled = true;
    const response = await apiFetch(`/api/ai-sessions/${encodeURIComponent(button.dataset.sessionId)}/archive`, { method: 'POST', body: JSON.stringify({}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) { showToast(data.error || 'Session konnte nicht archiviert werden.', true); button.disabled = false; return; }
    showToast('Session archiviert.');
    await loadBrainFiles('agent');
  }));
}


async function pollAiSessionJob(jobId) {
  if (!jobId) return;
  for (let attempt = 0; attempt < 900; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    try {
      const response = await apiFetch(`/api/write-ai/jobs/${encodeURIComponent(jobId)}`, { cache: 'no-store' });
      const job = await response.json().catch(() => ({}));
      if (!response.ok) return;
      if (['completed', 'proposed', 'applied', 'error', 'cancelled'].includes(job.status)) {
        if (brainState.mode === 'ai-agent') await loadBrainFiles('agent');
        if (job.status === 'completed') showToast('Pi-Folgeantwort ist fertig.');
        else if (job.status === 'error') showToast(job.error || 'Pi-Folgeauftrag ist fehlgeschlagen.', true);
        return;
      }
    } catch { return; }
  }
}


function renderBrainFiles(kind, files) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  const isNote = kind === 'note';
  const isAgent = kind === 'agent';
  const createLabel = isNote ? '+ Notiz' : '+ Projekt';
  const emptyLabel = isAgent ? 'Keine passenden Agent-Sitzungen gefunden.' : isNote ? 'Keine passenden Notizen gefunden.' : 'Keine passenden Projekte gefunden.';
  const controls = isAgent ? '' : `<div class="flex items-center justify-end mb-3">
    <button type="button" data-brain-create-file class="text-sm text-green-400 hover:text-green-300">${createLabel}</button>
  </div>`;
  if (!files.length) {
    container.innerHTML = controls + `<p class="text-gray-500 text-center mt-8">${emptyLabel}</p>`;
  } else {
    container.innerHTML = controls + files.map((file) => file.ai_session ? renderAiSession(file) : `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass('personal')}">Privat</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(file.path)}</span>
          ${brainTagBadges(file.tags)}
        </div>
        <div class="shrink-0">${brainEditButton(file.doc_id)}</div>
      </div>
      <div data-brain-display>
        <div class="text-base font-medium text-gray-100">${escapeHtml(file.title)}</div>
        <div class="mt-1 text-xs text-gray-500">Geändert ${escapeHtml(formatBrainModified(file.modified_at))}</div>
      </div>
    </article>`).join('');
  }
  const createButton = container.querySelector('[data-brain-create-file]');
  if (createButton) {
    createButton.addEventListener('click', async () => {
      createButton.disabled = true;
      try {
        await createBrainFile(kind);
      } finally {
        if (createButton.isConnected) createButton.disabled = false;
      }
    });
  }
  bindBrainCardActions(container);
  bindAiSessionActions(container);
}


async function loadBrainFiles(kind) {
  const mode = kind === 'note' ? 'notes' : kind === 'agent' ? 'ai-agent' : 'projects';
  const endpoint = kind === 'note' ? '/api/brain/notes' : kind === 'agent' ? '/api/brain/ai-agent' : '/api/brain/projects';
  if (brainState.mode !== mode) return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Dateien werden geladen...</p>';
  const request = beginBrainRequest('files');
  const params = brainQueryParams(false);
  params.delete('order');
  params.delete('kind');
  const query = brainElement('brain-query').value.trim();
  try {
    const response = await apiFetch(`${endpoint}?${params.toString()}`, {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Dateien konnten nicht geladen werden.')}</p>`;
      return;
    }
    if (kind === 'project' && !query) setBrainProjects(data.projects || []);
    renderBrainFiles(kind, kind === 'agent' ? (data.sessions || []) : (data[mode] || []));
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden der Dateien.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


function renderBrainFamilyFiles(notes, projects) {
  closeBrainInlineEditor();
  const container = brainElement('brain-browser');
  const renderSection = (title, kind, files) => {
    const createLabel = kind === 'note' ? '+ Familiennotiz' : '+ Familienprojekt';
    const emptyLabel = kind === 'note' ? 'Keine passenden Familiennotizen gefunden.' : 'Keine passenden Familienprojekte gefunden.';
    const cards = files.length ? files.map((file) => `<article data-brain-card data-doc-id="${escapeHtmlAttr(file.doc_id)}" class="bg-gray-900 border border-gray-800 rounded-xl p-4 brain-family-entry">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="min-w-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-500">
          <span class="border rounded-full px-2 py-0.5 ${brainSourceClass('family')}">Familie</span>
          <span class="break-all font-mono text-gray-600">${escapeHtml(file.path)}</span>
          ${brainTagBadges(file.tags)}
        </div>
        <div class="flex shrink-0 items-center justify-end">
          ${brainActionMenu([
            file.management?.can_manage ? brainManageButton(file.doc_id, kind === 'note' ? 'Notiz' : 'Projekt') : '',
            brainEditButton(file.doc_id),
          ])}
        </div>
      </div>
      <div data-brain-display>
        <div class="text-base font-medium text-gray-100">${escapeHtml(file.title)}</div>
        <div class="mt-1 text-xs text-gray-500">Geändert ${escapeHtml(formatBrainModified(file.modified_at))}</div>
      </div>
    </article>`).join('') : `<p class="text-gray-500 text-center py-4">${emptyLabel}</p>`;
    return `<section class="space-y-3"><div class="flex items-center justify-between gap-3"><h2 class="text-sm font-medium text-gray-300">${title}</h2><button type="button" data-brain-create-family="${kind}" class="text-sm text-green-400 hover:text-green-300">${createLabel}</button></div>${cards}</section>`;
  };
  container.innerHTML = `<div class="space-y-6">${renderSection('Familienprojekte', 'project', projects)}${renderSection('Familiennotizen', 'note', notes)}</div>`;
  container.querySelectorAll('[data-brain-create-family]').forEach((button) => {
    button.addEventListener('click', async () => {
      button.disabled = true;
      try {
        await createFamilyBrainFile(button.dataset.brainCreateFamily);
      } finally {
        if (button.isConnected) button.disabled = false;
      }
    });
  });
  bindBrainCardActions(container);
}


async function loadBrainFamilyFiles() {
  if (brainState.mode !== 'family') return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Familien-Dateien werden geladen...</p>';
  const request = beginBrainRequest('files');
  const params = brainQueryParams(false);
  params.delete('order');
  params.delete('kind');
  try {
    const response = await apiFetch(`/api/brain/family?${params.toString()}`, {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Familien-Dateien konnten nicht geladen werden.')}</p>`;
      return;
    }
    renderBrainFamilyFiles(data.notes || [], data.projects || []);
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden der Familien-Dateien.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadBrainCurrentMode() {
  if (brainState.catalogOpen) return;
  if (brainState.mode === 'tasks') await loadBrainTasks();
  else if (brainState.mode === 'notes') await loadBrainFiles('note');
  else if (brainState.mode === 'projects') await loadBrainFiles('project');
  else if (brainState.mode === 'ai-agent') await loadBrainFiles('agent');
  else if (brainState.mode === 'agent-audit') await loadAgentAudit();
  else if (brainState.mode === 'family') await loadBrainFamilyFiles();
  else await loadBrainResults();
}


async function loadAgentAudit(offset = 0) {
  if (brainState.mode !== 'agent-audit') return;
  const container = brainElement('brain-browser');
  container.innerHTML = '<p class="text-gray-500 text-center mt-8">Agent-Audit wird geladen...</p>';
  try {
    const query = brainElement('brain-query').value.trim();
    const params = new URLSearchParams({ offset: String(offset), limit: '100' });
    if (query) params.set('q', query);
    const response = await apiFetch(`/api/ai-sessions/audit?${params.toString()}`, { cache: 'no-store' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Agent-Audit konnte nicht geladen werden.')}</p>`;
      return;
    }
    const entries = data.entries || [];
    const cards = entries.map((item) => `<article class="rounded-xl border border-violet-900/60 bg-gray-900 p-4">
      <div class="flex flex-wrap items-start justify-between gap-2 text-xs"><div><span class="font-semibold text-violet-200">${escapeHtml(item.username || '(ohne Name)')}</span><span class="ml-2 font-mono text-gray-500">${escapeHtml(item.user_id)}</span></div><span class="rounded border border-gray-700 px-2 py-0.5 text-gray-300">${escapeHtml(item.status)}</span></div>
      <div class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-gray-500"><span>${escapeHtml(formatBrainModified(item.created_at || ''))}</span><span>#${escapeHtml(item.workflow_tag)}</span><span>${escapeHtml(item.delivery || 'interactive')}</span><span>${escapeHtml(item.model)}</span></div>
      <div class="mt-2 break-all font-mono text-[11px] text-gray-600">Job ${escapeHtml(item.id)} · Session ${escapeHtml(item.session_id)} · ${escapeHtml(item.source_path)}</div>
      <section class="mt-3 rounded-lg border border-gray-800 bg-gray-950/50 p-3"><div class="mb-1 text-xs text-gray-500">Anfrage</div><div class="whitespace-pre-wrap text-sm text-gray-200">${escapeHtml(item.request || '')}</div></section>
      ${item.agent_request && item.agent_request !== item.request ? `<details class="mt-2 text-xs text-gray-500"><summary class="cursor-pointer">An Pi übermittelter Auftrag ohne Routing-Hashtags</summary><div class="mt-1 whitespace-pre-wrap rounded border border-gray-800 bg-gray-950/50 p-2 text-gray-300">${escapeHtml(item.agent_request)}</div></details>` : ''}
      ${item.response ? `<section class="mt-2 rounded-lg border border-gray-800 bg-gray-950/50 p-3"><div class="mb-1 text-xs text-gray-500">Antwort</div><div class="whitespace-pre-wrap text-sm journal-ai-text">${typeof window.renderJournalText === 'function' ? window.renderJournalText(item.response) : escapeHtml(item.response)}</div></section>` : ''}
      ${item.error ? `<p class="mt-2 text-xs text-red-300">${escapeHtml(item.error)}</p>` : ''}
    </article>`).join('');
    const start = data.total ? data.offset + 1 : 0;
    const end = Math.min(data.total || 0, data.offset + entries.length);
    const navigation = `<div class="mt-3 flex items-center justify-between gap-3"><button type="button" data-audit-offset="${Math.max(0, data.offset - data.limit)}" class="text-sm text-violet-300 disabled:opacity-30"${data.offset <= 0 ? ' disabled' : ''}>Neuere</button><span class="text-xs text-gray-600">${start}–${end} von ${data.total || 0}</span><button type="button" data-audit-offset="${data.offset + data.limit}" class="text-sm text-violet-300 disabled:opacity-30"${end >= data.total ? ' disabled' : ''}>Ältere</button></div>`;
    container.innerHTML = `<div class="mb-3 text-xs text-gray-500">Agent-Anfragen aller Nutzer · nur für Admins</div>${cards || '<p class="text-gray-500 text-center mt-8">Keine passenden Agent-Anfragen.</p>'}${navigation}`;
    container.querySelectorAll('[data-audit-offset]').forEach((button) => button.addEventListener('click', () => loadAgentAudit(Number(button.dataset.auditOffset) || 0)));
  } catch {
    container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden des Agent-Audits.</p>';
  }
}


async function loadBrainBootstrap(preserveResults = false) {
  const container = brainElement('brain-browser');
  if (!preserveResults) {
    container.innerHTML = '<p class="text-gray-500 text-center mt-8">Brain wird geladen...</p>';
  }
  const request = beginBrainRequest('bootstrap');
  try {
    const params = brainQueryParams(false);
    params.set('limit', '100');
    const response = await apiFetch('/api/brain/bootstrap?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(data.error || 'Brain konnte nicht geladen werden.')}</p>`;
      return;
    }
    if (typeof window.setHashtagCatalog === 'function') window.setHashtagCatalog(data.catalog || {});
    setBrainProjects(data.projects || []);
    setBrainTags(data.tags || [], data.index_pending);
    renderBrainResults(data.results || [], data);
    brainState.supportLoaded = true;
  } catch {
    if (isCurrentBrainRequest(request)) {
      container.innerHTML = '<p class="text-red-400 text-center mt-8">Netzwerkfehler beim Laden von Brain.</p>';
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function loadBrain() {
  if (!brainElement('brain-browser')) return;
  brainElement('brain-mode-agent-audit')?.classList.toggle('hidden', !window.config?.current_user?.admin);
  if (brainState.catalogOpen) return;
  if (brainState.supportLoaded) {
    await loadBrainCurrentMode();
    return;
  }
  if (brainState.mode === 'search') {
    await loadBrainBootstrap();
    return;
  }
  const generation = brainState.generation;
  await Promise.all([
    loadBrainProjects(),
    loadBrainTagsUnlessSearchCarriesThem(),
    typeof window.loadFamilyHashtags === 'function' ? window.loadFamilyHashtags() : Promise.resolve(),
  ]);
  if (generation !== brainState.generation) return;
  brainState.supportLoaded = true;
  await loadBrainCurrentMode();
}
window.loadBrain = loadBrain;


function openBrainTab() {
  const query = brainElement('brain-query');
  if (query) query.focus({ preventScroll: true });
  if (brainState.requests.has('bootstrap')) return;
  if (!brainState.supportLoaded) loadBrain();
  else if (brainState.mode === 'search' || brainState.mode === 'journals') loadBrainBootstrap(true);
}
window.openBrainTab = openBrainTab;


function loadBrainForQuery() {
  return brainState.supportLoaded ? loadBrainCurrentMode() : loadBrain();
}


function setBrainMode(mode) {
  if (!['search', 'journals', 'db-journals', 'tasks', 'notes', 'projects', 'ai-agent', 'agent-audit', 'family'].includes(mode)) return;
  if (mode !== brainState.mode || brainState.catalogOpen) invalidateBrainContext();
  brainState.catalogOpen = false;
  brainState.mode = mode;
  brainState.contentFilter = mode === 'journals' ? 'journal' : 'all';
  brainElement('brain-mode-search').className = mode === 'search' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-journals').className = mode === 'journals' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  if (brainElement('brain-mode-db-journals')) brainElement('brain-mode-db-journals').className = mode === 'db-journals' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-tasks').className = mode === 'tasks' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-notes').className = mode === 'notes' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-projects').className = mode === 'projects' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-ai-agent').className = mode === 'ai-agent' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  brainElement('brain-mode-agent-audit').className = `${window.config?.current_user?.admin ? '' : 'hidden '}${mode === 'agent-audit' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300'}`;
  brainElement('brain-mode-family').className = mode === 'family' ? 'shrink-0 text-sm text-green-400 font-medium' : 'shrink-0 text-sm text-gray-500 hover:text-gray-300';
  updateBrainModeControls();
  showBrainBrowser();
  loadBrain();
}


function updateBrainModeControls() {
  const query = brainElement('brain-query');
  const order = brainElement('brain-order');
  const tagArea = brainElement('brain-tag-filter-area');
  const filterControls = brainElement('brain-filter-controls');
  if (query) {
    query.placeholder = {
      search: 'Journale, Notizen, Projekte und Agent-Sitzungen durchsuchen',
      journals: 'Journale durchsuchen',
      'db-journals': 'Bestätigte DB-Journale durchsuchen',
      tasks: 'Aufgaben durchsuchen',
      notes: 'Notizen durchsuchen',
      projects: 'Projekte durchsuchen',
      'ai-agent': 'Agent-Sitzungen durchsuchen',
      'agent-audit': 'Anfragen aller Benutzer durchsuchen',
      family: 'Familiennotizen und -projekte durchsuchen',
    }[brainState.mode];
  }
  if (order) order.classList.toggle('hidden', !['search', 'journals'].includes(brainState.mode));
  if (filterControls) filterControls.classList.toggle('hidden', brainState.mode === 'db-journals');
  if (tagArea) tagArea.classList.toggle('hidden', brainState.mode === 'db-journals');
}


function showBrainBrowser() {
  abortBrainRequest('document');
  closeBrainInlineEditor();
  // Der Chat ist ein eigener Bereich neben dem Browser: wer zurueck zu einer
  // Liste wechselt, verlaesst ihn.
  window.closeBrainChat?.();
  brainElement('brain-browser').classList.remove('hidden');
}


function closeBrainInlineEditor() {
  const editor = brainState.editor;
  if (!editor?.card) return;
  editor.card.querySelector('[data-brain-display]')?.classList.remove('hidden');
  editor.card.querySelector('[data-brain-inline-editor]')?.remove();
  brainState.editor = null;
}


async function openBrainDocument(docId, fingerprint, card = null) {
  const targetCard = card || Array.from(document.querySelectorAll('[data-brain-card]')).find(
    (item) => item.dataset.docId === docId,
  );
  if (!targetCard) return;
  closeBrainInlineEditor();
  const request = beginBrainRequest('document');
  try {
    const params = new URLSearchParams({ doc_id: docId, fingerprint });
    const response = await apiFetch('/api/brain/document?' + params.toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Datei konnte nicht geoeffnet werden.', true);
      return;
    }
    const display = targetCard.querySelector('[data-brain-display]');
    if (display) display.classList.add('hidden');
    const inlineEditor = document.createElement('div');
    inlineEditor.dataset.brainInlineEditor = '';
    inlineEditor.className = 'mt-3';
    const agentSession = data.agent_session;
    const agentControls = !data.read_only && agentSession && !agentSession.repair
      ? `<span class="self-center text-xs text-sky-300">KI-Session: ${escapeHtml(agentSession.status)}</span>${agentSession.status === 'active' ? '<button type="button" data-brain-agent-action="update" class="text-xs text-sky-300 hover:text-sky-200">Session aktualisieren</button><button type="button" data-brain-agent-action="pause" class="text-xs text-yellow-300 hover:text-yellow-200">Pausieren</button><button type="button" data-brain-agent-action="end" class="text-xs text-red-300 hover:text-red-200">Beenden</button>' : ''}${agentSession.status === 'paused' ? '<button type="button" data-brain-agent-action="resume" class="text-xs text-green-300 hover:text-green-200">Fortsetzen</button>' : ''}`
      : (agentSession?.state === 'repair' ? '<span class="self-center text-xs text-red-300">KI-Metadaten defekt – Session bitte beenden oder reparieren.</span>' : '');
    inlineEditor.innerHTML = `<textarea data-brain-inline-content class="w-full min-h-64 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 font-mono text-sm leading-relaxed text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y" rows="12" ${data.read_only ? 'readonly' : ''}>${escapeHtml(data.content || '')}</textarea><div class="mt-2 flex flex-wrap gap-2">${data.read_only ? '' : '<button type="button" data-brain-inline-save class="bg-green-600 hover:bg-green-500 text-white text-xs py-1.5 px-3 rounded transition">Speichern</button>'}<button type="button" data-brain-inline-cancel class="bg-gray-700 hover:bg-gray-600 text-white text-xs py-1.5 px-3 rounded transition">${data.read_only ? 'Schließen' : 'Abbrechen'}</button>${agentControls}${data.management?.can_manage ? '<button type="button" data-brain-inline-manage class="text-green-500 hover:text-green-400 text-xs px-2">Notiz/Projekt verwalten</button>' : ''}</div>`;
    targetCard.appendChild(inlineEditor);
    const textarea = inlineEditor.querySelector('[data-brain-inline-content]');
    brainState.editor = { ...data, card: targetCard, textarea };
    inlineEditor.querySelector('[data-brain-inline-cancel]').addEventListener('click', closeBrainInlineEditor);
    inlineEditor.querySelector('[data-brain-inline-save]')?.addEventListener('click', saveBrainDocument);
    inlineEditor.querySelector('[data-brain-inline-manage]')?.addEventListener('click', () => openBrainAccessManager(data));
    inlineEditor.querySelectorAll('[data-brain-agent-action]').forEach((button) => button.addEventListener('click', () => updateBrainAgentSession(button.dataset.brainAgentAction)));
    if (data.block_start_line) {
      const lines = textarea.value.split('\n');
      const offset = lines.slice(0, data.block_start_line - 1).join('\n').length + (data.block_start_line > 1 ? 1 : 0);
      textarea.setSelectionRange(offset, offset);
      textarea.scrollTop = Math.max(0, (data.block_start_line - 3) * 20);
    }
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Oeffnen der Datei.', true);
  } finally {
    finishBrainRequest(request);
  }
}


async function updateBrainAgentSession(action) {
  const editor = brainState.editor;
  if (!editor || brainState.requests.has('agent-session')) return;
  const request = beginBrainRequest('agent-session', false);
  try {
    const response = await apiFetch('/api/brain/document/agent-session', {
      method: 'POST',
      body: JSON.stringify({ doc_id: editor.doc_id, action, confirm_family_edit: editor.source === 'family' }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Session konnte nicht aktualisiert werden.');
    showToast(`KI-Session ${data.agent_session?.status || 'aktualisiert'}.`);
    closeBrainInlineEditor();
    await loadBrainCurrentMode();
  } catch (error) {
    if (isCurrentBrainRequest(request)) showToast(error.message || 'Session konnte nicht aktualisiert werden.', true);
  } finally {
    finishBrainRequest(request);
  }
}
window.openBrainDocument = openBrainDocument;


async function saveBrainDocument() {
  const editor = brainState.editor;
  if (!editor || editor.read_only || brainState.requests.has('document-save')) return;
  const saveButton = editor.card.querySelector('[data-brain-inline-save]');
  saveButton.disabled = true;
  const request = beginBrainRequest('document-save', false);
  try {
    const response = await apiFetch('/api/brain/document', {
      method: 'PUT',
      body: JSON.stringify({
        doc_id: editor.doc_id,
        content: editor.textarea.value,
        content_hash: editor.content_hash,
        confirm_family_edit: editor.source === 'family',
      }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request) || brainState.editor !== editor) return;
    if (!response.ok) {
      showToast(data.error || 'Speichern fehlgeschlagen.', true);
      return;
    }
    showToast('Originaldatei gespeichert. Quelle und Suchindex wurden sofort aktualisiert.');
    await Promise.all([loadBrainProjects(), loadBrainTagsUnlessSearchCarriesThem()]);
    closeBrainInlineEditor();
    await loadBrainCurrentMode();
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Speichern.', true);
  } finally {
    finishBrainRequest(request);
    if (saveButton.isConnected) saveButton.disabled = false;
  }
}


function openBrainAccessManager(editor) {
  if (!editor?.management?.can_manage) return;
  const users = Array.from(new Map(
    (((typeof config !== 'undefined' && config?.users) || []).map((user) => [user.id, user])),
  ).values());
  const assigned = new Set(editor.management.assigned_users || []);
  const entityLabel = editor.path?.startsWith('projects/') ? 'Projekt' : 'Notiz';
  const modal = document.createElement('div');
  modal.className = 'fixed inset-0 z-50 flex items-center justify-center px-4 bg-black/50';
  modal.innerHTML = `<div class="w-full max-w-md rounded-xl border border-gray-800 bg-gray-900 p-5 shadow-xl"><h2 class="text-lg font-semibold">${entityLabel} verwalten</h2><p class="mt-1 text-xs text-gray-500">Der Ersteller behält immer Zugriff.</p><div class="mt-4 space-y-2">${users.map((user) => `<label class="flex items-center gap-2 text-sm text-gray-200"><input type="checkbox" data-brain-access-user="${escapeHtmlAttr(user.id)}" ${assigned.has(user.id) ? 'checked' : ''} class="accent-green-500" /><span>${escapeHtml(user.username || user.id)}</span></label>`).join('')}</div><div class="mt-5 flex gap-2"><button type="button" data-brain-access-save class="rounded bg-green-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-green-500">Speichern</button><button type="button" data-brain-access-cancel class="rounded bg-gray-700 px-3 py-1.5 text-sm text-white hover:bg-gray-600">Abbrechen</button></div></div>`;
  document.body.appendChild(modal);
  modal.querySelector('[data-brain-access-cancel]').addEventListener('click', () => modal.remove());
  modal.querySelector('[data-brain-access-save]').addEventListener('click', async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    const assignedUsers = Array.from(modal.querySelectorAll('[data-brain-access-user]:checked')).map((input) => input.dataset.brainAccessUser);
    try {
      const response = await apiFetch('/api/brain/document/access', {
        method: 'PUT',
        body: JSON.stringify({ doc_id: editor.doc_id, assigned_users: assignedUsers, content_hash: editor.content_hash }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Zugriff konnte nicht gespeichert werden.');
      editor.content_hash = data.content_hash;
      editor.management.assigned_users = data.assigned_users || [];
      modal.remove();
      showToast('Zugriff gespeichert.');
      if (brainState.editor?.doc_id === editor.doc_id) closeBrainInlineEditor();
      await loadBrainCurrentMode();
    } catch (error) {
      showToast(error.message, true);
      button.disabled = false;
    }
  });
}


async function openBrainAccessManagerForDocument(docId) {
  const request = beginBrainRequest('document');
  try {
    const response = await apiFetch('/api/brain/document?' + new URLSearchParams({ doc_id: docId }).toString(), {
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Datei konnte nicht geöffnet werden.', true);
      return;
    }
    openBrainAccessManager(data);
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Öffnen der Verwaltung.', true);
  } finally {
    finishBrainRequest(request);
  }
}


async function toggleBrainTask(docId, fingerprint, checkbox) {
  const completed = checkbox.checked;
  checkbox.disabled = true;
  const request = beginBrainRequest(`task-toggle:${fingerprint}`, false);
  try {
    const response = await apiFetch('/api/brain/task/toggle', {
      method: 'POST',
      body: JSON.stringify({ doc_id: docId, fingerprint, completed }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      checkbox.checked = !completed;
      showToast(data.error || 'Aufgabe konnte nicht geaendert werden.', true);
      return;
    }
    showToast(completed ? 'Aufgabe erledigt.' : 'Aufgabe wieder geoeffnet.');
    if (brainState.mode === 'tasks') await loadBrainTasks();
    else await loadBrainResults();
  } catch {
    if (isCurrentBrainRequest(request)) {
      checkbox.checked = !completed;
      showToast('Netzwerkfehler beim Aendern der Aufgabe.', true);
    }
  } finally {
    finishBrainRequest(request);
    checkbox.disabled = false;
  }
}
window.toggleBrainTask = toggleBrainTask;


async function saveBrainMetadata(referenceType, docId, fingerprint, changes) {
  const source = referenceType === 'task' ? brainState.tasks.get(fingerprint) : brainState.results.get(fingerprint);
  if (!source) return;
  const payload = { doc_id: docId, reference_type: referenceType, fingerprint };
  ['tags', 'priority', 'project'].forEach((field) => {
    if (Object.prototype.hasOwnProperty.call(changes, field)) payload[field] = changes[field];
  });
  const request = beginBrainRequest(`metadata:${referenceType}:${fingerprint}`, false);
  try {
    const response = await apiFetch('/api/brain/metadata', {
      method: 'POST',
      body: JSON.stringify(payload),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Metadaten konnten nicht gespeichert werden.', true);
      return;
    }
    showToast('Brain-Metadaten gespeichert.');
    await Promise.all([
      loadBrainTagsUnlessSearchCarriesThem(),
      brainState.mode === 'tasks' ? loadBrainTasks() : loadBrainResults(),
    ]);
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Speichern der Metadaten.', true);
  } finally {
    finishBrainRequest(request);
  }
}


function editBrainMetadata(referenceType, docId, fingerprint) {
  const source = referenceType === 'task' ? brainState.tasks.get(fingerprint) : brainState.results.get(fingerprint);
  if (!source) return;
  const value = window.prompt('Manuelle Tags, durch Komma getrennt:', (source.manual_tags || []).join(', '));
  if (value === null) return;
  const tags = value.split(',').map((tag) => tag.trim()).filter(Boolean);
  saveBrainMetadata(referenceType, docId, fingerprint, { tags });
}
window.editBrainMetadata = editBrainMetadata;


async function createBrainFile(kind) {
  const isNote = kind === 'note';
  const entity = isNote ? 'note' : 'project';
  const requestKey = `${entity}-create`;
  if (brainState.requests.has(requestKey)) return;
  const initiatingMode = brainState.mode;
  const title = window.prompt(isNote ? 'Titel der neuen Notiz:' : 'Name des persoenlichen Projekts:');
  if (!title || !title.trim()) return;
  const mode = isNote ? 'notes' : 'projects';
  const endpoint = isNote ? '/api/brain/notes' : '/api/brain/projects';
  const request = beginBrainRequest(requestKey, false);
  try {
    const response = await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify({ title: title.trim() }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || (isNote ? 'Notiz konnte nicht erstellt werden.' : 'Projekt konnte nicht erstellt werden.'), true);
      return;
    }
    await loadBrainProjects();
    showToast(isNote ? 'Notiz erstellt.' : 'Persoenliches Projekt erstellt.');
    const brainTab = document.getElementById('tab-brain');
    const contextUnchanged = request.generation === brainState.generation
      && brainState.mode === initiatingMode
      && brainTab && !brainTab.classList.contains('hidden');
    if (contextUnchanged) {
      await loadBrainCurrentMode();
      await openBrainDocument(data[entity].doc_id, '');
    }
  } catch {
    if (isCurrentBrainRequest(request)) {
      showToast(isNote ? 'Netzwerkfehler beim Erstellen der Notiz.' : 'Netzwerkfehler beim Erstellen des Projekts.', true);
    }
  } finally {
    finishBrainRequest(request);
  }
}


async function createFamilyBrainFile(kind) {
  const isNote = kind === 'note';
  const entity = isNote ? 'note' : 'project';
  const requestKey = `family-${entity}-create`;
  if (brainState.requests.has(requestKey)) return;
  const initiatingMode = brainState.mode;
  const title = window.prompt(isNote ? 'Titel der neuen Familiennotiz:' : 'Name des neuen Familienprojekts:');
  if (!title || !title.trim()) return;
  const endpoint = isNote ? '/api/brain/family/notes' : '/api/brain/family/projects';
  const request = beginBrainRequest(requestKey, false);
  try {
    const response = await apiFetch(endpoint, {
      method: 'POST',
      body: JSON.stringify({ title: title.trim() }),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || (isNote ? 'Familiennotiz konnte nicht erstellt werden.' : 'Familienprojekt konnte nicht erstellt werden.'), true);
      return;
    }
    showToast(isNote ? 'Familiennotiz erstellt.' : 'Familienprojekt erstellt.');
    const brainTab = document.getElementById('tab-brain');
    const contextUnchanged = request.generation === brainState.generation
      && brainState.mode === initiatingMode
      && brainTab && !brainTab.classList.contains('hidden');
    if (contextUnchanged) {
      await loadBrainCurrentMode();
      await openBrainDocument(data[entity].doc_id, '');
    }
  } catch {
    if (isCurrentBrainRequest(request)) {
      showToast(isNote ? 'Netzwerkfehler beim Erstellen der Familiennotiz.' : 'Netzwerkfehler beim Erstellen des Familienprojekts.', true);
    }
  } finally {
    finishBrainRequest(request);
  }
}


function createBrainProject() {
  return createBrainFile('project');
}
window.createBrainProject = createBrainProject;


// Block facets are measured against the query, so the drill lists have to
// follow it - typing `ki:` must leave the hashtags of the remaining blocks.
// The local search delivers them inside its own answer (`facets=block`); only
// the DB journal search, which has none, still needs a request of its own.
function reloadBrainTagsForQuery() {
  if (brainState.mode === 'db-journals') loadBrainTags();
}
brainElement('brain-query').addEventListener('input', () => {
  clearTimeout(brainState.searchTimer);
  invalidateBrainContext();
  brainState.searchTimer = setTimeout(() => {
    brainState.searchTimer = null;
    loadBrainForQuery();
    reloadBrainTagsForQuery();
  }, 180);
});
brainElement('brain-query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    clearTimeout(brainState.searchTimer);
    brainState.searchTimer = null;
    invalidateBrainContext();
    loadBrainForQuery();
    reloadBrainTagsForQuery();
  }
});
brainElement('brain-order').addEventListener('change', () => {
  if (brainState.mode === 'search' || brainState.mode === 'journals') {
    invalidateBrainContext();
    loadBrainForQuery();
  }
});
['brain-range-start', 'brain-range-end'].forEach((id) => {
  brainElement(id).addEventListener('change', () => {
    updateBrainRangeSummary();
    invalidateBrainContext();
    loadBrainForQuery();
  });
});
brainElement('brain-range-clear').addEventListener('click', () => {
  brainElement('brain-range-start').value = '';
  brainElement('brain-range-end').value = '';
  updateBrainRangeSummary();
  invalidateBrainContext();
  loadBrainForQuery();
});
brainElement('brain-filters-clear').addEventListener('click', () => {
  brainElement('brain-range-start').value = '';
  brainElement('brain-range-end').value = '';
  brainState.selectedTags.clear();
  brainState.excludedTags.clear();
  brainState.anyTags.clear();
  brainClearAiQueryTerms();
  updateBrainRangeSummary();
  setBrainTags(brainState.tags, null);
  invalidateBrainContext();
  loadBrainTagsUnlessSearchCarriesThem();
  loadBrainForQuery();
});
updateBrainRangeSummary();
const brainFilterToggle = brainElement('brain-filter-toggle');
const brainFilterPanel = brainElement('brain-filter-panel');
if (brainFilterToggle && brainFilterPanel) {
  brainFilterToggle.addEventListener('click', () => {
    const expanded = brainFilterToggle.getAttribute('aria-expanded') !== 'true';
    brainFilterToggle.setAttribute('aria-expanded', String(expanded));
    brainFilterPanel.classList.toggle('hidden', !expanded);
    brainElement('brain-filter-chevron')?.classList.toggle('rotate-180', expanded);
  });
}
brainElement('brain-mode-search').addEventListener('click', () => setBrainMode('search'));
brainElement('brain-mode-journals').addEventListener('click', () => setBrainMode('journals'));
brainElement('brain-mode-db-journals')?.addEventListener('click', () => setBrainMode('db-journals'));
brainElement('brain-mode-tasks').addEventListener('click', () => setBrainMode('tasks'));
brainElement('brain-mode-notes').addEventListener('click', () => setBrainMode('notes'));
brainElement('brain-mode-projects').addEventListener('click', () => setBrainMode('projects'));
brainElement('brain-mode-ai-agent').addEventListener('click', () => setBrainMode('ai-agent'));
brainElement('brain-mode-agent-audit').addEventListener('click', () => setBrainMode('agent-audit'));
if (window.config?.current_user?.admin) brainElement('brain-mode-agent-audit').classList.remove('hidden');
brainElement('brain-mode-family').addEventListener('click', () => setBrainMode('family'));
brainElement('brain-tag-history').addEventListener('click', () => {
  const controls = brainElement('brain-tagging-controls');
  controls.classList.toggle('hidden');
  // Beim Aufklappen zeigen, was gerade auf dem Server laeuft — sonst wirkt ein
  // weiterlaufender Auftrag wie verschwunden.
  if (!controls.classList.contains('hidden')) attachTaggingRun();
});
// Auch beim blossen Oeffnen der Brain-Einstellungen: wer nach einer Handysperre
// zurueckkommt, soll den laufenden Auftrag sehen, ohne ihn zu suchen.
document.addEventListener('journl:settings-opened', (event) => {
  if (event.detail?.group === 'brain') attachTaggingRun();
});
brainElement('brain-tag-run').addEventListener('click', runHistoricalTagging);
brainElement('brain-tag-mode')?.addEventListener('change', (event) => {
  brainElement('brain-tag-stale')?.classList.toggle('hidden', event.target.value !== 'stale');
});
// Das Brain-Menue ist ein zweiter Weg zu Knoepfen, die sonst nur im
// Einstellungsmenue haengen: es klickt sie an, statt ihre Handler zu kopieren.
brainElement('brain-menu-catalog')?.addEventListener('click', () => brainElement('brain-tag-catalog')?.click());
brainElement('brain-tag-catalog').addEventListener('click', async () => {
  if (typeof window.closeSettings === 'function') window.closeSettings();
  if (typeof window.switchTab === 'function') window.switchTab('brain');
  invalidateBrainContext();
  try {
    await loadTagCatalog();
  } catch {
    brainState.catalogOpen = false;
    updateBrainModeControls();
    await loadBrainCurrentMode();
    showToast('Netzwerkfehler beim Laden des Katalogs.', true);
  }
});
brainElement('brain-rebuild').addEventListener('click', async () => {
  const request = beginBrainRequest('index-rebuild', false);
  try {
    const response = await apiFetch('/api/brain/index/rebuild', {
      method: 'POST',
      body: JSON.stringify({}),
      signal: request.controller.signal,
    });
    const data = await response.json().catch(() => ({}));
    if (!isCurrentBrainRequest(request)) return;
    if (!response.ok) {
      showToast(data.error || 'Index konnte nicht eingeplant werden.', true);
      return;
    }
    showToast(data.queued ? 'Indexierung wurde eingeplant.' : 'Indexierung laeuft bereits.');
    brainState.allTags = null;
    invalidateBrainContext();
    await loadBrain();
  } catch {
    if (isCurrentBrainRequest(request)) showToast('Netzwerkfehler beim Einplanen des Index.', true);
  } finally {
    finishBrainRequest(request);
  }
});
