// Einkaufslisten-Agent: Dialogfenster aus dem "⋯"-Menue einer Einkaufsliste.
// AI maintenance contract: reine Oberflaeche. Welche Werkzeuge es gibt, was sie
// duerfen und auf welche Liste sie wirken, entscheidet allein der Server
// (app/shopping_agent.py); hier wird nichts nachgebaut.
//
// Antworten kommen aus Modelltext und Listeninhalten und werden deshalb
// ausschliesslich als Text gesetzt, nie als HTML.
(function () {
  const state = {
    projectId: '',
    title: '',
    busy: false,
    messages: [],
    knowledge: [],
    configured: false,
    providers: [],
    providerId: '',
    canUndo: false,
    // Der zuletzt gewaehlte Laden je Liste — eine Bequemlichkeit im Browser,
    // keine Wahrheit. Der Server merkt sich davon nichts.
    store: '',
    runId: '',
    runTimer: null,
    changed: false,
  };

  const RUN_KIND = 'shopping_agent';
  const RUN_POLL_MS = 1500;
  const STORE_KEY = 'journl.shoppingAgent.store';

  const byId = (id) => document.getElementById(id);
  const icon = (name, cls) => (window.journlIcon ? window.journlIcon(name, cls) : '');

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, !!isError);
  }

  function rememberedStore(projectId) {
    try {
      return (JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {})[projectId] || '';
    } catch {
      return '';
    }
  }

  function rememberStore(projectId, tag) {
    try {
      const all = JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {};
      if (tag) all[projectId] = tag; else delete all[projectId];
      localStorage.setItem(STORE_KEY, JSON.stringify(all));
    } catch {
      // Ohne Merker laesst sich der Laden weiterhin jedes Mal waehlen.
    }
  }

  function currentProvider() {
    return state.providers.find((item) => item.id === state.providerId) || null;
  }

  // ─── Fenster ───────────────────────────────────────────────────────
  function ensurePanel() {
    let panel = byId('shopping-agent');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'shopping-agent';
    panel.className = 'fixed inset-0 z-50 hidden items-center justify-center bg-black/70 p-4';
    panel.innerHTML = `
      <div class="flex h-full max-h-[44rem] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-gray-800 bg-gray-950 shadow-2xl">
        <div class="flex items-center gap-2 border-b border-gray-800 px-4 py-3">
          <span class="text-green-500">${icon('cart', 'h-5 w-5')}</span>
          <div class="min-w-0 flex-1">
            <h2 id="shopping-agent-title" class="truncate text-sm font-medium text-gray-100">Einkaufsliste</h2>
            <div id="shopping-agent-provider" class="flex items-center gap-1.5"></div>
          </div>
          <button type="button" id="shopping-agent-undo"
                  class="hidden rounded-lg border border-gray-800 px-2 py-1 text-xs text-gray-400 hover:border-gray-700 hover:text-gray-200"
                  title="Den Stand vor dem letzten Lauf wiederherstellen">Rückgängig</button>
          <button type="button" id="shopping-agent-close"
                  class="rounded-lg p-1 text-gray-500 hover:text-gray-200" title="Schließen">${icon('close', 'h-4 w-4')}</button>
        </div>
        <div id="shopping-agent-quick" class="flex flex-wrap items-center gap-2 border-b border-gray-800 px-4 py-2"></div>
        <div id="shopping-agent-log" class="flex-1 space-y-2 overflow-y-auto px-4 py-3"></div>
        <div class="flex items-end gap-2 border-t border-gray-800 px-4 py-3">
          <textarea id="shopping-agent-input" rows="1" placeholder="Was soll ich mit der Liste tun?"
                    class="max-h-32 flex-1 resize-none rounded-xl border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-green-700 focus:outline-none"></textarea>
          <button type="button" id="shopping-agent-send"
                  class="rounded-xl bg-green-700 px-3 py-2 text-sm text-white hover:bg-green-600 disabled:opacity-40">${icon('arrowUp', 'h-4 w-4')}</button>
        </div>
      </div>`;
    document.body.appendChild(panel);
    byId('shopping-agent-close').addEventListener('click', closeShoppingAgent);
    byId('shopping-agent-undo').addEventListener('click', undo);
    byId('shopping-agent-send').addEventListener('click', send);
    const input = byId('shopping-agent-input');
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        send();
      }
    });
    input.addEventListener('input', () => {
      input.style.height = 'auto';
      input.style.height = `${Math.min(input.scrollHeight, 128)}px`;
    });
    panel.addEventListener('click', (event) => {
      if (event.target === panel) closeShoppingAgent();
    });
    // Auf dem Handy schiebt die Tastatur die Eingabezeile sonst aus dem Bild.
    window.journlAgentViewport?.bind(panel);
    return panel;
  }

  // ─── Zeichnen ──────────────────────────────────────────────────────
  function stepLine(step) {
    const parts = [step.tool];
    if (step.error) parts.push(`Fehler: ${step.error}`);
    if (typeof step.sortiert === 'number') parts.push(`${step.sortiert} sortiert`);
    if (step.nachgetragen) parts.push(`${step.nachgetragen} unter „Unsortiert“`);
    if (typeof step.hinzugefuegt === 'number') parts.push(`${step.hinzugefuegt} hinzugefügt`);
    if (typeof step.geaendert === 'number') parts.push(`${step.geaendert} geändert`);
    if (Array.isArray(step.entfernt) && step.entfernt.length) parts.push(`entfernt: ${step.entfernt.join(', ')}`);
    if (step.vorher) parts.push(`${step.vorher} → ${step.nachher}`);
    if (Array.isArray(step.hits) && step.hits.length) parts.push(step.hits.join(', '));
    return parts.join(' · ');
  }

  function stepsBlock(steps) {
    if (!Array.isArray(steps) || !steps.length) return null;
    const details = document.createElement('details');
    details.className = 'mt-2 text-[11px] text-gray-500';
    const summary = document.createElement('summary');
    summary.className = 'cursor-pointer text-gray-500 hover:text-gray-300';
    summary.textContent = `${steps.length} Schritte`;
    details.appendChild(summary);
    const list = document.createElement('ul');
    list.className = 'mt-1 space-y-0.5 break-words';
    for (const step of steps) {
      const item = document.createElement('li');
      item.textContent = stepLine(step);
      list.appendChild(item);
    }
    details.appendChild(list);
    return details;
  }

  function bubble(entry) {
    const wrap = document.createElement('div');
    const own = entry.role === 'user';
    wrap.className = own ? 'flex justify-end' : 'flex justify-start';
    const box = document.createElement('div');
    box.className = own
      ? 'max-w-[85%] rounded-xl border border-green-800/50 bg-green-900/20 px-3 py-2 text-sm text-gray-100 whitespace-pre-wrap break-words'
      : 'max-w-[85%] rounded-xl border border-gray-800 bg-gray-900/70 px-3 py-2 text-sm text-gray-200 whitespace-pre-wrap break-words';
    if (entry.pending) {
      box.className += ' text-gray-500 italic';
      box.textContent = entry.progress || 'Arbeitet an der Liste …';
    } else if (entry.error) {
      box.className = 'max-w-[85%] rounded-xl border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300 whitespace-pre-wrap break-words';
      box.textContent = entry.content;
    } else {
      box.textContent = entry.content;
    }
    const steps = entry.pending ? null : stepsBlock(entry.steps);
    if (steps) box.appendChild(steps);
    wrap.appendChild(box);
    return wrap;
  }

  function renderProvider() {
    const box = byId('shopping-agent-provider');
    if (!box) return;
    box.textContent = '';
    if (!state.providers.length) {
      const note = document.createElement('span');
      note.className = 'text-[11px] text-gray-600';
      note.textContent = 'keine KI-Verbindung konfiguriert';
      box.appendChild(note);
      return;
    }
    const select = document.createElement('select');
    select.className = 'max-w-[14rem] truncate rounded border border-transparent bg-transparent py-0 pl-0 pr-4 text-[11px] text-gray-500 hover:text-gray-300 focus:border-gray-700 focus:outline-none';
    select.disabled = state.busy;
    select.title = 'KI-Anbieter für diesen Agenten wählen';
    for (const item of state.providers) {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.model ? `${item.label} · ${item.model}` : item.label;
      if (item.id === state.providerId) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener('change', () => {
      // Nur fuer dieses Fenster. Welcher Agent beim naechsten Oeffnen
      // vorausgewaehlt ist, entscheidet der Standard-Agent in der Verwaltung
      // (Burger → AI-Worker → Anbieter) — ein zweiter, unsichtbarer Merker im
      // Browser hat diese Einstellung vorher still ueberstimmt.
      state.providerId = select.value;
      render();
    });
    box.appendChild(select);
    // Ein Anbieter ausserhalb des eigenen Netzes bekommt die Liste und die
    // gelesene Ladennotiz zu sehen. Das gehoert sichtbar, nicht versteckt.
    const provider = currentProvider();
    if (provider && !provider.local) {
      const cloud = document.createElement('span');
      cloud.className = 'shrink-0 rounded bg-amber-900/30 px-1 text-[10px] text-amber-500';
      cloud.textContent = 'extern';
      cloud.title = 'Dieser Anbieter läuft außerhalb deines Netzes.';
      box.appendChild(cloud);
    }
  }

  function renderQuick() {
    const bar = byId('shopping-agent-quick');
    if (!bar) return;
    bar.textContent = '';
    const sort = document.createElement('button');
    sort.type = 'button';
    sort.className = 'rounded-lg border border-green-800/60 bg-green-900/20 px-2.5 py-1 text-xs text-green-300 hover:border-green-700 disabled:opacity-40';
    sort.disabled = state.busy || !state.configured;
    sort.textContent = 'Sortieren';
    sort.addEventListener('click', () => {
      ask(state.store
        ? `Sortiere die Liste nach dem Ladenaufbau aus #${state.store}.`
        : 'Sortiere die Liste nach den üblichen Rubriken.');
    });
    bar.appendChild(sort);

    if (state.knowledge.length) {
      const select = document.createElement('select');
      select.className = 'rounded-lg border border-gray-800 bg-gray-900 px-2 py-1 text-xs text-gray-300 focus:border-green-700 focus:outline-none';
      select.disabled = state.busy;
      const none = document.createElement('option');
      none.value = '';
      none.textContent = 'ohne Laden';
      select.appendChild(none);
      for (const item of state.knowledge) {
        const option = document.createElement('option');
        option.value = item.hashtag;
        option.textContent = `#${item.hashtag}`;
        if (item.hashtag === state.store) option.selected = true;
        select.appendChild(option);
      }
      select.addEventListener('change', () => {
        state.store = select.value;
        rememberStore(state.projectId, state.store);
      });
      bar.appendChild(select);
    }

    const clean = document.createElement('button');
    clean.type = 'button';
    clean.className = 'rounded-lg border border-gray-800 px-2.5 py-1 text-xs text-gray-400 hover:border-gray-700 hover:text-gray-200 disabled:opacity-40';
    clean.disabled = state.busy || !state.configured;
    clean.textContent = 'Doppelte zusammenfassen';
    clean.addEventListener('click', () => {
      ask('Fasse doppelte Artikel zu einem zusammen und ergänze dabei die Mengen.');
    });
    bar.appendChild(clean);
  }

  function render() {
    const log = byId('shopping-agent-log');
    if (!log) return;
    log.textContent = '';
    if (!state.messages.length) {
      const hint = document.createElement('p');
      hint.className = 'mt-8 text-center text-sm text-gray-600';
      hint.textContent = state.configured
        ? 'Sortieren, zusammenfassen, ergänzen — sag einfach, was die Liste braucht.'
        : 'Noch keine KI-Verbindung konfiguriert: Brain → „…“ → Chat with your Notes → Einstellungen.';
      log.appendChild(hint);
    }
    for (const entry of state.messages) log.appendChild(bubble(entry));
    log.scrollTop = log.scrollHeight;
    const send_ = byId('shopping-agent-send');
    if (send_) send_.disabled = state.busy || !state.configured;
    const undoBtn = byId('shopping-agent-undo');
    if (undoBtn) undoBtn.classList.toggle('hidden', !state.canUndo || state.busy);
    const title = byId('shopping-agent-title');
    if (title) title.textContent = state.title || 'Einkaufsliste';
    renderProvider();
    renderQuick();
  }

  // ─── Oeffnen und schliessen ────────────────────────────────────────
  async function openShoppingAgent(projectId, title) {
    ensurePanel();
    state.projectId = projectId;
    state.title = title || 'Einkaufsliste';
    state.messages = [];
    state.changed = false;
    state.providerId = '';
    state.store = rememberedStore(projectId);
    const panel = byId('shopping-agent');
    panel.classList.remove('hidden');
    panel.classList.add('flex');
    window.journlAgentViewport?.show(panel);
    render();
    await loadState();
    render();
    byId('shopping-agent-input')?.focus();
  }

  function closeShoppingAgent() {
    stopRunPolling();
    const panel = byId('shopping-agent');
    if (!panel) return;
    panel.classList.add('hidden');
    panel.classList.remove('flex');
    window.journlAgentViewport?.hide(panel);
    // Erst beim Schliessen neu laden: waehrend des Dialogs wuerde ein
    // Neuaufbau des Boards das offene Fenster unter den Haenden wegziehen.
    if (state.changed && typeof window.loadFamily === 'function') window.loadFamily();
    state.changed = false;
  }

  window.openShoppingAgent = openShoppingAgent;
  window.closeShoppingAgent = closeShoppingAgent;

  async function loadState() {
    try {
      const response = await apiFetch(
        `/api/family/shopping-agent/${encodeURIComponent(state.projectId)}/state`,
        { cache: 'no-store' });
      if (!response.ok) {
        toast('Die Liste ist nicht erreichbar.', true);
        return;
      }
      const payload = await response.json();
      state.configured = !!payload.configured;
      state.providers = payload.providers || [];
      const wanted = state.providerId;
      state.providerId = state.providers.some((item) => item.id === wanted)
        ? wanted : (payload.default_provider || '');
      state.knowledge = payload.knowledge || [];
      state.canUndo = !!payload.can_undo;
      if (state.store && !state.knowledge.some((item) => item.hashtag === state.store)) {
        state.store = '';
      }
      if (payload.run && payload.run.active) watchRun(payload.run);
    } catch {
      toast('Netzwerkfehler.', true);
    }
  }

  // ─── Auftrag ───────────────────────────────────────────────────────
  function stopRunPolling() {
    if (state.runTimer) clearTimeout(state.runTimer);
    state.runTimer = null;
  }

  function setPending(text) {
    const last = state.messages[state.messages.length - 1];
    if (last?.pending) {
      last.progress = text;
      render();
    }
  }

  function finishRun(run) {
    state.runId = '';
    state.busy = false;
    if (state.messages[state.messages.length - 1]?.pending) state.messages.pop();
    if (run.status === 'done') {
      const result = run.result || {};
      state.messages.push({
        role: 'assistant', content: result.answer || '', steps: result.steps || [],
      });
      if (result.changed) state.changed = true;
      if (result.can_undo) state.canUndo = true;
    } else if (run.status === 'aborted') {
      state.messages.push({
        role: 'assistant', error: true,
        content: 'Die App wurde neu gestartet, bevor der Auftrag fertig war. Bitte noch einmal.',
      });
    } else {
      state.messages.push({
        role: 'assistant', error: true, content: run.error || 'Der Auftrag ist fehlgeschlagen.',
      });
    }
    render();
  }

  async function pollRun() {
    if (!state.runId) return;
    let run = null;
    try {
      const response = await apiFetch(`/api/runs/${state.runId}`, { cache: 'no-store' });
      if (response.status === 404) {
        finishRun({ status: 'failed', error: 'Der Auftrag ist nicht mehr vorhanden.' });
        return;
      }
      run = await response.json();
    } catch {
      // Kein Kontakt heisst nicht: kein Ergebnis. Der Server arbeitet weiter.
      setPending('Kein Kontakt zum Server – der Auftrag läuft weiter …');
      state.runTimer = setTimeout(pollRun, RUN_POLL_MS * 2);
      return;
    }
    if (run.active) {
      const calls = run.progress?.tool_calls || 0;
      setPending(calls ? `Arbeitet an der Liste … (${calls} Schritte)` : 'Arbeitet an der Liste …');
      state.runTimer = setTimeout(pollRun, RUN_POLL_MS);
      return;
    }
    finishRun(run);
  }

  function watchRun(run) {
    stopRunPolling();
    state.runId = run.id;
    state.busy = true;
    if (!state.messages[state.messages.length - 1]?.pending) {
      state.messages.push({ role: 'assistant', content: '', pending: true });
    }
    render();
    pollRun();
  }

  async function ask(text) {
    const question = (text || '').trim();
    if (!question || state.busy || !state.configured) return;
    state.messages.push({ role: 'user', content: question });
    state.busy = true;
    render();
    const history = state.messages
      .filter((entry) => !entry.pending && !entry.error)
      .map((entry) => ({ role: entry.role, content: entry.content }));
    try {
      const response = await apiFetch(
        `/api/family/shopping-agent/${encodeURIComponent(state.projectId)}/ask`,
        { method: 'POST', body: JSON.stringify({ messages: history, provider_id: state.providerId }) });
      const payload = await response.json().catch(() => ({}));
      if (response.status === 202 && payload.run) {
        watchRun(payload.run);
        return;
      }
      state.busy = false;
      state.messages.push({
        role: 'assistant', error: true,
        content: payload.error || 'Der Auftrag konnte nicht gestartet werden.',
      });
      render();
    } catch {
      state.busy = false;
      state.messages.push({ role: 'assistant', error: true, content: 'Netzwerkfehler.' });
      render();
    }
  }

  function send() {
    const input = byId('shopping-agent-input');
    if (!input) return;
    const text = input.value;
    input.value = '';
    input.style.height = 'auto';
    ask(text);
  }

  async function undo() {
    if (state.busy) return;
    try {
      const response = await apiFetch(
        `/api/family/shopping-agent/${encodeURIComponent(state.projectId)}/undo`,
        { method: 'POST', body: JSON.stringify({}) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast(payload.error || 'Rückgängig ist nicht mehr möglich.', true);
        state.canUndo = false;
        render();
        return;
      }
      state.canUndo = false;
      state.changed = true;
      state.messages.push({
        role: 'assistant', content: 'Der Stand vor dem letzten Lauf ist wiederhergestellt.',
      });
      render();
    } catch {
      toast('Netzwerkfehler.', true);
    }
  }
})();
