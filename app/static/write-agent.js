// Schreib-Agent: Dialogfenster fuer genau einen Text.
//
// AI maintenance contract: reine Oberflaeche. Welche Werkzeuge es gibt, welche
// Schreibziele es gibt und was ihr Systemtext sagt, entscheidet allein der
// Server (app/write_agent.py); hier wird nichts nachgebaut. Der Browser nennt
// nur IDs.
//
// Der Unterschied zum Einkaufslisten-Agenten steckt im Uebernehmen: der Agent
// speichert nichts. Sein Vorschlag lebt im Fenster, bis jemand „Uebernehmen"
// drueckt — erst dann landet er im Schreibfeld, und zwar ueber
// writeEditorReplace, damit das Rueckgaengig des Editors ihn wieder wegnimmt.
// Deshalb darf der Agent auch auf einen bereits abgelegten Journaleintrag
// losgelassen werden, ohne ihn anfassen zu koennen.
//
// Antworten kommen aus Modelltext und werden deshalb ausschliesslich als Text
// gesetzt, nie als HTML.
(function () {
  const state = {
    title: '',
    // Woher der Text kam — nur fuer die Kopfzeile und den Uebernehmen-Hinweis.
    origin: 'write',
    text: '',
    proposal: null,
    busy: false,
    messages: [],
    knowledge: [],
    selected: [],
    goals: [],
    goalId: '',
    configured: false,
    providers: [],
    providerId: '',
    maxChars: 40000,
    runId: '',
    runTimer: null,
    apply: null,
  };

  const RUN_POLL_MS = 1500;
  const GOAL_KEY = 'journl.writeAgent.goal';

  const byId = (id) => document.getElementById(id);
  const icon = (name, cls) => (window.journlIcon ? window.journlIcon(name, cls) : '');

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, !!isError);
  }

  function rememberedGoal() {
    try {
      return localStorage.getItem(GOAL_KEY) || '';
    } catch {
      return '';
    }
  }

  function rememberGoal(id) {
    try {
      if (id) localStorage.setItem(GOAL_KEY, id); else localStorage.removeItem(GOAL_KEY);
    } catch {
      // Ohne Merker faengt die Auswahl jedes Mal bei „ohne Ziel" an.
    }
  }

  function currentProvider() {
    return state.providers.find((item) => item.id === state.providerId) || null;
  }

  function currentGoal() {
    return state.goals.find((item) => item.id === state.goalId) || null;
  }

  // ─── Fenster ───────────────────────────────────────────────────────
  function ensurePanel() {
    let panel = byId('write-agent');
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'write-agent';
    panel.className = 'fixed inset-0 z-50 hidden items-center justify-center bg-black/70 p-4';
    panel.innerHTML = `
      <div class="flex h-full max-h-[46rem] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-gray-800 bg-gray-950 shadow-2xl">
        <div class="flex items-center gap-2 border-b border-gray-800 px-4 py-3">
          <span class="text-green-500">${icon('pencil', 'h-5 w-5')}</span>
          <div class="min-w-0 flex-1">
            <h2 id="write-agent-title" class="truncate text-sm font-medium text-gray-100">Schreib-Agent</h2>
            <div id="write-agent-provider" class="flex items-center gap-1.5"></div>
          </div>
          <button type="button" id="write-agent-close"
                  class="rounded-lg p-1 text-gray-500 hover:text-gray-200" title="Schließen">${icon('close', 'h-4 w-4')}</button>
        </div>
        <div id="write-agent-quick" class="space-y-2 border-b border-gray-800 px-4 py-2"></div>
        <div id="write-agent-log" class="flex-1 space-y-2 overflow-y-auto px-4 py-3"></div>
        <div id="write-agent-result" class="hidden border-t border-gray-800 px-4 py-2"></div>
        <div class="flex items-end gap-2 border-t border-gray-800 px-4 py-3">
          <textarea id="write-agent-input" rows="1" placeholder="Was soll ich mit dem Text tun?"
                    class="max-h-32 flex-1 resize-none rounded-xl border border-gray-800 bg-gray-900 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-green-700 focus:outline-none"></textarea>
          <button type="button" id="write-agent-send"
                  class="rounded-xl bg-green-700 px-3 py-2 text-sm text-white hover:bg-green-600 disabled:opacity-40">${icon('arrowUp', 'h-4 w-4')}</button>
        </div>
      </div>`;
    document.body.appendChild(panel);
    byId('write-agent-close').addEventListener('click', closeWriteAgent);
    byId('write-agent-send').addEventListener('click', send);
    const input = byId('write-agent-input');
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
      if (event.target === panel) closeWriteAgent();
    });
    // Auf dem Handy schiebt die Tastatur die Eingabezeile sonst aus dem Bild.
    window.journlAgentViewport?.bind(panel);
    return panel;
  }

  // ─── Zeichnen ──────────────────────────────────────────────────────
  function stepLine(step) {
    const parts = [step.tool];
    if (step.error) parts.push(`Fehler: ${step.error}`);
    if (typeof step.zeichen === 'number') parts.push(`${step.zeichen} Zeichen`);
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
      box.textContent = entry.progress || 'Arbeitet am Text …';
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
    const box = byId('write-agent-provider');
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
    select.title = 'Mit welchem Agenten der Auftrag läuft';
    for (const item of state.providers) {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.model ? `${item.label} · ${item.model}` : item.label;
      if (item.id === state.providerId) option.selected = true;
      select.appendChild(option);
    }
    select.addEventListener('change', () => {
      // Nur fuer dieses Fenster — welcher Agent beim Oeffnen vorne steht,
      // entscheidet der Standard-Agent in der Verwaltung.
      state.providerId = select.value;
      render();
    });
    box.appendChild(select);
    // Ein Anbieter ausserhalb des eigenen Netzes bekommt den ganzen Text zu
    // sehen. Das gehoert sichtbar, nicht versteckt.
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
    const bar = byId('write-agent-quick');
    if (!bar) return;
    bar.textContent = '';

    // Zeile 1: Schreibziel und der Knopf, der es ausfuehrt.
    const row = document.createElement('div');
    row.className = 'flex flex-wrap items-center gap-2';
    const goalSelect = document.createElement('select');
    goalSelect.className = 'min-w-0 flex-1 rounded-lg border border-gray-800 bg-gray-900 px-2 py-1 text-xs text-gray-300 focus:border-green-700 focus:outline-none';
    goalSelect.disabled = state.busy;
    goalSelect.title = 'KI-Schreibziel';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'ohne festes Schreibziel';
    goalSelect.appendChild(none);
    for (const goal of state.goals) {
      const option = document.createElement('option');
      option.value = goal.id;
      option.textContent = goal.label;
      if (goal.id === state.goalId) option.selected = true;
      goalSelect.appendChild(option);
    }
    goalSelect.addEventListener('change', () => {
      state.goalId = goalSelect.value;
      rememberGoal(state.goalId);
      render();
    });
    row.appendChild(goalSelect);

    const run = document.createElement('button');
    run.type = 'button';
    run.className = 'shrink-0 rounded-lg border border-green-800/60 bg-green-900/20 px-2.5 py-1 text-xs text-green-300 hover:border-green-700 disabled:opacity-40';
    run.disabled = state.busy || !state.configured || !state.goalId;
    run.textContent = 'Ausführen';
    run.title = state.goalId ? '' : 'Erst ein Schreibziel wählen';
    run.addEventListener('click', () => {
      const goal = currentGoal();
      if (goal) ask(`Führ „${goal.label}" auf dem Text aus.`);
    });
    row.appendChild(run);
    bar.appendChild(row);

    // Zeile 2: Wissens-Hashtags, mehrere gleichzeitig.
    if (state.knowledge.length) {
      const tags = document.createElement('div');
      tags.className = 'flex flex-wrap items-center gap-1.5';
      const label = document.createElement('span');
      label.className = 'text-[11px] text-gray-600';
      label.textContent = 'Wissen:';
      tags.appendChild(label);
      for (const item of state.knowledge) {
        const active = state.selected.includes(item.hashtag);
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = active
          ? 'rounded-full border border-green-700 bg-green-900/30 px-2 py-0.5 text-[11px] text-green-300'
          : 'rounded-full border border-gray-800 px-2 py-0.5 text-[11px] text-gray-500 hover:border-gray-700 hover:text-gray-300';
        chip.textContent = `#${item.hashtag}`;
        if (item.beschreibung) chip.title = item.beschreibung;
        chip.disabled = state.busy;
        chip.addEventListener('click', () => {
          state.selected = active
            ? state.selected.filter((tag) => tag !== item.hashtag)
            : state.selected.concat(item.hashtag);
          render();
        });
        tags.appendChild(chip);
      }
      bar.appendChild(tags);
    }
  }

  function renderResult() {
    const box = byId('write-agent-result');
    if (!box) return;
    box.textContent = '';
    if (state.proposal === null) {
      box.classList.add('hidden');
      return;
    }
    box.classList.remove('hidden');
    const row = document.createElement('div');
    row.className = 'flex flex-wrap items-center gap-2';

    const note = document.createElement('span');
    note.className = 'min-w-0 flex-1 text-[11px] text-gray-500';
    const delta = state.proposal.length - state.text.length;
    const sign = delta > 0 ? '+' : '';
    note.textContent = state.origin === 'write'
      ? `Vorschlag: ${state.proposal.length} Zeichen (${sign}${delta}).`
      : `Vorschlag: ${state.proposal.length} Zeichen (${sign}${delta}). Übernehmen legt ihn im Schreiben-Tab ab — der abgelegte Eintrag bleibt unberührt.`;
    row.appendChild(note);

    const show = document.createElement('button');
    show.type = 'button';
    show.className = 'shrink-0 rounded-lg border border-gray-800 px-2.5 py-1 text-xs text-gray-400 hover:border-gray-700 hover:text-gray-200';
    show.textContent = 'Ansehen';
    show.addEventListener('click', () => {
      state.messages.push({ role: 'assistant', content: state.proposal, verbatim: true });
      render();
    });
    row.appendChild(show);

    const discard = document.createElement('button');
    discard.type = 'button';
    discard.className = 'shrink-0 rounded-lg border border-gray-800 px-2.5 py-1 text-xs text-gray-400 hover:border-gray-700 hover:text-gray-200';
    discard.textContent = 'Verwerfen';
    discard.addEventListener('click', () => {
      state.proposal = null;
      render();
    });
    row.appendChild(discard);

    const accept = document.createElement('button');
    accept.type = 'button';
    accept.className = 'shrink-0 rounded-lg bg-green-700 px-2.5 py-1 text-xs text-white hover:bg-green-600';
    accept.textContent = 'Übernehmen';
    accept.addEventListener('click', applyProposal);
    row.appendChild(accept);

    box.appendChild(row);
  }

  function render() {
    const log = byId('write-agent-log');
    if (!log) return;
    log.textContent = '';
    if (!state.messages.length) {
      const hint = document.createElement('p');
      hint.className = 'mt-8 text-center text-sm text-gray-600';
      if (!state.configured) {
        hint.textContent = 'Noch keine KI-Verbindung konfiguriert: Burger-Menü → AI-Worker → Anbieter.';
      } else if (!state.text.trim()) {
        hint.textContent = 'Es gibt noch keinen Text, an dem gearbeitet werden könnte.';
      } else {
        hint.textContent = `${state.text.length} Zeichen im Blick. Schreibziel wählen oder einfach sagen, was der Text braucht.`;
      }
      log.appendChild(hint);
    }
    for (const entry of state.messages) log.appendChild(bubble(entry));
    log.scrollTop = log.scrollHeight;
    const sendButton = byId('write-agent-send');
    if (sendButton) sendButton.disabled = state.busy || !state.configured;
    const title = byId('write-agent-title');
    if (title) title.textContent = state.title || 'Schreib-Agent';
    renderProvider();
    renderQuick();
    renderResult();
  }

  // ─── Oeffnen, uebernehmen, schliessen ──────────────────────────────
  function intoWriteField(text) {
    if (typeof window.switchTab === 'function') window.switchTab('write');
    if (typeof window.writeEditorReplace === 'function') {
      // Ueber die Editor-Historie, damit das Rueckgaengig des Schreibfeldes
      // den Vorschlag wieder wegnimmt.
      window.writeEditorReplace(text);
      return true;
    }
    const field = document.getElementById('simple-input');
    if (!field) return false;
    field.value = text;
    field.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }

  function applyProposal() {
    const text = state.proposal;
    if (text === null) return;
    const field = document.getElementById('simple-input');
    const draft = field ? String(field.value || '').trim() : '';
    // Aus Heute oder Brain kommt ein fremder Text. Er darf einen laufenden
    // Entwurf nicht stillschweigend ueberschreiben.
    if (state.origin !== 'write' && draft && draft !== state.text.trim()
        && !window.confirm('Im Schreiben-Tab steht bereits ein Entwurf. Ersetzen?')) {
      return;
    }
    const applied = state.apply ? state.apply(text) : intoWriteField(text);
    if (applied === false) {
      toast('Der Text liess sich nicht übernehmen.', true);
      return;
    }
    state.text = text;
    state.proposal = null;
    toast('Übernommen.');
    closeWriteAgent();
  }

  async function openWriteAgent(options) {
    const settings = options || {};
    ensurePanel();
    state.title = settings.title || 'Schreib-Agent';
    state.origin = settings.origin || 'write';
    state.text = String(settings.text || '');
    state.apply = typeof settings.apply === 'function' ? settings.apply : null;
    state.proposal = null;
    state.messages = [];
    state.selected = [];
    state.providerId = '';
    const panel = byId('write-agent');
    panel.classList.remove('hidden');
    panel.classList.add('flex');
    window.journlAgentViewport?.show(panel);
    render();
    await loadState();
    render();
    byId('write-agent-input')?.focus();
  }

  function closeWriteAgent() {
    stopRunPolling();
    const panel = byId('write-agent');
    if (!panel) return;
    panel.classList.add('hidden');
    panel.classList.remove('flex');
    window.journlAgentViewport?.hide(panel);
  }

  window.openWriteAgent = openWriteAgent;
  window.closeWriteAgent = closeWriteAgent;

  async function loadState() {
    try {
      const response = await apiFetch('/api/write-agent/state', { cache: 'no-store' });
      if (!response.ok) {
        toast('Der Schreib-Agent ist nicht erreichbar.', true);
        return;
      }
      const payload = await response.json();
      state.configured = !!payload.configured;
      state.providers = payload.providers || [];
      state.providerId = state.providers.some((item) => item.id === state.providerId)
        ? state.providerId : (payload.default_provider || '');
      state.goals = payload.goals || [];
      const wantedGoal = rememberedGoal();
      state.goalId = state.goals.some((item) => item.id === wantedGoal) ? wantedGoal : '';
      state.knowledge = payload.knowledge || [];
      state.maxChars = payload.max_chars || state.maxChars;
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
      if (result.changed && typeof result.text === 'string') state.proposal = result.text;
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
      setPending(calls ? `Arbeitet am Text … (${calls} Schritte)` : 'Arbeitet am Text …');
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
    // Der Agent arbeitet auf dem zuletzt uebernommenen Stand, nicht auf einem
    // verworfenen Vorschlag — sonst haengt die zweite Runde in der Luft.
    const working = state.proposal === null ? state.text : state.proposal;
    if (working.length > state.maxChars) {
      toast(`Der Text ist länger als ${state.maxChars} Zeichen.`, true);
      return;
    }
    state.messages.push({ role: 'user', content: question });
    state.busy = true;
    render();
    const history = state.messages
      .filter((entry) => !entry.pending && !entry.error && !entry.verbatim)
      .map((entry) => ({ role: entry.role, content: entry.content }));
    try {
      const response = await apiFetch('/api/write-agent/ask', {
        method: 'POST',
        body: JSON.stringify({
          messages: history,
          text: working,
          provider_id: state.providerId,
          goal_id: state.goalId,
          knowledge: state.selected,
        }),
      });
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
    const input = byId('write-agent-input');
    if (!input) return;
    const text = input.value;
    input.value = '';
    input.style.height = 'auto';
    ask(text);
  }

  // ─── Einstiege aus den drei Tabs ───────────────────────────────────
  window.openWriteAgentFromWriteTab = function () {
    const field = document.getElementById('simple-input');
    openWriteAgent({
      title: 'Schreib-Agent · aktueller Text',
      origin: 'write',
      text: field ? field.value : '',
    });
  };

  window.openWriteAgentFromDashboard = function (editId) {
    const entry = document.getElementById('entry-' + editId);
    openWriteAgent({
      title: 'Schreib-Agent · Journaleintrag',
      origin: 'dashboard',
      text: entry ? entry.dataset.content || '' : '',
    });
  };

  // Der Eintrag im geteilten "⋯"-Menue des Schreiben-Tabs entsteht in
  // agent-lazy.js: das Modul laedt erst beim ersten Aufruf, der Menueeintrag
  // muss aber schon vorher sichtbar sein.
  window.openWriteAgentFromBrain = async function (docId, fingerprint) {
    try {
      const params = new URLSearchParams({ doc_id: docId, fingerprint: fingerprint || '' });
      const response = await apiFetch('/api/brain/document?' + params.toString(), { cache: 'no-store' });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        toast(payload.error || 'Das Dokument ist nicht erreichbar.', true);
        return;
      }
      openWriteAgent({
        title: 'Schreib-Agent · Brain-Treffer',
        origin: 'brain',
        text: payload.content || '',
      });
    } catch {
      toast('Netzwerkfehler.', true);
    }
  };
})();
