/* Aufzaehlungen im Schreibfeld fortsetzen.
 *
 * Steht die Zeile mit einem Listenzeichen (*, -, +, •, 1. / 1), - [ ]), bekommt
 * die naechste Zeile nach Enter dasselbe Zeichen (Nummern zaehlen hoch,
 * Checkboxen starten leer). Enter auf einem leeren Punkt beendet die Liste.
 *
 * Laeuft ueber beforeinput statt keydown, weil die iOS-Tastatur Enter dort
 * verlaesslich meldet. Hashtag-Auswahl und Cmd/Ctrl+Enter verhindern schon im
 * keydown die Eingabe, dann kommt hier nichts an.
 */
(function () {
  const LIST_LINE = /^(\s*)([-*+•]|\d+[.)])(\s+\[[ xX]\])?(\s+)(.*)$/;

  document.addEventListener('beforeinput', (event) => {
    const field = event.target;
    if (!field || field.id !== 'simple-input') return;
    if (event.inputType !== 'insertLineBreak' && event.inputType !== 'insertParagraph') return;
    if (event.isComposing || field.selectionStart !== field.selectionEnd) return;
    const cursor = field.selectionStart;
    const value = field.value;
    const lineStart = value.lastIndexOf('\n', cursor - 1) + 1;
    const match = LIST_LINE.exec(value.slice(lineStart, cursor));
    if (!match) return;
    const [, indent, marker, checkbox, gap, rest] = match;
    event.preventDefault();
    if (!rest.trim()) {
      // Leerer Punkt: Zeichen entfernen, die Liste ist zu Ende.
      field.setRangeText('', lineStart, cursor, 'end');
    } else {
      const numbered = /^(\d+)([.)])$/.exec(marker);
      const next = numbered ? `${Number(numbered[1]) + 1}${numbered[2]}` : marker;
      field.setRangeText(`\n${indent}${next}${checkbox ? ' [ ]' : ''}${gap}`, cursor, cursor, 'end');
    }
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });
})();
