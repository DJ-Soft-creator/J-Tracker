/* Screenshot upload token settings and the unlinked-media picker in Schreiben. */
(function () {
  const mediaMenu = document.getElementById('write-media-menu');
  const mediaStrip = document.getElementById('write-media-strip');
  const writeSession = window.journlWriteSession;
  if (!mediaMenu || !mediaStrip || !writeSession) return;

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value || '');
    return element.innerHTML;
  }

  async function jsonRequest(url, options = {}) {
    const response = await window.apiFetch(url, { timeoutMs: 15000, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Screenshot-Aktion fehlgeschlagen');
    return data;
  }

  const menuButton = document.createElement('button');
  menuButton.type = 'button';
  menuButton.className = 'block w-full px-3 py-2 text-left text-sm text-sky-200 hover:bg-sky-950/40';
  menuButton.textContent = 'Hochgeladene Screenshots';
  // Gehoert zu den Anhaengen und zieht deshalb in deren Gruppe, damit das
  // Menue auf dem Handy kurz bleibt.
  const attachGroup = mediaMenu.querySelector('[data-menu-group-body="attach"]');
  if (attachGroup) {
    attachGroup.appendChild(menuButton);
  } else {
    const separator = document.createElement('div');
    separator.className = 'my-1 border-t border-gray-700';
    separator.setAttribute('role', 'separator');
    mediaMenu.append(separator, menuButton);
  }

  const suggestion = document.createElement('button');
  suggestion.type = 'button';
  suggestion.id = 'write-screenshot-inbox-suggestion';
  suggestion.className = 'hidden mx-auto mb-1 rounded-full border border-sky-900/60 bg-sky-950/20 px-3 py-1 text-xs text-sky-300 hover:border-sky-700 hover:text-sky-200';
  suggestion.setAttribute('aria-live', 'polite');
  // Der Vorschlag haengt unter der ganzen Anhang-Huelle, nicht in ihr:
  // eingeklappte Anhaenge duerfen ihn nicht mitverstecken.
  (mediaStrip.closest('#write-media-shell') || mediaStrip).insertAdjacentElement('afterend', suggestion);

  const panel = document.createElement('div');
  panel.className = 'hidden fixed inset-0 z-50 items-center justify-center bg-black/60 px-4';
  panel.innerHTML = `
    <div class="w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-4">
      <div class="flex items-center justify-between gap-3">
        <div><h2 class="text-sm font-semibold text-gray-100">Hochgeladene Screenshots</h2><p data-summary class="mt-0.5 text-xs text-gray-500"></p></div>
        <button type="button" data-close class="rounded px-2 py-1 text-xl text-gray-400 hover:bg-gray-800" aria-label="Schließen">×</button>
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <label class="text-xs text-gray-400">Datum <input data-date type="date" class="ml-1 rounded border border-gray-700 bg-gray-800 px-2 py-1 text-gray-200"></label>
        <button type="button" data-all class="rounded border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:bg-gray-800">Alle offenen</button>
      </div>
      <div data-list class="mt-3 grid gap-2"><p class="py-5 text-center text-sm text-gray-500">Lade…</p></div>
    </div>`;
  document.body.appendChild(panel);

  let counts = { today: 0, total: 0 };
  let selectedDate = null;
  let lastRefresh = 0;

  function localToday() {
    const now = new Date();
    const part = value => String(value).padStart(2, '0');
    return `${now.getFullYear()}-${part(now.getMonth() + 1)}-${part(now.getDate())}`;
  }

  function formatTime(value) {
    try { return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value)); }
    catch (_) { return value || ''; }
  }

  function renderCounts() {
    suggestion.classList.toggle('hidden', counts.today === 0);
    suggestion.textContent = counts.today === 1
      ? '1 neuer Screenshot von heute'
      : `${counts.today} neue Screenshots von heute`;
    menuButton.textContent = counts.total
      ? `Hochgeladene Screenshots (${counts.total})`
      : 'Hochgeladene Screenshots';
  }

  function renderItems(data) {
    counts = { today: data.today_count || 0, total: data.total_count || 0 };
    renderCounts();
    panel.querySelector('[data-summary]').textContent = counts.total
      ? `${counts.total} noch nicht verlinkt`
      : 'Keine unverlinkten Uploads';
    const list = panel.querySelector('[data-list]');
    if (!data.items?.length) {
      list.innerHTML = '<p class="py-5 text-center text-sm text-gray-500">Für diese Auswahl gibt es keinen offenen Screenshot.</p>';
      return;
    }
    list.innerHTML = data.items.map(item => `
      <article class="flex items-center gap-3 rounded-lg border border-gray-800 bg-gray-950/40 p-2" data-upload-id="${escapeHtml(item.id)}">
        <button type="button" data-journal-image-viewer="/api/screenshot-inbox/items/${encodeURIComponent(item.id)}/preview" data-journal-image-alt="Screenshot" class="shrink-0 cursor-zoom-in" aria-label="Screenshot in der App öffnen">
          <img src="/api/screenshot-inbox/items/${encodeURIComponent(item.id)}/preview" loading="lazy" class="h-16 w-16 rounded object-cover" alt="Screenshot-Vorschau">
        </button>
        <div class="min-w-0 flex-1"><div class="truncate text-sm text-gray-200">${escapeHtml(item.original_filename || 'Screenshot')}</div><div class="text-xs text-gray-500">${escapeHtml(formatTime(item.received_at))}</div></div>
        <div class="flex shrink-0 flex-col gap-1"><button type="button" data-attach class="rounded bg-sky-900/60 px-3 py-1.5 text-xs text-sky-100 hover:bg-sky-800">Aufnehmen</button><button type="button" data-delete class="rounded px-3 py-1.5 text-xs text-red-300 hover:bg-red-950">Löschen</button></div>
      </article>`).join('');
  }

  async function loadItems(date = selectedDate) {
    const requested = date || localToday();
    selectedDate = requested;
    const data = await jsonRequest(`/api/screenshot-inbox/items?date=${encodeURIComponent(requested)}`, { cache: 'no-store' });
    if (requested !== 'all') panel.querySelector('[data-date]').value = requested;
    renderItems(data);
    lastRefresh = Date.now();
    return data;
  }

  async function openPanel(date) {
    mediaMenu.classList.add('hidden');
    panel.classList.remove('hidden');
    panel.classList.add('flex');
    panel.querySelector('[data-list]').innerHTML = '<p class="py-5 text-center text-sm text-gray-500">Lade…</p>';
    try { await loadItems(date || localToday()); }
    catch (error) { panel.querySelector('[data-list]').innerHTML = `<p class="py-5 text-center text-sm text-red-300">${escapeHtml(error.message)}</p>`; }
  }

  function closePanel() {
    panel.classList.add('hidden');
    panel.classList.remove('flex');
  }

  menuButton.addEventListener('click', () => openPanel(null));
  suggestion.addEventListener('click', () => openPanel(null));
  panel.querySelector('[data-close]').addEventListener('click', closePanel);
  panel.addEventListener('click', async (event) => {
    if (event.target === panel) { closePanel(); return; }
    const button = event.target.closest('[data-attach]');
    const deleteButton = event.target.closest('[data-delete]');
    const deleteRow = deleteButton?.closest('[data-upload-id]');
    if (deleteButton && deleteRow) {
      if (!window.confirm('Diesen neuen Screenshot wirklich löschen? Er kann danach nicht wiederhergestellt werden.')) return;
      deleteButton.disabled = true;
      deleteButton.textContent = 'Wird gelöscht…';
      try {
        await jsonRequest(`/api/screenshot-inbox/items/${encodeURIComponent(deleteRow.dataset.uploadId)}`, { method: 'DELETE' });
        window.showToast?.('Screenshot gelöscht.');
        await loadItems(selectedDate);
        if (!counts.total) closePanel();
      } catch (error) {
        window.showToast?.(error.message || 'Screenshot konnte nicht gelöscht werden.', true);
        deleteButton.disabled = false;
        deleteButton.textContent = 'Löschen';
      }
      return;
    }
    const row = button?.closest('[data-upload-id]');
    if (!button || !row) return;
    button.disabled = true;
    button.textContent = 'Wird übernommen…';
    try {
      await writeSession.attachInboxItem(row.dataset.uploadId);
      window.showToast?.(window.isWriteSourceEdit?.()
        ? 'Screenshot wird beim Speichern in den Eintrag übernommen.'
        : 'Screenshot wird beim Senden ins Journal übernommen.');
      await loadItems(selectedDate);
      if (!counts.total) closePanel();
    } catch (error) {
      window.showToast?.(error.message || 'Screenshot konnte nicht übernommen werden.', true);
      button.disabled = false;
      button.textContent = 'Aufnehmen';
    }
  });
  panel.querySelector('[data-date]').addEventListener('change', event => loadItems(event.target.value).catch(() => {}));
  panel.querySelector('[data-all]').addEventListener('click', () => loadItems('all').catch(() => {}));

  async function refreshCounts(force = false) {
    if (!force && Date.now() - lastRefresh < 30000) return;
    try {
      const data = await jsonRequest(`/api/screenshot-inbox/items?date=${encodeURIComponent(localToday())}`, { cache: 'no-store' });
      counts = { today: data.today_count || 0, total: data.total_count || 0 };
      renderCounts();
      lastRefresh = Date.now();
    }
    catch (_) { /* Writing remains available while offline or logged out. */ }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshCounts(true);
  });
  window.addEventListener('focus', () => refreshCounts(true));
  window.addEventListener('journl:screenshot-inbox-change', () => refreshCounts(true));
  const inboxPollTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') refreshCounts();
  }, 30000);
  window.addEventListener('pagehide', () => window.clearInterval(inboxPollTimer));

  const tokenStatus = document.getElementById('settings-screenshot-token-status');
  const tokenCreate = document.getElementById('settings-screenshot-token-create');
  const tokenRevoke = document.getElementById('settings-screenshot-token-revoke');
  const tokenReveal = document.getElementById('settings-screenshot-token-reveal');
  const tokenValue = document.getElementById('settings-screenshot-token-value');
  const endpointValue = document.getElementById('settings-screenshot-endpoint');
  if (endpointValue) endpointValue.value = `${window.location.origin}/api/screenshot-inbox/upload`;

  async function loadTokenStatus() {
    if (!tokenStatus) return;
    try {
      const data = await jsonRequest('/api/screenshot-inbox/token', { cache: 'no-store' });
      tokenStatus.textContent = data.enabled
        ? `Aktiv${data.last_used_at ? ` · zuletzt benutzt ${formatTime(data.last_used_at)}` : ''}`
        : 'Noch kein Upload-Schlüssel eingerichtet.';
      tokenCreate.textContent = data.enabled ? 'Neuen Schlüssel erzeugen' : 'Schlüssel erzeugen';
      tokenRevoke.classList.toggle('hidden', !data.enabled);
    } catch (_) { tokenStatus.textContent = 'Status nicht verfügbar.'; }
  }

  tokenCreate?.addEventListener('click', async () => {
    if (!tokenRevoke.classList.contains('hidden') && !window.confirm('Der bisherige Upload-Schlüssel funktioniert danach sofort nicht mehr. Fortfahren?')) return;
    tokenCreate.disabled = true;
    try {
      const data = await jsonRequest('/api/screenshot-inbox/token', { method: 'POST', body: '{}' });
      tokenValue.value = data.token;
      tokenReveal.classList.remove('hidden');
      await loadTokenStatus();
    } catch (error) { window.showToast?.(error.message, true); }
    finally { tokenCreate.disabled = false; }
  });
  tokenRevoke?.addEventListener('click', async () => {
    if (!window.confirm('Upload-Schlüssel widerrufen? Der iPhone-Kurzbefehl kann danach nicht mehr hochladen.')) return;
    try {
      await jsonRequest('/api/screenshot-inbox/token', { method: 'DELETE' });
      tokenReveal.classList.add('hidden');
      tokenValue.value = '';
      await loadTokenStatus();
    } catch (error) { window.showToast?.(error.message, true); }
  });
  document.querySelectorAll('[data-copy-screenshot-value]').forEach(button => button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copyScreenshotValue);
    try { await navigator.clipboard.writeText(input.value); window.showToast?.('Kopiert.'); }
    catch (_) { input.select(); document.execCommand('copy'); window.showToast?.('Kopiert.'); }
  }));
  document.addEventListener('journl:settings-opened', (event) => {
    if (!event.detail?.group || event.detail.group === 'integrations') loadTokenStatus();
  });

  refreshCounts(true);
  loadTokenStatus();
})();
