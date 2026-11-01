/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Der AI-Worker-Bereich: eine Stelle fuer Tickets, Jobs und Anbieter. Bis
 * hierher war der KI-Betrieb nur indirekt beobachtbar — Jobstatus einzeln per
 * ID, Anbieter nur in config.json, Agent-Projekt-Verbindungen getrennt davon.
 *
 * Der Bereich ist der Rahmen (Entscheidung E-18): er zeigt, was es gibt, und
 * startet nichts. Zwei Unterseiten gehoeren eigenen Modulen und werden hier
 * nur eingehaengt - die Ticket-Liste (write-ticket.js) und die
 * Anbieterverwaltung (provider-manager.js, Entscheidung E-17).
 */
(function () {
  const PANELS = [
    { id: 'tickets', label: 'Tickets' },
    { id: 'questions', label: 'Rückfragen' },
    { id: 'assistant', label: 'Assistent' },
    { id: 'jobs', label: 'Jobs' },
    { id: 'providers', label: 'Anbieter' },
  ];
  const JOB_STATE_CLASS = {
    done: 'text-green-400', completed: 'text-green-400', ok: 'text-green-400',
    error: 'text-red-400', failed: 'text-red-400',
    running: 'text-blue-400', queued: 'text-amber-400', pending: 'text-amber-400',
    cancelled: 'text-gray-500',
  };

  let activePanel = 'tickets';
  let loaded = {};

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function request(url) {
    const response = await window.apiFetch(url, { timeoutMs: 20000 });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Abruf fehlgeschlagen');
    return data;
  }

  function shortTime(value) {
    const text = String(value || '');
    return text.length >= 16 ? text.slice(0, 16).replace('T', ' ') : text;
  }

  // ─── Jobs ─────────────────────────────────────────────────────────
  function jobRow(job) {
    const tone = JOB_STATE_CLASS[String(job.status || '').toLowerCase()] || 'text-gray-300';
    const detail = job.has_output || job.error
      ? `<button type="button" data-job-detail="${escapeAttr(job.id)}"
           class="text-xs text-gray-400 hover:text-white underline decoration-dotted">Details</button>`
      : '';
    return `<li class="rounded-lg border border-gray-800 px-3 py-2">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate text-sm text-gray-200">
            ${escapeHtml(job.workflow_tag || job.agent || 'Job')}
            <span class="text-xs text-gray-500">${escapeHtml(job.model || '')}</span>
          </p>
          <p class="text-xs text-gray-500">
            ${escapeHtml(shortTime(job.created_at))} ·
            <span class="${tone}">${escapeHtml(job.status || 'unbekannt')}</span>
            ${job.error ? '· ' + escapeHtml(String(job.error).slice(0, 80)) : ''}
          </p>
        </div>
        ${detail}
      </div>
      <pre data-job-output="${escapeAttr(job.id)}"
        class="hidden mt-2 max-h-64 overflow-auto rounded bg-gray-950 p-2 text-[11px] text-gray-400 whitespace-pre-wrap"></pre>
    </li>`;
  }

  async function loadJobs(container) {
    container.innerHTML = '<p class="text-gray-500 text-sm">Lade…</p>';
    try {
      const data = await request('/api/worker/jobs?limit=50');
      if (!data.total) {
        container.innerHTML = '<p class="text-gray-500 text-sm">Noch keine KI-Jobs gelaufen.</p>';
        return;
      }
      const counts = Object.entries(data.counts || {})
        .map(([status, count]) => `${escapeHtml(status)}: ${count}`).join(' · ');
      const hint = data.items.length < data.total
        ? `<p class="text-xs text-gray-500 mt-2">${data.items.length} von ${data.total} gezeigt.</p>` : '';
      container.innerHTML = `<p class="text-xs text-gray-400 mb-2">${data.total} Jobs — ${counts}</p>`
        + `<ul class="space-y-2">${data.items.map(jobRow).join('')}</ul>${hint}`;
    } catch (error) {
      container.innerHTML = `<p class="text-red-400 text-sm">${escapeHtml(error.message)}</p>`;
    }
  }

  async function toggleJobDetail(jobId) {
    const target = document.querySelector(`[data-job-output="${CSS.escape(jobId)}"]`);
    if (!target) return;
    if (!target.classList.contains('hidden')) {
      target.classList.add('hidden');
      return;
    }
    target.classList.remove('hidden');
    target.textContent = 'Lade…';
    try {
      const job = await request(`/api/worker/jobs/${encodeURIComponent(jobId)}`);
      const parts = [];
      if (job.error) parts.push(`Fehler: ${job.error}`);
      if (job.exit_code != null) parts.push(`Exit-Code: ${job.exit_code}`);
      if (job.stderr) parts.push(`--- stderr ---\n${job.stderr}`);
      if (job.stdout) parts.push(`--- stdout ---\n${job.stdout}`);
      target.textContent = parts.join('\n\n') || 'Keine Ausgabe gespeichert.';
    } catch (error) {
      target.textContent = error.message;
    }
  }

  // ─── Rückfragen ───────────────────────────────────────────────────
  // Der Fragetyp bestimmt die Eingabe: Ja/Nein als zwei Schaltflächen,
  // Auswahl als Optionsliste, alles andere als Textfeld (E-04).
  function questionField(sessionId, question) {
    if (question.answer) {
      return `<p class="text-xs text-green-400">${escapeHtml(String(question.answer))}</p>`;
    }
    const attributes = (value) => `data-answer="${escapeAttr(value)}" `
      + `data-session="${escapeAttr(sessionId)}" data-question="${escapeAttr(question.id)}"`;
    if (question.type === 'boolean') {
      return `<div class="flex gap-2">
        <button type="button" ${attributes('ja')}
          class="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600">Ja</button>
        <button type="button" ${attributes('nein')}
          class="rounded border border-gray-700 px-3 py-1 text-xs text-gray-300 hover:text-white">Nein</button>
      </div>`;
    }
    if (question.type === 'choice') {
      return `<div class="flex flex-wrap gap-2">${question.options.map((option) =>
        `<button type="button" ${attributes(option)}
          class="rounded border border-gray-700 px-3 py-1 text-xs text-gray-200 hover:bg-gray-800">${escapeHtml(option)}</button>`).join('')}</div>`;
    }
    return `<div class="flex gap-2">
      <input type="text" data-answer-input="${escapeAttr(question.id)}"
        class="flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100"
        placeholder="Antwort" />
      <button type="button" data-answer-send data-session="${escapeAttr(sessionId)}"
        data-question="${escapeAttr(question.id)}"
        class="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600">Senden</button>
    </div>`;
  }

  function sessionCard(session) {
    const open = session.status === 'offen';
    return `<li class="rounded-lg border ${open ? 'border-amber-700/50' : 'border-gray-800'} px-3 py-2">
      <p class="text-sm text-gray-200">${escapeHtml(session.ticket_title)}</p>
      <p class="text-xs text-gray-500 mb-2">${escapeHtml(shortTime(session.created_at))} ·
        ${open ? `${session.open_count} offen` : 'beantwortet'}</p>
      <ul class="space-y-2">${session.questions.map((question) => `<li>
        <p class="text-xs text-gray-300 mb-1">${escapeHtml(question.question)}</p>
        ${questionField(session.id, question)}
      </li>`).join('')}</ul>
    </li>`;
  }

  async function loadQuestions(container) {
    container.innerHTML = '<p class="text-gray-500 text-sm">Lade…</p>';
    try {
      const data = await request('/api/worker/questions');
      if (!data.items.length) {
        container.innerHTML = '<p class="text-gray-500 text-sm">Keine Rückfragen.</p>';
        return;
      }
      container.innerHTML = `<ul class="space-y-2">${data.items.map(sessionCard).join('')}</ul>`;
    } catch (error) {
      container.innerHTML = `<p class="text-red-400 text-sm">${escapeHtml(error.message)}</p>`;
    }
  }

  async function sendAnswer(sessionId, questionId, answer) {
    if (!answer) return;
    try {
      const response = await window.apiFetch(
        `/api/worker/questions/${encodeURIComponent(sessionId)}/answer`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question_id: questionId, answer }), timeoutMs: 15000 });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Antwort fehlgeschlagen');
      window.showToast?.(data.remaining
        ? `Gespeichert, noch ${data.remaining} offen.`
        : 'Alle Fragen beantwortet – der Lauf wird fortgesetzt.');
    } catch (error) {
      window.showToast?.(error.message);
    }
    loaded.questions = false;
    render('questions');
    window.journlWorkerNotify?.();
  }

  // ─── Assistent ────────────────────────────────────────────────────
  // Sammelt Produktnotizen eines Zeitraums zu Vorhaben. Der Lauf schlägt nur
  // vor — was daraus wird, entscheidet der Product Owner (E-42).
  function proposalCard(entry) {
    const marks = Object.entries(entry.criteria)
      .map(([key, met]) => `<span class="inline-flex items-center gap-1 ${met ? 'text-green-400' : 'text-gray-600'}">`
        + `${window.journlIcon?.(met ? 'check' : 'close', 'h-3 w-3') || ''}<span>${escapeHtml(key)}</span></span>`).join(' · ');
    const fragments = entry.fragments.map((item) =>
      `<li class="text-[11px] text-gray-500">${escapeHtml(item.date)} — ${escapeHtml(item.text)}</li>`).join('');
    return `<li class="rounded-lg border ${entry.mature ? 'border-green-800/60' : 'border-gray-800'} px-3 py-2">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-sm text-gray-200">${escapeHtml(entry.title)}
            ${entry.mature ? '<span class="text-xs text-green-400">reif</span>'
                           : '<span class="text-xs text-gray-500">noch offen</span>'}</p>
          <p class="text-xs text-gray-500">${marks}</p>
          ${entry.reason ? `<p class="text-[11px] text-gray-500 mt-1">${escapeHtml(entry.reason)}</p>` : ''}
        </div>
        <button type="button" data-proposal-bucket="${escapeAttr(entry.title)}"
          class="shrink-0 rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:text-white">Als Mappe</button>
      </div>
      <ul class="mt-1 space-y-0.5">${fragments}</ul>
    </li>`;
  }

  let lastProposals = [];

  async function loadAssistant(container) {
    let providers = [];
    try {
      providers = (await request('/api/worker/providers')).shared || [];
    } catch (_) { /* Der Zeitraum lässt sich auch ohne Liste eintragen. */ }
    const today = new Date().toISOString().slice(0, 10);
    const ago = new Date(Date.now() - 13 * 86400000).toISOString().slice(0, 10);
    container.innerHTML = `
      <p class="text-xs text-gray-500 mb-2">Sammelt Notizen mit Produkt-Hashtags und gruppiert
        sie zu Vorhaben. Der Lauf schlägt nur vor und ändert nichts.</p>
      <div class="grid grid-cols-2 gap-2 mb-3">
        <label class="text-xs text-gray-400">Von
          <input type="date" data-scan-start value="${ago}"
            class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100" /></label>
        <label class="text-xs text-gray-400">Bis
          <input type="date" data-scan-end value="${today}"
            class="mt-1 w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100" /></label>
        <select data-scan-provider
          class="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100">
          ${providers.map((item) =>
            `<option value="${escapeAttr(item.id)}">${escapeHtml(item.label || item.id)}</option>`).join('')}
        </select>
        <button type="button" data-scan-run
          class="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600">Sammeln</button>
      </div>
      <div data-scan-result class="text-sm text-gray-300"></div>`;
  }

  async function runScan() {
    const host = document.querySelector('[data-scan-result]');
    if (!host) return;
    const start = document.querySelector('[data-scan-start]')?.value;
    const end = document.querySelector('[data-scan-end]')?.value;
    const provider = document.querySelector('[data-scan-provider]')?.value;
    if (!start || !end || !provider) {
      host.innerHTML = '<p class="text-amber-300 text-xs">Zeitraum und Anbieter wählen.</p>';
      return;
    }
    host.innerHTML = '<p class="text-gray-500 text-sm">Sammelt…</p>';
    try {
      const response = await window.apiFetch('/api/worker/assistant/scan', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start_date: start, end_date: end, provider_id: provider }),
        timeoutMs: 180000,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Der Lauf ist fehlgeschlagen');
      lastProposals = data.proposals || [];
      if (!lastProposals.length) {
        host.innerHTML = `<p class="text-gray-500 text-sm">${escapeHtml(data.note
          || 'Keine Vorhaben erkannt.')}</p>`;
        return;
      }
      host.innerHTML = `<p class="text-xs text-gray-400 mb-2">${data.fragments} Notizen ·
          ${lastProposals.length} Vorhaben · ${data.mature} reif</p>
        <ul class="space-y-2">${lastProposals.map(proposalCard).join('')}</ul>`;
    } catch (error) {
      host.innerHTML = `<p class="text-red-400 text-sm">${escapeHtml(error.message)}</p>`;
    }
  }

  async function proposalToBucket(title) {
    const entry = lastProposals.find((item) => item.title === title);
    if (!entry) return;
    try {
      const created = await window.apiFetch('/api/buckets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }), timeoutMs: 15000 });
      const data = await created.json().catch(() => ({}));
      const slug = data.slug;
      if (!created.ok && created.status !== 409) throw new Error(data.error || 'Mappe fehlgeschlagen');
      for (const fragment of entry.fragments) {
        await window.apiFetch(`/api/buckets/${encodeURIComponent(slug)}/items`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ date: fragment.date, anchor: fragment.anchor,
                                 note: entry.title }), timeoutMs: 15000 });
      }
      window.showToast?.(`Mappe „${title}" mit ${entry.fragments.length} Verweisen.`);
      window.journlBuckets?.reload?.();
    } catch (error) {
      window.showToast?.(error.message);
    }
  }

  // ─── Anbieter ─────────────────────────────────────────────────────
  // Anzeige und Verwaltung leben in provider-manager.js, damit dieser Bereich
  // der Rahmen bleibt und die config.json genau einen Schreiber im Browser hat.
  async function loadProviders(container) {
    // provider-manager.js ist ein Agentenmodul und laedt erst hier; danach
    // steht window.journlProviders bereit.
    if (!window.journlProviders && window.journlAgentModules) {
      container.innerHTML = '<p class="text-sm text-gray-500">Lade…</p>';
      try {
        await window.journlAgentModules.load('provider-manager.js');
      } catch (_) { /* unten als fehlend gemeldet */ }
    }
    if (!window.journlProviders) {
      container.innerHTML = '<p class="text-sm text-red-400">provider-manager.js fehlt.</p>';
      return;
    }
    window.journlProviders.render(container);
  }

  // ─── Rahmen ───────────────────────────────────────────────────────
  // Unterseiten entstehen aus PANELS statt aus dem Template: so kann keine
  // Unterseite existieren, deren Container jemand zu ergänzen vergisst.
  function buildShell() {
    const shell = document.querySelector('[data-worker-shell]');
    if (!shell || shell.dataset.ready === 'true') return;
    const tabs = PANELS.map((panel) =>
      `<button type="button" data-worker-tab="${panel.id}"
        class="border-b-2 border-transparent pb-1.5 text-gray-500 transition hover:text-gray-300"
        >${panel.label}</button>`).join('');
    const bodies = PANELS.map((panel) => panel.id === 'tickets'
      ? `<div data-worker-panel="tickets">
           <p class="text-xs text-gray-500 mb-2">Eingereichte Tickets. Erst die Freigabe macht ein
             Ticket zum Auftrag &mdash; die bloße Ablage löst nichts aus.</p>
           <div id="settings-tickets-list" class="space-y-2 text-sm text-gray-300">
             <p class="text-gray-500">Lade…</p>
           </div>
         </div>`
      : `<div data-worker-panel="${panel.id}" class="hidden text-sm text-gray-300"></div>`).join('');
    shell.innerHTML = `<div class="flex gap-4 border-b border-gray-800 mb-3 text-sm">${tabs}</div>${bodies}`;
    shell.dataset.ready = 'true';
  }

  function showPanel(id) {
    activePanel = id;
    PANELS.forEach((panel) => {
      const button = document.querySelector(`[data-worker-tab="${panel.id}"]`);
      const body = document.querySelector(`[data-worker-panel="${panel.id}"]`);
      const active = panel.id === id;
      button?.classList.toggle('text-white', active);
      button?.classList.toggle('border-gray-500', active);
      button?.classList.toggle('text-gray-500', !active);
      button?.classList.toggle('border-transparent', !active);
      body?.classList.toggle('hidden', !active);
    });
    render(id);
  }

  function render(id) {
    const body = document.querySelector(`[data-worker-panel="${id}"]`);
    if (!body) return;
    if (id === 'tickets') {
      // Die Ticket-Liste gehoert write-ticket.js; hier wird sie nur angestossen.
      document.dispatchEvent(new CustomEvent('journl:settings-opened', { detail: { group: 'tickets' } }));
      return;
    }
    if (loaded[id]) return;
    loaded[id] = true;
    if (id === 'jobs') loadJobs(body);
    if (id === 'providers') loadProviders(body);
    if (id === 'questions') loadQuestions(body);
    if (id === 'assistant') loadAssistant(body);
  }

  document.addEventListener('click', (event) => {
    const tab = event.target.closest('[data-worker-tab]');
    if (tab) {
      event.preventDefault();
      showPanel(tab.dataset.workerTab);
      return;
    }
    const detail = event.target.closest('[data-job-detail]');
    if (detail) {
      event.preventDefault();
      toggleJobDetail(detail.dataset.jobDetail);
      return;
    }
    const choice = event.target.closest('[data-answer]');
    if (choice) {
      event.preventDefault();
      sendAnswer(choice.dataset.session, choice.dataset.question, choice.dataset.answer);
      return;
    }
    if (event.target.closest('[data-scan-run]')) {
      event.preventDefault();
      runScan();
      return;
    }
    const proposal = event.target.closest('[data-proposal-bucket]');
    if (proposal) {
      event.preventDefault();
      proposalToBucket(proposal.dataset.proposalBucket);
      return;
    }
    const send = event.target.closest('[data-answer-send]');
    if (send) {
      event.preventDefault();
      const field = document.querySelector(
        `[data-answer-input="${CSS.escape(send.dataset.question)}"]`);
      sendAnswer(send.dataset.session, send.dataset.question, field ? field.value.trim() : '');
    }
  });

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'worker') return;
    buildShell();
    loaded = {};
    showPanel(activePanel);
    refreshNotify();
  });

  // ─── Hinweislinie im Schreiben-Tab ────────────────────────────────
  // Gleiches Muster wie die Familie-Benachrichtigung: eine dezente Linie im
  // Tab selbst. Rot, wenn eine Rückfrage wartet — sie blockiert einen Lauf;
  // blau, wenn nur ein Ergebnis oder ein Fehlschlag zu sichten ist (E-25).
  async function refreshNotify() {
    const element = document.getElementById('worker-notify');
    if (!element) return;
    let data;
    try {
      data = await request('/api/worker/notifications');
    } catch (_) {
      return;
    }
    const blocking = data.questions > 0;
    const waiting = blocking || data.review > 0 || data.failed > 0;
    element.classList.toggle('hidden', !waiting);
    if (!waiting) return;
    element.style.backgroundColor = blocking ? '#ef4444' : '#2563eb';
    const parts = [];
    if (data.questions) parts.push(`${data.questions} Rückfrage(n)`);
    if (data.review) parts.push(`${data.review} zur Prüfung`);
    if (data.failed) parts.push(`${data.failed} fehlgeschlagen`);
    element.title = parts.join(' · ');
  }

  window.journlWorkerNotify = refreshNotify;
  document.getElementById('burger-btn')?.addEventListener('click', refreshNotify);
  refreshNotify();
  // Ein wartender Lauf meldet sich nicht von selbst; einmal pro Minute reicht.
  setInterval(refreshNotify, 60000);
})();
