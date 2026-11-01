/* Einstellungen des Bildschirmschoner-Modus „Fragen“ im Einstellungen-Modal.

   Drei Teile: der Ablauf (speichert bei jeder Aenderung), der Set-Editor
   (speichert auf Knopfdruck, weil man dort mehrere Felder am Stueck tippt) und
   „Fragen generieren“ ueber einen lokalen KI-Anbieter, optional mit den
   Journalen der letzten Tage als Kontext. Das Generieren laeuft als
   Serverlauf; hier wird nur `/api/runs/<id>` abgefragt. Das Modell kommt aus
   der Live-Liste des Anbieters; Wunsch, Anzahl, Tage, Anbieter und Modell
   lassen sich als benannter Prompt speichern und spaeter wieder laden.

   Gespeichert wird ueber `/api/screensaver/questions` (Teil-Updates: `flow`,
   `sets` oder `prompts`). Laeuft der Schoner gerade im Fragen-Modus, gilt der neue Stand
   beim naechsten Moduswechsel. */
(function () {
  const container = document.getElementById('settings-screensaver-questions');
  if (!container) return;

  const ENDPOINT = '/api/screensaver/questions';
  const FLOWS = [['manual', 'Frei – eine Frage, ohne Zeit'], ['auto', 'Automatisch – wechselt nach Zeit'],
    ['quiz', 'Quiz – Countdown läuft sichtbar ab'], ['sequence', 'Sequenz – Set der Reihe nach, feste Zeit je Frage']];
  const ANSWERS = [['impulse', 'Impuls – Frage im Hintergrund, frei schreiben'],
    ['session', 'Fragen-Session – Textfeld oder Auswahl je Frage']];
  const TIMEOUTS = [['keep', 'Weiter – leere Antwort als „–“'], ['skip', 'Weiter – leere Fragen weglassen'],
    ['wait', 'Warten, bis beantwortet']];
  // Die letzte Stufe 0 heisst „Manuell“: keine Zeit, weiter geht es von Hand.
  const STEPS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1800, 0];

  const LABEL = 'block text-xs text-gray-400 mb-1';
  const SELECT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500';
  const INPUT = 'rounded-md bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-blue-500';

  let state = null;
  let editing = null; // Arbeitskopie des Sets im Editor
  let loaded = false;
  let generated = [];
  // Formularstand des Generators - ueberlebt das Neu-Rendern nach dem Speichern.
  let genForm = { promptId: '', provider: '', model: '', instruction: '', count: 5, days: 0, name: '' };
  const modelCache = {};

  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const options = (entries, current) => entries.map(([value, label]) =>
    `<option value="${escapeHtml(value)}"${String(value) === String(current) ? ' selected' : ''}>${escapeHtml(label)}</option>`).join('');
  const formatSeconds = (value) => (!value ? 'Manuell' : value < 60 ? `${value} s` : `${Math.round(value / 6) / 10} min`.replace('.', ','));
  const nearestStep = (value) => STEPS.reduce((best, step, index) =>
    (Math.abs(step - value) < Math.abs(STEPS[best] - value) ? index : best), 0);

  async function request(url, options) {
    const response = await window.apiFetch(url, { cache: 'no-store', ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Anfrage fehlgeschlagen');
    return data;
  }

  // ─── Rendern ──────────────────────────────────────────────────────
  function render() {
    readGenerator();
    const generatorOpen = container.querySelector('[data-generator]')?.open;
    const flow = state.flow;
    const sets = state.sets.map((item) => [item.id, `${item.label} (${item.questions.length})`]);
    container.innerHTML = `
      <h4 class="mb-1 mt-5 text-sm font-semibold text-gray-300">Fragen-Modus</h4>
      <p class="mb-3 text-xs text-gray-500">Fragen über der Bildershow – für gezielte Journaleinträge. Antworten landen im Schreibfeld, gesendet wird mit Strg/⌘+Enter.</p>
      <div class="space-y-3" data-flow>
        <div><label class="${LABEL}" for="sq-set">Fragenset</label>
          <select id="sq-set" data-flow-field="set_id" class="${SELECT}">${options(sets, flow.set_id)}</select></div>
        <div><label class="${LABEL}" for="sq-flow">Ablauf</label>
          <select id="sq-flow" data-flow-field="flow" class="${SELECT}">${options(FLOWS, flow.flow)}</select></div>
        <div><label class="${LABEL}" for="sq-answer">Antworten</label>
          <select id="sq-answer" data-flow-field="answer" class="${SELECT}">${options(ANSWERS, flow.answer)}</select></div>
        <div><label class="${LABEL}" for="sq-seconds">Zeit je Frage <span data-seconds-value class="text-gray-500">${formatSeconds(flow.seconds)}</span></label>
          <input id="sq-seconds" type="range" min="0" max="${STEPS.length - 1}" step="1" value="${nearestStep(flow.seconds)}" class="w-full accent-green-500" />
          <p class="mt-1 text-xs text-gray-500">Auch während der Show oben über den Bildern verstellbar. In Sequenzen gilt die Zeit der einzelnen Frage, sonst diese. Ganz rechts „Manuell“: nichts zählt herunter, weiter geht es von Hand.</p></div>
        <div data-timeout-row><label class="${LABEL}" for="sq-timeout">Wenn die Zeit abläuft (Fragen-Session)</label>
          <select id="sq-timeout" data-flow-field="on_timeout" class="${SELECT}">${options(TIMEOUTS, flow.on_timeout)}</select></div>
        <label class="flex items-center justify-between gap-3 text-sm text-gray-200">Zufällige Reihenfolge <span class="text-xs text-gray-500">(nicht in Sequenzen)</span>
          <input type="checkbox" data-flow-field="shuffle" class="ml-auto h-5 w-5 accent-green-500"${flow.shuffle ? ' checked' : ''} /></label>
        <label data-loop-row class="flex items-center justify-between gap-3 text-sm text-gray-200">Fragen im Loop wiederholen <span class="text-xs text-gray-500">(Frei, Automatisch)</span>
          <input type="checkbox" data-flow-field="loop" class="ml-auto h-5 w-5 accent-green-500"${flow.loop ? ' checked' : ''} /></label>
        <label class="flex items-center justify-between gap-3 text-sm text-gray-200">Frage als Überschrift in den Text <span class="text-xs text-gray-500">(Impuls)</span>
          <input type="checkbox" data-flow-field="heading" class="ml-auto h-5 w-5 accent-green-500"${flow.heading ? ' checked' : ''} /></label>
        <div class="flex items-center gap-3"><button type="button" data-start class="rounded bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blue-500">Fragen starten</button>
          <span data-flow-status class="text-xs text-gray-500"></span></div>
      </div>

      <details class="mt-5 rounded-lg border border-gray-800 p-3" data-editor>
        <summary class="cursor-pointer text-sm text-gray-300">Fragensets bearbeiten</summary>
        <div class="mt-3 space-y-3" data-editor-body></div>
      </details>

      <details class="mt-3 rounded-lg border border-gray-800 p-3" data-generator>
        <summary class="cursor-pointer text-sm text-gray-300">Fragen generieren (lokale KI)</summary>
        <div class="mt-3 space-y-3" data-generator-body></div>
      </details>`;
    renderEditor();
    renderGenerator();
    syncTimeoutRow();
    if (generatorOpen) container.querySelector('[data-generator]').open = true;
  }

  function syncTimeoutRow() {
    const row = container.querySelector('[data-timeout-row]');
    if (row) row.classList.toggle('hidden', state.flow.answer !== 'session');
    const loop = container.querySelector('[data-loop-row]');
    if (loop) loop.classList.toggle('hidden', !['manual', 'auto'].includes(state.flow.flow));
  }

  function renderEditor() {
    const body = container.querySelector('[data-editor-body]');
    if (!body) return;
    if (!editing) editing = JSON.parse(JSON.stringify(state.sets.find((item) => item.id === state.flow.set_id) || state.sets[0] || newSet()));
    const choices = state.sets.map((item) => [item.id, item.label]);
    if (!state.sets.some((item) => item.id === editing.id)) choices.push([editing.id, `${editing.label} (neu)`]);
    body.innerHTML = `
      <div class="flex flex-wrap items-center gap-2">
        <select data-edit-pick class="${INPUT} min-w-0 flex-1">${options(choices, editing.id)}</select>
        <button type="button" data-edit-new class="rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:border-gray-500">Neues Set</button>
        <button type="button" data-edit-delete class="rounded px-2 py-1 text-xs text-red-300/80 hover:bg-red-950/50">Set löschen</button>
      </div>
      <label class="block"><span class="${LABEL}">Name</span><input data-edit-label class="${INPUT} w-full" value="${escapeHtml(editing.label)}" maxlength="80"></label>
      <div class="space-y-2" data-edit-rows>${editing.questions.map(questionRow).join('') || '<p class="text-xs text-gray-500">Noch keine Fragen.</p>'}</div>
      <p class="text-xs text-gray-500">Sekunden leer = Zeit aus dem Ablauf. Antworten mit „|“ trennen (z. B. „Gut | Okay | Schlecht“) – dann gibt es Knöpfe statt Textfeld, wählbar auch mit den Tasten 1–9.</p>
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" data-edit-add class="rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:border-gray-500">+ Frage</button>
        <button type="button" data-edit-save class="rounded bg-green-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-600">Set speichern</button>
        <span data-edit-status class="text-xs text-gray-500"></span>
      </div>`;
  }

  function questionRow(question, index) {
    return `<div class="rounded-md border border-gray-800 p-2" data-row="${index}">
      <div class="flex items-start gap-2">
        <span class="pt-1.5 text-xs tabular-nums text-gray-600">${index + 1}</span>
        <textarea data-q="text" rows="2" class="${INPUT} min-w-0 flex-1 resize-y" placeholder="Frage">${escapeHtml(question.text)}</textarea>
        <button type="button" data-q-remove class="rounded px-1.5 text-gray-500 hover:text-red-300" aria-label="Frage entfernen">×</button>
      </div>
      <div class="mt-1.5 flex flex-wrap items-center gap-2 pl-5">
        <input data-q="seconds" type="number" min="5" max="1800" class="${INPUT} w-20" placeholder="Sek." value="${question.seconds || ''}" aria-label="Sekunden">
        <input data-q="choices" class="${INPUT} min-w-0 flex-1" placeholder="Antworten (optional), mit | trennen" value="${escapeHtml((question.choices || []).join(' | '))}">
        <label class="flex items-center gap-1.5 text-xs text-gray-400"><input data-q="required" type="checkbox" class="accent-green-600"${question.required ? ' checked' : ''}> Pflicht</label>
      </div>
    </div>`;
  }

  function newSet() {
    return { id: `set-${Date.now().toString(36)}`, label: 'Neues Set', questions: [] };
  }

  // Liest die Zeilen zurueck in die Arbeitskopie - vor jedem Umbau der Liste.
  function readEditor() {
    const body = container.querySelector('[data-editor-body]');
    if (!body || !editing) return;
    editing.label = body.querySelector('[data-edit-label]').value.trim() || 'Fragen';
    editing.questions = Array.from(body.querySelectorAll('[data-row]')).map((row, index) => {
      const old = editing.questions[index] || {};
      const seconds = Number(row.querySelector('[data-q="seconds"]').value);
      return {
        id: old.id,
        text: row.querySelector('[data-q="text"]').value.trim(),
        seconds: Number.isFinite(seconds) && seconds > 0 ? seconds : null,
        choices: row.querySelector('[data-q="choices"]').value.split('|').map((item) => item.trim()).filter(Boolean),
        required: row.querySelector('[data-q="required"]').checked,
      };
    });
  }

  function renderGenerator() {
    const body = container.querySelector('[data-generator-body]');
    if (!body) return;
    const providers = (state.providers || []).map((item) => [item.id, item.label]);
    if (!providers.length) {
      body.innerHTML = '<p class="text-xs text-gray-500">Kein lokaler KI-Anbieter (lm_* im privaten Netz) eingerichtet. Journalinhalte gehen hier bewusst nicht nach außen.</p>';
      return;
    }
    if (!providers.some(([id]) => id === genForm.provider)) genForm.provider = providers[0][0];
    const prompts = [['', '– Neuer Prompt –'], ...(state.prompts || []).map((item) => [item.id, item.label])];
    body.innerHTML = `
      <div><label class="${LABEL}" for="sq-prompt">Gespeicherter Prompt</label>
        <div class="flex items-center gap-2">
          <select id="sq-prompt" data-gen-prompt class="${SELECT} min-w-0 flex-1">${options(prompts, genForm.promptId)}</select>
          <button type="button" data-gen-prompt-delete class="rounded px-2 py-1 text-xs text-red-300/80 hover:bg-red-950/50${genForm.promptId ? '' : ' hidden'}">Löschen</button>
        </div></div>
      <div><label class="${LABEL}" for="sq-provider">Lokaler Anbieter</label>
        <select id="sq-provider" data-gen="provider" class="${SELECT}">${options(providers, genForm.provider)}</select></div>
      <div><label class="${LABEL}" for="sq-model">Modell <span data-gen-model-hint class="text-gray-500"></span></label>
        <select id="sq-model" data-gen="model" class="${SELECT}"><option value="">Automatisch</option></select></div>
      <div><label class="${LABEL}" for="sq-instruction">Wunsch an die KI</label>
        <textarea id="sq-instruction" data-gen="instruction" rows="3" class="${SELECT}" placeholder="z. B. Fragen für einen Wochenrückblick, mit Blick auf Arbeit und Familie">${escapeHtml(genForm.instruction)}</textarea></div>
      <div class="flex flex-wrap items-center gap-3 text-sm text-gray-300">
        <label class="flex items-center gap-2">Anzahl <input data-gen="count" type="number" min="1" max="20" value="${genForm.count}" class="${INPUT} w-16"></label>
        <label class="flex items-center gap-2">Journale der letzten <input data-gen="days" type="number" min="0" max="31" value="${genForm.days}" class="${INPUT} w-16"> Tage einbeziehen</label>
      </div>
      <p class="text-xs text-gray-500">0 Tage = ohne Journalinhalt. Mit Inhalt knüpfen die Fragen an deine Themen an; der Text geht nur an den lokalen Anbieter.</p>
      <div class="flex flex-wrap items-center gap-2">
        <input data-gen-prompt-name class="${INPUT} min-w-0 flex-1" maxlength="80" placeholder="Name des Prompts" value="${escapeHtml(genForm.name)}">
        <button type="button" data-gen-prompt-save class="rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:border-gray-500">${genForm.promptId ? 'Prompt aktualisieren' : 'Prompt speichern'}</button>
        <span data-gen-prompt-status class="text-xs text-gray-500"></span>
      </div>
      <div class="flex items-center gap-3"><button type="button" data-gen-start class="rounded bg-violet-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-600">Generieren</button>
        <span data-gen-status class="text-xs text-gray-500"></span></div>
      <div data-gen-results class="space-y-1.5"></div>`;
    renderGenerated();
    loadModels();
  }

  // Liest das Formular zurueck in genForm - vor jedem Neu-Rendern.
  function readGenerator() {
    const body = container.querySelector('[data-generator-body]');
    if (!body?.querySelector('[data-gen="provider"]')) return;
    const value = (selector) => body.querySelector(selector)?.value ?? '';
    genForm = {
      promptId: value('[data-gen-prompt]'),
      provider: value('[data-gen="provider"]'),
      model: value('[data-gen="model"]'),
      instruction: value('[data-gen="instruction"]'),
      count: Number(value('[data-gen="count"]')) || 5,
      days: Number(value('[data-gen="days"]')) || 0,
      name: value('[data-gen-prompt-name]'),
    };
  }

  /* Modelle kommen live vom Endpunkt (wie bei der Journal-Analyse), je Anbieter
     einmal pro Sitzung. Ein gespeichertes Modell, das der Endpunkt gerade nicht
     meldet, bleibt waehlbar - LM Studio laedt es beim Lauf ggf. nach. */
  async function loadModels() {
    const body = container.querySelector('[data-generator-body]');
    const select = body?.querySelector('[data-gen="model"]');
    const hint = body?.querySelector('[data-gen-model-hint]');
    if (!select) return;
    const provider = genForm.provider;
    const wanted = genForm.model;
    let data = modelCache[provider];
    if (!data) {
      if (hint) hint.textContent = '– wird gelesen…';
      try {
        data = await request(`${ENDPOINT}/providers/${encodeURIComponent(provider)}/models`);
        modelCache[provider] = data;
      } catch (error) {
        data = { models: [], configured: '', error: error.message };
      }
    }
    // Inzwischen anderer Anbieter gewaehlt oder neu gerendert: verwerfen.
    if (provider !== genForm.provider || !select.isConnected) return;
    const models = data.models || [];
    const auto = data.configured ? `Automatisch (${data.configured})` : 'Automatisch (erstes geladenes Modell)';
    let html = `<option value="">${escapeHtml(auto)}</option>`
      + models.map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`).join('');
    if (wanted && !models.includes(wanted)) html += `<option value="${escapeHtml(wanted)}">${escapeHtml(wanted)} (nicht gemeldet)</option>`;
    select.innerHTML = html;
    select.value = wanted;
    if (hint) hint.textContent = data.error ? `– ${data.error}` : `– ${models.length} gefunden`;
  }

  async function savePrompts(prompts, message, selectId) {
    const status = container.querySelector('[data-gen-prompt-status]');
    if (status) status.textContent = 'Wird gespeichert…';
    try {
      const data = await request(ENDPOINT, { method: 'PUT', body: JSON.stringify({ prompts }) });
      state.prompts = data.prompts || [];
      readGenerator();
      const selected = state.prompts.find((item) => item.id === selectId) || null;
      genForm.promptId = selected ? selected.id : '';
      genForm.name = selected ? selected.label : '';
      renderGenerator();
      const after = container.querySelector('[data-gen-prompt-status]');
      if (after) after.textContent = message;
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  }

  function saveCurrentPrompt() {
    readGenerator();
    const label = genForm.name.trim() || genForm.instruction.trim().split(/\s+/).slice(0, 6).join(' ');
    if (!label) {
      container.querySelector('[data-gen-prompt-status]').textContent = 'Erst einen Wunsch oder Namen eintragen.';
      return;
    }
    const id = genForm.promptId || `prompt-${Date.now().toString(36)}`;
    const entry = { id, label, instruction: genForm.instruction, count: genForm.count, days: genForm.days,
      provider_id: genForm.provider, model: genForm.model };
    const current = state.prompts || [];
    const index = current.findIndex((item) => item.id === id);
    const prompts = index < 0 ? [...current, entry] : current.map((item) => (item.id === id ? entry : item));
    savePrompts(prompts, index < 0 ? 'Prompt gespeichert' : 'Prompt aktualisiert', id);
  }

  function pickPrompt(id) {
    const prompt = (state.prompts || []).find((item) => item.id === id);
    readGenerator();
    genForm = prompt
      ? { promptId: prompt.id, provider: prompt.provider_id || genForm.provider, model: prompt.model || '',
        instruction: prompt.instruction, count: prompt.count, days: prompt.days, name: prompt.label }
      : { ...genForm, promptId: '', name: '' };
    renderGenerator();
  }

  function renderGenerated() {
    const box = container.querySelector('[data-gen-results]');
    if (!box) return;
    if (!generated.length) { box.innerHTML = ''; return; }
    box.innerHTML = generated.map((item, index) => `
      <label class="flex items-start gap-2 rounded-md border border-gray-800 px-2 py-1.5 text-sm text-gray-200">
        <input type="checkbox" data-gen-pick="${index}" checked class="mt-1 accent-green-600">
        <span>${escapeHtml(item.text)}${item.choices?.length ? `<span class="block text-xs text-gray-500">${escapeHtml(item.choices.join(' | '))}</span>` : ''}</span>
      </label>`).join('')
      + `<button type="button" data-gen-adopt class="rounded bg-green-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-green-600">Ausgewählte ins Set „${escapeHtml(editing?.label || '')}“ übernehmen</button>`;
  }

  // ─── Speichern ────────────────────────────────────────────────────
  async function saveFlow(patch) {
    const status = container.querySelector('[data-flow-status]');
    if (status) status.textContent = 'Wird gespeichert…';
    try {
      const data = await request(ENDPOINT, { method: 'PUT', body: JSON.stringify({ flow: patch }) });
      state.flow = data.flow;
      if (status) status.textContent = 'Gespeichert';
      // Presets vergleichen den Ablauf mit ihrem Stand („abweichend“).
      document.dispatchEvent(new CustomEvent('journl:screensaver-questions-saved'));
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  }

  async function saveSets(message) {
    readEditor();
    const status = container.querySelector('[data-edit-status]');
    const others = state.sets.filter((item) => item.id !== editing.id);
    const sets = [...others, editing];
    if (status) status.textContent = 'Wird gespeichert…';
    try {
      const data = await request(ENDPOINT, { method: 'PUT', body: JSON.stringify({ sets }) });
      state.sets = data.sets;
      state.flow = data.flow;
      // Der Server vergibt fehlende Kennungen; die Arbeitskopie folgt ihm.
      editing = JSON.parse(JSON.stringify(state.sets.find((item) => item.id === editing.id) || state.sets[state.sets.length - 1]));
      render();
      container.querySelector('[data-editor]').open = true;
      const after = container.querySelector('[data-edit-status]');
      if (after) after.textContent = message || 'Gespeichert';
    } catch (error) {
      if (status) status.textContent = error.message;
    }
  }

  // ─── Generieren ───────────────────────────────────────────────────
  async function generate() {
    const body = container.querySelector('[data-generator-body]');
    const status = body.querySelector('[data-gen-status]');
    const button = body.querySelector('[data-gen-start]');
    button.disabled = true;
    status.textContent = 'Wird gestartet…';
    try {
      const started = await request(`${ENDPOINT}/generate`, {
        method: 'POST',
        body: JSON.stringify({
          provider_id: body.querySelector('[data-gen="provider"]').value,
          model: body.querySelector('[data-gen="model"]').value,
          instruction: body.querySelector('[data-gen="instruction"]').value,
          count: Number(body.querySelector('[data-gen="count"]').value) || 5,
          days: Number(body.querySelector('[data-gen="days"]').value) || 0,
        }),
      });
      const startedAt = Date.now();
      let run = started.run;
      while (run && run.active) {
        status.textContent = `Das lokale Modell formuliert… ${Math.round((Date.now() - startedAt) / 1000)} s`;
        await new Promise((resolve) => setTimeout(resolve, 2500));
        run = await request(`/api/runs/${encodeURIComponent(run.id)}`);
      }
      if (!run || run.status !== 'done') throw new Error(run?.error || 'Generieren fehlgeschlagen');
      generated = run.result?.questions || [];
      const model = run.result?.model ? ` · ${run.result.model}` : '';
      status.textContent = (generated.length ? `${generated.length} Vorschläge` : 'Keine Vorschläge') + model;
      renderGenerated();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  // ─── Ereignisse ───────────────────────────────────────────────────
  container.addEventListener('change', (event) => {
    const field = event.target.closest('[data-flow-field]');
    if (field) {
      const key = field.dataset.flowField;
      const value = field.type === 'checkbox' ? field.checked : field.value;
      state.flow[key] = value;
      if (key === 'answer' || key === 'flow') syncTimeoutRow();
      if (key === 'set_id') { editing = null; renderEditor(); }
      saveFlow({ [key]: value });
      return;
    }
    if (event.target.id === 'sq-seconds') saveFlow({ seconds: STEPS[Number(event.target.value)] });
    if (event.target.matches('[data-gen-prompt]')) { pickPrompt(event.target.value); return; }
    if (event.target.matches('[data-gen="provider"]')) {
      readGenerator();
      genForm.model = '';
      loadModels();
      return;
    }
    if (event.target.matches('[data-edit-pick]')) {
      editing = JSON.parse(JSON.stringify(state.sets.find((item) => item.id === event.target.value) || newSet()));
      renderEditor();
      renderGenerated();
    }
  });

  container.addEventListener('input', (event) => {
    if (event.target.id !== 'sq-seconds') return;
    container.querySelector('[data-seconds-value]').textContent = formatSeconds(STEPS[Number(event.target.value)]);
  });

  container.addEventListener('click', (event) => {
    const target = event.target;
    if (target.closest('[data-start]')) {
      window.closeSettings?.();
      window.journlScreensaver?.open('questions');
    } else if (target.closest('[data-edit-new]')) {
      readEditor();
      editing = newSet();
      renderEditor();
      renderGenerated();
    } else if (target.closest('[data-edit-delete]')) {
      if (state.sets.length <= 1 && state.sets.some((item) => item.id === editing.id)) {
        container.querySelector('[data-edit-status]').textContent = 'Das letzte Set bleibt stehen.';
        return;
      }
      if (!window.confirm(`Set „${editing.label}“ löschen?`)) return;
      request(ENDPOINT, { method: 'PUT', body: JSON.stringify({ sets: state.sets.filter((item) => item.id !== editing.id) }) })
        .then((data) => { state.sets = data.sets; state.flow = data.flow; editing = null; render(); })
        .catch((error) => { container.querySelector('[data-edit-status]').textContent = error.message; });
    } else if (target.closest('[data-edit-add]')) {
      readEditor();
      editing.questions.push({ text: '', seconds: null, choices: [], required: false });
      renderEditor();
      const rows = container.querySelectorAll('[data-row] [data-q="text"]');
      rows[rows.length - 1]?.focus();
    } else if (target.closest('[data-q-remove]')) {
      readEditor();
      editing.questions.splice(Number(target.closest('[data-row]').dataset.row), 1);
      renderEditor();
    } else if (target.closest('[data-edit-save]')) {
      saveSets();
    } else if (target.closest('[data-gen-start]')) {
      generate();
    } else if (target.closest('[data-gen-prompt-save]')) {
      saveCurrentPrompt();
    } else if (target.closest('[data-gen-prompt-delete]')) {
      readGenerator();
      const prompt = (state.prompts || []).find((item) => item.id === genForm.promptId);
      if (!prompt || !window.confirm(`Prompt „${prompt.label}“ löschen?`)) return;
      savePrompts(state.prompts.filter((item) => item.id !== prompt.id), 'Prompt gelöscht', '');
    } else if (target.closest('[data-gen-adopt]')) {
      readEditor();
      const picked = generated.filter((_, index) => container.querySelector(`[data-gen-pick="${index}"]`)?.checked);
      editing.questions.push(...picked.map((item) => ({ text: item.text, seconds: null, choices: item.choices || [], required: false })));
      generated = [];
      renderEditor();
      saveSets(`${picked.length} Fragen übernommen und gespeichert`);
    }
  });

  async function load() {
    try {
      state = await request(ENDPOINT);
      loaded = true;
      render();
    } catch (_) {
      container.innerHTML = '<p class="mt-4 text-xs text-gray-500">Fragen-Einstellungen nicht erreichbar.</p>';
    }
  }

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'screensaver') return;
    if (!loaded) load();
  });
  document.addEventListener('journl:screensaver-preset-applied', () => {
    if (loaded) load();
  });
}());
