/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Reicht den Inhalt der Schreiben-Textbox als Ticket in das Entwicklungs-
 * Repository ein, statt ihn ins Journal zu senden, und verwaltet die
 * eingereichten Tickets (Liste + Freigabe) in den Einstellungen.
 *
 * Nur fuer Admins und nur, wenn der Eingangsordner gemountet ist — beides
 * beantwortet /api/tickets/config. Ohne das bleibt die Oberflaeche unveraendert.
 *
 * Wichtig (Verarbeitungsvertrag, docs/project/Anforderung-Ticket-Assistent.md
 * Abschnitt 5a): Ein eingereichtes Ticket ist zunaechst nur eine Datei im
 * Zustand `neu`. Erst die Freigabe hier macht es zum Auftrag.
 */
(function () {
  const STATE_LABELS = {
    neu: 'Neu',
    freigegeben: 'Freigegeben',
    in_arbeit: 'In Arbeit',
    rueckfrage: 'Rückfrage',
    pruefung: 'Prüfung',
    fehlgeschlagen: 'Fehlgeschlagen',
    erledigt: 'Erledigt',
    verworfen: 'Verworfen',
  };
  const STATE_CLASSES = {
    neu: 'text-gray-300',
    freigegeben: 'text-green-400',
    in_arbeit: 'text-blue-400',
    rueckfrage: 'text-amber-400',
    pruefung: 'text-amber-300',
    fehlgeschlagen: 'text-red-400',
    erledigt: 'text-gray-500',
    verworfen: 'text-gray-600',
  };
  // Der Browser darf nur diese Zustände setzen; den Rest vergibt ein Lauf.
  const SETTABLE = ['neu', 'freigegeben', 'erledigt', 'verworfen'];
  const EFFORTS = ['', 'low', 'medium', 'high'];
  const MODES = [['read', 'Nur analysieren'], ['write', 'Auch ändern']];

  let available = null;
  let options = null;

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function request(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 15000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Ticket-Aktion fehlgeschlagen');
    return data;
  }

  // ─── Einreichen aus dem Schreiben-Tab ─────────────────────────────
  async function submitTicket() {
    const input = document.getElementById('simple-input');
    const content = input ? input.value : '';
    if (!content.trim()) {
      window.showToast?.('Die Textbox ist leer.');
      return;
    }
    // Anhaenge liegen in der Schreibsession, nicht in der Textbox. Ohne diesen
    // Weg bliebe die Session mit ihren Medien verwaist zurueck.
    const blocked = window.writeSessionSubmissionBlockReason?.();
    if (blocked) {
      window.showToast?.(blocked);
      return;
    }
    try {
      let sessionId = null;
      try {
        sessionId = await window.writeSessionForSubmit?.(content);
      } catch (error) {
        window.showToast?.(error.message || 'Die Schreibsession konnte nicht gesichert werden.');
        return;
      }
      const data = await request('/api/tickets', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, session_id: sessionId }),
      });
      if (sessionId) await window.markWriteSessionSubmitted?.();
      if (input) {
        input.value = '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
      }
      const suffix = data.attachments
        ? ` (${data.attachments} ${data.attachments === 1 ? 'Anhang' : 'Anhänge'})` : '';
      window.showToast?.(`Ticket angelegt: ${data.title}${suffix}`);
    } catch (error) {
      window.showToast?.(error.message);
    }
  }

  // Der Schreiben-Tab hat genau ein Aktionsmenue: das "⋯" aus write-session.js
  // (#write-media-menu). Ein zweiter Menue-Button daneben war von aussen nicht
  // unterscheidbar, deshalb haengen die Ticket-Ziele hier mit ein — genauso wie
  // die Agent- und Screenshot-Eintraege. Weitere Ticket-Ziele reihen sich in
  // diesen Block ein.
  let menuBlock = null;

  function closeMediaMenu() {
    document.getElementById('write-media-menu')?.classList.add('hidden');
  }

  function ticketMenuBlock() {
    if (menuBlock) return menuBlock;
    const menu = document.getElementById('write-media-menu');
    if (!menu) return null;
    menuBlock = document.createElement('div');
    menuBlock.dataset.ticketBlock = '';
    menuBlock.className = 'hidden';
    menuBlock.innerHTML = '<div class="my-1 border-t border-gray-700" role="separator"></div>'
      + '<button type="button" data-ticket-submit'
      + ' class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">'
      + (window.journlIcon?.('ticket', 'h-4 w-4') || '') + '<span>Als Ticket: J-Tracker-Dev</span></button>'
      + '<button type="button" data-ticket-manage'
      + ' class="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">'
      + (window.journlIcon?.('clipboard', 'h-4 w-4') || '') + '<span>Tickets verwalten</span></button>';
    menu.appendChild(menuBlock);
    return menuBlock;
  }

  function renderTrigger() {
    const block = ticketMenuBlock();
    if (block) block.classList.toggle('hidden', !available);
  }

  // ─── Verwaltung in den Einstellungen ──────────────────────────────
  function ticketRow(item) {
    const state = STATE_LABELS[item.state] || item.state;
    const tone = STATE_CLASSES[item.state] || 'text-gray-300';
    const busy = item.state === 'in_arbeit';
    // Freigeben laeuft ueber ein eigenes Formular, weil dabei die
    // Agent-Konfiguration gewaehlt werden muss (E-28).
    const release = (item.state === 'neu' || item.state === 'fehlgeschlagen')
      ? `<button type="button" data-release-open="${escapeAttr(item.id)}"
           class="rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:text-white">Freigeben…</button>`
      : '';
    // Der aktuelle Zustand steht immer als ausgewählter Eintrag drin — auch
    // wenn er sich von hier nicht setzen lässt. Sonst zeigte das Feld einen
    // fremden Zustand an und ein Klick daneben würde ihn still überschreiben.
    const options = SETTABLE
      .filter((value) => value !== 'freigegeben' || item.state === 'freigegeben')
      .map((value) => `<option value="${value}"${value === item.state ? ' selected' : ''}>${STATE_LABELS[value]}</option>`);
    if (!SETTABLE.includes(item.state)) {
      options.unshift(`<option value="${escapeAttr(item.state)}" selected disabled>${escapeHtml(state)}</option>`);
    }
    const actions = busy
      ? '<span class="text-xs text-gray-500">wird bearbeitet</span>'
      : `<div class="flex items-center gap-2">${release}
         <select data-ticket-state="${escapeAttr(item.id)}"
           class="rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100">
           ${options.join('')}
         </select></div>`;
    const warning = item.content_missing
      ? '<span class="text-xs text-red-400">Inhalt fehlt</span>' : '';
    const clip = item.attachments
      ? `<span class="inline-flex items-center gap-1 text-xs text-gray-400">${window.journlIcon?.('paperclip', 'h-3 w-3') || ''}<span>${item.attachments}</span></span>` : '';
    // Protokoll und Bericht sind die einzige Sicht auf einen Lauf, solange die
    // Review-Ansicht fehlt — deshalb ab dem ersten Lauf immer anbieten.
    const links = [];
    if (item.runs) {
      links.push(fileLink(item.id, 'bericht', 'Bericht'));
      links.push(fileLink(item.id, 'protokoll', 'Protokoll'));
    }
    if (item.files && item.files.length) {
      links.push(fileLink(item.id, 'diff', `Änderungen (${item.files.length})`));
    }
    const inspect = links.length
      ? `<div class="mt-1 flex flex-wrap gap-3">${links.join('')}</div>` : '';

    // Übernehmen und Verwerfen erledigt der Host-Worker; hier wird der Wunsch
    // nur hinterlegt, deshalb der Hinweis auf die Warteschlange.
    const review = item.state === 'pruefung'
      ? (item.requested_action
          ? `<p class="mt-1 text-xs text-amber-300">${item.requested_action === 'merge'
              ? 'Übernahme angefordert – wird ausgeführt…' : 'Verwerfen angefordert – wird ausgeführt…'}</p>`
          : `<div class="mt-2 flex gap-2">
               <button type="button" data-review="merge" data-ticket-id="${escapeAttr(item.id)}"
                 class="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600">Übernehmen</button>
               <button type="button" data-review="discard" data-ticket-id="${escapeAttr(item.id)}"
                 class="rounded border border-gray-700 px-3 py-1 text-xs text-gray-300 hover:text-white">Verwerfen</button>
             </div>`)
      : '';
    const failure = item.last_error
      ? `<p class="text-xs text-red-400">${escapeHtml(item.last_error)}</p>` : '';
    const config = item.config
      ? `<p class="text-xs text-gray-600">${escapeHtml([item.config.endpoint, item.config.tool,
          item.config.model, item.config.effort,
          item.config.mode === 'write' ? 'darf ändern' : 'nur lesen'].filter(Boolean).join(' · '))}</p>`
      : '';
    return `<li class="rounded-lg border border-gray-800 px-3 py-2">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate text-sm text-gray-200">${escapeHtml(item.title)}</p>
          <p class="text-xs text-gray-500">${escapeHtml(item.created_at)} · <span class="${tone}">${escapeHtml(state)}</span> ${clip} ${warning}</p>
          ${config}
          ${failure}
          ${inspect}
          ${review}
        </div>
        ${actions}
      </div>
      <div data-release-form="${escapeAttr(item.id)}" class="hidden"></div>
      <pre data-ticket-output="${escapeAttr(item.id)}"
        class="hidden mt-2 max-h-80 overflow-auto rounded bg-gray-950 p-2 text-[11px] text-gray-400 whitespace-pre-wrap"></pre>
    </li>`;
  }

  async function loadTickets() {
    const list = document.getElementById('settings-tickets-list');
    if (!list) return;
    if (!available) {
      list.innerHTML = '<p class="text-gray-500 text-sm">Die Ticket-Ablage ist in dieser Umgebung nicht eingerichtet.</p>';
      return;
    }
    list.innerHTML = '<p class="text-gray-500 text-sm">Lade…</p>';
    try {
      const data = await request('/api/tickets');
      const items = data.items || [];
      if (!items.length) {
        list.innerHTML = '<p class="text-gray-500 text-sm">Noch keine Tickets eingereicht.</p>';
        return;
      }
      const broken = data.broken
        ? `<p class="text-xs text-red-400 mb-2">${data.broken} unlesbare Datei(en) übersprungen.</p>` : '';
      list.innerHTML = broken + `<ul class="space-y-2">${items.map(ticketRow).join('')}</ul>`;
    } catch (error) {
      list.innerHTML = `<p class="text-red-400 text-sm">${escapeHtml(error.message)}</p>`;
    }
  }

  // ─── Freigabe: Ziel wählen, Modell folgt daraus (E-35) ────────────
  // Endpunkt und Werkzeug gehören zusammen: Claude Code läuft über die
  // Host-Anmeldung, pi über seine models.json, eine Verbindung über ihren
  // eigenen Runner. Getrennte Felder erlaubten Kombinationen, die es nicht gibt.
  async function ensureOptions() {
    if (options) return options;
    try {
      options = await request('/api/worker/targets');
    } catch (_) {
      options = { targets: [] };
    }
    return options;
  }

  function targetById(id) {
    return (options?.targets || []).find((entry) => entry.id === id) || null;
  }

  function renderModelField(host, target) {
    const slot = host.querySelector('[data-model-slot]');
    if (!slot) return;
    if (!target) {
      slot.innerHTML = selectMarkup('model', [], 'Erst Ziel wählen');
      return;
    }
    slot.innerHTML = target.free_model || !target.models.length
      ? `<input data-release-field="model" type="text" placeholder="Modell eintragen"
           class="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100" />`
      : selectMarkup('model', target.models, 'Modell wählen');
    const effort = host.querySelector('[data-effort-slot]');
    if (effort) {
      effort.innerHTML = target.supports_effort
        ? selectMarkup('effort', EFFORTS.filter(Boolean), 'Effort (optional)')
        : '';
    }
    const note = host.querySelector('[data-target-note]');
    if (note) note.textContent = target.note || '';
  }

  function selectMarkup(name, entries, placeholder) {
    const rows = entries.map((entry) => {
      const [value, label] = Array.isArray(entry) ? entry : [entry, entry];
      return `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`;
    }).join('');
    return `<select data-release-field="${name}"
      class="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100">
      <option value="">${escapeHtml(placeholder)}</option>${rows}</select>`;
  }

  async function openReleaseForm(ticketId) {
    const host = document.querySelector(`[data-release-form="${CSS.escape(ticketId)}"]`);
    if (!host) return;
    if (!host.classList.contains('hidden')) {
      host.classList.add('hidden');
      return;
    }
    host.classList.remove('hidden');
    host.innerHTML = '<p class="text-xs text-gray-500">Lade Auswahl…</p>';
    const data = await ensureOptions();
    if (!data.targets.length) {
      host.innerHTML = '<p class="text-xs text-amber-300/80">Kein Ausführungsziel verfügbar. '
        + 'Der Host-Worker meldet die vorhandenen Werkzeuge erst nach seinem nächsten Durchlauf.</p>';
      return;
    }
    const targets = data.targets.map((entry) => [entry.id, entry.label]);
    host.innerHTML = `<div class="mt-2 space-y-2">
        ${selectMarkup('target', targets, 'Ziel wählen')}
        <p data-target-note class="text-[11px] text-gray-500"></p>
        <div class="grid grid-cols-2 gap-2">
          <div data-model-slot>${selectMarkup('model', [], 'Erst Ziel wählen')}</div>
          <div data-effort-slot></div>
          ${selectMarkup('mode', MODES, 'Rechte wählen')}
          <button type="button" data-release-confirm="${escapeAttr(ticketId)}"
            class="rounded bg-green-700 px-3 py-1 text-xs text-white hover:bg-green-600">Freigeben</button>
        </div>
      </div>`;
  }

  async function confirmRelease(ticketId) {
    const host = document.querySelector(`[data-release-form="${CSS.escape(ticketId)}"]`);
    if (!host) return;
    const config = {};
    host.querySelectorAll('[data-release-field]').forEach((field) => {
      if (field.value) config[field.dataset.releaseField] = field.value.trim();
    });
    // Der Server kennt weiterhin Endpunkt und Werkzeug getrennt; das Ziel ist
    // die Bedienoberfläche darüber.
    const target = targetById(config.target);
    if (!target) {
      window.showToast?.('Bitte ein Ziel wählen.');
      return;
    }
    config.endpoint = target.id;
    config.tool = target.tool;
    delete config.target;
    await changeState(ticketId, 'freigegeben', config);
  }

  async function changeState(ticketId, state, config) {
    try {
      await request(`/api/tickets/${encodeURIComponent(ticketId)}/state`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config ? { state, config } : { state }),
      });
      window.showToast?.('Zustand gespeichert.');
    } catch (error) {
      window.showToast?.(error.message);
    }
    loadTickets();
  }

  // ─── Verdrahtung ──────────────────────────────────────────────────
  async function refresh() {
    try {
      const data = await request('/api/tickets/config');
      const next = data.available === true;
      if (next === available) return;
      available = next;
    } catch (_) {
      available = false;
    }
    renderTrigger();
    document.querySelectorAll('[data-tickets-entry]').forEach((entry) =>
      entry.classList.toggle('hidden', !available));
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-ticket-submit]')) {
      event.preventDefault();
      closeMediaMenu();
      submitTicket();
      return;
    }
    if (event.target.closest('[data-ticket-manage]')) {
      event.preventDefault();
      closeMediaMenu();
      window.openSettingsGroup?.('worker', 'AI-Worker');
    }
  });

  document.addEventListener('change', (event) => {
    const select = event.target.closest('[data-ticket-state]');
    if (select) {
      changeState(select.dataset.ticketState, select.value);
      return;
    }
    const target = event.target.closest('[data-release-field="target"]');
    if (target) {
      const host = target.closest('[data-release-form]');
      if (host) renderModelField(host, targetById(target.value));
    }
  });

  function fileLink(ticketId, kind, label) {
    return `<button type="button" data-ticket-file="${kind}" data-ticket-id="${escapeAttr(ticketId)}"
      class="text-xs text-gray-400 hover:text-white underline decoration-dotted">${escapeHtml(label)}</button>`;
  }

  // Ein Diff liest sich nur mit Farbe; die Zeilen kommen als Text vom Server.
  function colourDiff(text) {
    return text.split('\n').map((line) => {
      const safe = escapeHtml(line);
      if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff ')) {
        return `<span class="text-gray-300">${safe}</span>`;
      }
      if (line.startsWith('@@')) return `<span class="text-blue-400">${safe}</span>`;
      if (line.startsWith('+')) return `<span class="text-green-400">${safe}</span>`;
      if (line.startsWith('-')) return `<span class="text-red-400">${safe}</span>`;
      return safe;
    }).join('\n');
  }

  async function requestReview(ticketId, action) {
    const label = action === 'merge' ? 'übernehmen' : 'verwerfen';
    if (!window.confirm(`Ergebnis wirklich ${label}?`)) return;
    try {
      await request(`/api/tickets/${encodeURIComponent(ticketId)}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      window.showToast?.('Wird ausgeführt…');
    } catch (error) {
      window.showToast?.(error.message);
    }
    loadTickets();
  }

  async function showFile(ticketId, kind) {
    const target = document.querySelector(`[data-ticket-output="${CSS.escape(ticketId)}"]`);
    if (!target) return;
    if (!target.classList.contains('hidden') && target.dataset.kind === kind) {
      target.classList.add('hidden');
      return;
    }
    target.classList.remove('hidden');
    target.dataset.kind = kind;
    target.textContent = 'Lade…';
    try {
      const data = await request(`/api/tickets/${encodeURIComponent(ticketId)}/${kind}`);
      const hint = data.truncated ? '… Anfang gekürzt …\n\n' : '';
      if (kind === 'diff') {
        target.innerHTML = escapeHtml(hint) + colourDiff(data.text);
      } else {
        target.textContent = hint + data.text;
      }
    } catch (error) {
      target.textContent = error.message;
    }
  }

  document.addEventListener('click', (event) => {
    const decision = event.target.closest('[data-review]');
    if (decision) {
      event.preventDefault();
      requestReview(decision.dataset.ticketId, decision.dataset.review);
      return;
    }
    const file = event.target.closest('[data-ticket-file]');
    if (file) {
      event.preventDefault();
      showFile(file.dataset.ticketId, file.dataset.ticketFile);
      return;
    }
    const open = event.target.closest('[data-release-open]');
    if (open) {
      event.preventDefault();
      openReleaseForm(open.dataset.releaseOpen);
      return;
    }
    const confirm = event.target.closest('[data-release-confirm]');
    if (confirm) {
      event.preventDefault();
      confirmRelease(confirm.dataset.releaseConfirm);
    }
  });

  document.addEventListener('journl:settings-opened', (event) => {
    if (!event.detail?.group || event.detail.group === 'tickets') loadTickets();
  });

  // Nach dem Login steht erst der zweite Aufruf auf Admin-Rechten; der
  // Burger-Button ist der frueheste verlaessliche Zeitpunkt dafuer.
  document.getElementById('burger-btn')?.addEventListener('click', refresh);
  refresh();
})();
