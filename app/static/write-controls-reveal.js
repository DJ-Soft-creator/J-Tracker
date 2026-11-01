/* Desktop: Werkzeugleiste im Schreiben-Tab blendet sich bei Annaeherung ein.
 *
 * Eingeklappt zeigt die Leiste nur „⋯“ und Senden. Faehrt die Maus auf die
 * Leiste zu, wachsen die uebrigen Knoepfe stufenweise auf (CSS-Variable
 * --write-reveal von 0 bis 1, das CSS steht unten). Einmal ganz
 * offen, bleibt die Leiste offen - auch nach einem Klick -, bis die Maus ein
 * Viertel der Bildschirmhoehe oberhalb der Leiste steht. Ein offenes Menue,
 * das Aufnahme-Panel, eine laufende Aufnahme oder Tastaturfokus halten sie
 * ebenfalls offen.
 *
 * Nur fuer feine Zeiger; auf Touch-Geraeten bleibt die Leiste wie gehabt.
 */
(function () {
  const controls = document.querySelector('#tab-write .desktop-controls');
  const fine = window.matchMedia?.('(hover: hover) and (pointer: fine)');
  if (!controls || !fine) return;

  // Das Aussehen gehoert zu diesem Verhalten und steht deshalb hier statt in
  // desktop.html (Vorbild: screensaver.js). Die Grundregeln der Leiste
  // (.write-session-actions, #resume-write-session) bleiben im Template.
  const ITEMS = [
    '.write-session-actions .write-session-action:not(#write-media-button)',
    '.write-session-actions #resume-write-session',
    '#template-select-wrap',
    '#screensaver-resume',
  ];
  const all = (prefix) => ITEMS.map((item) => `${prefix} ${item}`).join(',\n');
  const style = document.createElement('style');
  style.id = 'write-controls-reveal-style';
  style.textContent = `
    @media (hover: hover) and (pointer: fine) {
      .write-session-actions { gap: 0; }
      /* Eingeklappt: width 0. Zuruecklaufen mit kurzer Schonfrist, erst
         ausblenden, dann die Breite einziehen - verblassen statt zuschnappen. */
      ${all('.desktop-controls')} {
        width: 0; min-width: 0; margin: 0; padding: 0; overflow: hidden; opacity: 0; border-width: 0;
        transform: translateX(-.35rem); pointer-events: none;
        transition: opacity .4s ease .2s, transform .45s cubic-bezier(.4,0,.2,1) .2s,
                    width .45s cubic-bezier(.4,0,.2,1) .3s, margin .45s cubic-bezier(.4,0,.2,1) .3s;
      }
      /* Aufziehen ohne Verzoegerung; die kurze Dauer glaettet nur das
         Nachfuehren der Mausbewegung. */
      ${all('.desktop-controls.is-revealed')} {
        width: calc(2.5rem * var(--write-reveal, 1));
        margin-left: calc(.15rem * var(--write-reveal, 1));
        opacity: var(--write-reveal, 1); border-width: 1px; pointer-events: auto;
        transform: translateX(calc(-.35rem * (1 - var(--write-reveal, 1))));
        transition: width .3s cubic-bezier(.22,.61,.36,1), margin .3s cubic-bezier(.22,.61,.36,1),
                    transform .3s cubic-bezier(.22,.61,.36,1), opacity .3s ease;
      }
      .desktop-controls.is-revealed #template-select-wrap { width: calc(2.25rem * var(--write-reveal, 1)); border-width: 0; }
      /* Eine laufende Aufnahme bleibt immer sichtbar. */
      .desktop-controls .write-session-actions .write-session-action.is-recording:not(#write-media-button) {
        width: 2.5rem; margin-left: .15rem; padding: 0; overflow: visible;
        opacity: 1; border-width: 1px; transform: none; pointer-events: auto; transition-delay: 0s;
      }
      /* Beim Bearbeiten ist der Sessions-Knopf nur ein Aufklapper - das
         erledigt hier die Annaeherung. „Abbrechen“ steht im „⋯“-Menue. */
      .write-session-actions.is-source-edit #resume-write-session,
      .desktop-controls #write-edit-cancel { display: none !important; }
    }
    @media (prefers-reduced-motion: reduce) {
      ${all('.desktop-controls')} { transition-duration: .01ms !important; transition-delay: 0s !important; }
    }
    /* Ohne feinen Zeiger (Touch-Desktop) bleibt die Leiste offen; beim
       Bearbeiten klappt der Sessions-Knopf die Werkzeuge auf. */
    @media not ((hover: hover) and (pointer: fine)) {
      .write-session-actions.is-source-edit:not(:focus-within):not(.is-expanded) .write-session-action {
        width: 0; margin: 0; padding: 0; overflow: hidden; opacity: 0; border-width: 0;
        transform: translateX(-.35rem); pointer-events: none;
      }
      .write-session-actions.is-source-edit.is-expanded .write-session-action,
      .write-session-actions.is-source-edit .write-session-action.is-recording {
        width: 2.5rem; margin-left: .15rem; padding: 0; opacity: 1; border-width: 1px;
        transform: none; pointer-events: auto;
      }
      .write-session-actions.is-source-edit .write-session-action.is-recording { overflow: visible; }
    }`;
  document.head.appendChild(style);

  // Ab diesem Abstand (px) beginnt das Einblenden, ab FULL_PX ist alles da.
  const APPROACH_PX = 180;
  const FULL_PX = 16;

  let open = false;
  let level = -1;

  function holdOpen() {
    const voicePanel = document.getElementById('write-voice-panel');
    return Boolean(window.journlWriteMediaMenu?.isOpen?.()
      || (voicePanel && !voicePanel.classList.contains('hidden'))
      || controls.querySelector('.is-recording')
      || controls.querySelector(':focus-visible'));
  }

  function apply(next) {
    if (Math.abs(next - level) < 0.01) return;
    level = next;
    controls.style.setProperty('--write-reveal', next.toFixed(3));
    controls.classList.toggle('is-revealed', next > 0);
  }

  function update(clientY) {
    if (!fine.matches) { apply(1); return; }
    const rect = controls.getBoundingClientRect();
    if (!rect.height) return; // Schreiben-Tab nicht sichtbar
    const distance = clientY < rect.top ? rect.top - clientY
      : clientY > rect.bottom ? clientY - rect.bottom : 0;
    const proximity = Math.max(0, Math.min(1, (APPROACH_PX - distance) / (APPROACH_PX - FULL_PX)));
    if (proximity >= 1) open = true;
    else if (open && clientY < rect.top - window.innerHeight / 4 && !holdOpen()) open = false;
    apply(open || holdOpen() ? 1 : proximity);
  }

  document.addEventListener('pointermove', (event) => {
    if (event.pointerType === 'touch') return;
    update(event.clientY);
  }, { passive: true });

  // Tastatur: wer per Tab in die Leiste springt, sieht sie sofort ganz.
  controls.addEventListener('focusin', () => {
    if (controls.querySelector(':focus-visible')) { open = true; apply(1); }
  });

  apply(fine.matches ? 0 : 1);
})();
