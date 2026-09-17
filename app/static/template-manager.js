/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Burger-Menue -> "Meine Templates": Anzeige UND Verwaltung der
 * Journal-Vorlagen. Vertrag mit desktop-app-resize.js / mobile-app-features.js:
 * deren loadSettingsTemplates() ruft nur noch window.journlTemplates.render().
 *
 * Quelle ist GET/PUT /api/settings/templates (rohe config.json), nicht
 * /api/config: dessen Antwort ist env-injiziert und darf nie zurueckgeschrieben
 * werden. Geaendert wird immer die komplette Liste in einem Zug; bis zum
 * Speichern lebt alles in state.draft.
 *
 * Vorlagen sind global. assigned_users steuert nur die Sichtbarkeit, deshalb
 * ist das Bearbeiten Administratoren vorbehalten (Serverseite entscheidet).
 */
(function () {
  const FIELD_TYPE_LABELS = {
    text: 'Text (einzeilig)',
    textarea: 'Text (mehrzeilig)',
    number: 'Zahl',
    date: 'Datum',
    select: 'Auswahl',
    user: 'Benutzer',
  };

  const state = {
    loading: false,
    loaded: false,
    canEdit: false,
    protectedIds: new Set(),
    codeFormattedIds: new Set(),
    users: [],
    draft: [],
    baseline: '',
    open: null,
    error: '',
  };

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, isError);
  }

  function container() {
    return document.getElementById('settings-templates-list');
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function dirty() {
    return JSON.stringify(state.draft) !== state.baseline;
  }

  function isProtected(template) {
    return state.protectedIds.has(template.id);
  }

  // ─── Laden und Speichern ────────────────────────────────────────────

  async function load(force) {
    if (state.loading) return;
    // Ungespeicherte Arbeit ueberlebt ein erneutes Oeffnen der Einstellungen.
    if (state.loaded && !force && dirty()) { draw(); return; }
    state.loading = true;
    state.error = '';
    draw();
    try {
      const response = await window.apiFetch('/api/settings/templates');
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Vorlagen konnten nicht geladen werden.');
      state.canEdit = data.can_edit === true;
      state.protectedIds = new Set(data.protected_ids || []);
      state.codeFormattedIds = new Set(data.code_formatted_ids || []);
      state.users = data.users || [];
      state.draft = (data.templates || []).map(normalize);
      state.baseline = JSON.stringify(state.draft);
      state.loaded = true;
    } catch (error) {
      state.error = error.message || 'Vorlagen konnten nicht geladen werden.';
    } finally {
      state.loading = false;
      draw();
    }
  }

  function normalize(template) {
    const copy = clone(template || {});
    copy.id = copy.id || '';
    copy.label = copy.label || '';
    copy.type = copy.type === 'form' ? 'form' : 'simple';
    copy.write_to_journal = copy.write_to_journal !== false;
    copy.assigned_users = Array.isArray(copy.assigned_users) ? copy.assigned_users.slice() : [];
    copy.target_file = copy.target_file || null;
    if (copy.type === 'form') copy.fields = Array.isArray(copy.fields) ? copy.fields : [];
    else delete copy.fields;
    return copy;
  }

  async function save() {
    if (!state.canEdit) return;
    const button = container()?.querySelector('[data-act="save"]');
    if (button) { button.disabled = true; button.textContent = 'Speichert…'; }
    try {
      const response = await window.apiFetch('/api/settings/templates', {
        method: 'PUT',
        body: JSON.stringify({ templates: state.draft }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen.');
      state.draft = (data.templates || []).map(normalize);
      state.baseline = JSON.stringify(state.draft);
      state.error = '';
      applyToRunningApp(state.draft);
      toast('Vorlagen gespeichert');
    } catch (error) {
      state.error = error.message || 'Speichern fehlgeschlagen.';
      toast(state.error, true);
    } finally {
      draw();
    }
  }

  /* Die Auswahlliste im Schreiben-Tab wird beim Laden einmal aufgebaut. Nach
   * dem Speichern wird sie hier nachgezogen, damit kein Neuladen noetig ist.
   * window.config ist dasselbe Objekt wie das der App-Shell, deshalb wird das
   * templates-Array an Ort und Stelle ersetzt statt neu zugewiesen. */
  function applyToRunningApp(templates) {
    const live = window.config && Array.isArray(window.config.templates)
      ? window.config.templates : null;
    if (!live) return;
    live.splice(0, live.length, ...clone(templates));
    const select = document.getElementById('template-select');
    if (!select) return;
    const special = Array.from(select.options).filter(option => option.value.startsWith('__'));
    const previous = select.value;
    const me = window.config.current_user ? window.config.current_user.id : null;
    select.replaceChildren();
    for (const template of live) {
      const assigned = template.assigned_users || [];
      if (assigned.length && !(me && assigned.includes(me))) continue;
      const option = document.createElement('option');
      option.value = template.id;
      option.textContent = template.label;
      select.appendChild(option);
    }
    special.forEach(option => select.appendChild(option));
    const stillThere = Array.from(select.options).some(option => option.value === previous);
    select.value = stillThere ? previous : (select.options[0] ? select.options[0].value : '');
    // Sondermodi (KI, Agent) nicht neu betreten: ihr change-Handler hat
    // Nebenwirkungen. Fuer echte Vorlagen muss der Wechsel dagegen laufen,
    // damit ein geaendertes Formular sofort neu gezeichnet wird.
    if (select.value && !select.value.startsWith('__')) {
      select.dispatchEvent(new Event('change'));
    }
  }

  // ─── Mutationen auf dem Entwurf ─────────────────────────────────────

  function freshId(base) {
    const used = new Set(state.draft.map(template => template.id));
    let candidate = base;
    let counter = 2;
    while (!candidate || used.has(candidate)) candidate = `${base}-${counter++}`;
    return candidate;
  }

  function addTemplate() {
    const template = normalize({
      id: freshId('neue-vorlage'),
      label: 'Neue Vorlage',
      type: 'simple',
      write_to_journal: true,
      assigned_users: [],
      target_file: null,
    });
    template._new = true;
    state.draft.push(template);
    state.open = template.id;
    draw();
  }

  function duplicateTemplate(index) {
    const source = state.draft[index];
    if (!source) return;
    const copy = normalize(source);
    delete copy._new;
    copy.id = freshId(`${source.id}-kopie`);
    copy.label = `${source.label} (Kopie)`;
    copy._new = true;
    state.draft.splice(index + 1, 0, copy);
    state.open = copy.id;
    draw();
  }

  function removeTemplate(index) {
    const template = state.draft[index];
    if (!template) return;
    if (isProtected(template)) {
      toast('Systemvorlagen können nicht gelöscht werden.', true);
      return;
    }
    const warning = template.target_file
      ? `\n\nAchtung: Diese Vorlage schreibt in ${template.target_file}. Bestehende Familien-Dokumente verlieren ihre Zuordnung.`
      : '';
    if (!window.confirm(`„${template.label || template.id}“ löschen?${warning}`)) return;
    state.draft.splice(index, 1);
    if (state.open === template.id) state.open = null;
    draw();
  }

  function moveTemplate(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= state.draft.length) return;
    const [template] = state.draft.splice(index, 1);
    state.draft.splice(target, 0, template);
    draw();
  }

  function addField(index) {
    const template = state.draft[index];
    if (!template) return;
    const used = new Set((template.fields || []).map(field => field.id));
    let id = 'feld';
    let counter = 2;
    while (used.has(id)) id = `feld${counter++}`;
    template.fields = template.fields || [];
    template.fields.push({ id, label: 'Neues Feld', type: 'text' });
    draw();
  }

  function moveField(index, fieldIndex, delta) {
    const fields = state.draft[index] && state.draft[index].fields;
    if (!fields) return;
    const target = fieldIndex + delta;
    if (target < 0 || target >= fields.length) return;
    const [field] = fields.splice(fieldIndex, 1);
    fields.splice(target, 0, field);
    draw();
  }

  function removeField(index, fieldIndex) {
    const fields = state.draft[index] && state.draft[index].fields;
    if (!fields) return;
    fields.splice(fieldIndex, 1);
    draw();
  }

  // ─── Darstellung ────────────────────────────────────────────────────

  function badge(template) {
    const assigned = template.assigned_users || [];
    if (!assigned.length) return { text: 'Gemeinsam', color: 'text-blue-400' };
    const names = assigned.map(id => {
      const user = state.users.find(candidate => candidate.id === id);
      return user ? user.username : id;
    });
    return { text: 'Zugewiesen: ' + names.join(', '), color: 'text-amber-400' };
  }

  function readOnlyRow(template) {
    const mark = badge(template);
    return `<div class="flex items-center justify-between min-w-0 max-w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2">
      <div class="min-w-0"><div class="text-sm text-gray-200 break-words">${escapeHtml(template.label || template.id)}</div>
      <div class="text-xs ${mark.color} break-words">${escapeHtml(mark.text)}</div></div></div>`;
  }

  function optionRows(options) {
    return (options || []).map(option => {
      const value = option && option.value ? option.value : '';
      const label = option && option.label ? option.label : '';
      return label && label !== value ? `${value}|${label}` : value;
    }).join('\n');
  }

  function fieldEditor(field, index, fieldIndex) {
    const scope = `data-i="${index}" data-fi="${fieldIndex}"`;
    const types = Object.keys(FIELD_TYPE_LABELS).map(type =>
      `<option value="${type}"${field.type === type ? ' selected' : ''}>${escapeHtml(FIELD_TYPE_LABELS[type])}</option>`
    ).join('');
    let extra = '';
    if (field.type === 'number') {
      extra = `<div class="flex gap-2">
        <label class="flex-1 text-xs text-gray-500">Minimum<input type="number" ${scope} data-key="min" value="${escapeAttr(field.min == null ? '' : field.min)}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" /></label>
        <label class="flex-1 text-xs text-gray-500">Maximum<input type="number" ${scope} data-key="max" value="${escapeAttr(field.max == null ? '' : field.max)}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" /></label>
      </div>`;
    } else if (field.type === 'select') {
      extra = `<label class="block text-xs text-gray-500">Auswahlwerte — eine Zeile je Eintrag, optional <code>wert|Beschriftung</code>
        <textarea ${scope} data-key="options" rows="3" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 font-mono text-xs text-gray-100">${escapeHtml(optionRows(field.options))}</textarea></label>`;
    }
    return `<div class="rounded-lg border border-gray-700 bg-gray-900/60 p-2 space-y-2">
      <div class="flex items-center gap-1">
        <input type="text" ${scope} data-key="label" value="${escapeAttr(field.label || '')}" placeholder="Beschriftung" class="min-w-0 flex-1 rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" />
        <button type="button" data-act="field-up" ${scope} title="Nach oben" class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">↑</button>
        <button type="button" data-act="field-down" ${scope} title="Nach unten" class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">↓</button>
        <button type="button" data-act="field-remove" ${scope} title="Feld löschen" class="rounded bg-red-900/70 px-2 py-1 text-xs text-red-200 hover:bg-red-800">${window.journlIcon?.('trash', 'h-3.5 w-3.5') || ''}</button>
      </div>
      <div class="flex flex-wrap gap-2">
        <label class="min-w-0 flex-1 text-xs text-gray-500">Feld-ID (im Journal und in der Familie)
          <input type="text" ${scope} data-key="id" value="${escapeAttr(field.id || '')}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 font-mono text-xs text-gray-100" /></label>
        <label class="min-w-0 flex-1 text-xs text-gray-500">Typ
          <select ${scope} data-key="type" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100">${types}</select></label>
      </div>
      <div class="flex flex-wrap items-center gap-3">
        <label class="flex items-center gap-1 text-xs text-gray-400"><input type="checkbox" ${scope} data-key="required" class="accent-green-500"${field.required ? ' checked' : ''} /> Pflichtfeld</label>
        <label class="min-w-0 flex-1 text-xs text-gray-500">Platzhalter
          <input type="text" ${scope} data-key="placeholder" value="${escapeAttr(field.placeholder || '')}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" /></label>
      </div>
      ${extra}
    </div>`;
  }

  function userChecks(template, index) {
    if (!state.users.length) return '<p class="text-xs text-gray-500">Keine Benutzer gefunden.</p>';
    return state.users.map(user => `<label class="flex items-center gap-2 text-xs text-gray-300">
      <input type="checkbox" data-i="${index}" data-key="assigned" data-user="${escapeAttr(user.id)}" class="accent-green-500"${(template.assigned_users || []).includes(user.id) ? ' checked' : ''} />
      ${escapeHtml(user.username || user.id)}</label>`).join('');
  }

  function templateEditor(template, index) {
    const scope = `data-i="${index}"`;
    const locked = isProtected(template);
    const idLocked = locked || !template._new;
    const notes = [];
    if (locked) notes.push('Systemvorlage: ID, Typ und Ziel-Datei sind im Code verdrahtet und bleiben fest.');
    else if (idLocked) notes.push('Die ID bleibt nach dem Anlegen fest — Journaleinträge und Familien-Dokumente verweisen darauf. Für eine neue ID: duplizieren.');
    if (state.codeFormattedIds.has(template.id)) notes.push('Die Journalform dieser Vorlage steht fest im Code; eine Überschrift hier bleibt wirkungslos.');

    const headingRow = template.type === 'simple' && !state.codeFormattedIds.has(template.id)
      ? `<label class="block text-xs text-gray-500">Überschrift im Journal (leer = ohne Überschrift)
          <input type="text" ${scope} data-key="heading" value="${escapeAttr(template.heading || '')}" placeholder="z. B. Erkenntnis" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" /></label>`
      : '';

    const hashtagRow = template.type === 'simple'
      ? `<label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" ${scope} data-key="hashtag_template" class="accent-green-500"${template.hashtag_template ? ' checked' : ''} /> Hashtag-Vorlage (Schnellnotiz-Form, feste Hashtags)</label>
         ${template.hashtag_template ? `<label class="block text-xs text-gray-500">Hashtags, durch Komma getrennt
           <input type="text" ${scope} data-key="hashtags" value="${escapeAttr((template.hashtags || []).join(', '))}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 font-mono text-xs text-gray-100" /></label>` : ''}
         <label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" ${scope} data-key="project_selector" class="accent-green-500"${template.project_selector ? ' checked' : ''} /> Auswahl eines persönlichen Projekts anbieten</label>`
      : '';

    const fields = template.type === 'form'
      ? `<div class="space-y-2">
          <div class="flex items-center justify-between"><span class="text-xs font-semibold text-gray-400">Felder</span>
          <button type="button" data-act="field-add" ${scope} class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">+ Feld</button></div>
          ${(template.fields || []).map((field, fieldIndex) => fieldEditor(field, index, fieldIndex)).join('')}
          ${(template.fields || []).length ? '' : '<p class="text-xs text-gray-500">Ein Formular braucht mindestens ein Feld.</p>'}
        </div>`
      : '';

    return `<div class="border-t border-gray-700 px-3 py-3 space-y-3">
      ${notes.map(note => `<p class="text-xs text-gray-500">${escapeHtml(note)}</p>`).join('')}
      <div class="flex flex-wrap gap-2">
        <label class="min-w-0 flex-1 text-xs text-gray-500">Beschriftung (Emoji erlaubt)
          <input type="text" ${scope} data-key="label" value="${escapeAttr(template.label || '')}" class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100" /></label>
        <label class="min-w-0 flex-1 text-xs text-gray-500">ID
          <input type="text" ${scope} data-key="id" value="${escapeAttr(template.id || '')}"${idLocked ? ' disabled' : ''} class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 font-mono text-xs text-gray-100 disabled:opacity-60" /></label>
      </div>
      <div class="flex flex-wrap gap-2">
        <label class="min-w-0 flex-1 text-xs text-gray-500">Art
          <select ${scope} data-key="type"${locked ? ' disabled' : ''} class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 text-sm text-gray-100 disabled:opacity-60">
            <option value="simple"${template.type === 'simple' ? ' selected' : ''}>Einfach (ein Textfeld)</option>
            <option value="form"${template.type === 'form' ? ' selected' : ''}>Formular (mehrere Felder)</option>
          </select></label>
        <label class="min-w-0 flex-1 text-xs text-gray-500">Ziel-Datei in der Familie (leer = keine)
          <input type="text" ${scope} data-key="target_file" value="${escapeAttr(template.target_file || '')}" placeholder="z. B. haushalt.md"${locked ? ' disabled' : ''} class="mt-1 w-full rounded bg-gray-900 border border-gray-700 px-2 py-1 font-mono text-xs text-gray-100 disabled:opacity-60" /></label>
      </div>
      ${headingRow}
      <div class="space-y-1">
        <label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" ${scope} data-key="write_to_journal" class="accent-green-500"${template.write_to_journal ? ' checked' : ''} /> Eintrag ins Journal schreiben</label>
        <label class="flex items-center gap-2 text-xs text-gray-300"><input type="checkbox" ${scope} data-key="keep_template_after_submit" class="accent-green-500"${template.keep_template_after_submit ? ' checked' : ''} /> Vorlage nach dem Senden ausgewählt lassen</label>
        ${hashtagRow}
      </div>
      <div>
        <p class="mb-1 text-xs font-semibold text-gray-400">Sichtbar für (keine Auswahl = alle)</p>
        <div class="flex flex-wrap gap-x-4 gap-y-1">${userChecks(template, index)}</div>
      </div>
      ${fields}
    </div>`;
  }

  function editableRow(template, index) {
    const mark = badge(template);
    const open = state.open === template.id;
    const tags = [];
    if (isProtected(template)) tags.push('Systemvorlage');
    if (!template.write_to_journal) tags.push('kein Journaleintrag');
    if (template.target_file) tags.push(template.target_file);
    return `<div class="min-w-0 max-w-full rounded-lg border border-gray-700 bg-gray-800">
      <div class="flex items-center justify-between gap-2 px-3 py-2">
        <button type="button" data-act="toggle" data-i="${index}" class="min-w-0 flex-1 text-left">
          <div class="text-sm text-gray-200 break-words">${open ? '▾' : '▸'} ${escapeHtml(template.label || template.id)}</div>
          <div class="text-xs ${mark.color} break-words">${escapeHtml(mark.text)}${tags.length ? ' · ' + escapeHtml(tags.join(' · ')) : ''}</div>
        </button>
        <div class="flex shrink-0 items-center gap-1">
          <button type="button" data-act="up" data-i="${index}" title="Nach oben" class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">↑</button>
          <button type="button" data-act="down" data-i="${index}" title="Nach unten" class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">↓</button>
          <button type="button" data-act="duplicate" data-i="${index}" title="Duplizieren" class="rounded bg-gray-700 px-2 py-1 text-xs text-gray-200 hover:bg-gray-600">⧉</button>
          <button type="button" data-act="remove" data-i="${index}" title="Löschen" class="rounded bg-red-900/70 px-2 py-1 text-xs text-red-200 hover:bg-red-800 disabled:opacity-40"${isProtected(template) ? ' disabled' : ''}>${window.journlIcon?.('trash', 'h-3.5 w-3.5') || ''}</button>
        </div>
      </div>
      ${open ? templateEditor(template, index) : ''}
    </div>`;
  }

  function draw() {
    const list = container();
    if (!list) return;
    if (state.loading && !state.loaded) {
      list.innerHTML = '<p class="text-gray-500">Lade…</p>';
      return;
    }
    if (!state.loaded) {
      list.innerHTML = `<p class="text-red-400 text-xs">${escapeHtml(state.error || 'Vorlagen konnten nicht geladen werden.')}</p>`;
      return;
    }
    if (!state.canEdit) {
      list.innerHTML = state.draft.length
        ? state.draft.map(readOnlyRow).join('')
        : '<p class="text-gray-500">Keine Templates.</p>';
      return;
    }
    const changed = dirty();
    const head = `<div class="space-y-2">
      <p class="text-xs text-gray-500">Vorlagen gelten für alle Benutzer; die Zuweisung steuert nur, wer sie im Schreiben-Tab sieht. Die Reihenfolge hier ist die Reihenfolge der Auswahlliste.</p>
      ${state.error ? `<p class="text-xs text-red-400">${escapeHtml(state.error)}</p>` : ''}
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" data-act="add" class="rounded bg-gray-700 px-3 py-1.5 text-sm font-semibold text-white hover:bg-gray-600">+ Neue Vorlage</button>
        <button type="button" data-act="save" class="rounded px-3 py-1.5 text-sm font-semibold text-white transition ${changed ? 'bg-green-700 hover:bg-green-600' : 'bg-gray-700 opacity-50 cursor-not-allowed'}"${changed ? '' : ' disabled'}>Speichern</button>
        <button type="button" data-act="reset" class="rounded bg-gray-700 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-600 ${changed ? '' : 'hidden'}">Verwerfen</button>
        ${changed ? '<span class="text-xs text-amber-400">Ungespeicherte Änderungen</span>' : ''}
      </div>
    </div>`;
    list.innerHTML = head + '<div class="mt-2 space-y-2">'
      + state.draft.map(editableRow).join('') + '</div>';
  }

  // ─── Eingaben ───────────────────────────────────────────────────────

  function parseNumber(value) {
    if (String(value).trim() === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function parseOptions(text) {
    return String(text).split('\n').map(line => line.trim()).filter(Boolean).map(line => {
      const separator = line.indexOf('|');
      if (separator < 0) return { value: line, label: line };
      return { value: line.slice(0, separator).trim(), label: line.slice(separator + 1).trim() };
    });
  }

  function applyTemplateInput(template, key, input) {
    let redraw = false;
    if (key === 'assigned') {
      const id = input.dataset.user;
      const assigned = template.assigned_users.filter(candidate => candidate !== id);
      if (input.checked) assigned.push(id);
      template.assigned_users = assigned;
      redraw = true;
    } else if (key === 'type') {
      template.type = input.value === 'form' ? 'form' : 'simple';
      if (template.type === 'form') {
        if (!Array.isArray(template.fields) || !template.fields.length) {
          template.fields = [{ id: 'feld', label: 'Neues Feld', type: 'text' }];
        }
        delete template.heading;
        delete template.hashtag_template;
        delete template.hashtags;
        delete template.project_selector;
      } else {
        delete template.fields;
      }
      redraw = true;
    } else if (key === 'hashtag_template') {
      if (input.checked) {
        template.hashtag_template = true;
        template.hashtags = template.hashtags || [];
      } else {
        delete template.hashtag_template;
        delete template.hashtags;
      }
      redraw = true;
    } else if (key === 'hashtags') {
      template.hashtags = input.value.split(',').map(tag => tag.trim()).filter(Boolean);
    } else if (input.type === 'checkbox') {
      if (key === 'write_to_journal') template.write_to_journal = input.checked;
      else if (input.checked) template[key] = true;
      else delete template[key];
      redraw = true;  // Die Kopfzeile der Karte nennt diese Merkmale.
    } else if (key === 'target_file') {
      template.target_file = input.value.trim() || null;
    } else if (key === 'heading') {
      if (input.value.trim()) template.heading = input.value;
      else delete template.heading;
    } else if (key === 'id') {
      // Die aufgeklappte Karte wird ueber die ID verfolgt.
      if (state.open === template.id) state.open = input.value;
      template.id = input.value;
      syncRowHeader(template, input);
    } else {
      template[key] = input.value;
      if (key === 'label') syncRowHeader(template, input);
    }
    return redraw;
  }

  function applyFieldInput(field, key, input) {
    if (key === 'type') {
      field.type = input.value;
      delete field.min;
      delete field.max;
      if (field.type === 'select') field.options = field.options || [];
      else delete field.options;
      return true;
    }
    if (key === 'required') {
      if (input.checked) field.required = true; else delete field.required;
      return false;
    }
    if (key === 'min' || key === 'max') {
      const parsed = parseNumber(input.value);
      if (parsed === null) delete field[key]; else field[key] = parsed;
      return false;
    }
    if (key === 'options') {
      field.options = parseOptions(input.value);
      return false;
    }
    if (key === 'placeholder' && !input.value.trim()) {
      delete field.placeholder;
      return false;
    }
    field[key] = input.value;
    return false;
  }

  function onInput(event) {
    const input = event.target.closest('[data-key]');
    if (!input || !container()?.contains(input)) return;
    const index = Number(input.dataset.i);
    const template = state.draft[index];
    if (!template) return;
    const fieldIndex = input.dataset.fi;
    const key = input.dataset.key;
    let redraw;
    if (fieldIndex === undefined) {
      redraw = applyTemplateInput(template, key, input);
    } else {
      const field = (template.fields || [])[Number(fieldIndex)];
      if (!field) return;
      redraw = applyFieldInput(field, key, input);
    }
    if (redraw) draw();
    else updateSaveState();
  }

  /* Ein Redraw bei jedem Tastendruck wuerde den Fokus verlieren. Beschriftung
   * und ID stehen aber auch in der Kopfzeile der Karte — die wird hier direkt
   * nachgezogen, ohne die Karte neu zu zeichnen. */
  function syncRowHeader(template, input) {
    const card = input.closest('.rounded-lg');
    const header = card ? card.querySelector('[data-act="toggle"] > div') : null;
    if (header) header.textContent = `▾ ${template.label || template.id}`;
  }

  /* Ein Redraw bei jedem Tastendruck wuerde den Fokus verlieren, deshalb wird
   * bei reinen Texteingaben nur der Speicherknopf nachgezogen. */
  function updateSaveState() {
    const list = container();
    if (!list) return;
    const button = list.querySelector('[data-act="save"]');
    const reset = list.querySelector('[data-act="reset"]');
    if (!button) return;
    const changed = dirty();
    button.disabled = !changed;
    button.className = 'rounded px-3 py-1.5 text-sm font-semibold text-white transition '
      + (changed ? 'bg-green-700 hover:bg-green-600' : 'bg-gray-700 opacity-50 cursor-not-allowed');
    if (reset) reset.classList.toggle('hidden', !changed);
  }

  function onClick(event) {
    const button = event.target.closest('[data-act]');
    const list = container();
    if (!button || !list || !list.contains(button)) return;
    const action = button.dataset.act;
    const index = Number(button.dataset.i);
    const fieldIndex = Number(button.dataset.fi);
    if (action === 'add') return addTemplate();
    if (action === 'save') return save();
    if (action === 'reset') {
      state.draft = JSON.parse(state.baseline);
      state.error = '';
      return draw();
    }
    if (action === 'toggle') {
      const template = state.draft[index];
      state.open = template && state.open !== template.id ? template.id : null;
      return draw();
    }
    if (action === 'up') return moveTemplate(index, -1);
    if (action === 'down') return moveTemplate(index, 1);
    if (action === 'duplicate') return duplicateTemplate(index);
    if (action === 'remove') return removeTemplate(index);
    if (action === 'field-add') return addField(index);
    if (action === 'field-up') return moveField(index, fieldIndex, -1);
    if (action === 'field-down') return moveField(index, fieldIndex, 1);
    if (action === 'field-remove') return removeField(index, fieldIndex);
  }

  document.addEventListener('click', onClick);
  document.addEventListener('input', onInput);
  document.addEventListener('change', onInput);

  window.journlTemplates = {
    render() { load(false); },
    reload() { load(true); },
  };
})();
