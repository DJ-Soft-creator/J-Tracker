/* Das "..."-Menue des Schreiben-Tabs: Huelle, Gruppen und Ausrichtung.
 *
 * Die Eintraege selbst kommen aus write-session.js und fuenf weiteren Modulen
 * (write-ai.js, write-agent.js, write-deep-mode.js, write-read-mode.js,
 * screenshot-inbox.js, write-ticket.js). Dadurch war das Menue laenger als ein
 * Handy-Bildschirm hoch ist: auf dem iPhone lag der oberste Eintrag hinter der
 * Dynamic Island und liess sich weder lesen noch antippen.
 *
 * Deshalb zwei Regeln, die hier und nur hier stehen: selten gebrauchte Bloecke
 * liegen in aufklappbaren Gruppen, und das Menue bleibt immer zwischen
 * Kopfleiste und Tab-Leiste - beide liegen bereits innerhalb der Safe Area.
 * Passt es dort nicht hinein, scrollt es in der freien Hoehe.
 */
(function () {
  let anchor = null;

  // Oben stehen nur die Aktionen fuer den gerade geschriebenen Text; alles
  // Seltenere liegt in Gruppen, deren Zustand sich das Geraet merkt.
  const menuGroupStorageKey = 'journl.write.menu-group.';
  function menuGroupWasOpen(name) {
    try { return window.localStorage.getItem(menuGroupStorageKey + name) === '1'; }
    catch (_) { return false; }
  }
  function menuGroupMarkup(name, label, body) {
    return `<div data-menu-group="${name}">`
      + `<button type="button" data-menu-group-toggle="${name}" aria-expanded="false" aria-controls="write-media-group-${name}"`
      + ' class="flex w-full items-center justify-between gap-2 rounded px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">'
      + `<span>${label}</span>`
      + '<svg viewBox="0 0 24 24" class="h-4 w-4 shrink-0 text-gray-500 transition-transform" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m8 10 4 4 4-4"/></svg>'
      + '</button>'
      + `<div id="write-media-group-${name}" data-menu-group-body="${name}" class="hidden">${body}</div>`
      + '</div>';
  }
  const menuSeparator = '<div class="my-1 border-t border-gray-700" role="separator"></div>';
  const menuFontSection = '<section data-font-section class="px-2 py-1.5" aria-label="Schriftgröße"><div class="mb-1 flex items-center justify-between gap-3 text-[11px] font-medium uppercase tracking-wide text-gray-500"><span>Schrift</span><output data-font-size-label aria-live="polite">16 px</output></div><div class="flex items-center gap-1"><button type="button" data-font-step="-3" class="flex-1 rounded px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Schrift stark verkleinern" title="Schrift stark verkleinern">A−−</button><button type="button" data-font-step="-1" class="flex-1 rounded px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Schrift schrittweise verkleinern" title="Schrift verkleinern">A−</button><button type="button" data-font-step="1" class="flex-1 rounded px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Schrift schrittweise vergrößern" title="Schrift vergrößern">A+</button><button type="button" data-font-step="3" class="flex-1 rounded px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35" aria-label="Schrift stark vergrößern" title="Schrift stark vergrößern">A++</button></div></section>';
  const menuAttachEntries = '<button type="button" data-camera class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Foto aufnehmen</button><button type="button" data-photo class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Bild auswählen</button><button type="button" data-audio class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Sprachnachricht aufnehmen</button><button type="button" data-document class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800">Dokument anhängen</button>';

  const mediaMenu = document.createElement('div');
  mediaMenu.id = 'write-media-menu';
  mediaMenu.className = 'hidden fixed z-50 min-w-[16rem] overflow-y-auto overscroll-contain rounded-lg border border-gray-700 bg-gray-900 p-1 shadow-xl';
  mediaMenu.innerHTML = '<button type="button" data-background-agent class="block w-full px-3 py-2 text-left text-sm text-violet-200 hover:bg-violet-950/50">Im Hintergrund an KI senden</button>'
    + menuSeparator
    + '<button type="button" data-undo class="block w-full px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-35">↶ Rückgängig</button>'
    + menuSeparator
    + menuGroupMarkup('attach', 'Anhängen', menuAttachEntries)
    + menuGroupMarkup('view', 'Darstellung', menuFontSection);
  document.body.appendChild(mediaMenu);

  function setMenuGroup(name, open) {
    const toggle = mediaMenu.querySelector(`[data-menu-group-toggle="${name}"]`);
    const body = mediaMenu.querySelector(`[data-menu-group-body="${name}"]`);
    if (!toggle || !body) return;
    body.classList.toggle('hidden', !open);
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    toggle.querySelector('svg')?.classList.toggle('rotate-180', open);
    try { window.localStorage.setItem(menuGroupStorageKey + name, open ? '1' : '0'); }
    catch (_) { /* privates Fenster */ }
  }
  mediaMenu.querySelectorAll('[data-menu-group-toggle]').forEach((toggle) => {
    const name = toggle.dataset.menuGroupToggle;
    setMenuGroup(name, menuGroupWasOpen(name));
    toggle.addEventListener('click', () => {
      const body = mediaMenu.querySelector(`[data-menu-group-body="${name}"]`);
      setMenuGroup(name, body.classList.contains('hidden'));
      // Die Hoehe hat sich geaendert: neu ausrichten, sonst wandert das Menue
      // wieder unter die Kopfleiste.
      positionMediaMenu();
    });
  });
  /* Die Grenzen kommen aus Kopfleiste und Tab-Leiste statt aus der reinen
     Fensterhoehe: beide liegen bereits innerhalb der Safe Area, damit endet das
     Menue auf dem iPhone verlaesslich unter der Dynamic Island und ueber dem
     Home-Indicator. Passt es nicht in die freie Hoehe, bekommt es genau diese
     Hoehe und scrollt darin - abgeschnittene Eintraege gibt es nicht mehr. */
  function positionMediaMenu() {
    if (!anchor || !anchor.isConnected) return;
    const margin = 8;
    const rect = anchor.getBoundingClientRect();
    const header = document.getElementById('app-header');
    const nav = document.querySelector('#app > nav');
    const topLimit = (header ? header.getBoundingClientRect().bottom : 0) + margin;
    const bottomLimit = (nav ? nav.getBoundingClientRect().top : window.innerHeight) - margin;
    mediaMenu.style.maxHeight = 'none';
    const naturalHeight = mediaMenu.offsetHeight;
    const menuWidth = mediaMenu.offsetWidth;
    const spaceAbove = rect.top - margin - topLimit;
    const spaceBelow = bottomLimit - rect.bottom - margin;
    // Nach oben aufklappen bleibt die Regel - der Knopf sitzt unten im
    // Schreiben-Tab. Nur wenn darunter mehr Platz ist, dreht sich die Richtung.
    const openUp = spaceAbove >= spaceBelow;
    const available = Math.max(120, openUp ? spaceAbove : spaceBelow);
    const height = Math.min(naturalHeight, available);
    mediaMenu.style.maxHeight = `${Math.round(height)}px`;
    const top = openUp
      ? Math.max(topLimit, rect.top - margin - height)
      : Math.min(rect.bottom + margin, Math.max(topLimit, bottomLimit - height));
    mediaMenu.style.top = `${Math.round(top)}px`;
    mediaMenu.style.left = `${Math.round(Math.max(margin, Math.min(window.innerWidth - menuWidth - margin, rect.left)))}px`;
  }
  function closeMediaMenu() { mediaMenu.classList.add('hidden'); }

  window.journlWriteMediaMenu = {
    element: mediaMenu,
    close: closeMediaMenu,
    position: positionMediaMenu,
    isOpen() { return !mediaMenu.classList.contains('hidden'); },
    open(trigger) {
      anchor = trigger;
      mediaMenu.classList.remove('hidden');
      mediaMenu.scrollTop = 0;
      positionMediaMenu();
    },
    toggle(trigger) {
      if (this.isOpen()) closeMediaMenu();
      else this.open(trigger);
    },
  };
})();
