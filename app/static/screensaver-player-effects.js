/* Lichteffekte des Players, alle einzeln schaltbar (`settings.effects`):

   * color    - Akzent und Glow folgen dem Bild: im offenen Schoner dem
                aktuellen Schoner-Bild (`journl:screensaver-image`), sonst dem
                Cover. Die Farbe kommt aus einem 24x24-Abzug auf einem Canvas
                (alles Same-Origin, die CSP bleibt unberuehrt) und blendet ueber
                die registrierte Eigenschaft `--pa-color` weich um.
   * reactive - ein Aurora-Leuchten hinter Pille und Leiste folgt den tiefen
                Frequenzen (Web-Audio-Analyser, `--pa-energy`).
   * glass    - Frosted Glass mit wandernder Lichtkante; aus heisst: dunkle,
                deckende Flaeche ohne Unschaerfe.
   * breathe  - langsames Pulsieren des Glows, solange etwas spielt.

   Das CSS dazu liegt in screensaver-player-view.js; hier werden nur Attribute
   (`data-fx-*`) und Variablen am Player gesetzt.

   Der Analyser braucht die Decks im Audio-Graphen. Das geschieht erst, wenn
   „audio-reaktiv“ an ist oder ein anderes Modul ihn per `demand()` anfordert
   (das Klangbild des Schoners), und erst, wenn der AudioContext wirklich laeuft -
   ein haengender Kontext wuerde den Ton sonst stumm schalten. Einmal
   angeschlossen bleibt ein Deck im Graphen (das laesst sich nicht
   rueckgaengig machen); der Ton fliesst dann eben durch den Kontext. */
