/* Agent-Projekte („An Agent senden") — Projekt-, Verbindungs- und Kontextwahl.
 *
 * Das Textfeld im Schreiben-Tab ist hier der AUFTRAG, nicht das Dokument: es
 * genügt, die Box auszufüllen und zu senden.  Antworten laufen in ein
 * Antwortfenster unter der Box; jede Folgeanfrage wird wieder einfach in die
 * Box getippt und setzt dieselbe Agenten-Session fort (opencode --session /
 * pi --session-id).  Ordner, Modus, Verbindung und Modell hängen am Projekt,
 * nicht an einem Hashtag.
 */
(function () {
  'use strict';

  const MODE = '__assistant_mode__';
  const API = '/api/agent-projects';
  const JOB_URL = '/api/write-ai/jobs';
  const NEW_PROJECT = '__new__';
  const MODE_LABELS = { read: 'Lesen', write: 'Schreiben', execute: 'Ausführen' };

  let bootstrap = null;      // {roots, connections, projects, today, ...}
  let current = null;        // active project view
  let panel = null;
  let body = null;           // wechselnder Inhalt unter der festen Kopfzeile
  let active = false;
  let epoch = 0;
  let saveTimer = null;
  let turns = [];
  let pendingText = null;    // text handed in from Dashboard/Brain/⋯-Menü
  let injectedText = null;   // was aus Heute/Brain in die Box gelegt wurde

  // ─── Helfer ─────────────────────────────────────────────────────────
  function field() { return document.getElementById('simple-input'); }
  function select() { return document.getElementById('template-select'); }
  function pickers() { return window.agentProjectPickers; }
  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, isError);
  }
  function isActiveMode() { return Boolean(select()) && select().value === MODE; }

  async function call(path, options) {
    const response = await window.apiFetch(path, options || {});
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) {
      const error = new Error(data.error || 'Anfrage fehlgeschlagen.');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  async function refresh() {
    bootstrap = await call(API + '/bootstrap', { cache: 'no-store' });
    if (current) {
      current = bootstrap.projects.find(function (item) { return item.id === current.id; }) || null;
    }
    if (!current && bootstrap.projects.length) current = bootstrap.projects[0];
  }

  // ─── Panel ──────────────────────────────────────────────────────────
  function ensurePanel() {
    if (panel) return panel;
    panel = document.createElement('div');
    panel.id = 'agent-project-panel';
    panel.className = 'hidden mt-2 space-y-2 border-t border-gray-700/50 pt-2.5';
    // Kopfzeile und Inhalt sind getrennt: „Abbrechen“ muss auch dann noch da
    // sein, wenn der Inhalt gerade nur eine Lade- oder Fehlermeldung ist. Aus
    // dem Modus kam sonst nur heraus, wer die Vorlagenauswahl kennt — auf dem
    // Handy ist die nur ein Raster-Icon.
    const head = document.createElement('div');
    head.className = 'flex items-center gap-2';
    head.innerHTML = '<span class="min-w-0 flex-1 truncate text-xs font-semibold text-gray-300">Agent-Projekt</span>'
      + '<button type="button" data-leave class="shrink-0 rounded border border-gray-700 px-2 py-0.5 text-xs text-gray-400 '
      + 'hover:bg-gray-800 hover:text-gray-200" title="Agent-Projekt verlassen">Abbrechen</button>';
    head.querySelector('[data-leave]').addEventListener('click', exitMode);
    body = document.createElement('div');
    body.className = 'space-y-2';
    panel.append(head, body);
    return panel;
  }

  /* Zurueck in den normalen Schreiben-Modus. Ein selbst getippter Auftrag
     bleibt stehen; der unveraenderte Eintragstext, den der Sprung aus Heute
     oder Brain in die Box gelegt hat, wird geleert — sonst liegt dort ein
     fremder Eintrag als scheinbar neue Notiz. */
  function exitMode() {
    const box = field();
    if (box && injectedText !== null && box.value === injectedText) box.value = '';
    injectedText = null;
    pendingText = null;
    const chooser = select();
    if (!chooser) { window.leaveAssistantMode(); return; }
    const plain = Array.from(chooser.options)
      .map(function (option) { return option.value; })
      .filter(function (value) { return value && value.slice(0, 2) !== '__'; });
    chooser.value = plain.indexOf('schnell') >= 0 ? 'schnell' : (plain[0] || '');
    chooser.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function projectOptions() {
    const options = (bootstrap.projects || []).map(function (project) {
      return '<option value="' + esc(project.id) + '"' +
        (current && current.id === project.id ? ' selected' : '') + '>' +
        esc(project.label) + '</option>';
    });
    options.push('<option value="' + NEW_PROJECT + '">＋ Neues Projekt…</option>');
    return options.join('');
  }

  function summaryChips() {
    if (!current) return '';
    const chips = [];
    const folder = (current.root_label || current.root_id || '?') + ' › ' + (current.subpath || '.');
    chips.push('<button type="button" data-pick-folder class="rounded bg-gray-800/70 border border-gray-700 px-2 py-0.5 hover:bg-gray-700/70" title="Projekt-Ordner ändern"><span class="inline-flex items-center gap-1.5">' + (window.journlIcon?.('folder', 'h-3.5 w-3.5') || '') + '<span>' + esc(folder) + '</span></span></button>');
    chips.push('<button type="button" data-pick-folder class="rounded bg-gray-800/70 border border-gray-700 px-2 py-0.5 hover:bg-gray-700/70" title="Zugriffsmodus ändern">' +
      '<span class="inline-flex items-center gap-1.5">' + (current.mode === 'read' ? (window.journlIcon?.('eye', 'h-3.5 w-3.5') || '') : current.mode === 'write' ? (window.journlIcon?.('pencil', 'h-3.5 w-3.5') || '') : (window.journlIcon?.('bolt', 'h-3.5 w-3.5') || '')) + '<span>' + esc(MODE_LABELS[current.mode]) + '</span></span></button>');
    const connection = current.connection_label
      ? current.connection_label + ' · ' + (current.runner || '?')
      : 'keine Verbindung';
    chips.push('<button type="button" data-edit-connection class="rounded bg-gray-800/70 border border-gray-700 px-2 py-0.5 hover:bg-gray-700/70" title="Verbindung bearbeiten"><span class="inline-flex items-center gap-1.5">' + (window.journlIcon?.('plug', 'h-3.5 w-3.5') || '') + '<span>' + esc(connection) + '</span></span></button>');
    chips.push('<span class="rounded bg-gray-800/70 border border-gray-700 px-2 py-0.5 text-gray-400" title="Modell"><span class="inline-flex items-center gap-1.5">' + (window.journlIcon?.('chip', 'h-3.5 w-3.5') || '') + '<span>' + esc(current.model || '—') + '</span></span></span>');
    if (current.has_session) {
      chips.push('<button type="button" data-reset-session class="rounded bg-gray-800/70 border border-gray-700 px-2 py-0.5 text-gray-400 hover:bg-gray-700/70" title="Laufende Agenten-Session verwerfen und neu beginnen">↺ neue Session</button>');
    }
    return chips.join('');
  }

  function render() {
    if (!body) return;
    if (!bootstrap.roots_configured) {
      body.innerHTML = '<div class="rounded border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-xs text-amber-300">' +
        'Es ist noch kein Projekt-Ordner freigegeben. Bitte auf dem Host in <code>host_worker.json</code> ' +
        '<code>external_read_roots</code> bzw. <code>external_write_roots</code> setzen und den Worker neu starten.</div>';
      return;
    }
    const fileCount = current ? (current.context_files || []).length : 0;
    body.innerHTML = [
      '<div class="flex flex-wrap items-center gap-2">',
      '  <select id="ap-project" class="h-7 max-w-[13rem] rounded bg-gray-800 border border-gray-700 px-2 text-xs text-gray-100 focus:outline-none focus:ring-1 focus:ring-green-500">' + projectOptions() + '</select>',
      current ? '  <button type="button" data-rename class="text-xs text-gray-500 hover:text-gray-300" title="Projekt umbenennen">' + (window.journlIcon?.('pencil', 'h-3.5 w-3.5') || '') + '</button>' : '',
      current ? '  <button type="button" data-delete-project class="text-xs text-gray-600 hover:text-red-400" title="Projekt löschen">' + (window.journlIcon?.('trash', 'h-3.5 w-3.5') || '') + '</button>' : '',
      '  <span class="ml-auto flex items-center gap-1.5">',
      '    <button type="button" data-today class="rounded-full border px-2 py-0.5 text-[11px] transition ' +
        (current && current.include_today
          ? 'border-green-600/70 bg-green-900/40 text-green-300'
          : 'border-gray-700 text-gray-500 hover:text-gray-300') +
        '" title="Das gesamte heutige Journal als Kontext mitgeben">● heute</button>',
      '    <button type="button" data-files class="rounded-full border px-2 py-0.5 text-[11px] transition ' +
        (fileCount ? 'border-sky-600/70 bg-sky-900/40 text-sky-300' : 'border-gray-700 text-gray-500 hover:text-gray-300') +
        '" title="Einzelne Dateien über einen Tag auswählen"><span class="inline-flex items-center gap-1.5">' + (window.journlIcon?.('calendar', 'h-3 w-3') || '') + '<span>Dateien</span></span>' + (fileCount ? ' (' + fileCount + ')' : '') + '</button>',
      '  </span>',
      '</div>',
      current ? '<div class="flex flex-wrap items-center gap-1.5 text-[11px] text-gray-300">' + summaryChips() + '</div>' : '',
      current && current.problems.length
        ? '<div class="rounded border border-amber-800/60 bg-amber-950/30 px-2 py-1 text-[11px] text-amber-300">' +
          current.problems.map(esc).join('<br>') + '</div>'
        : '',
      '<div class="flex items-start gap-2">',
      '  <button type="button" id="ap-send" class="shrink-0 h-8 whitespace-nowrap rounded bg-green-700/70 hover:bg-green-600 px-3 text-xs font-semibold text-white transition">An Agent senden</button>',
      '  <div id="ap-status" class="min-w-0 flex-1 text-[11px] text-gray-500"></div>',
      '</div>',
      '<div id="ap-turns" class="space-y-1.5"></div>',
    ].join('\n');

    const projectSelect = panel.querySelector('#ap-project');
    if (projectSelect) {
      projectSelect.addEventListener('change', function () {
        if (projectSelect.value === NEW_PROJECT) {
          projectSelect.value = current ? current.id : '';
          createProject();
          return;
        }
        current = bootstrap.projects.find(function (item) { return item.id === projectSelect.value; }) || null;
        render();
        loadTurns();
      });
    }
    bind('[data-pick-folder]', pickFolder);
    bind('[data-edit-connection]', editConnection);
    bind('[data-today]', toggleToday);
    bind('[data-files]', pickFiles);
    bind('[data-rename]', renameProject);
    bind('[data-delete-project]', deleteProject);
    bind('[data-reset-session]', resetSession);
    const send = panel.querySelector('#ap-send');
    if (send) send.addEventListener('click', submit);
    renderTurns();
  }

  function bind(selector, handler) {
    panel.querySelectorAll(selector).forEach(function (element) {
      element.addEventListener('click', handler);
    });
  }

  function status(text, tone) {
    const box = panel && panel.querySelector('#ap-status');
    if (!box) return;
    box.className = 'min-w-0 flex-1 text-[11px] ' +
      (tone === 'error' ? 'text-red-400' : tone === 'ok' ? 'text-green-400' : 'text-gray-500');
    box.textContent = text || '';
  }

  // ─── Antwortfenster ─────────────────────────────────────────────────
  function renderTurns() {
    const box = panel && panel.querySelector('#ap-turns');
    if (!box) return;
    if (!turns.length) {
      box.innerHTML = current
        ? '<div class="text-[11px] text-gray-600">Noch keine Runde. Auftrag oben eintippen und senden.</div>'
        : '';
      return;
    }
    box.innerHTML = turns.slice(-12).map(function (turn, index) {
      const isAnswer = turn.kind === 'answer';
      return '<div class="rounded border p-2 text-xs ' +
        (isAnswer ? 'border-green-900/60 bg-green-950/20' : 'border-gray-700/50 bg-gray-900/50') + '">' +
        '<div class="mb-1 flex items-center justify-between gap-2">' +
        '<span class="text-[10px] uppercase tracking-wide ' + (isAnswer ? 'text-green-500' : 'text-gray-500') + '">' +
        (isAnswer ? 'Agent' : 'Auftrag') + '</span>' +
        '<span class="flex items-center gap-1.5">' +
        (isAnswer ? '<button type="button" data-copy="' + index + '" class="text-[10px] text-gray-500 hover:text-gray-200" title="Antwort in die Textbox übernehmen">→ Textbox</button>' : '') +
        '<span class="text-[10px] text-gray-600">' + esc((turn.at || '').replace('T', ' ').slice(0, 16)) + '</span>' +
        '</span></div>' +
        '<div class="whitespace-pre-wrap break-words ' + (isAnswer ? 'text-gray-200' : 'text-gray-400') + '">' +
        esc(turn.body.length > 4000 ? turn.body.slice(0, 4000) + '\n…' : turn.body) + '</div></div>';
    }).join('');
    box.querySelectorAll('[data-copy]').forEach(function (button) {
      button.addEventListener('click', function () {
        const turn = turns.slice(-12)[parseInt(button.dataset.copy, 10)];
        if (turn && field()) {
          field().value = turn.body;
          field().focus();
        }
      });
    });
    box.scrollTop = box.scrollHeight;
  }

  async function loadTurns() {
    turns = [];
    if (!current || !current.has_session) {
      renderTurns();
      return;
    }
    try {
      const data = await call(API + '/' + encodeURIComponent(current.id) + '/turns', { cache: 'no-store' });
      turns = data.turns || [];
    } catch (error) {
      turns = [];
    }
    renderTurns();
  }

  // ─── Projekt bearbeiten ─────────────────────────────────────────────
  async function saveProject(patch, options) {
    const settings = options || {};
    const base = current || {};
    const project = {
      label: patch.label !== undefined ? patch.label : base.label,
      root_id: patch.root_id !== undefined ? patch.root_id : base.root_id,
      subpath: patch.subpath !== undefined ? patch.subpath : base.subpath,
      mode: patch.mode !== undefined ? patch.mode : base.mode,
      connection_id: patch.connection_id !== undefined ? patch.connection_id : base.connection_id,
      model: patch.model !== undefined ? patch.model : base.model,
      include_today: patch.include_today !== undefined ? patch.include_today : base.include_today,
      journal_date: patch.journal_date !== undefined ? patch.journal_date : base.journal_date,
      context_files: patch.context_files !== undefined ? patch.context_files : base.context_files,
    };
    try {
      const data = await call(API + '/projects', {
        method: 'POST',
        body: JSON.stringify({ action: 'save', id: settings.create ? undefined : base.id, project: project }),
      });
      await refresh();
      current = bootstrap.projects.find(function (item) { return item.id === data.project.id; }) || data.project;
      render();
      if (settings.create) loadTurns();
      return true;
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }

  function chooseModel(connection, preselected, onPick) {
    const dialog = pickers().overlay('Modell wählen', '<div data-list class="text-xs text-gray-400">Frage ' +
      esc(connection.label) + ' ab…</div>', '');
    const list = dialog.host.querySelector('[data-list]');
    call(API + '/models?connection_id=' + encodeURIComponent(connection.id), { cache: 'no-store', timeoutMs: 20000 })
      .then(function (data) {
        list.innerHTML = data.models.map(function (model) {
          return '<button type="button" data-model="' + esc(model) + '" class="block w-full rounded px-2 py-1 text-left ' +
            (model === preselected ? 'bg-green-900/40 text-green-200' : 'text-gray-200 hover:bg-gray-800') + '">' +
            esc(model) + '</button>';
        }).join('');
        list.querySelectorAll('[data-model]').forEach(function (button) {
          button.addEventListener('click', function () {
            dialog.close();
            onPick(button.dataset.model);
          });
        });
      })
      .catch(function (error) {
        list.innerHTML = '<div class="text-red-400">' + esc(error.message) + '</div>' +
          '<p class="mt-2 text-[11px] text-gray-500">Basis-URL und laufenden Server prüfen, dann erneut versuchen.</p>';
      });
  }

  function createProject() {
    if (!bootstrap.connections.length) {
      toast('Bitte zuerst eine Verbindung anlegen.', true);
      editConnection();
      return;
    }
    pickers().openFolderPicker({
      roots: bootstrap.roots, mode: 'read',
      onPick: function (folder) {
        const connection = bootstrap.connections[0];
        chooseModel(connection, '', function (model) {
          const label = (folder.subpath || '').split('/').pop() || folder.root_label;
          saveProject({
            label: label, root_id: folder.root_id, subpath: folder.subpath, mode: folder.mode,
            connection_id: connection.id, model: model, include_today: false,
            journal_date: '', context_files: [],
          }, { create: true }).then(function (ok) {
            if (ok) toast('Projekt „' + label + '“ angelegt');
          });
        });
      },
    });
  }

  function pickFolder() {
    if (!current) return;
    pickers().openFolderPicker({
      roots: bootstrap.roots, rootId: current.root_id, subpath: current.subpath, mode: current.mode,
      onPick: function (folder) {
        saveProject({ root_id: folder.root_id, subpath: folder.subpath, mode: folder.mode });
      },
    });
  }

  function editConnection() {
    const existing = current
      ? bootstrap.connections.find(function (item) { return item.id === current.connection_id; })
      : null;
    if (existing) {
      openConnectionMenu(existing);
      return;
    }
    pickers().openConnectionEditor({
      connections: bootstrap.connections, connection: null,
      onSaved: async function (connection) {
        await refresh();
        if (connection && current) {
          chooseModel(connection, current.model, function (model) {
            saveProject({ connection_id: connection.id, model: model });
          });
        } else {
          render();
        }
      },
    });
  }

  function openConnectionMenu(connection) {
    const rows = bootstrap.connections.map(function (item) {
      return '<button type="button" data-use="' + esc(item.id) + '" class="block w-full rounded px-2 py-1 text-left ' +
        (item.id === connection.id ? 'bg-green-900/40 text-green-200' : 'text-gray-200 hover:bg-gray-800') + '">' +
        esc(item.label) + ' <span class="text-gray-500">· ' + esc(pickers().kindLabel(item.kind)) +
        ' · ' + esc(item.runner) + '</span></button>';
    }).join('');
    const dialog = pickers().overlay('Verbindung', rows, [
      '<button type="button" data-new class="' + pickers().classes.ghost + ' mr-auto">＋ Neue Verbindung</button>',
      '<button type="button" data-edit class="' + pickers().classes.ghost + '">Aktuelle bearbeiten</button>',
      '<button type="button" data-model class="' + pickers().classes.primary + '">Modell wählen</button>',
    ].join(''));
    dialog.host.querySelectorAll('[data-use]').forEach(function (button) {
      button.addEventListener('click', function () {
        const chosen = bootstrap.connections.find(function (item) { return item.id === button.dataset.use; });
        dialog.close();
        chooseModel(chosen, current.model, function (model) {
          saveProject({ connection_id: chosen.id, model: model });
        });
      });
    });
    dialog.footer.querySelector('[data-new]').addEventListener('click', function () {
      dialog.close();
      pickers().openConnectionEditor({
        connections: bootstrap.connections, connection: null,
        onSaved: async function (created) {
          await refresh();
          if (created) {
            chooseModel(created, '', function (model) {
              saveProject({ connection_id: created.id, model: model });
            });
          } else { render(); }
        },
      });
    });
    dialog.footer.querySelector('[data-edit]').addEventListener('click', function () {
      dialog.close();
      pickers().openConnectionEditor({
        connections: bootstrap.connections, connection: connection,
        onSaved: async function () { await refresh(); render(); },
      });
    });
    dialog.footer.querySelector('[data-model]').addEventListener('click', function () {
      dialog.close();
      chooseModel(connection, current.model, function (model) { saveProject({ model: model }); });
    });
  }

  function toggleToday() {
    if (!current) { createProject(); return; }
    saveProject({ include_today: !current.include_today, journal_date: bootstrap.today });
  }

  function pickFiles() {
    if (!current) { createProject(); return; }
    pickers().openFilePicker({
      today: bootstrap.today, date: current.journal_date || bootstrap.today,
      selected: current.context_files || [],
      onPick: function (result) { saveProject({ context_files: result.files }); },
    });
  }

  function renameProject() {
    if (!current) return;
    const label = window.prompt('Projektname', current.label);
    if (label === null) return;
    saveProject({ label: label.trim() || current.label });
  }

  async function deleteProject() {
    if (!current) return;
    if (!window.confirm('Projekt „' + current.label + '“ entfernen? Die Agent-Session bleibt in Brain erhalten.')) return;
    try {
      await call(API + '/projects', { method: 'POST', body: JSON.stringify({ action: 'delete', id: current.id }) });
      current = null;
      await refresh();
      render();
      loadTurns();
    } catch (error) {
      toast(error.message, true);
    }
  }

  async function resetSession() {
    if (!current) return;
    try {
      await call(API + '/' + encodeURIComponent(current.id) + '/session', { method: 'POST', body: '{}' });
      await refresh();
      render();
      loadTurns();
      toast('Neue Agenten-Session wird beim nächsten Auftrag gestartet.');
    } catch (error) {
      toast(error.message, true);
    }
  }

  // ─── Senden ─────────────────────────────────────────────────────────
  async function submit() {
    if (!current) { createProject(); return; }
    if (current.problems.length) { toast(current.problems[0], true); return; }
    const instruction = field() ? field().value.trim() : '';
    if (!instruction) { toast('Bitte einen Auftrag in die Textbox schreiben.', true); return; }

    const token = window.writeSubmitState && window.writeSubmitState.begin({
      scope: 'assistant', supersedeScopes: ['ai'],
      readyText: 'An Agent senden', busyText: 'Agent arbeitet…',
      activeMessage: 'Es läuft bereits ein Auftrag – du kannst weiter schreiben.',
      timeoutMs: 1_860_000,
      timeoutMessage: 'Der Auftrag wurde im Browser freigegeben. Das Ergebnis erscheint in Brain.',
    });
    if (!token) return;

    const localEpoch = epoch;
    const send = panel.querySelector('#ap-send');
    if (send) { send.disabled = true; send.textContent = 'Agent arbeitet…'; }
    turns.push({ kind: 'request', at: new Date().toISOString(), body: instruction });
    renderTurns();
    status('Auftrag wird eingereiht…');
    try {
      const queued = await call(API + '/' + encodeURIComponent(current.id) + '/submit', {
        method: 'POST', body: JSON.stringify({ instruction: instruction }), timeoutMs: 60000,
      });
      if (field()) field().value = '';
      injectedText = null;
      const answer = await waitForJob(queued.job_id);
      await refresh();
      if (epoch !== localEpoch) return;
      render();
      if (answer) {
        turns.push({ kind: 'answer', at: new Date().toISOString(), body: answer });
        renderTurns();
        status('Fertig.', 'ok');
        toast('Der Agent hat geantwortet');
      } else {
        await loadTurns();
      }
    } catch (error) {
      status('Fehler: ' + error.message, 'error');
      toast(error.message, !(error && error.cancelled));
      if (field() && !field().value.trim()) field().value = instruction;
    } finally {
      if (send && epoch === localEpoch) { send.disabled = false; send.textContent = 'An Agent senden'; }
      window.writeSubmitState && window.writeSubmitState.finish(token);
    }
  }

  async function waitForJob(jobId) {
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      await new Promise(function (resolve) { setTimeout(resolve, 1000); });
      const response = await window.apiFetch(JOB_URL + '/' + encodeURIComponent(jobId), { timeoutMs: 30000 });
      const job = await response.json().catch(function () { return {}; });
      if (!response.ok) throw new Error(job.error || 'Job konnte nicht gelesen werden.');
      if (job.status === 'completed') return job.response || '';
      if (job.status === 'error') throw new Error(job.error || 'Der Agentenauftrag ist fehlgeschlagen.');
      if (job.status === 'cancelled') {
        const cancelled = new Error('Auftrag abgebrochen.');
        cancelled.cancelled = true;
        throw cancelled;
      }
      status(job.status === 'running' ? 'Agent arbeitet im Projektordner…' : 'Wartet auf den Host-Worker…');
    }
    throw new Error('Der Agentenauftrag hat das Zeitlimit überschritten.');
  }

  // ─── Aktivierung ────────────────────────────────────────────────────
  window.handleAssistantMode = async function () {
    active = true;
    ensurePanel();
    const area = document.getElementById('input-area');
    if (area && !panel.parentNode) area.insertAdjacentElement('afterend', panel);
    body.innerHTML = '<div class="text-xs text-gray-500">Lade Agent-Projekte…</div>';
    panel.classList.remove('hidden');
    try {
      await refresh();
    } catch (error) {
      body.innerHTML = '<div class="text-xs text-red-400">' + esc(error.message) + '</div>';
      return;
    }
    render();
    await loadTurns();
    if (pendingText && field()) {
      field().value = pendingText;
      injectedText = pendingText;
      pendingText = null;
    }
    if (field()) field().focus();
    const info = document.getElementById('app-info-box');
    if (info) {
      info.textContent = 'Agent-Projekt aktiv: Auftrag in die Textbox schreiben und senden. '
        + 'Ordner, Modus, Verbindung und Modell hängen am gewählten Projekt.';
    }
  };

  window.leaveAssistantMode = function () {
    if (!active) return;
    active = false;
    epoch += 1;
    clearTimeout(saveTimer);
    if (panel) panel.classList.add('hidden');
    const submitBtn = document.getElementById('submit-btn');
    if (submitBtn) submitBtn.textContent = 'Senden';
    const info = document.getElementById('app-info-box');
    if (info) {
      info.textContent = 'Text eingeben und „Senden" klicken oder ⌘/Ctrl+Enter drücken. '
        + 'KI-Modus: ⌥⌘K (Windows/Linux: Ctrl+Alt+K).';
    }
  };

  // ─── Einsprünge ─────────────────────────────────────────────────────
  function openWithText(text) {
    pendingText = text || null;
    if (!select()) return;
    if (isActiveMode()) {
      if (pendingText && field()) {
        field().value = pendingText;
        injectedText = pendingText;
        pendingText = null;
        field().focus();
      }
      return;
    }
    select().value = MODE;
    select().dispatchEvent(new Event('change', { bubbles: true }));
    if (typeof window.switchTab === 'function') window.switchTab('write');
  }

  window.openAssistantFromWriteTab = function () {
    if (isActiveMode()) { submit(); return; }
    openWithText(field() && field().value.trim() ? field().value : null);
  };

  window.openAssistantFromDashboard = function (editId) {
    const entry = document.getElementById('entry-' + editId);
    openWithText(entry ? entry.dataset.content || '' : '');
  };

  window.openAssistantFromBrain = async function (docId, fingerprint) {
    try {
      const params = new URLSearchParams({ doc_id: docId, fingerprint: fingerprint || '' });
      const data = await call('/api/brain/document?' + params.toString(), { cache: 'no-store' });
      openWithText(data.content || '');
    } catch (error) {
      toast(error.message, true);
    }
  };

  /** Reopen the Agent-Projekt that owns one Brain agent session. */
  window.openAgentProjectSession = async function (sessionId) {
    try {
      const data = await call(API + '/by-session/' + encodeURIComponent(sessionId), { cache: 'no-store' });
      await refresh();
      current = bootstrap.projects.find(function (item) { return item.id === data.project.id; }) || data.project;
      openWithText(null);
      if (isActiveMode()) { render(); loadTurns(); }
      toast('Projekt „' + current.label + '“ geöffnet.');
    } catch (error) {
      toast(error.message, true);
    }
  };

  const originalSubmit = window.handleSubmit;
  window.handleSubmit = function (options) {
    if (isActiveMode()) { submit(); return; }
    return typeof originalSubmit === 'function' ? originalSubmit(options) : undefined;
  };
})();
