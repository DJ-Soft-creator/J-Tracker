/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Ein Agentenfenster ist `fixed inset-0` — es misst sich also am Layout-
 * Viewport. Auf dem Handy schrumpft der aber nicht, wenn die Tastatur
 * aufklappt: das Fenster behaelt seine volle Hoehe, die Eingabezeile am
 * unteren Rand rutscht unter die Tastatur, und man sieht nicht mehr, was man
 * tippt. Genau das war der Befund am Einkaufslisten-Agenten.
 *
 * Was tatsaechlich schrumpft, ist der *visuelle* Viewport. Dieses Modul
 * spannt ein gebundenes Fenster auf `visualViewport` statt auf den
 * Layout-Viewport — damit liegt die Eingabezeile wieder direkt ueber der
 * Tastatur, ohne dass ein Fenster eigenes Tastatur-Wissen braucht.
 *
 * Ohne `visualViewport` (aeltere Browser, Desktop ohne Tastaturleiste)
 * passiert nichts: das Fenster behaelt sein CSS und sieht aus wie bisher.
 */
(function () {
  const viewport = window.visualViewport || null;
  const bound = new Set();

  function fit(panel) {
    if (!viewport || panel.classList.contains('hidden')) return;
    // offsetTop faengt den Fall ab, dass iOS die ganze Seite hochschiebt,
    // statt nur den sichtbaren Bereich zu verkleinern.
    panel.style.height = `${Math.round(viewport.height)}px`;
    panel.style.top = `${Math.round(viewport.offsetTop)}px`;
    panel.style.bottom = 'auto';
  }

  function reset(panel) {
    panel.style.height = '';
    panel.style.top = '';
    panel.style.bottom = '';
  }

  function fitAll() {
    for (const panel of bound) {
      if (panel.classList.contains('hidden')) reset(panel);
      else fit(panel);
    }
  }

  if (viewport) {
    viewport.addEventListener('resize', fitAll);
    viewport.addEventListener('scroll', fitAll);
  }

  window.journlAgentViewport = {
    /** Ein Fenster dauerhaft an den sichtbaren Viewport binden. */
    bind(panel) {
      if (!panel || !viewport || bound.has(panel)) return;
      bound.add(panel);
      // Die Tastatur meldet sich erst, nachdem das Feld den Fokus hat — ein
      // Klick ins Eingabefeld muss deshalb ein zweites Mal nachmessen.
      panel.addEventListener('focusin', () => setTimeout(() => fit(panel), 120));
    },
    /** Beim Oeffnen einmal messen, beim Schliessen das CSS zurueckgeben. */
    show(panel) {
      if (panel) fit(panel);
    },
    hide(panel) {
      if (panel) reset(panel);
    },
  };
})();
