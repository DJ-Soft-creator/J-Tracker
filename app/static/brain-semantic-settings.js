/* Einstellungen der semantischen Suche (Burger → Brain).
 *
 * Zwei Ebenen, wie in app/semantic_settings.py: jede Person schaltet die Suche
 * für sich ein und wählt die vorausgewählten Quellen; Administratoren sehen
 * zusätzlich Verbindung, Modell, Nachtlauf und den Indexlauf für alle Konten.
 * Der API-Schlüssel kommt nie zurück in den Browser.
 *
 * Klassisches Script nach `brain-chat.js`; rendert in `#settings-semantic` und
 * lädt erst, wenn die Brain-Einstellungen geöffnet werden.
 */
(function () {
  const RUN_KIND = 'semantic_index';
  const INPUT = 'block w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100 focus:outline-none focus:ring-1 focus:ring-green-500';
  const BUTTON = 'rounded bg-gray-700 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-gray-600 disabled:opacity-50';
  const PRIMARY = 'rounded bg-green-700 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-green-600';
  const SOURCE_LABEL = {
    journal: 'Journale', notes: 'Projekte & Notes', family: 'Familie', agent: 'Agent & Chat',
  };
  const RUN_LABEL = {
    queued: 'wartet', running: 'läuft', done: 'fertig', failed: 'fehlgeschlagen',
    cancelled: 'abgebrochen', aborted: 'durch Neustart beendet',
  };
  // Stand des Gateways laut docs/features/embedding-gateway-llama-swap.md.
  const MODEL_HINT = 'Damit Embedding und Chat gleichzeitig auf dem Mac mini laufen, hier ein Profil '
    + 'aus der llama-swap-Gruppe <code>embeddings</code> wählen – derzeit <code>qwen3-embed-4b-e</code> '
    + '(Alias <code>embed-4b-e</code>). Diese Gruppe verdrängt keine Chatmodelle. Kein Chatprofil wählen: '
    + 'es hängt rund 90 s und endet mit HTTP 502. Daneben passen im Chat <code>ArmyKnife2-t</code> '
    + '(Alltag, schnell) oder <code>bonsai-code-t</code> (größtes Kontextfenster); <code>code-t</code> '
    + 'lässt nur noch ~8 % RAM frei.';

  const state = { data: null, status: null, timer: null };
  const root = () => document.getElementById('settings-semantic');

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  async function request(url, options) {
    const response = await window.apiFetch(url, { cache: 'no-store', timeoutMs: 90000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || `Abruf fehlgeschlagen (HTTP ${response.status})`);
      error.data = data;
      throw error;
    }
    return data;
  }
  const post = (url, payload) => request(url, { method: 'POST', body: JSON.stringify(payload || {}) });
  const field = (name) => root()?.querySelector(`[data-semantic="${name}"]`);

  function say(name, message, isError) {
    const target = field(name);
    if (!target) return;
    target.textContent = message;
    target.className = `text-xs ${isError ? 'text-red-400' : 'text-gray-500'}`;
  }

  // ─── Markup ────────────────────────────────────────────────────────
  function userMarkup(data) {
    const chips = data.sources.map((source) => `
      <label class="inline-flex items-center gap-1.5 rounded-full border border-gray-700 px-2.5 py-1 text-xs text-gray-300">
        <input type="checkbox" data-semantic-source="${source}" class="accent-green-600" ${data.user.sources.includes(source) ? 'checked' : ''} />
        ${SOURCE_LABEL[source] || source}
      </label>`).join('');
    const unavailable = !data.configured || !data.vector_store;
    return `
      <p class="mb-2 text-xs text-gray-500">Findet Einträge auch ohne das genaue Wort: Wort- und Bedeutungstreffer in einer Liste. Eingebettet wird nachts auf dem Mac mini im lokalen Netz.</p>
      ${unavailable ? '<p class="mb-2 text-xs text-amber-400">Noch nicht eingerichtet – ein Admin muss Modell und Vektor-DB hinterlegen.</p>' : ''}
      <label class="mb-2 flex items-center gap-2 text-sm text-gray-300">
        <input type="checkbox" data-semantic="enabled" class="accent-green-600" ${data.user.enabled ? 'checked' : ''} />
        Für mein Konto einschalten
      </label>
      <p class="mb-1 text-xs text-gray-400">Vorausgewählte Quellen</p>
      <div class="mb-2 flex flex-wrap gap-2">${chips}</div>
      <label class="mb-2 flex items-center gap-2 text-sm text-gray-500" title="Das Reranker-Profil ist auf dem Mac mini noch nicht angelegt.">
        <input type="checkbox" data-semantic="rerank" class="accent-green-600" disabled ${data.user.rerank ? 'checked' : ''} />
        Reranker (folgt, sobald das Profil auf dem Mac mini steht)
      </label>
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" data-semantic-action="save-user" class="${PRIMARY}">Speichern</button>
        <span data-semantic="user-status" class="text-xs text-gray-500" role="status" aria-live="polite"></span>
      </div>`;
  }

  function modelOptions(models, selected) {
    const names = models.length ? models : (selected ? [selected] : []);
    if (!names.length) return '<option value="">– erst „Modelle laden“ –</option>';
    return names.map((name) => {
      const embedding = /embed/i.test(name);
      return `<option value="${escapeHtml(name)}" ${name === selected ? 'selected' : ''}>${escapeHtml(name)}${embedding ? ' – Embedding' : ''}</option>`;
    }).join('');
  }

  function adminMarkup(settings, dims) {
    return `
      <div class="mt-4 space-y-2 border-t border-gray-700 pt-3">
        <p class="text-xs font-semibold uppercase tracking-wide text-gray-400">Admin – gilt für alle Konten</p>
        <label class="block text-xs text-gray-400">Endpunkt (OpenAI-kompatibel, nur lokales Netz)
          <input data-semantic="base_url" type="url" inputmode="url" placeholder="http://192.168.170.211:8080" value="${escapeHtml(settings.base_url)}" class="${INPUT} mt-1" />
        </label>
        <label class="block text-xs text-gray-400">API-Schlüssel (optional)
          <input data-semantic="api_key" type="password" autocomplete="off" placeholder="${settings.has_api_key ? 'gespeichert – leer lassen = unverändert' : 'leer lassen = unverändert'}" class="${INPUT} mt-1" />
        </label>
        <label class="block text-xs text-gray-400" for="semantic-model">Embedding-Modell</label>
        <div class="flex flex-wrap items-center gap-2">
          <select id="semantic-model" data-semantic="model" class="min-w-0 flex-1 rounded bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100">${modelOptions([], settings.model)}</select>
          <button type="button" data-semantic-action="models" class="${BUTTON}">Modelle laden</button>
          <button type="button" data-semantic-action="test" class="${BUTTON}">Testen</button>
        </div>
        <p class="rounded border border-sky-900/60 bg-sky-950/20 px-2 py-1.5 text-xs text-gray-400">${MODEL_HINT} Gespeichert werden immer ${dims} Dimensionen; ein Modellwechsel bettet beim nächsten Lauf alles neu ein.</p>
        <div class="flex flex-wrap items-center gap-3 text-sm text-gray-300">
          <label class="flex items-center gap-2"><input type="checkbox" data-semantic="job_enabled" class="accent-green-600" ${settings.job_enabled ? 'checked' : ''} /> Nachtlauf um</label>
          <input data-semantic="schedule_time" type="time" value="${escapeHtml(settings.schedule_time)}" class="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100" />
        </div>
        <div class="grid grid-cols-2 gap-2">
          <label class="block text-xs text-gray-400">Texte je Anfrage (1–32)
            <input data-semantic="batch_size" type="number" min="1" max="32" value="${settings.batch_size}" class="${INPUT} mt-1" />
          </label>
          <label class="block text-xs text-gray-400">Mindestähnlichkeit (0–1)
            <input data-semantic="min_score" type="number" min="0" max="1" step="0.05" value="${settings.min_score}" class="${INPUT} mt-1" />
          </label>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button type="button" data-semantic-action="save-global" class="${PRIMARY}">Speichern</button>
          <span data-semantic="global-status" class="text-xs text-gray-500" role="status" aria-live="polite"></span>
        </div>
        <div class="rounded border border-gray-700 bg-gray-900/60 p-2 text-xs text-gray-400">
          <div data-semantic="index-status">Status wird geladen …</div>
          <div class="mt-2 flex flex-wrap gap-2">
            <button type="button" data-semantic-action="run" class="${BUTTON}">Jetzt abgleichen</button>
            <button type="button" data-semantic-action="rebuild" class="${BUTTON}">Komplett neu aufbauen</button>
            <button type="button" data-semantic-action="cancel" class="${BUTTON} hidden">Abbrechen</button>
          </div>
        </div>
      </div>`;
  }

  function render() {
    const host = root();
    if (!host || !state.data) return;
    host.innerHTML = userMarkup(state.data) + (state.data.is_admin ? adminMarkup(state.data.global, state.data.dims) : '');
  }

  // ─── Status und Lauf ───────────────────────────────────────────────
  function runText(run) {
    if (!run) return 'Noch kein Indexlauf.';
    const values = { ...(run.progress || {}), ...(run.result || {}) };
    const parts = [`Letzter Lauf: ${RUN_LABEL[run.status] || run.status}`];
    if (values.embedded != null) parts.push(`${values.embedded} eingebettet`);
    if (values.unchanged != null) parts.push(`${values.unchanged} unverändert`);
    if (values.removed) parts.push(`${values.removed} entfernt`);
    if (values.pending) parts.push(`${values.pending} offen`);
    if (run.error) parts.push(run.error);
    return parts.join(' · ');
  }

  function renderStatus(status) {
    const target = field('index-status');
    if (!target) return;
    if (!status.available) {
      target.textContent = 'Vektor-DB nicht eingerichtet (QDRANT_URL fehlt im Container).';
      return;
    }
    if (status.error) {
      target.textContent = status.error;
      return;
    }
    const collections = Object.values(status.collections || {}).filter(Boolean);
    const points = collections.reduce((sum, item) => sum + (item.points || 0), 0);
    const lines = [
      `Qdrant ${status.health?.version || ''} · ${collections.length} Collections · ${points} Abschnitte`,
      runText(status.last_run),
    ];
    if (status.state?.last_run_at) lines.push(`Nachtlauf zuletzt: ${String(status.state.last_run_at).slice(0, 16).replace('T', ' ')}`);
    target.innerHTML = lines.map((line) => `<div>${escapeHtml(line)}</div>`).join('');
    const active = status.last_run && ['queued', 'running'].includes(status.last_run.status);
    root()?.querySelector('[data-semantic-action="cancel"]')?.classList.toggle('hidden', !active);
    clearTimeout(state.timer);
    if (active) state.timer = setTimeout(loadStatus, 3000);
  }

  async function loadStatus() {
    if (!state.data?.is_admin) return;
    try {
      state.status = await request('/api/brain/semantic/status');
      renderStatus(state.status);
    } catch (error) {
      renderStatus({ available: true, error: error.message });
    }
  }

  async function load() {
    try {
      state.data = await request('/api/brain/semantic/settings');
    } catch (error) {
      const host = root();
      if (host) host.textContent = error.message;
      return;
    }
    render();
    loadStatus();
  }

  // ─── Aktionen ──────────────────────────────────────────────────────
  async function saveUser() {
    const sources = Array.from(root().querySelectorAll('[data-semantic-source]'))
      .filter((input) => input.checked).map((input) => input.dataset.semanticSource);
    say('user-status', 'Wird gespeichert …');
    try {
      const data = await post('/api/brain/semantic/settings/user', { enabled: field('enabled').checked, sources });
      state.data.user = data.user;
      document.dispatchEvent(new CustomEvent('journl:semantic-search-changed', { detail: data.user }));
      say('user-status', 'Gespeichert.');
    } catch (error) {
      say('user-status', error.message, true);
    }
  }

  function connectionPayload() {
    const payload = { base_url: field('base_url').value.trim(), model: field('model').value };
    if (field('api_key').value) payload.api_key = field('api_key').value;
    return payload;
  }

  async function loadModels() {
    const base = field('base_url').value.trim();
    if (!base) {
      say('global-status', 'Bitte zuerst den Endpunkt eintragen.', true);
      return;
    }
    say('global-status', 'Modelle werden gelesen …');
    try {
      const data = await request(`/api/brain/semantic/models?${new URLSearchParams({ base_url: base })}`);
      const current = field('model').value || state.data.global.model;
      const preferred = current || (data.models || []).find((name) => /embed/i.test(name)) || '';
      field('model').innerHTML = modelOptions(data.models || [], preferred);
      say('global-status', `${(data.models || []).length} Modelle gefunden – Embedding-Profile sind markiert.`);
    } catch (error) {
      say('global-status', error.message, true);
    }
  }

  async function testConnection() {
    say('global-status', 'Testaufruf läuft – ein kaltes Modell braucht bis zu 30 s …');
    try {
      const data = await post('/api/brain/semantic/test', connectionPayload());
      say('global-status', `Embedding ok: ${data.native_dims} Dimensionen nativ, gespeichert werden ${data.dims} (${data.elapsed_ms} ms).`);
    } catch (error) {
      say('global-status', error.message, true);
    }
  }

  async function saveGlobal() {
    const payload = {
      ...connectionPayload(),
      job_enabled: field('job_enabled').checked,
      schedule_time: field('schedule_time').value,
      batch_size: Number(field('batch_size').value),
      min_score: Number(field('min_score').value),
    };
    say('global-status', 'Wird gespeichert …');
    try {
      const data = await post('/api/brain/semantic/settings/global', payload);
      state.data.global = data.global;
      state.data.configured = data.global.configured;
      field('api_key').value = '';
      say('global-status', data.model_changed
        ? 'Gespeichert. Neues Modell – der nächste Lauf bettet alles neu ein.'
        : 'Gespeichert.');
      // Wie beim Speichern versprochen: gleich prüfen, ob das Profil Embeddings liefert.
      testConnection();
    } catch (error) {
      say('global-status', error.message, true);
    }
  }

  async function startRun(full) {
    if (full && !window.confirm('Alle Vektoren löschen und komplett neu einbetten?')) return;
    try {
      await post('/api/brain/semantic/index/run', { full });
    } catch (error) {
      say('global-status', error.message, true);
    }
    loadStatus();
  }

  async function cancelRun() {
    const run = state.status?.last_run;
    if (!run) return;
    try {
      await post(`/api/runs/${encodeURIComponent(run.id)}/cancel`);
    } catch (error) {
      say('global-status', error.message, true);
    }
    loadStatus();
  }

  const ACTIONS = {
    'save-user': saveUser, models: loadModels, test: testConnection, 'save-global': saveGlobal,
    run: () => startRun(false), rebuild: () => startRun(true), cancel: cancelRun,
  };

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-semantic-action]');
    if (!button || !root()?.contains(button)) return;
    ACTIONS[button.dataset.semanticAction]?.();
  });
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'brain') load();
  });
  window.JournlSemanticSettings = { load, RUN_KIND };
})();
