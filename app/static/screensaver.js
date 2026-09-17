/* Bildschirmschoner: vollflaechige Diashow als Schreib- und Diktierkulisse.

   Zwei Modi im selben Overlay:

   * **Diashow** — nur Bilder. Jede Taste, jeder Klick weckt auf, wie man es
     von einem Bildschirmschoner erwartet; zurueck geht es dann ueber den
     blauen Knopf neben "Senden". Esc und das Kreuz beenden ganz, dann
     verschwindet auch dieser Knopf. Pfeiltasten blaettern, Strg/Cmd+Alt+B
     startet neu.
   * **Kulisse** — die Bilder bleiben, darueber steht nur das Schreibfeld.
     Getippt wird in eine eigene Textarea, die ihren Wert bei jedem Anschlag in
     `#simple-input` spiegelt; dadurch greifen Entwurfsspeicherung und
     Absenden unveraendert.

   Eine laufende Sprachaufnahme bleibt unberuehrt: `MediaRecorder` haengt nicht
   am sichtbaren DOM, und die Aufnahme-Bedienelemente aus write-session.js
   werden weder entfernt noch neu gerendert. Die Infozeile liest den Stand nur
   ueber `window.journlWriteSession.isRecording()` aus.

   Die Bilder kommen ausschliesslich von `/api/screensaver/*`; der Server holt
   und cacht sie. Damit bleibt die CSP bei `img-src 'self'`. */
