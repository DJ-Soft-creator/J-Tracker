// AI maintenance contract: classic script, shared by both app shells.
// Owns the Lese-/Schreibmodus of the Schreiben tab: the "Ansicht" entry in the
// ⋯-Menü (built by write-session.js, hence the load order) switches the primary
// editor between the raw textarea, a rendered Markdown view and - on the
// desktop shell - both next to each other.
//
// Why a rendered view at all: a textarea shows a link as text. Notes and family
// documents are read far more often than they are written, and there the
// address has to be clickable. Rendering goes through window.renderJournalText
// (journal-highlighting.js), the same renderer the journal cards use, so the
// Schreiben tab never grows a second Markdown dialect.

(function () {
  const field = document.getElementById('simple-input');
  const menu = document.getElementById('write-media-menu');
  if (!field || !menu) return;

  // write-hashtags.js has already wrapped the textarea in its highlight
  // container at this point; the pane has to sit next to that wrapper, not
  // inside it.
  const editor = field.closest('.write-hashtag-editor') || field;
  // Only the desktop shell has a sized surface that can hold two columns.
  const surface = document.getElementById('input-surface');
  const MODES = surface ? ['edit', 'read', 'split'] : ['edit', 'read'];
  const LABELS = { edit: 'Bearbeiten', read: 'Lesen', split: 'Nebeneinander' };
  const TITLES = {
    edit: 'Nur das Textfeld zeigen',
    read: 'Markdown gerendert lesen, Links anklickbar',
    split: 'Textfeld und Lesefassung nebeneinander',
  };

  const pane = document.createElement('div');
  pane.className = 'write-read-pane hidden';
  pane.id = 'write-read-pane';
  pane.setAttribute('aria-label', 'Lesefassung');
  editor.parentNode.insertBefore(pane, editor.nextSibling);

  const section = document.createElement('section');
  section.className = 'px-2 py-1.5';
  section.setAttribute('aria-label', 'Ansicht');
  section.innerHTML = '<div class="mb-1 text-[11px] font-medium uppercase tracking-wide text-gray-500">Ansicht</div>'
    + `<div class="flex items-center gap-1">${MODES.map(mode => (
      `<button type="button" data-read-mode="${mode}" title="${TITLES[mode]}"`
      + ' class="flex-1 rounded px-2 py-1.5 text-xs text-gray-300 hover:bg-gray-800">'
      + `${LABELS[mode]}</button>`
    )).join('')}</div>`;
  const separator = document.createElement('div');
  separator.className = 'my-1 border-t border-gray-700';
  separator.setAttribute('role', 'separator');
  // Die Schriftgroesse steht in der aufklappbaren Gruppe "Darstellung"; die
  // Ansicht gehoert daneben und nicht auf die oberste Menueebene.
  const fontSection = menu.querySelector('[data-font-section]');
  if (fontSection) {
    fontSection.insertAdjacentElement('beforebegin', section);
    fontSection.insertAdjacentElement('beforebegin', separator);
  } else {
    menu.append(section, separator);
  }

  /* Womit die App startet, sagt allein die Einstellung "Ansicht beim Start"
     unter Einstellungen -> Schreiben. Das Umschalten im ⋯-Menue gilt dagegen nur
     fuer die laufende Sitzung und wird nicht gemerkt.

     Der Unterschied ist der Kern dieses Moduls: die Lesefassung sieht aus wie
     das Schreibfeld, nimmt aber keine Eingabe an. Als der zuletzt benutzte Modus
     noch automatisch zurueckkam, stand nach einem Fehlgriff ein scheinbar toter
     Schreiben-Tab da - auf dem Handy half auch das Abschiessen der PWA nicht
     mehr heraus. Ein Zustand, in dem man nicht schreiben kann, darf nur
     wiederkehren, wenn jemand ihn ausdruecklich als Start gewaehlt hat, und dann
     an der Stelle stehen, an der man ihn auch wieder los wird.

     Die Wahl haengt am Geraet, nicht am Konto: "Nebeneinander" gibt es nur auf
     dem Desktop, und dasselbe Konto schreibt auf dem Handy in einer Spalte. */
  const START_KEY = 'journl.write.start-view';

  function startMode() {
    try {
      const value = window.localStorage.getItem(START_KEY);
      return MODES.includes(value) ? value : 'edit';
    } catch (_) { return 'edit'; }
  }

  function setStartMode(next) {
    const value = MODES.includes(next) ? next : 'edit';
    try { window.localStorage.setItem(START_KEY, value); } catch (_) { /* privates Fenster */ }
    return value;
  }

  let mode = startMode();

  // A form template replaces the textarea with its own fields; the reading
  // pane has nothing to show then and must not take up the space.
  function editorIsHidden() {
    return field.style.display === 'none';
  }

  function render() {
    const text = field.value || '';
    if (!text.trim()) {
      pane.innerHTML = '<span class="text-gray-600">Noch nichts geschrieben.</span>';
      return;
    }
    if (typeof window.renderJournalText === 'function') pane.innerHTML = window.renderJournalText(text);
    else pane.textContent = text;
    // Neu gerendert heisst: die Lesefassung steht wieder oben. Nebeneinander
    // soll sie aber zeigen, was links gerade geschrieben wird.
    if (isSplit()) follow(field, pane);
  }

  // Nebeneinander laufen beide Spalten synchron: wer links schreibt, will
  // rechts das Kontrollbild derselben Stelle sehen. Verglichen wird anteilig,
  // weil gerendertes Markdown nie genauso hoch ist wie sein Quelltext.
  function isSplit() {
    return Boolean(surface && surface.classList.contains('is-split'));
  }

  function scrollRatio(element) {
    const range = element.scrollHeight - element.clientHeight;
    return range > 0 ? element.scrollTop / range : 0;
  }

  let followLock = false;
  function follow(source, target) {
    if (!isSplit() || followLock) return;
    followLock = true;
    const range = target.scrollHeight - target.clientHeight;
    target.scrollTop = range > 0 ? scrollRatio(source) * range : 0;
    // Das gesetzte scrollTop loest selbst ein scroll-Event aus; erst danach
    // darf die Gegenrichtung wieder greifen, sonst schaukeln sich beide auf.
    window.requestAnimationFrame(() => { followLock = false; });
  }

  field.addEventListener('scroll', () => follow(field, pane));
  pane.addEventListener('scroll', () => follow(pane, field));

  function apply({ focus = false } = {}) {
    const active = editorIsHidden() ? 'edit' : mode;
    const showPane = active !== 'edit';
    const hideEditor = active === 'read';
    pane.classList.toggle('hidden', !showPane);
    editor.classList.toggle('hidden', hideEditor);
    if (surface) surface.classList.toggle('is-split', active === 'split');
    // Follow the font size the ⋯-Menü sets on the textarea.
    pane.style.fontSize = field.style.fontSize || '';
    for (const button of menu.querySelectorAll('[data-read-mode]')) {
      const selected = button.dataset.readMode === active;
      button.className = 'flex-1 rounded px-2 py-1.5 text-xs '
        + (selected ? 'bg-gray-800 text-green-400' : 'text-gray-300 hover:bg-gray-800');
      button.setAttribute('aria-pressed', selected ? 'true' : 'false');
    }
    if (showPane) render();
    if (active === 'split') follow(field, pane);
    if (hideEditor) field.blur();
    else if (focus) field.focus({ preventScroll: true });
  }

  function setMode(next, options) {
    mode = MODES.includes(next) ? next : 'edit';
    apply(options);
  }

  menu.addEventListener('click', (event) => {
    const button = event.target instanceof Element ? event.target.closest('[data-read-mode]') : null;
    if (!button) return;
    menu.classList.add('hidden');
    setMode(button.dataset.readMode, { focus: true });
  });

  // Every programmatic write goes through writeEditorReplace and dispatches an
  // input event, so this covers typing, undo and "Text bearbeiten" alike.
  field.addEventListener('input', () => { if (!pane.classList.contains('hidden')) render(); });
  // The template switch only toggles the inline display of the textarea.
  new MutationObserver(() => apply()).observe(field, { attributes: true, attributeFilter: ['style'] });

  /* Die Einstellung fuellt das Modul selbst: welche Ansichten es gibt, weiss nur
     diese Datei - auf dem Handy sind es zwei, auf dem Desktop drei. */
  const startSelect = document.getElementById('settings-start-view');
  if (startSelect) {
    startSelect.innerHTML = MODES
      .map(name => `<option value="${name}">${LABELS[name]}</option>`)
      .join('');
    startSelect.value = startMode();
    startSelect.addEventListener('change', () => {
      const chosen = setStartMode(startSelect.value);
      startSelect.value = chosen;
      // Sofort anwenden: wer den Start umstellt, will sehen, was er bestellt hat.
      setMode(chosen);
    });
  }

  apply();
}());