window.journlScreensaverPlayerEffects = (function () {
  const DEFAULT_COLOR = [96, 165, 250];
  const SAMPLE = 24;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');
  /* Safari/WebKit (auch die Mac-App): ein Deck im Audio-Graphen springt dort
     beim Spulen, nach Pause und zwischendurch an den Titelanfang, und Live-
     Streams liefern ohnehin nur Stille. Dort wird nie angeschlossen - das
     Klangbild nimmt dann den simulierten Takt (`levels()` bleibt null). */
  const webkit = /apple/i.test(navigator.vendor || '') && !/chrome|crios|fxios|edg/i.test(navigator.userAgent);

  let root = null;
  let audios = [];
  let options = { color: true, reactive: false, glass: true, breathe: false };
  let cover = '';
  let scene = '';
  let wanted = '';
  let playing = false;
  let context = null;
  let analyser = null;
  let bins = null;
  let sharp = null;
  let fresh = null;
  let before = null;
  let onset = 0;
  let frame = 0;
  let energy = 0;
  let baseline = 0;
  const colors = new Map();
  const wired = new WeakSet();
  const demands = new Set();
  // Bass, Mitten, Hoehen 0..1 - fuer alle, die den Analyser mitbenutzen.
  const bands = { bass: 0, mid: 0, high: 0 };
  // Dieselben Baender ohne Nachlauf - fuer Bewegung im Takt.
  const quick = { bass: 0, mid: 0, high: 0 };

  // ─── Farbe aus dem Bild ───────────────────────────────────────────

  function rgbToHsl(r, g, b) {
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h;
    if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    return [h / 6, s, l];
  }

  function hslToRgb(h, s, l) {
    const hue = (p, q, t) => {
      const x = t < 0 ? t + 1 : (t > 1 ? t - 1 : t);
      if (x < 1 / 6) return p + (q - p) * 6 * x;
      if (x < 1 / 2) return q;
      if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
      return p;
    };
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    return [hue(p, q, h + 1 / 3), hue(p, q, h), hue(p, q, h - 1 / 3)].map(v => Math.round(v * 255));
  }

  /* Gewichteter Mittelwert, bei dem kraeftige, nicht zu dunkle Pixel zaehlen:
     ein blauer Himmel ueber grauem Fels soll blau leuchten, nicht grau. Das
     Ergebnis wird auf eine Helligkeit gebracht, die im Dark Mode gluehen kann. */
  function dominant(pixels) {
    let r = 0; let g = 0; let b = 0; let total = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      const [red, green, blue] = [pixels[index] / 255, pixels[index + 1] / 255, pixels[index + 2] / 255];
      const max = Math.max(red, green, blue);
      const saturation = max ? (max - Math.min(red, green, blue)) / max : 0;
      const weight = saturation * saturation * (max > 0.18 ? max : 0) + 0.002;
      r += red * weight; g += green * weight; b += blue * weight; total += weight;
    }
    const [h, s] = rgbToHsl(r / total, g / total, b / total);
    if (s < 0.08) return DEFAULT_COLOR;  // Schwarzweiss: beim Blau bleiben
    return hslToRgb(h, Math.min(0.85, Math.max(0.5, s)), 0.66);
  }

  function extract(src) {
    if (colors.has(src)) return Promise.resolve(colors.get(src));
    return new Promise((resolve) => {
      const image = new Image();
      image.decoding = 'async';
      image.onload = () => {
        let color = null;
        try {
          const canvas = document.createElement('canvas');
          canvas.width = SAMPLE;
          canvas.height = SAMPLE;
          const painter = canvas.getContext('2d', { willReadFrequently: true });
          painter.drawImage(image, 0, 0, SAMPLE, SAMPLE);
          color = dominant(painter.getImageData(0, 0, SAMPLE, SAMPLE).data);
        } catch (_) { /* fremdes oder kaputtes Bild: Standardfarbe */ }
        if (colors.size > 200) colors.clear();
        colors.set(src, color);
        resolve(color);
      };
      image.onerror = () => resolve(null);
      image.src = src;
    });
  }

  /* Drei verwandte Farben aus einem Bild: die Grundfarbe, eine hellere
     Nachbarin und eine kraeftigere Gegenstimme - genug fuer einen Verlauf. */
  function palette(src) {
    return (src ? extract(src) : Promise.resolve(null)).then((color) => {
      const [h, s] = rgbToHsl(...(color || DEFAULT_COLOR).map(v => v / 255));
      return [hslToRgb(h, s, 0.6), hslToRgb((h + 0.09) % 1, Math.min(1, s + 0.1), 0.72),
        hslToRgb((h + 0.88) % 1, Math.min(1, s + 0.15), 0.5)];
    });
  }

  function setColor(color) {
    root?.style.setProperty('--pa-color', `rgb(${(color || DEFAULT_COLOR).join(' ')})`);
  }

  function pickColor() {
    if (!root) return;
    const open = Boolean(window.journlScreensaver?.isOpen?.());
    const src = options.color ? ((open && scene) || cover || '') : '';
    wanted = src;
    if (!src) { setColor(null); return; }
    extract(src).then((color) => { if (wanted === src) setColor(color); });
  }

  // ─── Audio-reaktiv ────────────────────────────────────────────────

  function needed() {
    return Boolean(options.reactive) || demands.size > 0;
  }

  function connect() {
    if (!context || context.state !== 'running') return;
    if (!analyser) {
      analyser = context.createAnalyser();
      // 512 Punkte: ein Band ~94 Hz breit, fein genug, um Bass zu trennen.
      analyser.fftSize = 512;
      analyser.smoothingTimeConstant = 0.82;
      bins = new Uint8Array(analyser.frequencyBinCount);
      /* Ein zweiter, kaum geglaetteter Analyser fuer Schlaege: der erste
         zieht einen Kick ueber Zehntelsekunden, das wird nie ein Puls.
         1024 Punkte: ~47 Hz je Band, Kick (50-120 Hz) faellt nicht mehr
         zwischen zwei Baender. */
      sharp = context.createAnalyser();
      sharp.fftSize = 1024;
      sharp.smoothingTimeConstant = 0.3;
      fresh = new Uint8Array(sharp.frequencyBinCount);
      before = new Uint8Array(sharp.frequencyBinCount);
    }
    audios.forEach((audio) => {
      if (wired.has(audio)) return;
      const source = context.createMediaElementSource(audio);
      source.connect(context.destination);
      source.connect(analyser);
      source.connect(sharp);
      wired.add(audio);
    });
    loop();
  }

  /* Aus `play()` aufgerufen - meist also mit einer Geste, die der Browser
     zum Starten des Kontexts verlangt. Ohne Geste wartet das Anschliessen
     auf die naechste Beruehrung. */
  function wake() {
    if (webkit || !needed() || !root) return;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return;
    if (!context) {
      try { context = new Context(); } catch (_) { return; }
    }
    if (context.state === 'running') { connect(); return; }
    context.resume().then(connect).catch(() => {});
  }

  function average(from, to, data = bins) {
    let sum = 0;
    for (let index = from; index < to; index += 1) sum += data[index];
    return sum / ((to - from) * 255);
  }

  /* Spektraler Fluss: nur was gegenueber dem letzten Bild lauter wurde.
     Tiefen zaehlen voll (Kick), die Mitten halb (Snare, Anschlaege) - so
     findet es auch Musik ohne durchgehenden Bass. Gemerkt wird die Spitze
     seit dem letzten `levels()`, damit ein Klangbild mit 24 fps keinen
     Schlag verpasst. */
  function listenSharp() {
    sharp.getByteFrequencyData(fresh);
    let low = 0;
    let mid = 0;
    for (let index = 1; index < 96; index += 1) {
      const rise = fresh[index] - before[index];
      if (rise > 0) {
        if (index < 4) low += rise;
        else mid += rise;
      }
      before[index] = fresh[index];
    }
    onset = Math.max(onset, low / (3 * 255) + 0.5 * mid / (92 * 255));
    quick.bass = average(1, 4, fresh);
    quick.mid = average(4, 64, fresh);
    quick.high = average(64, 220, fresh);
  }

  function loop() {
    cancelAnimationFrame(frame);
    const active = needed() && playing && analyser;
    const aurora = options.reactive && !reduced?.matches;
    if (!active || !aurora) {
      energy = 0;
      root?.style.setProperty('--pa-energy', '0');
    }
    if (!active) {
      bands.bass = 0; bands.mid = 0; bands.high = 0;
      quick.bass = 0; quick.mid = 0; quick.high = 0;
      onset = 0;
      return;
    }
    const tick = () => {
      analyser.getByteFrequencyData(bins);
      bands.bass = average(1, 4);
      bands.mid = average(4, 32);
      bands.high = average(32, 110);
      listenSharp();
      if (aurora) {
        // Bass und untere Mitten - dort sitzt der Puls der Musik.
        const raw = average(1, 40);
        // Gegen den eigenen gleitenden Mittelwert: so pulsiert es auch bei
        // dauerhaft lauter Musik im Takt, statt einfach hell zu bleiben.
        baseline = baseline ? baseline * 0.985 + raw * 0.015 : raw;
        const pulse = Math.max(0, Math.min(1, 0.3 + (raw - baseline) * 5));
        energy = energy * 0.7 + pulse * 0.3;
        root.style.setProperty('--pa-energy', energy.toFixed(3));
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  }

  // ─── Schnittstelle fuer den Player ────────────────────────────────

  function apply() {
    if (!root) return;
    root.toggleAttribute('data-fx-color', Boolean(options.color));
    root.toggleAttribute('data-fx-reactive', Boolean(options.reactive));
    root.toggleAttribute('data-fx-glass', Boolean(options.glass));
    root.toggleAttribute('data-fx-breathe', Boolean(options.breathe));
    pickColor();
    if (needed() && playing) wake();
    loop();
  }

  function mount(element, elements) {
    root = element;
    audios = elements;
    apply();
  }

  function configure(effects) {
    options = { ...options, ...(effects || {}) };
    apply();
  }

  function setCover(src) {
    if (cover === (src || '')) return;
    cover = src || '';
    pickColor();
  }

  function setPlaying(value) {
    playing = Boolean(value);
    if (playing && needed()) wake();
    loop();
  }

  /* Ein anderes Modul braucht den Analyser (`name` fuer an/aus je Nutzer).
     Wer auf iOS laeuft, fragt gar nicht erst - dort bliebe der Ton sonst im
     Hintergrund stehen (siehe configureEffects im Player). */
  function demand(name, wanted) {
    if (wanted) demands.add(name);
    else demands.delete(name);
    if (needed() && playing) wake();
    loop();
  }

  /* Null, solange nichts Echtes zu hoeren ist - dann simuliert der Aufrufer.
     `onset` ist die Schlag-Spitze seit dem letzten Aufruf und wird dabei
     geleert - es gibt nur einen Leser, das Klangbild. */
  function levels() {
    if (!(playing && analyser && context?.state === 'running' && needed())) return null;
    const heard = { ...bands, quick: { ...quick }, onset };
    onset = 0;
    return heard;
  }

  document.addEventListener('journl:screensaver-image', (event) => {
    scene = event.detail?.src || '';
    pickColor();
  });
  document.addEventListener('journl:screensaver-opened', pickColor);
  document.addEventListener('journl:screensaver-closed', pickColor);
  // Ein Kontext, der ohne Geste nicht anlaufen durfte, bekommt die naechste.
  document.addEventListener('pointerdown', () => { if (needed() && context && context.state !== 'running') wake(); }, { passive: true });
  reduced?.addEventListener?.('change', loop);

  return { mount, configure, setCover, setPlaying, wake, demand, levels, palette };
}());
