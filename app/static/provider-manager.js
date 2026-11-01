/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Die Verwaltung der KI-Anbieter im Burger-Menue (AI-Worker → Anbieter).
 * Bis 4.7.14 liessen sie sich nur in der config.json von Hand pflegen; wer
 * einen Agenten umstellen wollte, brauchte Shellzugriff auf das Datenvolumen.
 *
 * Zwei Regeln, die hier nicht verhandelbar sind:
 *
 * 1. Gelesen und geschrieben werden die **Rohwerte** aus `/api/worker/providers`
 *    — mit `{locale_LLM_IP}`, ohne Schluessel. `/api/config` ist env-injiziert
 *    und darf nie zurueckgeschrieben werden (dieselbe Falle wie bei
 *    template-manager.js).
 * 2. Ein API-Schluessel kommt nie vom Server. Das Feld bleibt leer und wird nur
 *    mitgeschickt, wenn jemand etwas hineinschreibt — sonst bleibt der
 *    gespeicherte Schluessel unangetastet.
 *
 * Schreiben ist Administratoren vorbehalten; fuer alle anderen antwortet der
 * Endpunkt mit 403 und dieser Bereich bleibt leer.
 */
(function () {
  const ENDPOINT = '/api/worker/providers';

  const state = {
    providers: [],
    defaultId: '',
    chatId: '__chat__',
    resolved: {},
    connections: [],
    functions: [],
    note: '',
    dirty: false,
    container: null,
  };

  const icon = (name, cls) => (window.journlIcon ? window.journlIcon(name, cls) : '');

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(message, isError) {
    if (typeof window.showToast === 'function') window.showToast(message, !!isError);
  }

  // ─── Laden und Speichern ───────────────────────────────────────────
  async function load() {
    const response = await window.apiFetch(ENDPOINT, { timeoutMs: 20000 });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Abruf fehlgeschlagen');
    state.providers = (data.shared || []).map((item) => ({
      id: item.id || '',
      label: item.label || '',
      api_url: item.api_url || '',
      model: item.model || '',
      max_tokens: item.max_tokens == null ? '' : item.max_tokens,
      temperature: item.temperature == null ? '' : item.temperature,
      thinking_budget: item.thinking_budget == null ? '' : item.thinking_budget,
      journal_analysis: item.journal_analysis === true,
      has_key: !!item.has_key,
      env_managed: item.env_managed || [],
      // Nur was hier drinsteht, geht als Schluessel an den Server. Leer
      // heisst: unveraendert lassen.
      new_key: null,
      is_new: false,
    }));
    state.defaultId = data.default_id || '';
    state.chatId = data.chat_provider_id || '__chat__';
    state.resolved = data.resolved || {};
    state.connections = data.connections || [];
    state.functions = data.functions || [];
    state.note = data.note || '';
    state.dirty = false;
  }

  async function save() {
    const payload = {
      default_id: state.defaultId,
      providers: state.providers.map((item) => {
        const out = {
          id: item.id,
          label: item.label,
          api_url: item.api_url,
          model: item.model,
          max_tokens: item.max_tokens === '' ? null : item.max_tokens,
          temperature: item.temperature === '' ? null : item.temperature,
          thinking_budget: item.thinking_budget === '' ? null : item.thinking_budget,
          journal_analysis: item.journal_analysis === true,
        };
        if (item.new_key !== null) out.api_key = item.new_key;
        return out;
      }),
    };
    const response = await window.apiFetch(ENDPOINT, {
      method: 'PUT', body: JSON.stringify(payload), timeoutMs: 20000,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Speichern fehlgeschlagen');
    return data;
  }

  // ─── Zeichnen ──────────────────────────────────────────────────────
  function defaultSelect() {
    const options = [
      `<option value=""${state.defaultId ? '' : ' selected'}>— Notizen-Chat (Vorgabe)</option>`,
      `<option value="${escapeAttr(state.chatId)}"${state.defaultId === state.chatId ? ' selected' : ''}>Notizen-Chat</option>`,
    ];
    for (const item of state.providers) {
      if (!item.id) continue;
      options.push(`<option value="${escapeAttr(item.id)}"${state.defaultId === item.id ? ' selected' : ''}>${escapeHtml(item.label || item.id)}</option>`);
    }
    return `<select data-provider-default
      class="w-full rounded-lg border border-gray-800 bg-gray-900 px-2 py-1.5 text-sm text-gray-200 focus:border-gray-600 focus:outline-none">
      ${options.join('')}</select>`;
  }

  function field(index, name, label, value, opts) {
    const options = opts || {};
    const locked = options.locked ? ' disabled' : '';
    const hint = options.hint
      ? `<p class="mt-0.5 text-[11px] text-gray-600">${escapeHtml(options.hint)}</p>` : '';
    return `<label class="block ${options.wide ? 'sm:col-span-2' : ''}">
      <span class="text-[11px] text-gray-500">${escapeHtml(label)}</span>
      <input type="${options.type || 'text'}" data-provider-field="${name}" data-provider-index="${index}"
        value="${escapeAttr(value)}" placeholder="${escapeAttr(options.placeholder || '')}"${locked}
        class="mt-0.5 w-full rounded-lg border border-gray-800 bg-gray-900 px-2 py-1.5 text-sm text-gray-200 placeholder-gray-600 focus:border-gray-600 focus:outline-none disabled:opacity-50" />
      ${hint}</label>`;
  }

  function providerCard(item, index) {
    const managed = item.env_managed || [];
    const isManaged = (name) => managed.includes(name);
    const resolvedUrl = state.resolved[item.id];
    const urlHint = resolvedUrl && resolvedUrl !== item.api_url
      ? `zeigt aktuell auf ${resolvedUrl}` : '';
    const keyPlaceholder = item.has_key
      ? 'gespeichert — leer lassen, um ihn zu behalten' : 'kein Schlüssel hinterlegt';
    const envNote = managed.length
      ? `<p class="mt-2 rounded border border-amber-900/50 bg-amber-950/30 px-2 py-1 text-[11px] text-amber-400">
           Von außen gesetzt und deshalb hier nicht änderbar: ${escapeHtml(managed.join(', '))}.
           Diese Werte kommen aus Umgebungsvariablen und überschreiben die config.json bei jedem Start.</p>`
      : '';
    const isDefault = state.defaultId === item.id && !!item.id;

    return `<li class="rounded-lg border ${isDefault ? 'border-green-900/70' : 'border-gray-800'} px-3 py-2.5">
      <div class="mb-2 flex items-center gap-2">
        <span class="text-gray-500">${icon('chip', 'h-4 w-4')}</span>
        <span class="min-w-0 flex-1 truncate text-sm text-gray-200">${escapeHtml(item.label || item.id || 'Neuer Anbieter')}</span>
        ${isDefault ? '<span class="shrink-0 rounded bg-green-900/40 px-1.5 text-[10px] text-green-400">Standard</span>' : ''}
        <button type="button" data-provider-remove="${index}"
          class="shrink-0 rounded p-1 text-gray-600 hover:text-red-400" title="Anbieter entfernen">${icon('trash', 'h-4 w-4')}</button>
      </div>
      <div class="grid gap-2 sm:grid-cols-2">
        ${field(index, 'id', 'ID (fest nach dem Anlegen)', item.id,
          { locked: !item.is_new, placeholder: 'lm_studio',
            hint: item.is_new ? 'Kleinbuchstaben, Ziffern, _ und -' : 'Wird von gespeicherten Einstellungen referenziert.' })}
        ${field(index, 'label', 'Beschriftung', item.label, { placeholder: 'LM Studio (lokal)' })}
        ${field(index, 'api_url', 'Adresse (/v1/chat/completions)', item.api_url,
          { wide: true, locked: isManaged('api_url'),
            placeholder: 'http://{locale_LLM_IP}:1234/v1/chat/completions', hint: urlHint })}
        ${field(index, 'model', 'Modell', item.model, { locked: isManaged('model'), placeholder: 'qwen3.6-35b-a3b' })}
        ${field(index, 'api_key', 'API-Schlüssel', '',
          { type: 'password', locked: isManaged('api_key'), placeholder: keyPlaceholder })}
        ${field(index, 'max_tokens', 'Token-Grenze (optional)', item.max_tokens,
          { locked: isManaged('max_tokens'), placeholder: '10000' })}
        ${field(index, 'temperature', 'Temperatur (optional)', item.temperature,
          { locked: isManaged('temperature'), placeholder: '0.7' })}
        ${field(index, 'thinking_budget', 'Denk-Budget in Token (optional)', item.thinking_budget,
          { locked: isManaged('thinking_budget'), placeholder: '4000',
            hint: 'Für Reasoning-Modelle (llama.cpp): danach muss das Modell antworten. Die Token-Grenze muss größer sein.' })}
      </div>
      ${String(item.id || '').startsWith('lm_') ? '' : `
      <label class="mt-2 flex items-start gap-2 text-[11px] text-gray-400">
        <input type="checkbox" data-provider-field="journal_analysis" data-provider-index="${index}"${item.journal_analysis ? ' checked' : ''} class="mt-0.5 accent-amber-500" />
        <span>Für die Journal-Analyse freigeben — dann dürfen Analyse-Jobs diesen Cloud-Anbieter wählen, und deren Journalinhalte gehen an ihn (nur über HTTPS).</span>
      </label>`}
      ${envNote}
    </li>`;
  }

  function render() {
    const container = state.container;
    if (!container) return;
    const cards = state.providers.map(providerCard).join('');
    const connections = (state.connections || []).map((item) => `<li class="rounded-lg border border-gray-800 px-3 py-2">
        <p class="text-sm text-gray-200">${escapeHtml(item.label || item.id)}</p>
        <p class="text-xs text-gray-500">${escapeHtml(item.kind || '')} · ${escapeHtml(item.model || '')} · ${escapeHtml(item.base_url || '')}</p>
      </li>`).join('');
    const functions = (state.functions || [])
      .map((item) => escapeHtml(item.label || item.id)).join(' · ');

    container.innerHTML = `
      <div class="mb-3 rounded-lg border border-gray-800 px-3 py-2">
        <p class="mb-1 text-[11px] text-gray-500">Standard-Agent — vorausgewählt, wenn ein Agentenfenster aufgeht.</p>
        ${defaultSelect()}
      </div>
      <div class="mb-2 flex items-center justify-between gap-2">
        <h4 class="text-xs font-semibold text-gray-400">Anbieter</h4>
        <button type="button" data-provider-add
          class="rounded-lg border border-gray-800 px-2 py-1 text-xs text-gray-400 hover:border-gray-700 hover:text-gray-200">Anbieter hinzufügen</button>
      </div>
      <ul class="space-y-2">${cards || '<li class="text-sm text-gray-500">Noch keiner eingetragen.</li>'}</ul>
      <div class="mt-3 flex items-center gap-2">
        <button type="button" data-provider-save
          class="rounded-lg bg-green-700 px-3 py-1.5 text-sm text-white hover:bg-green-600 disabled:opacity-40"${state.dirty ? '' : ' disabled'}>Speichern</button>
        <span class="text-[11px] text-gray-600">${state.dirty ? 'Ungespeicherte Änderungen.' : 'Alles gespeichert.'}</span>
      </div>
      <h4 class="mb-1 mt-4 text-xs font-semibold text-gray-400">Verbindungen der Agent-Projekte</h4>
      <p class="mb-1 text-[11px] text-gray-600">${escapeHtml(state.note)}</p>
      <ul class="mb-3 space-y-2">${connections || '<li class="text-sm text-gray-500">Keine.</li>'}</ul>
      <h4 class="mb-1 text-xs font-semibold text-gray-400">KI-Schreibziele</h4>
      <p class="text-sm text-gray-400">${functions || 'Keine.'}</p>`;
  }

  function markDirty() {
    if (state.dirty) return;
    state.dirty = true;
    const button = state.container?.querySelector('[data-provider-save]');
    if (button) button.disabled = false;
    const note = button?.nextElementSibling;
    if (note) note.textContent = 'Ungespeicherte Änderungen.';
  }

  // ─── Bedienung ─────────────────────────────────────────────────────
  // Delegiert statt je Feld gebunden: die Liste wird komplett neu gezeichnet,
  // sobald sich ihre Struktur aendert, und Tippen darf das nicht ausloesen.
  function onInput(event) {
    const input = event.target.closest('[data-provider-field]');
    if (!input || !state.container?.contains(input)) return;
    const item = state.providers[Number(input.dataset.providerIndex)];
    if (!item) return;
    const name = input.dataset.providerField;
    if (name === 'api_key') item.new_key = input.value;
    else if (input.type === 'checkbox') item[name] = input.checked;
    else item[name] = input.value;
    markDirty();
  }

  async function onClick(event) {
    const container = state.container;
    if (!container || !container.contains(event.target)) return;

    const add = event.target.closest('[data-provider-add]');
    if (add) {
      state.providers.push({
        id: '', label: '', api_url: '', model: '', max_tokens: '', temperature: '', thinking_budget: '',
        journal_analysis: false, has_key: false, env_managed: [], new_key: null, is_new: true,
      });
      state.dirty = true;
      render();
      return;
    }

    const remove = event.target.closest('[data-provider-remove]');
    if (remove) {
      const index = Number(remove.dataset.providerRemove);
      const item = state.providers[index];
      if (!item) return;
      if (!item.is_new && !window.confirm(`„${item.label || item.id}" wirklich entfernen?`)) return;
      if (state.defaultId === item.id) state.defaultId = '';
      state.providers.splice(index, 1);
      state.dirty = true;
      render();
      return;
    }

    const saveButton = event.target.closest('[data-provider-save]');
    if (saveButton) {
      saveButton.disabled = true;
      try {
        await save();
        await load();
        render();
        toast('Anbieter gespeichert.');
        document.dispatchEvent(new CustomEvent('journl:providers-changed'));
      } catch (error) {
        saveButton.disabled = false;
        toast(error.message, true);
      }
    }
  }

  function onChange(event) {
    const select = event.target.closest('[data-provider-default]');
    if (!select || !state.container?.contains(select)) return;
    state.defaultId = select.value;
    state.dirty = true;
    render();
  }

  document.addEventListener('input', onInput);
  document.addEventListener('click', onClick);
  document.addEventListener('change', onChange);

  window.journlProviders = {
    async render(container) {
      state.container = container;
      container.innerHTML = '<p class="text-sm text-gray-500">Lade…</p>';
      try {
        await load();
      } catch (error) {
        container.innerHTML = `<p class="text-sm text-red-400">${escapeHtml(error.message)}</p>`;
        return;
      }
      render();
    },
  };
})();
