/* Doppelklick bzw. Doppeltipp auf eine Eintragskarte.
 *
 *   - Heute: oeffnet den Editor (wie „Bearbeiten“ im „⋯“-Menue).
 *   - Brain: der erste Doppelklick klappt eine gekuerzte Karte ganz aus, der
 *     naechste auf dieselbe (nun volle) Karte oeffnet den Editor. Karten ohne
 *     Kuerzung oeffnen gleich den Editor, Nur-lesen-Karten gar nichts.
 *
 * Nutzt nur die vorhandenen Wege (openDashboardWriteEditor, das Ausklappen
 * und den Brain-Editor aus brain-cards.js), damit Pruefungen nicht doppelt
 * entstehen. Klicks auf Knoepfe, Links, Medien und Eingabefelder zaehlen nicht.
 * Auf Touch erkennt das Skript den Doppeltipp selbst; touch-action:
 * manipulation verhindert dort das Zoomen beim zweiten Tipp.
 */
(function () {
  const TODAY_CARD = '[id^="entry-"][data-content]';
  const BRAIN_CARD = '[data-brain-card]';
  const IGNORE = 'button, a, input, textarea, select, label, summary, audio, video, img, [contenteditable="true"], [role="menu"]';
  const TAP_DELAY = 320;
  const TAP_DISTANCE = 30;
  let lastAction = 0;
  let lastTap = null;

  const style = document.createElement('style');
  style.textContent = `${TODAY_CARD}, ${BRAIN_CARD} { touch-action: manipulation; }`;
  document.head.appendChild(style);

  function act(target) {
    if (!(target instanceof Element) || target.closest(IGNORE)) return false;
    // Ein Doppeltipp kann auf iOS zusaetzlich ein dblclick ausloesen - nur einmal handeln.
    if (Date.now() - lastAction < 600) return true;
    const today = target.closest(TODAY_CARD);
    if (today && typeof window.openDashboardWriteEditor === 'function') {
      lastAction = Date.now();
      window.openDashboardWriteEditor(today.id.slice('entry-'.length));
      return true;
    }
    const card = target.closest(BRAIN_CARD);
    if (!card) return false;
    lastAction = Date.now();
    const fingerprint = card.dataset.fingerprint || '';
    const expand = card.querySelector('[data-brain-expand]');
    if (expand && typeof toggleBrainCardExpansion === 'function' && !brainState.expanded.has(fingerprint)) {
      toggleBrainCardExpansion(expand);
      return true;
    }
    // Ohne Bearbeiten-Knopf ist die Karte nur lesbar.
    if (!card.querySelector('[data-brain-edit]')) return true;
    const open = window.openBrainWriteEditor || window.openBrainDocument;
    if (typeof open === 'function') open(card.dataset.docId, fingerprint, card);
    return true;
  }

  document.addEventListener('dblclick', (event) => {
    if (act(event.target)) window.getSelection?.()?.removeAllRanges();
  });

  document.addEventListener('pointerup', (event) => {
    if (event.pointerType !== 'touch') return;
    const card = event.target instanceof Element ? event.target.closest(`${TODAY_CARD}, ${BRAIN_CARD}`) : null;
    if (!card) { lastTap = null; return; }
    const now = Date.now();
    const twice = lastTap && lastTap.card === card && now - lastTap.time < TAP_DELAY
      && Math.hypot(event.clientX - lastTap.x, event.clientY - lastTap.y) < TAP_DISTANCE;
    if (twice) {
      lastTap = null;
      act(event.target);
      return;
    }
    lastTap = { card, time: now, x: event.clientX, y: event.clientY };
  });
})();
