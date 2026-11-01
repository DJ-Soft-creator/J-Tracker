/* Oberflaeche des Players: volle Leiste, Mini-Pille, kurze Einblendungen.

   Wiedergabe, Bereich und Kuerzel besitzt screensaver-player.js; es reicht
   seinen Zustand und seine Aktionen ueber `create(ctx)` herein und ruft die
   render-Funktionen, sobald sich etwas aendert. Hier entsteht nur das Markup
   samt CSS (Equalizer, Regler, Lichteffekte) und die Bedienung daran:
   Liste der Titel/Episoden/Sender, Tempo (nur Podcasts) und Sleep-Timer.

   Mobil (Template mit `#mobile-tab-bar`) sitzt der Player unten ueber der
   Tab-Leiste statt in der engen Kopfzeile, waechst nach oben auf und
   verschwindet, solange die Bildschirmtastatur offen ist. Touch-Ziele sind
   bei grobem Zeiger mindestens 40 px gross, das Kuerzel-Panel faellt weg und
   der Lautstaerke-Regler auch (dafuer gibt es die Geraetetasten). */
window.journlScreensaverPlayerView = (function () {
  const AUTOHIDE_AFTER = 8000;
  const ACTION_LABELS = {
    play_pause: 'Abspielen / Pause',
    back: 'Zurückspulen',
    forward: 'Vorspulen',
    previous: 'Vorheriger Titel',
    next: 'Nächster Titel',
    volume_up: 'Lauter',
    volume_down: 'Leiser',
  };
  const REPEAT_LABELS = { off: 'Wiederholen aus', folder: 'Ordner wiederholen', track: 'Titel wiederholen' };
  const BUTTON = 'h-8 w-8 shrink-0 inline-flex items-center justify-center rounded-full text-white/60 hover:text-white hover:bg-white/10 transition';
  const ICONS = {
    play: '<path d="M8 5.2v13.6a.6.6 0 0 0 .9.5l10.6-6.8a.6.6 0 0 0 0-1L8.9 4.7a.6.6 0 0 0-.9.5Z" fill="currentColor" stroke="none"/>',
    pause: '<rect x="6.5" y="5" width="3.8" height="14" rx="1.2" fill="currentColor" stroke="none"/><rect x="13.7" y="5" width="3.8" height="14" rx="1.2" fill="currentColor" stroke="none"/>',
    previous: '<path d="M6 5.5v13"/><path d="M18 6.2v11.6a.5.5 0 0 1-.8.4L9.4 12.4a.5.5 0 0 1 0-.8l7.8-5.8a.5.5 0 0 1 .8.4Z"/>',
    next: '<path d="M18 5.5v13"/><path d="M6 6.2v11.6a.5.5 0 0 0 .8.4l7.8-5.8a.5.5 0 0 0 0-.8L6.8 5.8a.5.5 0 0 0-.8.4Z"/>',
    back: '<path d="M4.5 12a7.5 7.5 0 1 0 2.2-5.3"/><path d="M4.5 4v3.5H8"/>',
    forward: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v3.5H16"/>',
    shuffle: '<path d="M3 7h3c2 0 3.2 1 4.2 2.7l2.6 4.6C13.8 16 15 17 17 17h4"/><path d="M3 17h3c1.5 0 2.6-.6 3.5-1.8"/><path d="M14.5 8.8C15.4 7.6 16.5 7 18 7h3"/><path d="m18.5 4 2.5 3-2.5 3"/><path d="m18.5 14 2.5 3-2.5 3"/>',
    repeat: '<path d="m17 3 3 3-3 3"/><path d="M4 11.5v-.5a5 5 0 0 1 5-5h11"/><path d="m7 21-3-3 3-3"/><path d="M20 12.5v.5a5 5 0 0 1-5 5H4"/>',
    volume: '<path d="M4 9.5h3.2L11.5 6v12l-4.3-3.5H4z"/><path d="M15 9.2a4 4 0 0 1 0 5.6"/><path d="M17.6 6.6a7.6 7.6 0 0 1 0 10.8"/>',
    muted: '<path d="M4 9.5h3.2L11.5 6v12l-4.3-3.5H4z"/><path d="m15.5 9.5 5 5"/><path d="m20.5 9.5-5 5"/>',
    keys: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><path d="M6.5 10h.01M10 10h.01M13.5 10h.01M17 10h.01M7.5 14h9"/>',
    settings: '<circle cx="12" cy="12" r="3"/><path d="M12 3v2.2M12 18.8V21M4.6 7.8l1.9 1.1M17.5 15.1l1.9 1.1M4.6 16.2l1.9-1.1M17.5 8.9l1.9-1.1"/>',
    collapse: '<path d="m6 9 6 6 6-6"/>',
    note: '<path d="M9 17.5V6.2l10-2v11.3"/><circle cx="6.6" cy="17.6" r="2.4"/><circle cx="16.6" cy="15.6" r="2.4"/>',
    list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><path d="M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/>',
    moon: '<path d="M19.5 14.6A7.8 7.8 0 0 1 9.4 4.5a7.8 7.8 0 1 0 10.1 10.1Z"/>',
    check: '<path d="m5 12.5 4.2 4.2L19 7"/>',
    radio: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
    hidden: '<path d="M9.9 5.7A9.6 9.6 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.4 3.2M6.6 6.6A15.6 15.6 0 0 0 2.5 12S6 18.5 12 18.5a9.4 9.4 0 0 0 5.4-1.7"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/><path d="m3 3 18 18"/>',
  };
  const PLACEHOLDER = { music: 'note', podcast: 'mic', radio: 'radio' };


  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');

  function icon(name, size = 'h-4 w-4', extra = '') {
    return `<svg viewBox="0 0 24 24" class="${size} pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}${extra}</svg>`;
  }

  // Die Sprungweite steht im Symbol - die Einstellung kennt 5 bis 60 Sekunden.
  function skipIcon(name, seconds) {
    return icon(name, 'h-[18px] w-[18px]', `<text x="12" y="15.2" text-anchor="middle" font-size="7.2" font-weight="600" fill="currentColor" stroke="none">${seconds}</text>`);
  }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function clock(seconds) {
    const total = Math.max(0, Math.floor(seconds || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = String(total % 60).padStart(2, '0');
    return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
  }

  function formatRate(rate) {
    return `${String(Math.round(rate * 100) / 100).replace('.', ',')}×`;
  }

  function day(timestamp) {
    if (!timestamp) return '';
    return new Date(timestamp * 1000).toLocaleDateString('de-DE', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  const KEY_NAMES = {
    Space: 'Leertaste', ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Enter: 'Enter',
    Comma: ',', Period: '.', Slash: '/', Semicolon: ';', Quote: "'", BracketLeft: '[', BracketRight: ']',
    Minus: '-', Equal: '=', Backquote: '`', Backslash: '\\', IntlBackslash: '<', PageUp: 'Bild↑', PageDown: 'Bild↓',
  };
  function formatShortcut(combo) {
    if (!combo) return '';
    const parts = combo.split('+');
    const key = parts.pop();
    const name = KEY_NAMES[key] || key.replace(/^Key|^Digit/, '');
    if (mac) {
      const symbols = { Ctrl: '⌃', Alt: '⌥', Shift: '⇧' };
      return parts.map(part => symbols[part] || part).join('') + name;
    }
    return [...parts.map(part => (part === 'Ctrl' ? 'Strg' : part)), name].join('+');
  }


  function create(ctx) {
    const { state, volumeLocked, current, playing, inArea, screensaverOpen, run, play, seek, setVolume,
      toggleShuffle, cycleRepeat, toggleMute, persist, updateSession,
      mode, live, playItem, cycleRate, cycleSleep, hasSource } = ctx;
    let root = null;

    // ─── Oberflaeche ──────────────────────────────────────────────────

    function styles() {
      if (document.getElementById('journl-player-style')) return;
      const style = document.createElement('style');
      style.id = 'journl-player-style';
      // Lichteffekte (screensaver-player-effects.js) schalten nur `data-fx-*`
      // und setzen `--pa-color`/`--pa-energy`; alles Sichtbare steht hier.
      style.textContent = `
        @property --pa-color { syntax: '<color>'; inherits: true; initial-value: rgb(96 165 250); }
        @property --pa-angle { syntax: '<angle>'; inherits: false; initial-value: 0deg; }
        @keyframes journl-eq { 0%, 100% { transform: scaleY(.28); } 50% { transform: scaleY(1); } }
        @keyframes journl-player-in { from { opacity: 0; transform: translateY(-4px) scale(.97); } to { opacity: 1; transform: none; } }
        @keyframes journl-player-breathe {
          0%, 100% { box-shadow: 0 10px 40px rgba(0,0,0,.45), 0 0 14px color-mix(in srgb, var(--pa-color) 18%, transparent), inset 0 0 0 1px color-mix(in srgb, var(--pa-color) 16%, transparent); }
          50% { box-shadow: 0 10px 40px rgba(0,0,0,.45), 0 0 30px color-mix(in srgb, var(--pa-color) 42%, transparent), inset 0 0 0 1px color-mix(in srgb, var(--pa-color) 32%, transparent); } }
        @keyframes journl-player-edge { to { --pa-angle: 360deg; } }
        #journl-player { --pa-energy: 0; transition: --pa-color 1.6s ease; }
        #journl-player [data-enter] { animation: journl-player-in .22s ease-out; }
        #journl-player [data-glass] { position: relative; background: rgba(9,12,24,.95);
          box-shadow: 0 10px 40px rgba(0,0,0,.45), inset 0 0 0 1px rgba(255,255,255,.09); }
        #journl-player[data-fx-glass] [data-glass] { background: rgba(9,12,24,.62);
          -webkit-backdrop-filter: blur(22px) saturate(1.5); backdrop-filter: blur(22px) saturate(1.5); }
        #journl-player[data-fx-color] [data-glass] { box-shadow: 0 10px 40px rgba(0,0,0,.45),
          0 0 26px -8px color-mix(in srgb, var(--pa-color) 55%, transparent), inset 0 0 0 1px color-mix(in srgb, var(--pa-color) 20%, rgba(255,255,255,.07)); }
        #journl-player[data-fx-glass] [data-glass]::before { content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
          pointer-events: none; opacity: .6; animation: journl-player-edge 14s linear infinite;
          background: conic-gradient(from var(--pa-angle), transparent 0deg 250deg, color-mix(in srgb, var(--pa-color) 55%, white) 305deg, transparent 345deg);
          -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor;
          mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); }
        #journl-player[data-fx-breathe][data-playing] [data-pill], #journl-player[data-fx-breathe][data-playing] [data-bar] {
          animation: journl-player-breathe 5.5s ease-in-out infinite; }
        #journl-player [data-aurora] { position: absolute; inset: -22px -34px; z-index: -1; border-radius: 999px; pointer-events: none;
          opacity: 0; filter: blur(26px); transform: scale(calc(.9 + var(--pa-energy) * .45));
          background: radial-gradient(55% 70% at 50% 55%, color-mix(in srgb, var(--pa-color) 75%, transparent), transparent 72%),
            radial-gradient(40% 60% at 22% 40%, color-mix(in srgb, var(--pa-color) 45%, rgb(167 139 250 / .5)), transparent 70%),
            radial-gradient(40% 60% at 80% 60%, color-mix(in srgb, var(--pa-color) 45%, rgb(34 211 238 / .45)), transparent 70%); }
        #journl-player[data-fx-reactive][data-playing] [data-aurora] { opacity: min(.95, calc(.1 + var(--pa-energy) * 1.6)); }
        #journl-player [data-action="play_pause"] { box-shadow: 0 0 18px color-mix(in srgb, var(--pa-color) 45%, transparent); }
        #journl-player [data-eq] span { display: block; width: 3px; height: 14px; border-radius: 2px; transform-origin: bottom;
          transform: scaleY(.28); transition: transform .3s;
          background: linear-gradient(to top, var(--pa-color), color-mix(in srgb, var(--pa-color) 40%, white)); }
        #journl-player[data-playing] [data-eq] span { animation: journl-eq 1s ease-in-out infinite; }
        #journl-player[data-playing] [data-eq] span:nth-child(2) { animation-duration: .8s; animation-delay: -.3s; }
        #journl-player[data-playing] [data-eq] span:nth-child(3) { animation-duration: 1.15s; animation-delay: -.55s; }
        #journl-player[data-playing] [data-eq] span:nth-child(4) { animation-duration: .9s; animation-delay: -.15s; }
        #journl-player input[type=range] { -webkit-appearance: none; appearance: none; height: 4px; border-radius: 999px; cursor: pointer;
          --pa-fill: color-mix(in srgb, var(--pa-color) 80%, white);
          background: linear-gradient(to right, var(--pa-fill) var(--fill, 0%), rgba(255,255,255,.14) var(--fill, 0%)); }
        #journl-player input[type=range]:focus-visible { outline: 2px solid color-mix(in srgb, var(--pa-color) 60%, transparent); outline-offset: 4px; }
        #journl-player input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 12px; height: 12px; border-radius: 50%;
          background: #f0f9ff; box-shadow: 0 0 8px var(--pa-color); opacity: 0; transition: opacity .15s; }
        #journl-player input[type=range]::-moz-range-thumb { width: 12px; height: 12px; border: 0; border-radius: 50%;
          background: #f0f9ff; box-shadow: 0 0 8px var(--pa-color); opacity: 0; transition: opacity .15s; }
        #journl-player input[type=range]:hover::-webkit-slider-thumb, #journl-player input[type=range]:focus-visible::-webkit-slider-thumb { opacity: 1; }
        #journl-player input[type=range]:hover::-moz-range-thumb, #journl-player input[type=range]:focus-visible::-moz-range-thumb { opacity: 1; }
        #journl-player[data-volume-locked] [data-volume] { display: none; }
        #journl-player[data-mobile] [data-list-panel] { max-height: 45vh; }
        #journl-player[data-mobile] [data-volume] { display: none; }
        #journl-player[data-mobile] [data-aurora] { top: auto; height: 5rem; bottom: -18px; }
        #journl-player[data-mobile][data-fx-glass] [data-glass] { -webkit-backdrop-filter: blur(12px) saturate(1.3); backdrop-filter: blur(12px) saturate(1.3); }
        #journl-player[data-mobile] [data-glass]::before { animation: none; }
        @media (pointer: coarse) {
          #journl-player [data-bar] button { min-width: 2.5rem; min-height: 2.5rem; }
          #journl-player [data-keys], #journl-player [data-keys-panel] { display: none; }
        }
        @media (prefers-reduced-motion: reduce) {
          #journl-player *, #journl-player *::before { animation: none !important; transition: none !important; }
          #journl-player[data-playing] [data-eq] span { transform: scaleY(.7); }
          #journl-player[data-fx-reactive][data-playing] [data-aurora] { opacity: .25; transform: none; }
        }`;
      document.head.appendChild(style);
    }

    function build() {
      if (root) return;
      styles();
      root = document.createElement('div');
      root.id = 'journl-player';
      // Klicks hier wecken den Schoner nicht (siehe screensaver.js).
      root.dataset.screensaverKeep = '';
      root.className = 'fixed left-1/2 z-40 hidden -translate-x-1/2 flex-col items-center gap-2 text-white/85';
      root.innerHTML = `
        <div data-aurora aria-hidden="true"></div>
        <div data-flash data-glass class="hidden rounded-full px-3 py-1 text-xs text-white/80"></div>
        <button type="button" data-pill data-glass class="hidden items-center gap-2.5 rounded-full px-3.5 py-1.5 text-xs text-white/75 hover:text-white transition">
          <span data-eq class="flex h-3.5 items-end gap-[3px]" aria-hidden="true"><span></span><span></span><span></span><span></span></span>
          <span data-pill-label class="max-w-[16rem] truncate"></span>
        </button>
        <div data-bar data-glass class="hidden w-[min(34rem,calc(100vw-1.5rem))] rounded-2xl px-3 pb-2 pt-2.5">
          <div class="flex items-center gap-3">
            <div data-cover class="relative h-11 w-11 shrink-0 overflow-hidden rounded-lg bg-gradient-to-br from-blue-500/30 via-indigo-500/20 to-cyan-400/20 ring-1 ring-white/10">
              <span data-cover-placeholder class="absolute inset-0 flex items-center justify-center text-white/45">${icon('note', 'h-5 w-5')}</span>
              <img data-cover-image alt="" class="absolute inset-0 hidden h-full w-full object-cover" />
            </div>
            <div class="min-w-0 flex-1">
              <div data-title class="truncate text-sm font-medium text-white/90"></div>
              <div data-subtitle class="truncate text-xs text-white/45"></div>
            </div>
            <div class="flex items-center">
              <button type="button" data-action="previous" class="${BUTTON}" title="Vorheriger Titel" aria-label="Vorheriger Titel">${icon('previous')}</button>
              <button type="button" data-action="back" class="${BUTTON}" data-skip="back"></button>
              <button type="button" data-action="play_pause" class="h-10 w-10 shrink-0 inline-flex items-center justify-center rounded-full bg-white/90 text-gray-950 hover:bg-white transition" title="Abspielen" aria-label="Abspielen"></button>
              <button type="button" data-action="forward" class="${BUTTON}" data-skip="forward"></button>
              <button type="button" data-action="next" class="${BUTTON}" title="Nächster Titel" aria-label="Nächster Titel">${icon('next')}</button>
            </div>
          </div>
          <div data-timeline class="mt-2 flex items-center gap-2 text-[11px] tabular-nums text-white/45">
            <span data-elapsed class="w-10 text-right">0:00</span>
            <input data-seek type="range" min="0" max="1000" step="1" value="0" aria-label="Position" class="min-w-0 flex-1" />
            <span data-remaining class="w-12">0:00</span>
          </div>
          <div data-live class="mt-2 hidden items-center justify-center gap-2 text-[11px] uppercase tracking-[.18em] text-white/55">
            <span class="h-1.5 w-1.5 rounded-full bg-rose-400 shadow-[0_0_8px_2px_rgba(251,113,133,.55)]"></span>Live
          </div>
          <div class="mt-1 flex flex-wrap items-center gap-0.5">
            <button type="button" data-shuffle class="${BUTTON}" aria-label="Zufällige Reihenfolge">${icon('shuffle')}</button>
            <button type="button" data-repeat class="${BUTTON} relative" aria-label="Wiederholen"></button>
            <button type="button" data-mute class="${BUTTON} ml-1" aria-label="Stumm"></button>
            <input data-volume type="range" min="0" max="100" step="1" aria-label="Lautstärke" class="w-24" />
            <span class="flex-1"></span>
            <button type="button" data-rate class="hidden h-8 min-w-[2.6rem] shrink-0 rounded-full px-2 text-[11px] font-semibold tabular-nums text-white/60 hover:text-white hover:bg-white/10 transition" title="Tempo" aria-label="Tempo"></button>
            <button type="button" data-sleep class="${BUTTON} relative !w-auto min-w-[2rem] gap-1 px-1.5" title="Sleep-Timer" aria-label="Sleep-Timer"></button>
            <button type="button" data-list class="${BUTTON}" title="Liste" aria-label="Liste" aria-expanded="false">${icon('list')}</button>
            <button type="button" data-keys class="${BUTTON}" title="Tastenkürzel" aria-label="Tastenkürzel" aria-expanded="false">${icon('keys')}</button>
            <button type="button" data-open-settings class="${BUTTON}" title="Player einstellen" aria-label="Player einstellen">${icon('settings')}</button>
            <button type="button" data-collapse class="${BUTTON}" title="Minimieren" aria-label="Minimieren">${icon('collapse')}</button>
          </div>
          <div data-list-panel class="hidden mt-2 max-h-64 overflow-y-auto overscroll-contain border-t border-white/10 pt-1.5"></div>
          <div data-keys-panel class="hidden mt-2 border-t border-white/10 pt-2"></div>
        </div>`;
      document.body.appendChild(root);
      root.toggleAttribute('data-mobile', mobile());
      root.toggleAttribute('data-volume-locked', Boolean(volumeLocked));

      const seekbar = root.querySelector('[data-seek]');
      seekbar.addEventListener('input', () => {
        state.seeking = true;
        const duration = current().audio.duration;
        if (Number.isFinite(duration)) {
          root.querySelector('[data-elapsed]').textContent = clock(duration * seekbar.value / 1000);
          seekbar.style.setProperty('--fill', `${seekbar.value / 10}%`);
        }
      });
      seekbar.addEventListener('change', () => {
        state.seeking = false;
        const duration = current().audio.duration;
        if (Number.isFinite(duration)) seek(duration * seekbar.value / 1000);
      });
      const volume = root.querySelector('[data-volume]');
      volume.addEventListener('input', () => setVolume(Number(volume.value)));

      root.addEventListener('click', (event) => {
        const action = event.target.closest('[data-action]')?.dataset.action;
        if (action) { run(action); return; }
        if (event.target.closest('[data-pill]')) {
          if (state.needsGesture || state.resumable) { play(); return; }
          state.expanded = true;
          render();
          return;
        }
        const entry = event.target.closest('[data-list-item]');
        if (entry) { playItem(Number(entry.dataset.listItem)); return; }
        if (event.target.closest('[data-shuffle]')) toggleShuffle();
        else if (event.target.closest('[data-repeat]')) cycleRepeat();
        else if (event.target.closest('[data-rate]')) cycleRate();
        else if (event.target.closest('[data-sleep]')) cycleSleep();
        else if (event.target.closest('[data-list]')) { state.listOpen = !state.listOpen; if (state.listOpen) state.keysOpen = false; renderKeys(); renderList(true); }
        else if (event.target.closest('[data-mute]')) toggleMute();
        else if (event.target.closest('[data-keys]')) { state.keysOpen = !state.keysOpen; if (state.keysOpen) state.listOpen = false; renderKeys(); renderList(); }
        else if (event.target.closest('[data-open-settings]')) openSettings();
        else if (event.target.closest('[data-collapse]')) collapse();
      });

      // Die Mini-Pille klappt beim Darueberfahren auf und nach dem Verlassen
      // mit kurzer Gnadenfrist wieder zu - sonst flackert sie am Rand.
      let leaveTimer = null;
      root.addEventListener('pointerenter', (event) => {
        if (event.pointerType !== 'mouse') return;
        clearTimeout(leaveTimer);
        if (state.settings?.visibility === 'minimal' && !state.needsGesture && !state.resumable) {
          state.expanded = true;
          render();
        }
      });
      root.addEventListener('pointerleave', (event) => {
        if (event.pointerType !== 'mouse' || !state.expanded) return;
        leaveTimer = setTimeout(() => { state.expanded = false; state.keysOpen = false; state.listOpen = false; render(); }, 700);
      });
      document.addEventListener('pointerdown', (event) => {
        if (state.expanded && !root.contains(event.target)) { state.expanded = false; state.keysOpen = false; state.listOpen = false; render(); }
      });
      ['pointermove', 'keydown'].forEach(name => document.addEventListener(name, wake, { passive: true }));
      window.addEventListener('resize', settle);
      window.visualViewport?.addEventListener('resize', settle);
      window.visualViewport?.addEventListener('scroll', settle);
      // Mobil: offene Tastatur heisst Schreiben - der Player geht so lange weg.
      document.addEventListener('focusin', (event) => { if (typing(event.target) !== state.typing) { state.typing = typing(event.target); render(); } });
      document.addEventListener('focusout', () => setTimeout(() => {
        const now = typing(document.activeElement);
        if (now !== state.typing) { state.typing = now; render(); }
        settle();
      }, 0));
      // mobile-app-core.js setzt `keyboard-open`, sobald der sichtbare Bereich steht.
      new MutationObserver(settle).observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
      place();
    }

    /* Tastatur auf/zu und der Wechsel aus dem Schoner verschieben Tab-Leiste
       und Senden-Zeile in mehreren Schritten (mobile-app-core.js zieht die
       App-Hoehe erst im naechsten Frame nach, iOS animiert noch). Wer sofort
       misst, misst die Zwischenlage - deshalb jetzt, im uebernaechsten Frame
       und noch einmal, wenn sich alles gesetzt hat. */
    let settleFrame = 0;
    let settleTimer = null;
    function settle() {
      if (!root || !mobile()) { place(); return; }
      if (!settleFrame) settleFrame = requestAnimationFrame(() => requestAnimationFrame(() => { settleFrame = 0; render(); }));
      clearTimeout(settleTimer);
      settleTimer = setTimeout(render, 450);
    }

    function mobile() {
      return Boolean(document.getElementById('mobile-tab-bar'));
    }

    function keyboardOpen() {
      return mobile() && document.documentElement.classList.contains('keyboard-open');
    }

    function typing(element) {
      if (!mobile() || !element || root?.contains(element)) return false;
      return element.matches?.('textarea, [contenteditable="true"], input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button])') || false;
    }

    function openSettings() {
      if (screensaverOpen()) window.journlScreensaver.openSettings?.('player', 'Player');
      else window.openSettingsGroup?.('player', 'Player');
    }

    // „Minimieren“ aus der vollen Leiste merkt sich die Stufe „minimal“.
    function collapse() {
      state.expanded = false;
      state.keysOpen = false;
      state.listOpen = false;
      if (state.settings.visibility === 'visible') persist({ visibility: 'minimal' });
      render();
    }

    // Im Schoner unten mittig (dort ist zwischen Infozeile und Hinweis Platz),
    // mobil ueber der Tab-Leiste, auf dem Desktop mittig in der Kopfzeile.
    function place() {
      if (!root) return;
      const overlay = screensaverOpen();
      const tabs = overlay ? null : document.getElementById('mobile-tab-bar');
      const fromBottom = overlay || Boolean(tabs);
      root.classList.toggle('z-40', !overlay);
      root.classList.toggle('z-[125]', overlay);
      root.classList.toggle('flex-col', !fromBottom);
      root.classList.toggle('flex-col-reverse', fromBottom);
      if (overlay) {
        root.style.top = '';
        root.style.bottom = 'calc(1.25rem + env(safe-area-inset-bottom, 0px))';
        return;
      }
      if (tabs) {
        // Ueber der Tab-Leiste und - im Schreiben-Tab - ueber Mikro/Senden.
        // Nur Kanten im unteren Viertel zaehlen: bei offener Tastatur sitzt die
        // Senden-Zeile mitten im Bild (dann ist der Player ohnehin aus).
        const height = window.innerHeight;
        const tops = [tabs, document.getElementById('write-actions-row')]
          .map(element => element?.getBoundingClientRect())
          .filter(rect => rect && rect.height > 0 && rect.top > height * 0.75 && rect.top < height)
          .map(rect => rect.top);
        root.style.top = '';
        root.style.bottom = tops.length && !keyboardOpen() ? `${Math.max(8, height - Math.min(...tops) + 8)}px`
          : 'calc(4.5rem + env(safe-area-inset-bottom, 0px))';
        return;
      }
      const header = document.getElementById('app-header')?.getBoundingClientRect();
      root.style.bottom = '';
      root.style.top = `${Math.max(6, header ? header.top + header.height / 2 - 16 : 12)}px`;
    }

    let autohideTimer = null;
    function wake() {
      if (!state.settings?.autohide) return;
      if (state.autoHidden) { state.autoHidden = false; render(); }
      clearTimeout(autohideTimer);
      autohideTimer = setTimeout(() => {
        if (state.settings?.autohide && state.settings.visibility === 'visible' && playing() && !state.keysOpen && !state.listOpen) {
          state.autoHidden = true;
          render();
        }
      }, AUTOHIDE_AFTER);
    }

    let flashTimer = null;
    function flash(text) {
      if (screensaverOpen()) { window.journlScreensaver.flashHint?.(text); return; }
      if (!root) { window.showToast?.(text); return; }
      const element = root.querySelector('[data-flash]');
      element.textContent = text;
      element.classList.remove('hidden');
      element.removeAttribute('data-enter');
      void element.offsetWidth;
      element.setAttribute('data-enter', '');
      root.classList.remove('hidden');
      root.classList.add('flex');
      clearTimeout(flashTimer);
      flashTimer = setTimeout(() => { element.classList.add('hidden'); render(); }, 1800);
    }

    function pillLabel() {
      const item = current().track || state.tracks[state.order[state.cursor]];
      if (state.needsGesture) return 'Tippen zum Abspielen';
      if (state.resumable && item) return `Fortsetzen · ${item.title}`;
      if (!state.settings.show_title || !item) return playing() ? '' : 'Pausiert';
      return item.live && playing() ? `${item.title} · Live` : item.title;
    }

    function emptyTitle() {
      return { podcast: 'Kein Podcast', radio: 'Kein Sender' }[mode()] || 'Keine Musik';
    }

    function renderTrack() {
      if (!root) return;
      const item = current().track || state.tracks[state.order[state.cursor]];
      const settings = state.settings;
      const title = root.querySelector('[data-title]');
      const subtitle = root.querySelector('[data-subtitle]');
      title.textContent = settings.show_title ? (item?.title || emptyTitle()) : (playing() ? 'Läuft' : 'Pausiert');
      subtitle.textContent = settings.show_title && item
        ? [item.artist, item.album || item.source].filter(Boolean).join(' · ')
        : (state.missing.length ? (mode() === 'music' ? `${state.missing.length} Ordner fehlt` : 'Quelle fehlt') : state.problem);
      root.querySelector('[data-cover-placeholder]').innerHTML = icon(PLACEHOLDER[mode()] || 'note', 'h-5 w-5');
      const cover = root.querySelector('[data-cover]');
      const image = root.querySelector('[data-cover-image]');
      cover.classList.toggle('hidden', !settings.show_cover);
      if (settings.show_cover && item?.cover) {
        if (image.getAttribute('src') !== item.cover) image.src = item.cover;
        image.classList.remove('hidden');
        image.onerror = () => image.classList.add('hidden');
      } else {
        image.classList.add('hidden');
        image.removeAttribute('src');
      }
      root.querySelector('[data-pill-label]').textContent = pillLabel();
      root.querySelector('[data-pill-label]').classList.toggle('hidden', !pillLabel());
    }

    function renderProgress() {
      if (!root || state.seeking) return;
      const { audio, track } = current();
      const isLive = Boolean(track?.live);
      root.querySelector('[data-timeline]').classList.toggle('hidden', isLive);
      root.querySelector('[data-live]').classList.toggle('hidden', !isLive);
      root.querySelector('[data-live]').classList.toggle('flex', isLive);
      if (isLive) return;
      // Vor dem ersten Abspielen kennt eine Episode ihre Dauer nur aus dem Feed.
      const duration = Number.isFinite(audio.duration) ? audio.duration : (track?.duration || 0);
      const position = track ? (audio.currentTime || (audio.readyState ? 0 : track.position || 0)) : 0;
      const seekbar = root.querySelector('[data-seek]');
      const fill = duration ? Math.min(1000, Math.round(position / duration * 1000)) : 0;
      seekbar.value = String(fill);
      seekbar.style.setProperty('--fill', `${fill / 10}%`);
      seekbar.disabled = !Number.isFinite(audio.duration) || !audio.duration;
      root.querySelector('[data-elapsed]').textContent = clock(position);
      root.querySelector('[data-remaining]').textContent = duration ? `−${clock(duration - position)}` : '0:00';
    }

    function renderVolume() {
      if (!root) return;
      const settings = state.settings;
      const volume = root.querySelector('[data-volume]');
      const shown = settings.muted ? 0 : settings.volume;
      volume.value = String(shown);
      volume.style.setProperty('--fill', `${shown}%`);
      const mute = root.querySelector('[data-mute]');
      mute.innerHTML = icon(settings.muted || !settings.volume ? 'muted' : 'volume');
      mute.title = settings.muted ? 'Ton an' : 'Stumm';
    }

    function renderModes() {
      if (!root) return;
      const settings = state.settings;
      const music = mode() === 'music';
      const isLive = live() || (!current().track && mode() === 'radio');
      root.querySelector('[data-shuffle]').classList.toggle('hidden', !music);
      root.querySelector('[data-repeat]').classList.toggle('hidden', !music);
      root.querySelectorAll('[data-skip]').forEach(button => button.classList.toggle('hidden', isLive));
      const unit = { podcast: ['Vorherige Episode', 'Nächste Episode'], radio: ['Vorheriger Sender', 'Nächster Sender'] }[mode()]
        || ['Vorheriger Titel', 'Nächster Titel'];
      [['previous', unit[0]], ['next', unit[1]]].forEach(([action, label]) => {
        const button = root.querySelector(`[data-action="${action}"]`);
        button.title = label;
        button.setAttribute('aria-label', label);
      });
      const rate = root.querySelector('[data-rate]');
      rate.classList.toggle('hidden', mode() !== 'podcast');
      rate.textContent = formatRate(state.rate || 1);
      rate.classList.toggle('!text-blue-300', Math.abs((state.rate || 1) - 1) > 0.001);
      const sleep = root.querySelector('[data-sleep]');
      const choice = state.sleep.choice;
      const minutes = state.sleep.until ? Math.max(1, Math.ceil((state.sleep.until - Date.now()) / 60000)) : 0;
      sleep.innerHTML = icon('moon') + (choice
        ? `<span class="text-[10px] font-semibold tabular-nums">${choice === 'end' ? 'Ende' : `${minutes}′`}</span>` : '');
      sleep.classList.toggle('!text-blue-300', Boolean(choice));
      sleep.title = choice ? (choice === 'end' ? 'Sleep-Timer: am Ende' : `Sleep-Timer: noch ${minutes} min`) : 'Sleep-Timer (aus)';
      const shuffle = root.querySelector('[data-shuffle]');
      shuffle.classList.toggle('!text-blue-300', settings.shuffle);
      shuffle.title = settings.shuffle ? 'Zufällig (an)' : 'Zufällig (aus)';
      shuffle.setAttribute('aria-pressed', String(settings.shuffle));
      const repeat = root.querySelector('[data-repeat]');
      repeat.innerHTML = icon('repeat') + (settings.repeat === 'track'
        ? '<span class="absolute right-1 top-1 text-[9px] font-bold leading-none">1</span>' : '');
      repeat.classList.toggle('!text-blue-300', settings.repeat !== 'off');
      repeat.title = REPEAT_LABELS[settings.repeat];
      root.querySelector('[data-skip="back"]').innerHTML = skipIcon('back', settings.skip_back);
      root.querySelector('[data-skip="back"]').title = `${settings.skip_back} s zurück`;
      root.querySelector('[data-skip="forward"]').innerHTML = skipIcon('forward', settings.skip_forward);
      root.querySelector('[data-skip="forward"]').title = `${settings.skip_forward} s vor`;
    }

    function renderState() {
      if (!root) return;
      const isPlaying = playing();
      root.toggleAttribute('data-playing', isPlaying);
      const button = root.querySelector('[data-action="play_pause"]');
      button.innerHTML = icon(isPlaying ? 'pause' : 'play', 'h-5 w-5');
      button.title = isPlaying ? 'Pause' : 'Abspielen';
      button.setAttribute('aria-label', button.title);
      root.querySelector('[data-pill]').title = isPlaying ? 'Player' : 'Player (pausiert)';
      renderTrack();
      updateSession();
    }

    function renderKeys() {
      if (!root) return;
      const panel = root.querySelector('[data-keys-panel]');
      panel.classList.toggle('hidden', !state.keysOpen);
      root.querySelector('[data-keys]').setAttribute('aria-expanded', String(state.keysOpen));
      root.querySelector('[data-keys]').classList.toggle('!text-blue-300', state.keysOpen);
      if (!state.keysOpen) return;
      const shortcuts = state.settings.shortcuts || {};
      panel.innerHTML = `<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">${Object.keys(ACTION_LABELS).map(action => `
        <div class="flex items-center justify-between gap-2"><dt class="text-white/45">${ACTION_LABELS[action]}</dt>
        <dd><kbd class="rounded bg-white/10 px-1.5 py-0.5 font-sans text-white/75">${escape(formatShortcut(shortcuts[action]))}</kbd></dd></div>`).join('')}</dl>`;
    }

    /* Liste der Titel, Episoden oder Sender - die Reihenfolge ist die der
       Wiedergabe. Episoden zeigen Datum, Dauer und wie weit sie gehoert sind. */
    function renderList(scroll = false) {
      if (!root) return;
      const panel = root.querySelector('[data-list-panel]');
      const button = root.querySelector('[data-list]');
      panel.classList.toggle('hidden', !state.listOpen);
      button.setAttribute('aria-expanded', String(state.listOpen));
      button.classList.toggle('!text-blue-300', state.listOpen);
      button.title = { podcast: 'Episoden', radio: 'Sender' }[mode()] || 'Titel';
      if (!state.listOpen) return;
      const playingId = current().track?.id;
      const rows = state.order.slice(0, 300).map((index) => {
        const item = state.tracks[index];
        if (!item) return '';
        const active = item.id === playingId;
        let meta = [item.artist, item.album].filter(Boolean).join(' · ');
        let progress = '';
        if (mode() === 'podcast') {
          meta = [day(item.published), item.duration ? clock(item.duration) : ''].filter(Boolean).join(' · ');
          const share = item.duration && item.position ? Math.min(100, Math.round(item.position / item.duration * 100)) : 0;
          progress = item.done
            ? `<span class="shrink-0 text-emerald-300/70" title="Gehört">${icon('check', 'h-3.5 w-3.5')}</span>`
            : (share ? `<span class="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-white/10"><span class="block h-full rounded-full bg-blue-300/70" style="width:${share}%"></span></span>` : '');
        } else if (mode() === 'radio') {
          meta = item.artist || '';
        }
        return `<button type="button" data-list-item="${index}"${active ? ' data-active' : ''} class="flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left transition ${active ? 'bg-white/10 text-white' : 'text-white/70 hover:bg-white/5 hover:text-white'}">
          <span class="h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'bg-blue-300 shadow-[0_0_8px_2px_rgba(147,197,253,.6)]' : (item.done ? 'bg-white/15' : 'bg-white/30')}"></span>
          <span class="min-w-0 flex-1"><span class="block truncate text-xs ${item.done && !active ? 'text-white/45' : ''}">${escape(item.title)}</span>
          ${meta ? `<span class="block truncate text-[10px] text-white/40">${escape(meta)}</span>` : ''}</span>${progress}</button>`;
      }).join('');
      panel.innerHTML = rows || `<p class="px-2 py-2 text-xs text-white/45">${escape(state.problem || 'Nichts zu zeigen')}</p>`;
      if (scroll) panel.querySelector('[data-active]')?.scrollIntoView({ block: 'nearest' });
    }

    function show(element, visible) {
      const wasHidden = element.classList.contains('hidden');
      element.classList.toggle('hidden', !visible);
      element.classList.toggle('flex', visible && element.matches('[data-pill]'));
      if (visible && wasHidden) {
        element.removeAttribute('data-enter');
        void element.offsetWidth;
        element.setAttribute('data-enter', '');
      }
    }

    function render() {
      if (!root || !state.settings) return;
      const settings = state.settings;
      if (mobile()) place();  // die Tab-Leiste kann je nach Ansicht wandern
      // Mobil verschwindet der Player beim Tippen ganz - fixiert am unteren
      // Rand laege er auf iOS sonst hinter der Tastatur.
      const away = !screensaverOpen() && (state.typing || keyboardOpen());
      if (away && state.expanded) { state.expanded = false; state.keysOpen = false; state.listOpen = false; }
      const usable = inArea() && hasSource() && state.tracks.length > 0 && !away;
      const mode = settings.visibility;
      const bar = usable && (state.expanded || (mode === 'visible' && !state.autoHidden));
      const pill = usable && !bar && (mode !== 'hidden' || state.needsGesture);
      const flashing = !root.querySelector('[data-flash]').classList.contains('hidden');
      show(root.querySelector('[data-bar]'), bar);
      show(root.querySelector('[data-pill]'), pill);
      root.classList.toggle('hidden', !bar && !pill && !flashing);
      root.classList.toggle('flex', bar || pill || flashing);
      renderState();
      renderProgress();
      renderVolume();
      renderModes();
      renderKeys();
      renderList();
      renderMenu();
    }

    // Das Auge im Menue zeigt, was ein Tipp bewirkt.
    function renderMenu() {
      const hidden = state.settings.visibility === 'hidden';
      document.querySelectorAll('[data-player-toggle]').forEach((button) => {
        if (button.dataset.hiddenState === String(hidden)) return;
        button.dataset.hiddenState = String(hidden);
        const label = hidden ? 'Player einblenden' : 'Player ausblenden';
        button.title = label;
        button.setAttribute('aria-label', label);
        button.innerHTML = icon(hidden ? 'hidden' : 'eye', 'h-4 w-4');
      });
    }


    return { build, render, renderProgress, renderState, renderTrack, renderVolume, renderModes, renderList, flash, place, settle, wake, formatRate };
  }

  return { create, formatShortcut, formatRate, ACTION_LABELS, REPEAT_LABELS };
}());