(function () {
  const PREFETCH_AHEAD = 3;
  const RELOAD_AT = 4;
  const state = {
    settings: null,
    items: [],
    index: -1,
    timer: null,
    clock: null,
    layer: 0,
    open: false,
    mode: 'screensaver',
    wakeLock: null,
    loading: false,
  };

  let root = null;
  let layers = [];
  let infoLine = null;
  let creditLine = null;
  let editor = null;
  let modeButton = null;
  let hintLine = null;

  function field() { return document.getElementById('simple-input'); }

  // Das Overlay entsteht komplett hier, sein CSS gehoert deshalb ebenfalls
  // hierher statt doppelt in beide Templates.
  function styles() {
    if (document.getElementById('screensaver-style')) return;
    const style = document.createElement('style');
    style.id = 'screensaver-style';
    style.textContent = `
      @keyframes journl-kenburns { from { transform: scale(1); } to { transform: scale(1.08); } }
      @media (prefers-reduced-motion: reduce) {
        #screensaver [data-layer] { animation: none !important; transition: none !important; }
      }`;
    document.head.appendChild(style);
  }

  function build() {
    if (root) return root;
    styles();
    root = document.createElement('div');
    root.id = 'screensaver';
    root.className = 'fixed inset-0 z-[120] hidden bg-black';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-label', 'Bildschirmschoner');
    root.innerHTML = `
      <div data-layer class="absolute inset-0 bg-center bg-cover opacity-0 transition-opacity duration-1000"></div>
      <div data-layer class="absolute inset-0 bg-center bg-cover opacity-0 transition-opacity duration-1000"></div>
      <div data-dim class="absolute inset-0 bg-black pointer-events-none" style="opacity:0"></div>
      <div data-editor-wrap class="absolute inset-0 hidden items-center justify-center px-6 py-16 pointer-events-none">
        <textarea data-editor rows="10" spellcheck="false" placeholder="Schreiben…"
          class="pointer-events-auto w-full max-w-3xl max-h-full resize-none bg-transparent text-gray-50 text-lg leading-relaxed focus:outline-none"
          style="text-shadow:0 2px 12px rgba(0,0,0,.85)"></textarea>
      </div>
      <!-- Ein gemeinsamer Stapel: Infozeile und Lizenznachweis koennen beide
           sichtbar sein (etwa bei "Nur Uhrzeit"), duerfen sich dann aber nicht
           uebereinander legen. Lesbarkeit geht dabei vor Zurueckhaltung - auf
           hellen Bildern stand die Zeile faktisch nicht mehr im Bild. Den
           Kontrast liefert der doppelte Schatten, kein Kasten. -->
      <div class="absolute bottom-4 left-5 max-w-[70%] flex flex-col gap-0.5 pointer-events-none"
           style="text-shadow:0 1px 3px rgba(0,0,0,.95), 0 2px 12px rgba(0,0,0,.85)">
        <div data-info class="text-sm text-white/85 leading-snug"></div>
        <div data-credit class="text-xs text-white/60 leading-snug hidden"></div>
      </div>
      <div class="absolute top-4 right-4 flex items-center gap-1">
        <button type="button" data-settings aria-label="Einstellungen" title="Einstellungen"
                class="rounded-lg p-2 text-white/25 hover:text-white/80 hover:bg-white/10 transition">
          <svg viewBox="0 0 24 24" class="h-5 w-5 pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7.9 19.4l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3 15.9H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9.2l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 10 4.6V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1.3Z"/></svg>
        </button>
        <button type="button" data-mode aria-label="Modus wechseln"
                class="rounded-lg px-2.5 py-2 text-xs font-medium text-white/25 hover:text-white/80 hover:bg-white/10 transition">Kulisse</button>
        <button type="button" data-close aria-label="Schließen" title="Schließen (Esc)"
                class="rounded-lg p-2 text-white/25 hover:text-white/80 hover:bg-white/10 transition">
          <svg viewBox="0 0 24 24" class="h-5 w-5 pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 6 12 12"/><path d="m18 6-12 12"/></svg>
        </button>
      </div>
      <div data-hint class="absolute bottom-4 right-5 text-[11px] text-white/45 pointer-events-none"
           style="text-shadow:0 1px 3px rgba(0,0,0,.95)"></div>`;
    document.body.appendChild(root);

    layers = Array.from(root.querySelectorAll('[data-layer]'));
    infoLine = root.querySelector('[data-info]');
    creditLine = root.querySelector('[data-credit]');
    editor = root.querySelector('[data-editor]');
    modeButton = root.querySelector('[data-mode]');
    hintLine = root.querySelector('[data-hint]');

    root.querySelector('[data-close]').addEventListener('click', () => close({ resume: false }));
    modeButton.addEventListener('click', () => setMode(state.mode === 'focus' ? 'screensaver' : 'focus'));
    root.querySelector('[data-settings]').addEventListener('click', () => {
      close({ resume: false });
      window.openSettingsGroup?.('screensaver', 'Bildschirmschoner');
    });
    // Nur der Kulissen-Modus haelt Eingaben fest; sonst weckt jeder Anschlag auf.
    editor.addEventListener('input', () => {
      const target = field();
      if (!target) return;
      target.value = editor.value;
      target.dispatchEvent(new Event('input', { bubbles: true }));
    });
    root.addEventListener('pointerdown', (event) => {
      if (state.mode === 'focus' || event.target.closest('button') || event.target === editor) return;
      close();
    });
    return root;
  }

  // ─── Einstellungen ────────────────────────────────────────────────

  async function settings() {
    if (state.settings) return state.settings;
    try {
      const response = await window.apiFetch('/api/screensaver/settings', { cache: 'no-store' });
      const data = await response.json();
      state.settings = data.settings;
    } catch (_) {
      state.settings = { interval: 30, sources: ['theme:landscapes'], info: 'full', transition: 'fade', dim: 20, mode: 'screensaver' };
    }
    return state.settings;
  }

  // Die Einstellungsmaske meldet Aenderungen, damit ein offener Schoner sie
  // sofort uebernimmt statt erst beim naechsten Start.
  document.addEventListener('journl:screensaver-settings', (event) => {
    state.settings = event.detail || null;
    if (!state.open) return;
    applyLook();
    schedule();
  });

  // ─── Bilder ───────────────────────────────────────────────────────

  async function loadPlaylist(replace) {
    if (state.loading) return;
    state.loading = true;
    try {
      const response = await window.apiFetch('/api/screensaver/playlist?count=12', { cache: 'no-store' });
      const data = await response.json();
      const items = Array.isArray(data.items) ? data.items : [];
      if (!items.length) {
        if (replace) showMessage(data.error || 'Keine Bilder gefunden.');
        return;
      }
      state.items = replace ? items : state.items.concat(items);
      if (replace) state.index = -1;
      prefetch();
    } catch (_) {
      if (replace) showMessage('Bilder konnten nicht geladen werden.');
    } finally {
      state.loading = false;
    }
  }

  function prefetch() {
    for (let step = 1; step <= PREFETCH_AHEAD; step += 1) {
      const next = state.items[state.index + step];
      if (next) new Image().src = next.src;
    }
  }

  function showMessage(text) {
    if (!infoLine) return;
    infoLine.classList.remove('hidden');
    infoLine.textContent = text;
  }

  function advance() {
    if (!state.items.length) return;
    state.index += 1;
    if (state.index >= state.items.length) {
      // Am Ende der Liste von vorn, damit nie eine Luecke entsteht.
      state.index = 0;
      loadPlaylist(true);
    } else if (state.items.length - state.index <= RELOAD_AT) {
      loadPlaylist(false);
    }
    show(state.items[state.index]);
    prefetch();
  }

  // Zurueck bleibt innerhalb der geladenen Liste - nachladen wuerde hier neue
  // Bilder anhaengen, nicht die schon gesehenen zurueckholen.
  function rewind() {
    if (!state.items.length) return;
    state.index = state.index <= 0 ? state.items.length - 1 : state.index - 1;
    show(state.items[state.index]);
  }

  function show(item) {
    if (!item) return;
    const next = layers[state.layer];
    const current = layers[1 - state.layer];
    const kenburns = state.settings?.transition === 'kenburns';
    next.style.backgroundImage = `url("${item.src}")`;
    next.style.animation = 'none';
    if (kenburns) {
      // Reflow erzwingen, sonst startet dieselbe Animation nicht neu.
      void next.offsetWidth;
      next.style.animation = `journl-kenburns ${Math.max(10, state.settings.interval + 2)}s ease-out forwards`;
    }
    next.style.opacity = '1';
    current.style.opacity = '0';
    state.layer = 1 - state.layer;
    renderInfo(item);
  }

  // ─── Infozeile ────────────────────────────────────────────────────

  function credit(item) {
    const parts = [item.title, item.author].filter(Boolean);
    const license = item.license ? `(${item.license})` : '';
    return [parts.join(' — '), license].filter(Boolean).join(' ');
  }

  /* Die laufende Aufnahmezeit steht als letztes und in Zehnersekunden.

     Beides dient der Ruhe im Bild: als letztes Element schiebt eine wachsende
     Zahl nichts mehr vor sich her, und ein Wechsel alle zehn Sekunden faellt
     im Augenwinkel kaum auf. Die restliche Breitenaenderung faengt
     `tabular-nums` ab - damit ist jede Ziffer gleich breit, die Zeile steht
     also selbst beim Wechsel von 09:50 auf 10:00 still. */
  function recordingTime() {
    const total = window.journlWriteSession?.recordingSeconds?.();
    if (typeof total !== 'number') return '';
    const steady = Math.floor(total / 10) * 10;
    const minutes = Math.floor(steady / 60);
    const seconds = String(steady % 60).padStart(2, '0');
    if (minutes < 60) return `${String(minutes).padStart(2, '0')}:${seconds}`;
    return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}:${seconds}`;
  }

  function renderInfo(item) {
    const mode = state.settings?.info || 'full';
    const current = item || state.items[state.index] || {};
    const pieces = [];
    if (mode === 'full' || mode === 'clock') {
      pieces.push(new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date()));
    }
    if (mode === 'full' || mode === 'image') {
      const text = credit(current);
      if (text) pieces.push(text);
    }
    const elapsed = (mode === 'full' || mode === 'clock') ? recordingTime() : '';
    infoLine.textContent = pieces.join('  ·  ');
    if (elapsed) {
      const timer = document.createElement('span');
      timer.className = 'font-mono tabular-nums text-white/60';
      timer.textContent = `${pieces.length ? '  ·  ' : ''}${elapsed}`;
      infoLine.appendChild(timer);
    }
    infoLine.classList.toggle('hidden', !pieces.length && !elapsed);

    // Freie Lizenzen verlangen die Namensnennung. Wer die Infozeile abschaltet,
    // bekommt deshalb weiterhin einen minimalen Nachweis - nur eben so leise,
    // dass er die Kulisse nicht stoert.
    const needsCredit = mode !== 'full' && mode !== 'image' && current.license && current.author;
    creditLine.textContent = needsCredit ? credit(current) : '';
    creditLine.classList.toggle('hidden', !needsCredit);
  }

  function fadeSeconds() {
    const config = state.settings || {};
    if (config.transition === 'cut') return 0;
    return typeof config.fade_seconds === 'number' ? config.fade_seconds : 1;
  }

  function applyLook() {
    const config = state.settings || {};
    root.querySelector('[data-dim]').style.opacity = String((config.dim || 0) / 100);
    layers.forEach(layer => { layer.style.transitionDuration = `${fadeSeconds()}s`; });
  }

  // ─── Modi ─────────────────────────────────────────────────────────

  // Pfeiltasten nur dort erwaehnen, wo es welche gibt.
  function hintText() {
    const keyboard = window.matchMedia?.('(pointer: fine)')?.matches;
    const parts = keyboard ? ['←/→ Bild'] : [];
    parts.push('Esc beendet', 'Taste oder Klick pausiert');
    return parts.join('  ·  ');
  }

  function setMode(mode) {
    state.mode = mode === 'focus' ? 'focus' : 'screensaver';
    const focus = state.mode === 'focus';
    root.querySelector('[data-editor-wrap]').classList.toggle('hidden', !focus);
    root.querySelector('[data-editor-wrap]').classList.toggle('flex', focus);
    modeButton.textContent = focus ? 'Diashow' : 'Kulisse';
    hintLine.textContent = focus ? 'Esc beendet' : hintText();
    if (focus) {
      editor.value = field()?.value || '';
      editor.focus({ preventScroll: true });
    } else {
      editor.blur();
    }
  }

  // ─── Tastatur ─────────────────────────────────────────────────────

  function onKeydown(event) {
    if (!state.open) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close({ resume: false });
      return;
    }
    if (state.mode === 'focus') return;
    // Reine Modifikatoren wecken nicht auf - sonst beendet schon das Greifen
    // nach einer Tastenkombination die Diashow.
    if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'Tab'].includes(event.key)) return;
    if (event.key === 'ArrowRight' || event.key === ' ') {
      event.preventDefault();
      advance();
      schedule();
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      rewind();
      schedule();
      return;
    }
    close();
  }

  // ─── Lauf ─────────────────────────────────────────────────────────

  function schedule() {
    clearInterval(state.timer);
    const seconds = Math.max(5, state.settings?.interval || 30);
    state.timer = setInterval(advance, seconds * 1000);
  }

  async function acquireWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try { state.wakeLock = await navigator.wakeLock.request('screen'); }
    catch (_) { state.wakeLock = null; }
  }

  async function open(mode) {
    build();
    const config = await settings();
    state.open = true;
    state.mode = mode || config.mode || 'screensaver';
    root.classList.remove('hidden');
    applyLook();
    setMode(state.mode);
    document.addEventListener('keydown', onKeydown, true);
    acquireWakeLock();
    if (!state.items.length) await loadPlaylist(true);
    if (state.index < 0) advance();
    else renderInfo();
    schedule();
    clearInterval(state.clock);
    state.clock = setInterval(() => renderInfo(), 10000);
    resumeButton()?.classList.add('hidden');
  }

  /* `resume: false` beendet den Bildschirmschoner ganz: dann verschwindet auch
     der blaue Rueckkehr-Knopf. Nur das beilaeufige Aufwecken (Taste, Klick)
     laesst ihn stehen, weil man da erkennbar nur kurz weg will. */
  function close(options) {
    if (!state.open) return;
    const resume = options?.resume !== false;
    state.open = false;
    clearInterval(state.timer);
    clearInterval(state.clock);
    document.removeEventListener('keydown', onKeydown, true);
    root.classList.add('hidden');
    state.wakeLock?.release?.().catch(() => {});
    state.wakeLock = null;
    if (state.mode === 'focus' && field()) field().value = editor.value;
    resumeButton()?.classList.toggle('hidden', !resume);
    // Zurueck im Schreibfeld, damit der naechste Anschlag wieder ankommt.
    const target = field();
    if (target && !document.getElementById('tab-write')?.classList.contains('hidden')) {
      target.focus({ preventScroll: true });
    }
  }

  // ─── Einstiege ────────────────────────────────────────────────────

  function resumeButton() { return document.getElementById('screensaver-resume'); }

  document.addEventListener('click', (event) => {
    const starter = event.target.closest('[data-screensaver-start]');
    if (starter) {
      document.getElementById('burger-menu')?.classList.add('hidden');
      open(starter.dataset.screensaverStart || '');
      return;
    }
    if (event.target.closest('[data-screensaver-settings]')) {
      document.getElementById('burger-menu')?.classList.add('hidden');
      window.openSettingsGroup?.('screensaver', 'Bildschirmschoner');
    }
  });

  // Strg/Cmd + Alt + B startet den Schoner von ueberall her - dieselbe Familie
  // wie Strg/Cmd + Alt + K fuer den Schreibmodus.
  document.addEventListener('keydown', (event) => {
    if (state.open) return;
    if (!(event.ctrlKey || event.metaKey) || !event.altKey || event.shiftKey) return;
    if (event.code !== 'KeyB') return;
    event.preventDefault();
    open('');
  });

  window.journlScreensaver = { open, close, isOpen: () => state.open };
}());
