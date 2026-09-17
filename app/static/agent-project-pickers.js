/* Agent-Projekte — Auswahl-Dialoge (Ordner, Verbindung, Datei-Kontext).
 *
 * Diese Datei enthält nur Dialoge: sie liest und schreibt nichts selbst am
 * Projektzustand, sondern liefert die Auswahl über Callbacks an
 * agent-projects.js zurück.  Alle drei Dialoge sind bewusst leichte Overlays
 * ohne Template-Änderung, damit Desktop und Mobile identisch funktionieren.
 */
(function () {
  'use strict';

  const API = '/api/agent-projects';

  function esc(value) {
    return String(value === null || value === undefined ? '' : value)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, isError);
  }

  async function call(path, options) {
    const response = await window.apiFetch(path, options || {});
    const data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen.');
    return data;
  }

  // ─── Overlay-Grundgerüst ────────────────────────────────────────────
  function overlay(title, bodyHtml, footerHtml) {
    const host = document.createElement('div');
    host.className = 'fixed inset-0 z-[60] flex items-center justify-center bg-gray-950/80 px-3 py-6';
    host.innerHTML = [
      '<div class="w-full max-w-lg max-h-full flex flex-col rounded-lg border border-gray-700 bg-gray-900 shadow-2xl">',
      '  <div class="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">',
      '    <h3 class="text-sm font-semibold text-gray-100">' + esc(title) + '</h3>',
      '    <button type="button" data-close class="text-gray-500 hover:text-gray-200 text-lg leading-none px-1" aria-label="Schließen">×</button>',
      '  </div>',
      '  <div data-body class="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs text-gray-300">' + bodyHtml + '</div>',
      '  <div data-footer class="flex flex-wrap items-center justify-end gap-2 border-t border-gray-800 px-4 py-2.5">' + (footerHtml || '') + '</div>',
      '</div>',
    ].join('\n');
    document.body.appendChild(host);
    const close = function () {
      document.removeEventListener('keydown', onKey);
      host.remove();
    };
    const onKey = function (event) { if (event.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    host.addEventListener('click', function (event) { if (event.target === host) close(); });
    host.querySelector('[data-close]').addEventListener('click', close);
    return { host: host, body: host.querySelector('[data-body]'), footer: host.querySelector('[data-footer]'), close: close };
  }

  const INPUT_CLASS = 'w-full rounded bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-100 focus:outline-none focus:ring-1 focus:ring-green-500';
  const BTN_PRIMARY = 'rounded bg-green-700/80 hover:bg-green-600 px-3 py-1.5 text-xs font-semibold text-white transition';
  const BTN_GHOST = 'rounded border border-gray-700 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800 transition';
  const BTN_DANGER = 'rounded border border-red-800 px-3 py-1.5 text-xs text-red-300 hover:bg-red-950/60 transition';

  // ─── Ordner-Auswahl ─────────────────────────────────────────────────
  function openFolderPicker(options) {
    const roots = options.roots || [];
    if (!roots.length) {
      toast('Es ist keine Host-Wurzel freigegeben. Bitte external_read_roots/external_write_roots in host_worker.json setzen.', true);
      return;
    }
    let rootId = options.rootId || roots[0].id;
    let subpath = options.subpath || '';
    let mode = options.mode || 'read';

    const dialog = overlay('Projekt-Ordner wählen', [
      '<label class="block">Host-Wurzel',
      '  <select data-root class="' + INPUT_CLASS + ' mt-1"></select>',
      '</label>',
      '<div data-crumbs class="font-mono text-[11px] text-gray-400 break-all"></div>',
      '<div data-warn class="hidden rounded border border-amber-800/60 bg-amber-950/30 px-2 py-1.5 text-amber-300"></div>',
      '<div data-list class="space-y-1"></div>',
    ].join('\n'), [
      '<label class="mr-auto flex items-center gap-1.5 text-gray-400">Modus',
      '  <select data-mode class="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100">',
      '    <option value="read">Lesen</option><option value="write">Schreiben</option><option value="execute">Ausführen</option>',
      '  </select>',
      '</label>',
      '<button type="button" data-cancel class="' + BTN_GHOST + '">Abbrechen</button>',
      '<button type="button" data-choose class="' + BTN_PRIMARY + '">Diesen Ordner wählen</button>',
    ].join('\n'));

    const rootSelect = dialog.body.querySelector('[data-root]');
    const modeSelect = dialog.footer.querySelector('[data-mode]');
    const crumbs = dialog.body.querySelector('[data-crumbs]');
    const list = dialog.body.querySelector('[data-list]');
    const warn = dialog.body.querySelector('[data-warn]');
    rootSelect.innerHTML = roots.map(function (root) {
      return '<option value="' + esc(root.id) + '">' + esc(root.label) +
        (root.writable ? '' : ' (nur lesen)') + '</option>';
    }).join('');
    rootSelect.value = rootId;
    modeSelect.value = mode;

    function checkMode() {
      const root = roots.find(function (item) { return item.id === rootSelect.value; });
      const needsWrite = modeSelect.value !== 'read';
      if (root && needsWrite && !root.writable) {
        warn.textContent = '„' + root.label + '“ ist nur lesend freigegeben. Für Schreiben/Ausführen muss die Wurzel in host_worker.json unter external_write_roots stehen.';
        warn.classList.remove('hidden');
        return false;
      }
      warn.classList.add('hidden');
      return true;
    }

    async function load() {
      list.innerHTML = '<div class="text-gray-500">Lade…</div>';
      let data;
      try {
        const params = new URLSearchParams({ root_id: rootSelect.value, subpath: subpath });
        data = await call(API + '/folders?' + params.toString(), { cache: 'no-store' });
      } catch (error) {
        list.innerHTML = '<div class="text-red-400">' + esc(error.message) + '</div>';
        return;
      }
      rootId = data.root_id;
      subpath = data.subpath;
      crumbs.textContent = data.root_label + ' › ' + (subpath || '.');
      const rows = [];
      if (!data.at_root) {
        rows.push('<button type="button" data-up class="w-full text-left rounded px-2 py-1 text-gray-400 flex items-center gap-2 hover:bg-gray-800">' + (window.journlIcon?.('arrowUp', 'h-3.5 w-3.5') || '') + '<span>Übergeordneter Ordner</span></button>');
      }
      data.folders.forEach(function (folder) {
        rows.push('<button type="button" data-into="' + esc(folder.subpath) + '" class="w-full text-left rounded px-2 py-1 text-gray-200 flex items-center gap-2 hover:bg-gray-800">' + (window.journlIcon?.('folder', 'h-3.5 w-3.5') || '') + '<span>' + esc(folder.name) + '</span></button>');
      });
      if (!data.folders.length) rows.push('<div class="text-gray-500">Keine Unterordner.</div>');
      list.innerHTML = rows.join('\n');
      const up = list.querySelector('[data-up]');
      if (up) up.addEventListener('click', function () { subpath = data.parent; load(); });
      list.querySelectorAll('[data-into]').forEach(function (button) {
        button.addEventListener('click', function () { subpath = button.dataset.into; load(); });
      });
      checkMode();
    }

    rootSelect.addEventListener('change', function () { subpath = ''; load(); });
    modeSelect.addEventListener('change', checkMode);
    dialog.footer.querySelector('[data-cancel]').addEventListener('click', dialog.close);
    dialog.footer.querySelector('[data-choose]').addEventListener('click', function () {
      if (!checkMode()) return;
      const root = roots.find(function (item) { return item.id === rootSelect.value; });
      dialog.close();
      options.onPick({
        root_id: rootSelect.value, subpath: subpath, mode: modeSelect.value,
        root_label: root ? root.label : '', root_writable: Boolean(root && root.writable),
      });
    });
    load();
  }

  // ─── Verbindung ─────────────────────────────────────────────────────
  const KIND_LABELS = {
    lmstudio: 'LM Studio', ollama: 'Ollama', llamacpp: 'llama.cpp', openai: 'OpenAI-kompatibel',
  };
  const KIND_HINTS = {
    lmstudio: 'Modelle werden über /v1/models gelesen. Standard-Port 1234.',
    ollama: 'Modelle werden über /api/tags gelesen. Standard-Port 11434.',
    llamacpp: 'Modelle werden über /v1/models gelesen. Standard-Port 8080.',
    openai: 'Beliebiger OpenAI-kompatibler Server im lokalen Netz (/v1/models).',
  };

  function openConnectionEditor(options) {
    const existing = options.connection || null;
    const dialog = overlay(existing ? 'Verbindung bearbeiten' : 'Neue Verbindung', [
      '<label class="block">Name',
      '  <input data-label type="text" maxlength="80" class="' + INPUT_CLASS + ' mt-1" placeholder="LM Studio Werkstatt" value="' + esc(existing && existing.label) + '">',
      '</label>',
      '<div class="grid grid-cols-2 gap-2">',
      '  <label class="block">Art',
      '    <select data-kind class="' + INPUT_CLASS + ' mt-1">',
      Object.keys(KIND_LABELS).map(function (kind) {
        return '<option value="' + kind + '">' + esc(KIND_LABELS[kind]) + '</option>';
      }).join(''),
      '    </select>',
      '  </label>',
      '  <label class="block">Agent',
      '    <select data-runner class="' + INPUT_CLASS + ' mt-1">',
      '      <option value="opencode">opencode run</option><option value="pi">pi</option><option value="claude">Claude Code</option>',
      '    </select>',
      '  </label>',
      '</div>',
      '<label class="block">Basis-URL',
      '  <input data-base type="url" maxlength="200" class="' + INPUT_CLASS + ' mt-1" placeholder="http://192.168.1.50:1234" value="' + esc(existing && existing.base_url) + '">',
      '</label>',
      '<p data-hint class="text-[11px] text-gray-500"></p>',
      '<details class="rounded border border-gray-800 px-2 py-1.5">',
      '  <summary class="cursor-pointer text-gray-400">Erweitert</summary>',
      '  <div class="mt-2 grid grid-cols-2 gap-2">',
      '    <label class="block">Provider-Kennung',
      '      <input data-provider type="text" maxlength="40" class="' + INPUT_CLASS + ' mt-1" placeholder="lmstudio" value="' + esc(existing && existing.provider_id) + '">',
      '    </label>',
      '    <label class="block">API-Key aus Host-Variable',
      '      <input data-keyenv type="text" maxlength="64" class="' + INPUT_CLASS + ' mt-1" placeholder="(leer = keiner)" value="' + esc(existing && existing.api_key_env) + '">',
      '    </label>',
      '  </div>',
      '  <p class="mt-2 text-[11px] text-gray-500">Bei Agent <code>pi</code> muss dieser Provider zusätzlich in der models.json des Host-Benutzers stehen. Bei <code>opencode run</code> wird die Verbindung pro Auftrag injiziert. <code>Claude Code</code> nutzt die Anmeldung des Host-Benutzers und ignoriert diese Verbindung.</p>',
      '</details>',
      '<div data-models class="text-[11px] text-gray-500"></div>',
    ].join('\n'), [
      existing ? '<button type="button" data-delete class="' + BTN_DANGER + ' mr-auto">Löschen</button>' : '',
      '<button type="button" data-test class="' + BTN_GHOST + '">Modelle lesen</button>',
      '<button type="button" data-save class="' + BTN_PRIMARY + '">Speichern</button>',
    ].join('\n'));

    const fields = {
      label: dialog.body.querySelector('[data-label]'),
      kind: dialog.body.querySelector('[data-kind]'),
      runner: dialog.body.querySelector('[data-runner]'),
      base: dialog.body.querySelector('[data-base]'),
      provider: dialog.body.querySelector('[data-provider]'),
      keyenv: dialog.body.querySelector('[data-keyenv]'),
    };
    const hint = dialog.body.querySelector('[data-hint]');
    const modelsBox = dialog.body.querySelector('[data-models]');
    fields.kind.value = (existing && existing.kind) || 'lmstudio';
    fields.runner.value = (existing && existing.runner) || 'opencode';

    function syncHint() {
      hint.textContent = KIND_HINTS[fields.kind.value] || '';
      if (!fields.provider.value.trim()) {
        fields.provider.placeholder = fields.kind.value === 'openai' ? 'openai-compatible' : fields.kind.value;
      }
    }
    fields.kind.addEventListener('change', syncHint);
    syncHint();

    function payload() {
      return {
        label: fields.label.value, kind: fields.kind.value, runner: fields.runner.value,
        base_url: fields.base.value, provider_id: fields.provider.value.trim() || undefined,
        api_key_env: fields.keyenv.value.trim(),
      };
    }

    dialog.footer.querySelector('[data-test]').addEventListener('click', async function () {
      modelsBox.textContent = 'Frage Endpunkt ab…';
      try {
        const params = new URLSearchParams({ kind: fields.kind.value, base_url: fields.base.value });
        const data = await call(API + '/models?' + params.toString(), { cache: 'no-store', timeoutMs: 20000 });
        modelsBox.innerHTML = '<span class="text-green-400">' + data.models.length + ' Modell(e):</span> ' +
          esc(data.models.slice(0, 12).join(', ')) + (data.models.length > 12 ? ' …' : '');
      } catch (error) {
        modelsBox.innerHTML = '<span class="text-red-400">' + esc(error.message) + '</span>';
      }
    });

    dialog.footer.querySelector('[data-save]').addEventListener('click', async function () {
      try {
        const data = await call(API + '/connections', {
          method: 'POST',
          body: JSON.stringify({ action: 'save', id: existing ? existing.id : undefined, connection: payload() }),
        });
        dialog.close();
        toast('Verbindung gespeichert');
        options.onSaved(data.connection);
      } catch (error) {
        toast(error.message, true);
      }
    });

    const remove = dialog.footer.querySelector('[data-delete]');
    if (remove) {
      remove.addEventListener('click', async function () {
        try {
          await call(API + '/connections', {
            method: 'POST', body: JSON.stringify({ action: 'delete', id: existing.id }),
          });
          dialog.close();
          toast('Verbindung gelöscht.');
          options.onSaved(null);
        } catch (error) {
          toast(error.message, true);
        }
      });
    }
  }

  // ─── Datei-Kontext über Datum ───────────────────────────────────────
  function openFilePicker(options) {
    const selected = new Set(options.selected || []);
    const dialog = overlay('Dateien in den Kontext legen', [
      '<label class="block">Tag',
      '  <input data-date type="date" class="' + INPUT_CLASS + ' mt-1" max="' + esc(options.today) + '" value="' + esc(options.date || options.today) + '">',
      '</label>',
      '<p class="text-[11px] text-gray-500">Zeigt das Journal dieses Tages sowie alle Notizen, Projekte und Agent-Sessions, die an diesem Tag zuletzt geändert wurden.</p>',
      '<div data-list class="space-y-1"></div>',
      '<div data-chosen class="border-t border-gray-800 pt-2"></div>',
    ].join('\n'), [
      '<button type="button" data-clear class="' + BTN_GHOST + ' mr-auto">Auswahl leeren</button>',
      '<button type="button" data-cancel class="' + BTN_GHOST + '">Abbrechen</button>',
      '<button type="button" data-apply class="' + BTN_PRIMARY + '">Übernehmen</button>',
    ].join('\n'));

    const dateInput = dialog.body.querySelector('[data-date]');
    const list = dialog.body.querySelector('[data-list]');
    const chosen = dialog.body.querySelector('[data-chosen]');

    function renderChosen() {
      const values = Array.from(selected);
      if (!values.length) {
        chosen.innerHTML = '<span class="text-gray-500">Keine Datei ausgewählt.</span>';
        return;
      }
      chosen.innerHTML = '<div class="mb-1 text-gray-400">Ausgewählt (' + values.length + '):</div>' +
        values.map(function (path) {
          return '<div class="flex items-center gap-2"><button type="button" data-drop="' + esc(path) +
            '" class="text-gray-500 hover:text-red-400" aria-label="Entfernen">×</button>' +
            '<span class="font-mono text-[11px] break-all text-gray-300">' + esc(path) + '</span></div>';
        }).join('');
      chosen.querySelectorAll('[data-drop]').forEach(function (button) {
        button.addEventListener('click', function () {
          selected.delete(button.dataset.drop);
          renderChosen();
          syncChecks();
        });
      });
    }

    function syncChecks() {
      list.querySelectorAll('[data-file]').forEach(function (box) {
        box.checked = selected.has(box.value);
      });
    }

    async function load() {
      list.innerHTML = '<div class="text-gray-500">Lade…</div>';
      let data;
      try {
        data = await call(API + '/files?date=' + encodeURIComponent(dateInput.value), { cache: 'no-store' });
      } catch (error) {
        list.innerHTML = '<div class="text-red-400">' + esc(error.message) + '</div>';
        return;
      }
      if (!data.files.length) {
        list.innerHTML = '<div class="text-gray-500">An diesem Tag gibt es keine passenden Dateien.</div>';
        return;
      }
      list.innerHTML = data.files.map(function (file) {
        const kb = Math.max(1, Math.round(file.bytes / 1024));
        return '<label class="flex items-start gap-2 rounded px-1 py-0.5 hover:bg-gray-800/60' +
          (file.oversized ? ' opacity-50' : '') + '">' +
          '<input type="checkbox" data-file value="' + esc(file.path) + '" class="mt-0.5 accent-green-500"' +
          (file.oversized ? ' disabled' : '') + '>' +
          '<span class="min-w-0"><span class="text-gray-200">' + esc(file.label) + '</span> ' +
          '<span class="text-gray-500">· ' + esc(file.kind) + ' · ' + kb + ' kB' +
          (file.oversized ? ' · zu groß' : '') + '</span><br>' +
          '<span class="font-mono text-[10px] break-all text-gray-500">' + esc(file.path) + '</span></span></label>';
      }).join('');
      list.querySelectorAll('[data-file]').forEach(function (box) {
        box.addEventListener('change', function () {
          if (box.checked) selected.add(box.value); else selected.delete(box.value);
          renderChosen();
        });
      });
      syncChecks();
    }

    dateInput.addEventListener('change', load);
    dialog.footer.querySelector('[data-clear]').addEventListener('click', function () {
      selected.clear();
      renderChosen();
      syncChecks();
    });
    dialog.footer.querySelector('[data-cancel]').addEventListener('click', dialog.close);
    dialog.footer.querySelector('[data-apply]').addEventListener('click', function () {
      dialog.close();
      options.onPick({ files: Array.from(selected), date: dateInput.value });
    });
    renderChosen();
    load();
  }

  window.agentProjectPickers = {
    openFolderPicker: openFolderPicker,
    openConnectionEditor: openConnectionEditor,
    openFilePicker: openFilePicker,
    overlay: overlay,
    kindLabel: function (kind) { return KIND_LABELS[kind] || kind || ''; },
    classes: { input: INPUT_CLASS, primary: BTN_PRIMARY, ghost: BTN_GHOST, danger: BTN_DANGER },
  };
})();
