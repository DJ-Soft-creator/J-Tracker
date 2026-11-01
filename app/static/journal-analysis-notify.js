/* Journal-Analyse-Hinweise: Linie/Fenster im Schreiben-Tab und Reiter
 * „Hinweise“ in der Verwaltung.
 *
 * Das Fenster im Schreiben-Tab zeigt fällige offene/ignorierte Hinweise und
 * kann sie mit „Alle schließen“ auf einmal abhaken. Die Verwaltung zeigt alles
 * der letzten RETENTION_DAYS Tage, auch Geschlossenes (zum Nachschauen und
 * Wieder-öffnen); ältere Hinweise stehen nur noch im Protokoll.
 *
 * Klassisches Script, vor `journal-analysis.js` geladen; stellt
 * `window.JournlAnalysisNotify` bereit (loadPanel, open).
 */
(function () {
  const ENDPOINT = '/api/journal-analysis';
  // Muss zu NOTIFICATION_RETENTION_DAYS in journal_analysis.py passen.
  const RETENTION_DAYS = 30;
  const NOTIFY_STATUS_LABEL = { open: 'offen', ignored: 'ignoriert', done: 'abgehakt', taken: 'übernommen' };
  let panelItems = [];

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function request(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 20000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Abruf fehlgeschlagen');
    return data;
  }

  function localStamp(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 16).replace('T', ' ');
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'short', timeStyle: 'short' }).format(date);
  }

  // Zweite, eigenständige Linie neben `#worker-notify`, bewusst für alle
  // Nutzer. Blau wie die Familie-Meldung bei „nur zu sichten"; der Klick
  // öffnet einen kleinen View. Ignorierte Hinweise bleiben sichtbar.
  const NOTIFY_BLUE = '#2563eb';

  function appendToWrite(text) {
    const input = document.getElementById('simple-input');
    if (!input) return false;
    const current = input.value || '';
    const separator = current && !current.endsWith('\n') ? '\n\n' : '';
    input.value = current + separator + text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    return true;
  }

  function plannedLabel(item) {
    if (!item.notify_at) return '';
    const millis = Date.parse(item.notify_at);
    if (Number.isNaN(millis) || millis <= Date.now()) return '';
    const text = new Date(millis).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
    return `<span class="ml-1 rounded bg-amber-900/30 px-1.5 text-[10px] text-amber-300">Geplant am ${escapeHtml(text)}</span>`;
  }

  // Datum + Uhrzeit der Meldung: fällige Erinnerungen zeigen ihren
  // Zeitpunkt, Lauf-Zusammenfassungen den Entstehungszeitpunkt. Der
  // Journaltag steht dahinter, weil er vom Anzeigezeitpunkt abweichen kann.
  function notificationMeta(item) {
    const parts = [];
    const when = localStamp(item.notify_at || item.created_at);
    if (when) parts.push(`${when} Uhr`);
    if (item.run_date) {
      const day = new Date(`${String(item.run_date).slice(0, 10)}T12:00:00`);
      parts.push(`Journaltag ${Number.isNaN(day.getTime()) ? item.run_date
        : day.toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit', year: '2-digit' })}`);
    }
    return parts.join(' · ');
  }

  function notificationItem(item) {
    const ignored = item.status === 'ignored';
    const closed = item.status === 'done' || item.status === 'taken';
    const button = (action, label, extra = 'bg-gray-700 hover:bg-gray-600') => `<button type="button" data-analysis-notify-action="${action}" data-analysis-notify-id="${escapeAttr(item.id)}"
          class="rounded ${extra} px-2 py-1 text-white">${label}</button>`;
    const actions = closed
      ? button('taken', 'In den Schreiben-Tab') + button('open', 'Wieder öffnen')
      : button('taken', 'In den Schreiben-Tab', 'bg-green-700 font-semibold hover:bg-green-600')
        + button('ignored', 'Ignorieren') + button('done', 'Abhaken');
    return `<li class="rounded-lg border border-gray-800 px-3 py-2${closed ? ' opacity-60' : ''}" data-notify-item="${escapeAttr(item.id)}">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="text-sm text-gray-200">${escapeHtml(item.title || 'Journal-Analyse')}
            ${ignored || closed ? `<span class="text-xs text-gray-500">· ${escapeHtml(NOTIFY_STATUS_LABEL[item.status])}</span>` : ''}
            ${item.kind === 'error' ? '<span class="text-xs text-red-400">· Fehler</span>' : ''}
            ${item.kind === 'reminder' ? '<span class="text-xs text-blue-400">· Erinnerung</span>' : ''}
            ${plannedLabel(item)}</p>
          <p class="text-xs text-gray-500">${escapeHtml(notificationMeta(item))}</p>
          <p class="mt-1 whitespace-pre-wrap text-xs text-gray-400">${escapeHtml(String(item.body || '').slice(0, 4000))}</p>
        </div>
      </div>
      <div class="mt-2 flex flex-wrap gap-2 text-xs">${actions}</div>
    </li>`;
  }

  function notificationView() {
    let view = document.getElementById('analysis-notify-view');
    if (view) return view;
    view = document.createElement('div');
    view.id = 'analysis-notify-view';
    view.className = 'fixed inset-0 z-[60] hidden items-center justify-center bg-black/50 px-4';
    view.innerHTML = `<div class="w-full max-w-lg max-h-[80vh] overflow-y-auto rounded-xl border border-gray-700 bg-gray-900 p-4">
      <div class="mb-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-gray-100">Journal-Analyse</h2>
        <div class="flex items-center gap-3">
          <button type="button" data-analysis-notify-close-all class="hidden rounded bg-gray-700 px-2 py-1 text-xs text-white hover:bg-gray-600">Alle schließen</button>
          <button type="button" data-analysis-notify-close class="text-gray-500 hover:text-white text-xl leading-none">×</button>
        </div>
      </div>
      <div data-analysis-notify-list class="space-y-2 text-sm text-gray-300"></div>
      <p class="mt-3 text-[11px] text-gray-500">Geschlossene Hinweise bleiben ${RETENTION_DAYS} Tage unter Journal Analyse → Hinweise nachlesbar.</p>
    </div>`;
    document.body.appendChild(view);
    view.addEventListener('click', (event) => {
      if (event.target.closest('[data-analysis-notify-close-all]')) {
        closeAllNotifications();
        return;
      }
      if (event.target === view || event.target.closest('[data-analysis-notify-close]')) closeNotificationView();
    });
    return view;
  }

  function renderNotificationView(items) {
    const host = notificationView().querySelector('[data-analysis-notify-list]');
    if (!host) return;
    host.innerHTML = items.length
      ? items.map(notificationItem).join('')
      : '<p class="text-gray-500">Keine offenen Hinweise.</p>';
    notificationView().querySelector('[data-analysis-notify-close-all]')?.classList.toggle('hidden', !items.length);
  }

  async function closeAllNotifications() {
    try {
      const data = await request(`${ENDPOINT}/notifications/close-all`, { method: 'POST', body: '{}' });
      closeNotificationView();
      window.showToast?.(`${data.closed || 0} Hinweis(e) geschlossen.`);
      await refreshNotificationViews();
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  function openNotificationView(items) {
    renderNotificationView(items || []);
    notificationView().classList.remove('hidden');
    notificationView().classList.add('flex');
  }

  function closeNotificationView() {
    const view = document.getElementById('analysis-notify-view');
    view?.classList.add('hidden');
    view?.classList.remove('flex');
  }

  async function loadPanelNotifications(body) {
    if (!body.querySelector('[data-analysis-notify-panel-list]')) {
      body.innerHTML = `<div class="mb-1 text-xs text-gray-500">Hinweise der letzten ${RETENTION_DAYS} Tage, auch geschlossene. Fällige offene erscheinen zusätzlich im Schreiben-Tab; ältere stehen nur noch im Protokoll.</div>`
        + '<div data-analysis-notify-panel-list class="space-y-2"></div>';
    }
    const host = body.querySelector('[data-analysis-notify-panel-list]');
    if (!host) return;
    let data;
    try {
      data = await request(`${ENDPOINT}/notifications?status=recent`);
    } catch (_) {
      host.innerHTML = '<p class="text-gray-500">Abruf fehlgeschlagen.</p>';
      return;
    }
    const stamp = (value) => {
      if (!value) return 0;
      const millis = Date.parse(value);
      return Number.isNaN(millis) ? 0 : millis;
    };
    // Offene/ignorierte zuerst (älteste oben, Geplantes am Ende), danach die
    // geschlossenen der letzten Tage, neueste oben.
    const isClosed = (item) => item.status === 'done' || item.status === 'taken';
    const when = (item) => stamp(item.notify_at || item.created_at);
    panelItems = (data.notifications || []).slice().sort((a, b) => (isClosed(a) - isClosed(b))
      || (isClosed(a) ? when(b) - when(a) : when(a) - when(b)));
    host.innerHTML = panelItems.length
      ? panelItems.map(notificationItem).join('')
      : '<p class="text-gray-500">Keine Hinweise.</p>';
  }

  async function loadNotifications() {
    const line = document.getElementById('analysis-notify');
    if (!line) return [];
    let data;
    try {
      data = await request(`${ENDPOINT}/notifications?status=active`);
    } catch (_) {
      return [];
    }
    const items = data.notifications || [];
    const open = items.filter((item) => item.status === 'open').length;
    line.classList.toggle('hidden', items.length === 0);
    // Rand/Langdruck am Schreiben-Tab: analysis-notify-trigger.js.
    window.dispatchEvent(new CustomEvent('journl:analysis-notify', { detail: { count: items.length, color: NOTIFY_BLUE } }));
    const dot = line.querySelector('[data-analysis-dot]');
    if (dot) dot.style.backgroundColor = NOTIFY_BLUE;
    line._items = items;
    line.title = open ? `${open} neue Journal-Analyse-Hinweis(e)` : 'Journal-Analyse-Hinweise';
    if (document.getElementById('analysis-notify-view')?.classList.contains('flex')) {
      renderNotificationView(items);
    }
    return items;
  }

  async function refreshNotificationViews() {
    await loadNotifications();
    const body = document.querySelector('[data-analysis-panel="notifications"]');
    if (body && !body.classList.contains('hidden')) loadPanelNotifications(body);
  }

  async function runNotificationAction(notificationId, action) {
    if (action === 'taken') {
      const items = notifyLine?._items?.length ? notifyLine._items
        : panelItems.length ? panelItems
          : (await request(`${ENDPOINT}/notifications?status=recent`)).notifications || [];
      const item = items.find((entry) => entry.id === notificationId);
      if (item && !appendToWrite(item.body || item.title || '')) {
        window.showToast?.('Kein Schreibfeld gefunden.', true);
        return;
      }
    }
    try {
      await request(`${ENDPOINT}/notifications/${encodeURIComponent(notificationId)}/status`, {
        method: 'POST', body: JSON.stringify({ status: action }),
      });
      if (action === 'taken') {
        window.showToast?.('Text übernommen.');
        closeNotificationView();
      }
      await refreshNotificationViews();
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  const notifyLine = document.getElementById('analysis-notify');
  const openNotifications = () => {
    openNotificationView(notifyLine?._items || []);
    loadNotifications().then((items) => openNotificationView(items));
  };
  // Mobil (data-open-on="longpress") nur per langem Druck auf den Tab.
  if (notifyLine && notifyLine.dataset.openOn !== 'longpress') {
    notifyLine.addEventListener('click', (event) => {
      event.stopPropagation();
      event.preventDefault();
      openNotifications();
    });
    notifyLine.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      event.stopPropagation();
      openNotifications();
    });
  }
  document.addEventListener('click', (event) => {
    const action = event.target.closest('[data-analysis-notify-action]');
    if (!action) return;
    event.preventDefault();
    runNotificationAction(action.dataset.analysisNotifyId, action.dataset.analysisNotifyAction);
  });
  document.getElementById('burger-btn')?.addEventListener('click', () => { loadNotifications(); });
  loadNotifications();
  setInterval(loadNotifications, 60000);
  // Im Hintergrund drosselt iOS den Timer; eine heute faellige Erinnerung soll
  // beim Zurueckkehren in die App sofort da sein, nicht erst nach einer Minute.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') loadNotifications();
  });

  window.JournlAnalysisNotify = {
    loadPanel: loadPanelNotifications, open: openNotifications,
  };
})();
