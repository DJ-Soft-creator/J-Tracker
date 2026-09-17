// Journl-Iconsatz: dezente Strichicons statt Emojis.
// AI maintenance contract: hier stehen ausschliesslich Icons. Neue Knoepfe
// bekommen ein Icon aus diesem Satz — nie ein Emoji, auch nicht als
// Platzhalter. Emojis in Nutzerinhalten (Journaltext, Hashtags, Vorlagen)
// sind davon nicht betroffen.
//
// Alle Icons teilen dieselbe Sprache: 24x24, keine Fuellung, Strichstaerke 1.8,
// runde Enden, Farbe aus `currentColor`. Dadurch nimmt jedes Icon die Textfarbe
// seines Knopfes an und passt sich Hover- und Fokuszustaenden von selbst an.
//
// `pointer-events-none` ist Absicht: action-menu.js und mehrere Delegierungen
// suchen per `closest()` den Knopf, nicht das Icon.
(function () {
  const PATHS = {
    trash: '<path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/>',
    agent: '<rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4"/><path d="M9.5 13h.01"/><path d="M14.5 13h.01"/><path d="M9.5 17h5"/>',
    user: '<circle cx="12" cy="8" r="4"/><path d="M20 21v-1.5A4.5 4.5 0 0 0 15.5 15h-7A4.5 4.5 0 0 0 4 19.5V21"/>',
    pencil: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    cart: '<circle cx="9.5" cy="20" r="1.3"/><circle cx="18" cy="20" r="1.3"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.6L21 7H6"/>',
    folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    folders: '<path d="M3 9a2 2 0 0 1 2-2h3l2 2h6a2 2 0 0 1 2 2"/><path d="M3 11h18a1 1 0 0 1 1 1.2l-1.4 6A2 2 0 0 1 18.7 20H5.3a2 2 0 0 1-2-1.6L2 12.2A1 1 0 0 1 3 11Z"/>',
    note: '<path d="M5 5a2 2 0 0 1 2-2h7l5 5v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2Z"/><path d="M14 3v5h5"/><path d="M9 13h6"/><path d="M9 17h4"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8.5 12.5 2.5 2.5 4.5-5"/>',
    close: '<path d="M18 6 6 18"/><path d="M6 6l12 12"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3"/><path d="M12 18.5v3"/><path d="M2.5 12h3"/><path d="M18.5 12h3"/><path d="m5.3 5.3 2.1 2.1"/><path d="m16.6 16.6 2.1 2.1"/><path d="m18.7 5.3-2.1 2.1"/><path d="m7.4 16.6-2.1 2.1"/>',
    chip: '<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4"/><path d="M14 3v4"/><path d="M10 17v4"/><path d="M14 17v4"/><path d="M3 10h4"/><path d="M3 14h4"/><path d="M17 10h4"/><path d="M17 14h4"/>',
    network: '<circle cx="12" cy="5" r="2.2"/><circle cx="5" cy="18" r="2.2"/><circle cx="19" cy="18" r="2.2"/><path d="M10.6 6.8 6.4 15.9"/><path d="m13.4 6.8 4.2 9.1"/><path d="M7.2 18h9.6"/>',
    warning: '<path d="M12 3.5 2.5 20h19Z"/><path d="M12 9.5v4.5"/><path d="M12 17.5h.01"/>',
    checkbox: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="m8 12.3 2.8 2.8L16.2 9"/>',
    square: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/>',
    selectAll: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M12 8v8"/><path d="M8 12h8"/>',
    selectNone: '<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><path d="M8 12h8"/>',
    copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
    download: '<path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M4 20h16"/>',
    arrowUp: '<path d="M12 20V5"/><path d="m6 11 6-6 6 6"/>',
    swap: '<path d="M4 9h15"/><path d="m16 6 3 3-3 3"/><path d="M20 15H5"/><path d="m8 12-3 3 3 3"/>',
    puzzle: '<path d="M9.5 4.5a2.5 2.5 0 0 1 5 0V6H18a1 1 0 0 1 1 1v3.5h1.5a2.5 2.5 0 0 1 0 5H19V19a1 1 0 0 1-1 1h-3.5v-1.5a2.5 2.5 0 0 0-5 0V20H6a1 1 0 0 1-1-1v-3.5H3.5a2.5 2.5 0 0 1 0-5H5V7a1 1 0 0 1 1-1h3.5Z"/>',
    shield: '<path d="M12 3.5 4.5 6.2V12c0 4.6 3.1 7.7 7.5 8.5 4.4-.8 7.5-3.9 7.5-8.5V6.2Z"/>',
    logout: '<path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3"/><path d="m10 17-5-5 5-5"/><path d="M5 12h11"/>',
    link: '<path d="M10.5 13.5a4.5 4.5 0 0 0 6.4 0l2.2-2.2a4.5 4.5 0 0 0-6.4-6.4L11.4 6.2"/><path d="M13.5 10.5a4.5 4.5 0 0 0-6.4 0l-2.2 2.2a4.5 4.5 0 0 0 6.4 6.4l1.3-1.3"/>',
    lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.6-3.6"/>',
    plug: '<path d="M9 2.5v6"/><path d="M15 2.5v6"/><path d="M6 8.5h12v2.5a6 6 0 0 1-12 0Z"/><path d="M12 17v4.5"/>',
    chat: '<path d="M20.5 12a8 8 0 0 1-8 8H4.5l2.2-3.1A8 8 0 1 1 20.5 12Z"/>',
    tag: '<path d="M3.5 12.6V4.5a1 1 0 0 1 1-1h8.1L20.5 11.4 12.4 19.5Z"/><path d="M7.8 7.8h.01"/>',
    film: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7.5 5v14"/><path d="M16.5 5v14"/><path d="M3 12h18"/>',
    ticket: '<path d="M4 6.5h16v3a2.5 2.5 0 0 0 0 5v3H4v-3a2.5 2.5 0 0 0 0-5Z"/><path d="M14 6.5v11"/>',
    clipboard: '<path d="M9 4.5H7a2 2 0 0 0-2 2V19a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6.5a2 2 0 0 0-2-2h-2"/><rect x="9" y="2.5" width="6" height="4" rx="1"/><path d="M9 12h6"/><path d="M9 16h4"/>',
    paperclip: '<path d="M20.5 11.5 12.4 19.6a5 5 0 0 1-7.1-7.1l8.5-8.5a3.4 3.4 0 0 1 4.8 4.8l-8.5 8.5a1.8 1.8 0 0 1-2.5-2.5l7.8-7.8"/>',
    pin: '<path d="M12 17v4.5"/><path d="M9 2.5h6l-1 6.2 3.5 3.4V15H6.5v-2.9L10 8.7Z"/>',
    calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4"/><path d="M16 3v4"/><path d="M3 10h18"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    bolt: '<path d="M13.5 2.5 4.5 14H11l-.5 7.5L19.5 10H13Z"/>',
    listClean: '<path d="M4 6h11"/><path d="M4 12h7"/><path d="M4 18h7"/><path d="m15 14 5.5 5.5"/><path d="m20.5 14-5.5 5.5"/>',
    menu: '<path d="M4 7h16"/><path d="M4 12h16"/><path d="M4 17h16"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.3l3.3 2"/>',
    home: '<path d="m3.5 10.5 8.5-7 8.5 7"/><path d="M5.5 9.4V19a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1V9.4"/>',
    spinner: '<path d="M12 3a9 9 0 1 0 9 9"/>',
  };

  // Farbpunkte der Einkaufsgruppen. Frueher farbige Emoji-Quadrate — als
  // Tailwind-Punkt tragen sie dieselbe Information ohne Fremdstil und
  // rendern auf jedem System gleich.
  const GROUP_DOTS = {
    'Obst & Gemüse': 'bg-green-500',
    'Kühlung': 'bg-sky-400',
    'Tiefkühlung': 'bg-cyan-200',
    'Non-Food': 'bg-yellow-400',
    'Konserven': 'bg-orange-400',
    'Süßigkeiten': 'bg-purple-400',
    'Sonstiges': 'bg-gray-400',
  };

  /** Ein Icon als SVG-Zeichenkette. Unbekannter Name ergibt nichts, nie Text. */
  window.journlIcon = function journlIcon(name, className) {
    const body = PATHS[name];
    if (!body) return '';
    const size = className || 'h-4 w-4';
    return `<svg viewBox="0 0 24 24" class="${size} pointer-events-none shrink-0" fill="none"`
      + ' stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
      + ` aria-hidden="true">${body}</svg>`;
  };

  /** Farbpunkt einer Einkaufsgruppe; unbekannte Gruppen bleiben neutral. */
  window.journlGroupDot = function journlGroupDot(group) {
    const colour = GROUP_DOTS[group] || 'bg-gray-600';
    return `<span class="inline-block h-2 w-2 shrink-0 rounded-full ${colour}" aria-hidden="true"></span>`;
  };

}());
