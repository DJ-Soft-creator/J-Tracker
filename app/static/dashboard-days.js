/* Heute tab: append earlier journal days below the current one.
 *
 * AI maintenance contract: this module owns the "Vortag laden" footer and the
 * request for the next earlier day. The day markup itself stays in the active
 * shell, because desktop and mobile use different card classes — this module
 * calls the global `dashboardDayHtml(entries, date, emptyText)` that
 * `desktop-app-core.js` / `mobile-app-core.js` provide. Loading a day never
 * replaces the view, so the reading position survives.
 */
(function () {
  let oldestLoaded = null;
  let loading = false;

  function escapeAttr(value) {
    return typeof window.escapeHtmlAttr === 'function' ? window.escapeHtmlAttr(value) : String(value);
  }

  function escapeText(value) {
    return typeof window.escapeHtml === 'function' ? window.escapeHtml(value) : String(value);
  }

  function mount(oldestDate) {
    oldestLoaded = oldestDate;
    document.getElementById('dashboard-more')?.remove();
    const container = document.getElementById('dashboard-container');
    if (!container || !oldestDate) return;
    container.insertAdjacentHTML('beforeend', `
      <div id="dashboard-more" class="flex justify-center pt-4 pb-2">
        <button type="button" data-dashboard-more
          class="rounded-full border border-gray-800 px-4 py-1.5 text-xs text-gray-600 transition hover:border-gray-700 hover:text-gray-400">
          Vortag laden
        </button>
      </div>`);
  }

  async function loadPrevious() {
    if (loading || !oldestLoaded) return;
    loading = true;
    const footer = document.getElementById('dashboard-more');
    const button = footer?.querySelector('button');
    if (button) { button.disabled = true; button.textContent = 'Lade…'; }
    try {
      const response = await window.apiFetch('/api/dashboard/previous?before=' + encodeURIComponent(oldestLoaded));
      if (response.status === 401) { window.showLogin?.(); return; }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || 'Vortag konnte nicht geladen werden.');
      if (!data.date) {
        // Nothing older exists. Replace the button instead of leaving one that
        // would keep asking the same question and getting the same answer.
        if (footer) footer.outerHTML = '<p class="pt-4 pb-2 text-center text-xs text-gray-700">Keine älteren Journaltage</p>';
        oldestLoaded = null;
        return;
      }
      const day = `<div class="space-y-3" data-journal-date="${escapeAttr(data.date)}">`
        + '<div class="flex items-center gap-3 pt-4"><span class="h-px flex-1 bg-gray-800"></span>'
        + `<span class="text-xs text-gray-600">${escapeText(data.date)}</span>`
        + '<span class="h-px flex-1 bg-gray-800"></span></div>'
        + window.dashboardDayHtml(data.entries || [], data.date, 'Keine Einträge an diesem Tag.')
        + '</div>';
      footer?.insertAdjacentHTML('beforebegin', day);
      mount(data.date);
    } catch (error) {
      window.showToast?.(error.message || 'Vortag konnte nicht geladen werden.', true);
      if (button) { button.disabled = false; button.textContent = 'Vortag laden'; }
    } finally {
      loading = false;
    }
  }

  document.addEventListener('click', (event) => {
    if (event.target.closest('[data-dashboard-more]')) loadPrevious();
  });

  window.journlDashboardDays = { mount, loadPrevious };
}());
