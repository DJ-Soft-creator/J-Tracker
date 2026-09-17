/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Burger-Menue -> "Meine Abkürzungen": Anzeige UND Verwaltung der
 * Text-Bausteine, die write-snippets.js beim Tippen im Schreiben-Tab
 * vorschlägt. Vertrag mit desktop-app-features.js / mobile-app-features.js:
 * deren loadSettingsSnippets() ruft nur window.journlSnippets.render().
 *
 * Quelle ist GET/PUT /api/settings/snippets (rohe config.json), nicht
 * /api/config. Anders als bei Templates darf jeder angemeldete Nutzer
 * speichern - keine Admin-Prüfung nötig.
 */
(function () {
  const state = {
    loading: false,
    loaded: false,
    draft: [],
    baseline: '',
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
    return document.getElementById('settings-snippets-list');
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function dirty() {
    return JSON.stringify(state.draft) !== state.baseline;
  }

  async function load(force) {
    if (state.loading) return;
    // Ungespeicherte Arbeit ueberlebt ein erneutes Oeffnen der Einstellungen.
    if (state.loaded && !force && dirty()) { draw(); return; }
    state.loading = true;
    state.error = '';
    draw();
    try {
      const response = await window.apiFetch('/api/settings/snippets');
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Abkürzungen konnten nicht geladen werden.');
      state.draft = clone(data.snippets || []);
      state.baseline = JSON.stringify(state.draft);
      state.loaded = true;
    } catch (error) {
      state.error = error.message || 'Abkürzungen konnten nicht geladen werden.';
    } finally {
      state.loading = false;
      draw();
    }
  }

  async function save() {
    const button = container()?.querySelector('[data-act="save"]');
    if (button) { button.disabled = true; button.textContent = 'Speichert…'; }
    try {
      const response = await window.apiFetch('/api/settings/snippets', {
        method: 'PUT',
        body: JSON.stringify({ snippets: state.draft }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen.');
      state.draft = clone(data.snippets || []);
      state.baseline = JSON.stringify(state.draft);
      state.error = '';
      // window.config ist dasselbe Objekt wie das der App-Shell: das Array an
      // Ort und Stelle ersetzen, damit write-snippets.js sofort mitzieht.
      if (window.config) window.config.text_snippets = clone(state.draft);
      toast('Abkürzungen gespeichert');
    } catch (error) {
      state.error = error.message || 'Speichern fehlgeschlagen.';
      toast(state.error, true);
    } finally {
      draw();
    }
  }

  function addSnippet() {
    state.draft.push({ id: '', trigger: '', expansion: '' });
    draw();
    container()?.querySelector('tr:last-child input[data-field="trigger"]')?.focus();
  }

  function removeSnippet(index) {
    state.draft.splice(index, 1);
    draw();
  }

  function row(snippet, index) {
    return `
      <tr data-row="${index}">
        <td class="py-1 pr-2">
          <input data-field="trigger" data-index="${index}" value="${escapeAttr(snippet.trigger)}"
            placeholder="z. B. -" maxlength="40"
            class="w-24 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </td>
        <td class="py-1 pr-2 w-full">
          <input data-field="expansion" data-index="${index}" value="${escapeAttr(snippet.expansion)}"
            placeholder="z. B. - [ ] " maxlength="500"
            class="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500" />
        </td>
        <td class="py-1 pl-1">
          <button type="button" data-act="remove" data-index="${index}" title="Entfernen"
            class="text-gray-500 hover:text-red-400 text-xs px-1">${window.journlIcon?.('trash', 'h-4 w-4') || '×'}</button>
        </td>
      </tr>`;
  }

  function draw() {
    const el = container();
    if (!el) return;
    if (state.loading && !state.loaded) { el.innerHTML = '<p class="text-gray-500">Lade…</p>'; return; }
    const rows = state.draft.map(row).join('') || '';
    el.innerHTML = `
      <p class="text-xs text-gray-500 mb-2">Beim Tippen im Schreiben-Tab erscheint für einen Auslöser eine Vorschlags-Kachel, die ihn durch den Text rechts daneben ersetzt - hilfreich vor allem am Handy, wo Sonderzeichen wie "[" und "]" mühsam zu erreichen sind.</p>
      ${state.error ? `<p class="text-xs text-red-400 mb-2">${escapeHtml(state.error)}</p>` : ''}
      <table class="w-full text-left mb-2">
        <thead><tr class="text-xs text-gray-500">
          <th class="pb-1 font-normal">Auslöser</th><th class="pb-1 font-normal">Text</th><th></th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="3" class="py-1 text-gray-600 text-sm">Noch keine Abkürzungen.</td></tr>'}</tbody>
      </table>
      <div class="flex flex-wrap gap-2">
        <button type="button" data-act="add" class="bg-gray-700 hover:bg-gray-600 text-white text-sm font-semibold py-1.5 px-3 rounded transition">+ Abkürzung</button>
        <button type="button" data-act="save" ${dirty() ? '' : 'disabled'}
          class="bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold py-1.5 px-3 rounded transition">Speichern</button>
      </div>`;
  }

  function onInput(event) {
    const input = event.target.closest('#settings-snippets-list input[data-field]');
    if (!input) return;
    const index = Number(input.dataset.index);
    const snippet = state.draft[index];
    if (!snippet) return;
    snippet[input.dataset.field] = input.value;
    const saveButton = container()?.querySelector('[data-act="save"]');
    if (saveButton) saveButton.disabled = !dirty();
  }

  function onClick(event) {
    const el = container();
    if (!el) return;
    const button = event.target.closest('[data-act]');
    if (!button || !el.contains(button)) return;
    if (button.dataset.act === 'add') addSnippet();
    else if (button.dataset.act === 'remove') removeSnippet(Number(button.dataset.index));
    else if (button.dataset.act === 'save') save();
  }

  document.addEventListener('click', onClick);
  document.addEventListener('input', onInput);

  window.journlSnippets = {
    render: () => load(false),
  };
})();
