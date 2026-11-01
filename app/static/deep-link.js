/* Deep-Links aus Benachrichtigungen (Pushover): `?tab=family`,
   `?tab=write&open=notifications` bzw. `?open=tagging` (Protokoll von
   „Hashtags nachtragen“ in der Journal-Analyse-Verwaltung).

   Wirkt einmal, sobald die App nach dem Login sichtbar ist (beide Shells
   blenden `#app` erst dann ein), und raeumt die Parameter danach aus der
   Adresszeile, damit ein Neuladen nicht erneut springt. */
(function () {
  const params = new URLSearchParams(window.location.search);
  const tab = params.get('tab');
  const open = params.get('open');
  if (!tab && !open) return;
  const app = document.getElementById('app');
  if (!app) return;
  const TABS = ['write', 'dashboard', 'family', 'brain'];

  let done = false;
  function apply() {
    if (done || app.classList.contains('hidden')) return;
    done = true;
    observer.disconnect();
    // Kurz warten: der Start waehlt selbst noch den Schreiben-Tab.
    setTimeout(() => {
      if (TABS.includes(tab) && typeof window.switchTab === 'function') window.switchTab(tab);
      if (open === 'notifications') window.journlJournalAnalysis?.openNotifications?.();
      if (open === 'tagging') window.journlJournalAnalysis?.openTaggingLog?.();
      const url = new URL(window.location.href);
      url.searchParams.delete('tab');
      url.searchParams.delete('open');
      window.history.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
    }, 600);
  }
  const observer = new MutationObserver(apply);
  observer.observe(app, { attributes: true, attributeFilter: ['class'] });
  apply();
}());
