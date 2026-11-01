/* Oeffnen der Journal-Analyse-Hinweise ueber den Schreiben-Tab.
 *
 * Mobil (index.html, Linie mit data-open-on="longpress"): ein normaler Tipp
 * auf „Schreiben“ wechselt nur den Tab, die Hinweise oeffnen sich erst bei
 * langem Druck - die duenne Linie war zu leicht versehentlich zu treffen.
 * Solange Hinweise da sind, traegt der Tab einen ganzen blauen Rand; die
 * Linie selbst bleibt mobil unsichtbar.
 * Desktop: Klick auf die Linie wie bisher (journal-analysis.js), zusaetzlich
 * Doppelklick auf den Tab.
 *
 * Laedt nach journal-analysis.js; das meldet die Anzahl per
 * `journl:analysis-notify` und oeffnet ueber journlJournalAnalysis.
 */
(function () {
  const line = document.getElementById('analysis-notify');
  const tab = document.getElementById('tab-btn-write');
  if (!line || !tab) return;
  const longPressOnly = line.dataset.openOn === 'longpress';
  const hasNotifications = () => !line.classList.contains('hidden');
  const open = () => window.journlJournalAnalysis?.openNotifications?.();

  if (!longPressOnly) {
    tab.addEventListener('dblclick', (event) => {
      if (!hasNotifications()) return;
      event.preventDefault();
      open();
    });
    return;
  }

  // Der Rand ersetzt die Linie: zwei blaue Indikatoren am selben Tab waren
  // doppelt. Die Linie bleibt als Zustand (hidden = keine Hinweise) erhalten.
  line.style.visibility = 'hidden';
  window.addEventListener('journl:analysis-notify', (event) => {
    const { count, color } = event.detail || {};
    tab.style.boxShadow = count ? `inset 0 0 0 2px ${color}` : '';
    tab.style.borderRadius = count ? '0.75rem' : '';
  });

  let timer = null;
  let start = null;
  let fired = false;
  const cancel = () => { clearTimeout(timer); timer = null; };
  tab.style.webkitTouchCallout = 'none';
  tab.style.userSelect = 'none';
  tab.addEventListener('pointerdown', (event) => {
    fired = false;
    if (!hasNotifications()) return;
    start = { x: event.clientX, y: event.clientY };
    cancel();
    timer = setTimeout(() => {
      timer = null;
      fired = true;
      navigator.vibrate?.(15);
      open();
    }, 500);
  });
  tab.addEventListener('pointermove', (event) => {
    if (timer && start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 10) cancel();
  });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach((type) => tab.addEventListener(type, cancel));
  tab.addEventListener('contextmenu', (event) => { if (hasNotifications()) event.preventDefault(); });
  // Der Klick nach dem langen Druck darf die gerade geoeffneten Hinweise
  // nicht wieder schliessen.
  tab.addEventListener('click', (event) => {
    if (!fired) return;
    fired = false;
    event.stopImmediatePropagation();
    event.preventDefault();
  }, true);
})();
