// "Chat with your Notes": Chatbereich im Brain-Tab und seine Einstellungen.
// AI maintenance contract: reine Oberflaeche. Werkzeugauswahl, Modellaufruf und
// Sichtbarkeit entscheidet der Server (app/brain_chat.py); hier wird nichts
// nachgebaut und nichts zwischengespeichert, was der Server nicht geschickt hat.
//
// Antworten und Nachweise kommen aus Modelltext und Journalinhalten und werden
// deshalb ausschliesslich als Text gesetzt, nie als HTML.
(function () {
  const state = {
    open: false,
    busy: false,
    // Der reine Verlauf; er wird bei jeder Frage vollstaendig mitgeschickt.
    // Die Wahrheit liegt trotzdem auf dem Server: jeder Turn landet in der
    // Chatdatei des Tages, damit ein geschlossener Browser nichts kostet.
    messages: [],
    // Werkzeugaufrufe stehen eingeklappt unter jeder Antwort; der Menuepunkt
    // blendet sie ganz aus. Die Wahl merkt sich das Geraet.
    showSteps: (() => { try { return localStorage.getItem('journl.brain-chat.steps') !== '0'; } catch (_) { return true; } })(),
    settings: null,
    // Der laufende Serverauftrag und die Datei, an die er anhaengt.
    runId: '',
    runTimer: null,
    sessionPath: '',
    // Ein noch laufendes Speichern der Quellen-Schalter. Die naechste Frage
    // wartet darauf, sonst liefe sie mit dem alten Stand.
    scopeSave: null,
  };

  // Schalter fuer zusaetzliche Quellen; die Felder heissen wie in brain-chat.json.
  const SCOPE_BUTTONS = {
    include_projects: 'brain-chat-scope-projects', include_notes: 'brain-chat-scope-notes',
    semantic_search: 'brain-chat-scope-semantic',
  };
  const SCOPE_ON = ['border-green-700/70', 'bg-green-900/30', 'text-green-300'];
  const SCOPE_OFF = ['border-gray-700', 'bg-gray-800/60', 'text-gray-400'];

  const RUN_KIND = 'brain_chat_ask';
  const RUN_POLL_MS = 1500;

  const byId = (id) => document.getElementById(id);

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, !!isError);
  }

  function panel() {
    return byId('brain-chat');
  }

  // ─── Verlauf zeichnen ──────────────────────────────────────────────
  // Lesbare Namen der Journalwerkzeuge (mcp_tools.py); Unbekanntes bleibt roh.
  const TOOL_LABELS = {
    search: 'Suche', get_blocks: 'Blöcke lesen', list_tags: 'Hashtags auflisten',
    list_days: 'Tage auflisten', get_day: 'Tag lesen',
    list_projects: 'Projekte auflisten', get_project: 'Projekt lesen',
    list_notes: 'Notes auflisten', get_note: 'Notiz lesen',
  };
  const ARG_KEYS = ['query', 'q', 'tag', 'tags', 'date', 'day', 'from', 'to', 'path', 'id', 'ids'];

  function argText(value) {
    if (Array.isArray(value)) return value.join(', ');
    if (value && typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  // Die Kopfzeile eines Aufrufs: Werkzeug · Hauptargument · Ergebnis.
  function stepHeadline(step) {
    const parts = [TOOL_LABELS[step.tool] || step.tool];
    const args = step.arguments || {};
    const key = ARG_KEYS.find((name) => args[name] != null && args[name] !== '');
    if (key) parts.push(`„${argText(args[key]).slice(0, 80)}“`);
    if (step.error) parts.push('Fehler');
    else if (typeof step.count === 'number') parts.push(`${step.count} Treffer`);
    else if (Array.isArray(step.hits)) parts.push(`${step.hits.length} Treffer`);
    if (step.truncated) parts.push('gekürzt');
    return parts.join(' · ');
  }

  // Ein Aufruf, einzeln aufklappbar: Kopfzeile, darunter alle Argumente und Treffer.
  function stepItem(step) {
    const item = document.createElement('details');
    item.className = 'group rounded-md border border-gray-800/80 bg-gray-950/40';
    const head = document.createElement('summary');
    head.className = 'flex cursor-pointer list-none items-center gap-1.5 px-2 py-1 text-gray-400 hover:text-gray-200';
    const chevron = document.createElement('span');
    chevron.className = 'inline-block text-gray-600 transition-transform group-open:rotate-90';
    chevron.textContent = '›';
    const label = document.createElement('span');
    label.className = step.error ? 'text-red-300/80' : '';
    label.textContent = stepHeadline(step);
    head.append(chevron, label);
    item.appendChild(head);
    const body = document.createElement('dl');
    body.className = 'space-y-0.5 border-t border-gray-800/80 px-2 py-1.5 text-gray-500';
    const row = (name, value) => {
      const line = document.createElement('div');
      const term = document.createElement('dt');
      term.className = 'inline text-gray-600';
      term.textContent = `${name}: `;
      const text = document.createElement('dd');
      text.className = 'inline break-words text-gray-400';
      text.textContent = value;
      line.append(term, text);
      body.appendChild(line);
    };
    row('Werkzeug', step.tool);
    Object.entries(step.arguments || {}).forEach(([name, value]) => row(name, argText(value)));
    if (step.error) row('Fehler', step.error);
    if (typeof step.total === 'number') row('insgesamt', String(step.total));
    if (Array.isArray(step.hits) && step.hits.length) row('Treffer', step.hits.join(', '));
    item.appendChild(body);
    return item;
  }

  // Alle Aufrufe einer Antwort: eingeklappt, waehrend des Suchens offen (live).
  function stepsBlock(steps, truncated, live) {
    if (!state.showSteps || !Array.isArray(steps) || !steps.length) return null;
    const details = document.createElement('details');
    details.className = 'mt-2 text-[11px] text-gray-500 not-italic';
    details.open = Boolean(live);
    const summary = document.createElement('summary');
    summary.className = 'cursor-pointer select-none text-gray-500 hover:text-gray-300';
    const tools = [...new Set(steps.map((step) => TOOL_LABELS[step.tool] || step.tool))].join(', ');
    summary.textContent = `${steps.length} Werkzeugaufruf${steps.length === 1 ? '' : 'e'} · ${tools}${truncated ? ' · Recherche gekürzt' : ''}`;
    details.appendChild(summary);
    const list = document.createElement('div');
    list.className = 'mt-1.5 space-y-1';
    steps.forEach((step) => list.appendChild(stepItem(step)));
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
      box.textContent = entry.progress || 'Sucht in deinen Journalen …';
    } else if (entry.error) {
      box.className = 'max-w-[85%] rounded-xl border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-300 whitespace-pre-wrap break-words';
      box.textContent = entry.content;
    } else {
      box.textContent = entry.content;
    }
    const steps = stepsBlock(entry.steps, entry.truncated, entry.pending);
    if (steps) box.appendChild(steps);
    wrap.appendChild(box);
    return wrap;
  }

  function render() {
    const log = byId('brain-chat-log');
    if (!log) return;
    log.textContent = '';
    if (!state.messages.length) {
      const hint = document.createElement('p');
      hint.className = 'mt-8 text-center text-sm text-gray-600';
      hint.textContent = state.settings?.configured
        ? 'Frag dein Journal — zum Beispiel „Worum ging es letzte Woche bei #server?“'
        : 'Noch keine Verbindung konfiguriert: Einstellungen → Brain → Chat with your Notes.';
      log.appendChild(hint);
    }
    for (const entry of state.messages) log.appendChild(bubble(entry));
    log.scrollTop = log.scrollHeight;
    const send = byId('brain-chat-send');
    if (send) send.disabled = state.busy;
    const note = byId('brain-chat-model-note');
    if (note) {
      note.textContent = state.settings?.configured
        ? `${state.settings.model} · ${state.settings.base_url}`
        : 'keine Verbindung konfiguriert';
    }
    renderScopes();
  }

  function renderScopes() {
    Object.entries(SCOPE_BUTTONS).forEach(([field, id]) => {
      const button = byId(id);
      if (!button) return;
      const on = !!state.settings?.[field];
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
      button.classList.remove(...(on ? SCOPE_OFF : SCOPE_ON));
      button.classList.add(...(on ? SCOPE_ON : SCOPE_OFF));
    });
  }

  // Sofort umlegen, dann speichern; schlaegt das fehl, springt der Schalter zurueck.
  async function toggleScope(field) {
    if (!state.settings) await loadSettings();
    if (!state.settings) {
      toast('Die Chat-Einstellungen sind nicht erreichbar.', true);
      return;
    }
    const previous = !!state.settings[field];
    state.settings = { ...state.settings, [field]: !previous };
    renderScopes();
    const save = (async () => {
      try {
        const response = await apiFetch('/api/brain/chat/settings', {
          method: 'POST', body: JSON.stringify({ [field]: !previous }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen.');
        state.settings = { ...state.settings, ...data };
      } catch (error) {
        state.settings = { ...state.settings, [field]: previous };
        toast(error?.message || 'Netzwerkfehler beim Speichern.', true);
      }
      renderScopes();
    })();
    state.scopeSave = save;
    await save;
    if (state.scopeSave === save) state.scopeSave = null;
  }

  // ─── Oeffnen und schliessen ────────────────────────────────────────
  async function openBrainChat() {
    const chat = panel();
    if (!chat) return;
    if (typeof window.closeSettings === 'function') window.closeSettings();
    if (typeof window.switchTab === 'function') window.switchTab('brain');
    state.open = true;
    byId('brain-browser')?.classList.add('hidden');
    byId('brain-toolbar')?.classList.add('hidden');
    chat.classList.remove('hidden');
    chat.classList.add('flex');
    render();
    await loadSettings();
    render();
    await resumeRun();
    byId('brain-chat-input')?.focus();
  }

  function closeBrainChat() {
    const chat = panel();
    if (!chat || !state.open) return;
    state.open = false;
    chat.classList.add('hidden');
    chat.classList.remove('flex');
    byId('brain-toolbar')?.classList.remove('hidden');
  }

  window.openBrainChat = openBrainChat;
  window.closeBrainChat = closeBrainChat;

  // ─── Fragen ────────────────────────────────────────────────────────
  // Die Frage ist ein Serverauftrag. Dieser Tab startet ihn und fragt den Stand
  // ab; sperrt sich das Handy, rechnet der Server zu Ende und die Antwort steht
  // beim naechsten Oeffnen da — samt Verlauf aus der Chatdatei des Tages.
  function stopRunPolling() {
    if (state.runTimer) clearTimeout(state.runTimer);
    state.runTimer = null;
  }

  function setPending(text, steps) {
    const last = state.messages[state.messages.length - 1];
    if (last?.pending) {
      last.progress = text;
      if (Array.isArray(steps)) last.steps = steps;
      render();
    }
  }

  function finishRun(run) {
    state.runId = '';
    state.busy = false;
    if (state.messages[state.messages.length - 1]?.pending) state.messages.pop();
    if (run.status === 'done') {
      const result = run.result || {};
      state.sessionPath = result.session?.path || state.sessionPath;
      state.messages.push({
        role: 'assistant', content: result.answer || '',
        steps: result.steps || [], truncated: !!result.truncated,
      });
      if (result.session_error) toast(result.session_error, true);
    } else if (run.status === 'aborted') {
      state.messages.push({
        role: 'assistant', error: true,
        content: 'Die App wurde neu gestartet, bevor die Antwort fertig war. Bitte erneut fragen.',
      });
    } else {
      state.messages.push({
        role: 'assistant', error: true,
        content: run.error || 'Die Anfrage ist fehlgeschlagen.',
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
      // Kein Kontakt heisst nicht: kein Ergebnis. Der Server rechnet weiter.
      setPending('Kein Kontakt zum Server – die Frage läuft weiter …');
      state.runTimer = setTimeout(pollRun, RUN_POLL_MS * 2);
      return;
    }
    if (run.active) {
      const calls = run.progress?.tool_calls || 0;
      // Das Lauf-Protokoll enthaelt jeden fertigen Werkzeugschritt: live zeigen.
      const live = (run.log || []).filter((step) => step && typeof step === 'object' && step.tool);
      setPending(calls
        ? `Sucht in deinen Journalen … (${calls} Werkzeugaufrufe)`
        : 'Sucht in deinen Journalen …', live);
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

  // Nach einer Handysperre: den noch laufenden Auftrag uebernehmen und, wenn der
  // Verlauf im Browser leer ist, ihn aus der Chatdatei zurueckholen.
  async function resumeRun() {
    try {
      const response = await apiFetch(`/api/runs?kind=${RUN_KIND}`, { cache: 'no-store' });
      if (!response.ok) return;
      const runs = (await response.json()).runs || [];
      const latest = runs[0];
      if (!latest) return;
      const path = latest.result?.session?.path || '';
      if (!state.messages.length && (path || state.sessionPath)) {
        await restoreSession(path || state.sessionPath);
      }
      if (latest.active) {
        if (!state.messages.length || !state.messages[state.messages.length - 1]?.pending) {
          state.messages.push({ role: 'assistant', content: '', pending: true });
        }
        watchRun(latest);
      } else if (path && path !== state.sessionPath) {
        state.sessionPath = path;
      }
    } catch {
      // Ohne Antwort bleibt der Chat, wie er ist.
    }
  }

  async function restoreSession(path) {
    if (!path) return;
    try {
      const response = await apiFetch(`/api/brain/chat/session?path=${encodeURIComponent(path)}`,
        { cache: 'no-store' });
      if (!response.ok) return;
      const session = await response.json();
      state.sessionPath = session.path || path;
      state.messages = (session.turns || []).map((turn) => ({
        role: turn.role, content: turn.content,
        steps: turn.steps || [], truncated: !!turn.truncated,
      }));
      render();
    } catch {
      // Ein nicht lesbarer Verlauf ist kein Grund, den Chat zu blockieren.
    }
  }

  async function send() {
    if (state.busy) return;
    const input = byId('brain-chat-input');
    const text = (input?.value || '').trim();
    if (!text) return;
    state.busy = true;
    input.value = '';
    state.messages.push({ role: 'user', content: text });
    state.messages.push({ role: 'assistant', content: '', pending: true });
    render();
    if (state.scopeSave) await state.scopeSave;

    const history = state.messages
      .filter((entry) => !entry.pending && !entry.error)
      .map((entry) => ({ role: entry.role, content: entry.content }));
    try {
      const response = await apiFetch('/api/brain/chat/ask', {
        method: 'POST',
        body: JSON.stringify({ messages: history, session_path: state.sessionPath }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 409 && data.run) {
          toast('Es läuft bereits eine Frage – sie wird jetzt angezeigt.', true);
          watchRun(data.run);
          return;
        }
        state.messages.pop();
        state.busy = false;
        state.messages.push({
          role: 'assistant', error: true,
          content: data.error || 'Die Anfrage ist fehlgeschlagen.',
        });
        render();
        return;
      }
      watchRun(data.run);
    } catch {
      state.messages.pop();
      state.busy = false;
      state.messages.push({ role: 'assistant', content: 'Netzwerkfehler beim Fragen.', error: true });
      render();
    } finally {
      byId('brain-chat-input')?.focus();
    }
  }

  async function discardSession() {
    const path = state.sessionPath;
    if (!path) {
      state.messages = [];
      render();
      return;
    }
    if (!window.confirm('Diesen Chatverlauf endgültig löschen?')) return;
    try {
      const response = await apiFetch('/api/brain/chat/session', {
        method: 'DELETE', body: JSON.stringify({ path }),
      });
      if (!response.ok) {
        toast('Der Verlauf konnte nicht gelöscht werden.', true);
        return;
      }
      state.sessionPath = '';
      state.messages = [];
      render();
      toast('Chatverlauf gelöscht.');
    } catch {
      toast('Netzwerkfehler beim Löschen des Verlaufs.', true);
    }
  }

  // ─── Einstellungen ─────────────────────────────────────────────────
  async function loadSettings() {
    try {
      const response = await apiFetch('/api/brain/chat/settings', { cache: 'no-store' });
      state.settings = response.ok ? await response.json() : null;
    } catch {
      state.settings = null;
    }
    return state.settings;
  }

  function settingsStatus(message, isError) {
    const status = byId('brain-chat-settings-status');
    if (!status) return;
    status.textContent = message;
    status.className = `ml-2 text-xs ${isError ? 'text-red-400' : 'text-gray-500'}`;
  }

  function fillModelOptions(models, selected) {
    const select = byId('brain-chat-model');
    if (!select) return;
    select.textContent = '';
    const names = models && models.length ? models : (selected ? [selected] : []);
    if (!names.length) {
      const option = document.createElement('option');
      option.value = '';
      option.textContent = '– noch keine Modelle geladen –';
      select.appendChild(option);
      return;
    }
    for (const name of names) {
      const option = document.createElement('option');
      option.value = name;
      option.textContent = name;
      if (name === selected) option.selected = true;
      select.appendChild(option);
    }
  }

  async function fillSettingsForm() {
    const settings = await loadSettings();
    const base = byId('brain-chat-base-url');
    const key = byId('brain-chat-api-key');
    if (!base) return;
    base.value = settings?.base_url || '';
    if (key) key.placeholder = settings?.has_api_key ? 'gespeichert – leer lassen = unverändert' : 'leer lassen = unverändert';
    fillModelOptions([], settings?.model || '');
    settingsStatus(settings?.configured ? '' : 'Endpunkt eintragen, Modelle laden, Modell wählen.');
  }

  async function loadModels() {
    const base = byId('brain-chat-base-url');
    if (!base?.value.trim()) {
      settingsStatus('Bitte zuerst den Endpunkt eintragen.', true);
      return;
    }
    settingsStatus('Modelle werden gelesen …');
    try {
      const params = new URLSearchParams({ base_url: base.value.trim() });
      const response = await apiFetch(`/api/brain/chat/models?${params.toString()}`, { cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        settingsStatus(data.error || 'Der Endpunkt hat keine Modelle gemeldet.', true);
        return;
      }
      fillModelOptions(data.models || [], state.settings?.model || '');
      settingsStatus(`${(data.models || []).length} Modelle gefunden.`);
    } catch {
      settingsStatus('Netzwerkfehler beim Lesen der Modelle.', true);
    }
  }

  async function saveSettings() {
    const base = byId('brain-chat-base-url');
    const model = byId('brain-chat-model');
    const key = byId('brain-chat-api-key');
    const payload = { base_url: base?.value.trim() || '', model: model?.value || '' };
    // Ein leeres Feld laesst den gespeicherten Schluessel unangetastet: der
    // Browser bekommt ihn nie zu sehen und kann ihn deshalb nicht zurueckschicken.
    if (key && key.value) payload.api_key = key.value;
    settingsStatus('Wird gespeichert …');
    try {
      const response = await apiFetch('/api/brain/chat/settings', {
        method: 'POST', body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        settingsStatus(data.error || 'Speichern fehlgeschlagen.', true);
        return;
      }
      if (key) {
        key.value = '';
        key.placeholder = data.has_api_key ? 'gespeichert – leer lassen = unverändert' : 'leer lassen = unverändert';
      }
      state.settings = data;
      settingsStatus('Gespeichert.');
      if (state.open) render();
    } catch {
      settingsStatus('Netzwerkfehler beim Speichern.', true);
    }
  }

  // ─── Verdrahtung ───────────────────────────────────────────────────
  byId('brain-menu-chat')?.addEventListener('click', openBrainChat);
  byId('brain-chat-back')?.addEventListener('click', () => {
    closeBrainChat();
    byId('brain-browser')?.classList.remove('hidden');
    if (typeof window.loadBrainCurrentMode === 'function') window.loadBrainCurrentMode();
  });
  byId('brain-chat-send')?.addEventListener('click', send);
  Object.entries(SCOPE_BUTTONS).forEach(([field, id]) => {
    byId(id)?.addEventListener('click', () => toggleScope(field));
  });
  byId('brain-chat-input')?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  byId('brain-chat-menu-clear')?.addEventListener('click', () => {
    // Neuer Chat: der bisherige Verlauf bleibt als Datei im Tagesordner liegen.
    stopRunPolling();
    state.messages = [];
    state.sessionPath = '';
    state.runId = '';
    state.busy = false;
    render();
  });
  byId('brain-chat-menu-discard')?.addEventListener('click', discardSession);
  byId('brain-chat-menu-steps')?.addEventListener('click', () => {
    state.showSteps = !state.showSteps;
    try { localStorage.setItem('journl.brain-chat.steps', state.showSteps ? '1' : '0'); } catch (_) { /* privat */ }
    toast(state.showSteps ? 'Werkzeugaufrufe werden angezeigt.' : 'Werkzeugaufrufe ausgeblendet.');
    render();
  });
  byId('brain-chat-menu-settings')?.addEventListener('click', () => {
    if (typeof window.openSettingsGroup === 'function') window.openSettingsGroup('brain', 'Brain');
  });
  byId('brain-chat-models-load')?.addEventListener('click', loadModels);
  byId('brain-chat-settings-save')?.addEventListener('click', saveSettings);
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'brain') fillSettingsForm();
  });
})();
