/* Klangbild: eine Effektebene im Bildschirmschoner, die sich zur Musik bewegt.

   Formen aus einem WebGL-Fragment-Shader (`settings.visual.form`):

   * lava    - ein organischer Klecks: Bass laesst ihn pulsieren, die Mitten
               verformen den Rand, Hoehen lassen ihn flimmern.
   * blobs   - vier Kleckse, je einer an einem Band, die ineinanderfliessen.
   * ink     - Farbschlieren wie Tinte im Wasser (Domain-Warping-Rauschen;
               eine echte Stroemungssimulation waere viel teurer).
   * kaleido - Kaleidoskop: ein Rauschmuster, in Keilen gespiegelt.
   * mandala - psychedelische Figur aus vier Rosenkurven, die gegeneinander
               drehen und je an einem Band atmen.
   * tunnel  - Ringe und Streifen, die auf den Betrachter zufliegen.
   * fractal - Julia-Menge, deren Parameter im Kreis wandert und morpht.
   * plasma  - ueberlagerte Sinuswellen, Farbe ueber den ganzen Schirm.

   Die Reaktion (`settings.visual.reaction`) bestimmt, wie hart die Musik
   durchschlaegt: `free` hoert gar nicht hin und wabert in einem Tempo, das
   langsam und unregelmaessig schneller und ruhiger wird; `soft` wabert nur
   mit den Baendern, `beat` stoesst bei
   jedem erkannten Schlag (Zoom, Aufhellen, Drehschub), `wild` zusaetzlich
   mit Farbspruengen, wechselnder Keilzahl und Richtungswechseln. Schlaege
   erkennt ein Schwellwert ueber dem spektralen Fluss aus
   screensaver-player-effects.js; ein Mindestabstand haelt das Blitzen
   unter gut drei pro Sekunde. Die Reaktion waehlt der Nutzer ausdruecklich -
   sie gilt deshalb auch bei „Bewegung reduzieren“ im System (das hatte
   vorher jede Form still auf Sanft gedrosselt, die Musik kam nie an); dort
   wird nur das Aufhellen halbiert.

   Die Ebene liegt in der Diashow ueber Bild und Abdunklung, in Kulisse und
   Fragen darunter - Text bleibt lesbar. Gemischt wird per `mix-blend-mode`:
   Leuchten (screen), Farbschleier (soft-light), Kraeftig (normal). Ohne Bild
   (Quelle „Keine Bilder“) leuchtet sie auf Schwarz in kraeftigerer Farbe.

   Den Ton liefert der Analyser aus screensaver-player-effects.js
   (`demand`/`levels`). Auf iOS gibt es ihn nicht - eine echte Analyse wuerde
   dort die Wiedergabe im Hintergrund anhalten -, dann bewegt sich der Klecks
   in einem simulierten Takt, solange etwas spielt. Ohne Musik atmet er
   ruhig weiter.

   Gerechnet wird nur bei offenem Schoner; requestAnimationFrame ruht ohnehin
   im Hintergrund-Tab. Die Qualitaet bestimmt Aufloesung und Bildrate. */
window.journlScreensaverVisual = (function () {
  const FORMS = ['lava', 'blobs', 'ink', 'kaleido', 'mandala', 'tunnel', 'fractal', 'plasma'];
  const FORM_LABELS = {
    lava: 'Lavalampe', blobs: 'Kleckse', ink: 'Tinte', kaleido: 'Kaleidoskop',
    mandala: 'Mandala', tunnel: 'Tunnel', fractal: 'Fraktal', plasma: 'Plasma',
  };
  const BLENDS = { glow: 'screen', tint: 'soft-light', bold: 'normal' };
  const QUALITY = {
    saver: { scale: 0.34, fps: 24, octaves: 3, steps: 32 },
    balanced: { scale: 0.5, fps: 30, octaves: 4, steps: 48 },
    full: { scale: 1, fps: 60, octaves: 5, steps: 80 },
  };
  /* Wie hart die Musik durchschlaegt. gain: Baender verstaerkt; attack und
     release: Anstieg/Abfall je Bild bei 60 fps; zoom, flash, jolt, spin:
     Stoss je Schlag (Zoom nach innen, Aufhellen, Zeitsprung, Drehschub). */
  const REACTIONS = {
    free: { quick: false, free: true, gain: 1, attack: 0.1, release: 0.1, zoom: 0, flash: 0, jolt: 0, spin: 0, jumps: false },
    soft: { quick: false, gain: 1, attack: 0.45, release: 0.08, zoom: 0, flash: 0, jolt: 0, spin: 0, jumps: false },
    beat: { quick: true, gain: 1.3, attack: 0.9, release: 0.18, zoom: 0.09, flash: 0.3, jolt: 0.25, spin: 1.2, jumps: false },
    wild: { quick: true, gain: 1.7, attack: 1, release: 0.3, zoom: 0.18, flash: 0.45, jolt: 0.6, spin: 2.6, jumps: true },
  };
  const SEGMENTS = [6, 8, 5, 10, 12, 7];
  // Mindestabstand zweier Schlaege: haelt das Aufhellen unter ~3,5 pro Sekunde.
  const REFRACTORY = 0.28;
  const DEFAULT = { enabled: false, form: 'lava', blend: 'glow', strength: 70, quality: 'balanced', reaction: 'beat' };
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)');

  const VERTEX = `
    attribute vec2 a_position;
    void main() { gl_Position = vec4(a_position, 0.0, 1.0); }`;

  // WebGL 1: Schleifen brauchen feste Grenzen, deshalb `break` statt Variable.
  // highp, wo es geht: das Fraktal zerfiele mit mediump (fp16) in Bloecke.
  const FRAGMENT = `
    #ifdef GL_FRAGMENT_PRECISION_HIGH
    precision highp float;
    #else
    precision mediump float;
    #endif
    uniform vec2 u_res;
    uniform float u_time, u_bass, u_mid, u_high, u_strength, u_bare;
    uniform float u_kick, u_zoom, u_flash, u_shift, u_angle, u_segments;
    uniform vec3 u_c1, u_c2, u_c3;
    uniform int u_form, u_octaves, u_steps;

    float hash(vec2 p) { p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
    float noise(vec2 p) {
      vec2 i = floor(p); vec2 f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
    }
    float fbm(vec2 p) {
      float v = 0.0; float a = 0.5;
      for (int i = 0; i < 6; i++) {
        if (i >= u_octaves) break;
        v += a * noise(p); p = p * 2.03 + vec2(17.1, 9.2); a *= 0.5;
      }
      return v;
    }

    // Zyklisch durch die drei Farben; u_shift dreht die Palette (Farbsprung).
    vec3 pal(float x) {
      x = fract(x + u_shift) * 3.0;
      vec3 a = x < 1.0 ? u_c1 : (x < 2.0 ? u_c2 : u_c3);
      vec3 b = x < 1.0 ? u_c2 : (x < 2.0 ? u_c3 : u_c1);
      return mix(a, b, smoothstep(0.0, 1.0, fract(x)));
    }
    // Bildfarben sind oft gedeckt - psychedelisch braucht es Saettigung.
    vec3 vivid(vec3 c) {
      float l = dot(c, vec3(0.299, 0.587, 0.114));
      return mix(vec3(l), c, 1.6);
    }
    vec2 turn(vec2 p, float a) { float c = cos(a); float s = sin(a); return vec2(c * p.x - s * p.y, s * p.x + c * p.y); }

    vec4 lava(vec2 p, float t) {
      vec2 c = vec2(sin(t * 0.11) * 0.22, cos(t * 0.083) * 0.12);
      vec2 d = p - c;
      float r = length(d);
      vec2 dir = d / max(r, 0.0001);
      // Der Rand folgt Rauschen ueber der Richtung - stetig rundum, ohne Naht.
      // Nur zwei grobe Lagen: feiner wuerde er zackig statt zaehfluessig.
      float wobble = noise(dir * 1.1 + vec2(t * 0.16, -t * 0.12)) * 0.7
        + noise(dir * 2.3 + vec2(-t * 0.21, t * 0.17)) * 0.3 - 0.5;
      float radius = 0.26 + 0.12 * u_bass + 0.02 * sin(t * 0.5);
      float edge = r - radius * (1.0 + wobble * (0.5 + 0.55 * u_mid));
      float body = smoothstep(0.05, -0.05, edge) * 0.92;
      float halo = exp(-max(edge, 0.0) * 6.0) * 0.4;
      float inner = fbm(p * 2.2 + vec2(t * 0.07, t * 0.05));
      vec3 col = mix(u_c1, u_c2, smoothstep(0.25, 0.75, inner));
      col = mix(col, u_c3, smoothstep(0.0, -0.18, edge) * 0.35);
      float shimmer = (noise(p * 38.0 + t * 4.0) - 0.5) * u_high * 0.5 * smoothstep(0.06, 0.0, abs(edge));
      return vec4(col + shimmer, max(body, halo));
    }

    vec4 blobs(vec2 p, float t) {
      float field = 0.0; vec3 acc = vec3(0.0);
      for (int i = 0; i < 4; i++) {
        float k = float(i);
        vec2 c = vec2(sin(t * (0.13 + k * 0.031) + k * 1.7) * 0.42, cos(t * (0.11 + k * 0.027) + k * 2.3) * 0.26);
        float level = i == 0 ? u_bass : (i == 1 ? u_mid : (i == 2 ? u_high : (u_bass + u_mid) * 0.5));
        float r = 0.10 + 0.09 * level + 0.015 * sin(t + k);
        float f = r * r / max(dot(p - c, p - c), 0.0001);
        field += f;
        vec3 tint = i == 0 ? u_c1 : (i == 1 ? u_c2 : (i == 2 ? u_c3 : mix(u_c1, u_c2, 0.5)));
        acc += tint * f;
      }
      vec3 col = acc / max(field, 0.0001);
      float body = smoothstep(0.85, 1.15, field);
      float halo = smoothstep(0.15, 0.9, field) * 0.4;
      return vec4(col, max(body, halo));
    }

    vec4 ink(vec2 p, float t) {
      vec2 s = p * 1.6;
      float speed = 0.05 + 0.08 * u_bass;
      vec2 q = vec2(fbm(s + vec2(0.0, t * speed)), fbm(s + vec2(5.2, 1.3) - t * speed));
      vec2 r = vec2(fbm(s + 4.0 * q + vec2(1.7, 9.2) + t * 0.07), fbm(s + 4.0 * q + vec2(8.3, 2.8) - t * 0.06));
      float f = fbm(s + (3.2 + 2.0 * u_mid) * r);
      vec3 col = mix(u_c1, u_c2, clamp(f * f * 2.2, 0.0, 1.0));
      col = mix(col, u_c3, clamp(length(q) - 0.4, 0.0, 1.0) * 0.7);
      col += (noise(p * 30.0 + t * 3.0) - 0.5) * u_high * 0.25;
      // Ohne Bild deckt die Tinte mehr - auf Schwarz waeren duenne Schlieren zu blass.
      float alpha = smoothstep(mix(0.38, 0.22, u_bare), 0.82, f) * (0.65 + 0.35 * u_bass);
      return vec4(col, alpha);
    }

    // Gespiegelte Keile; das Muster darin wandert nach aussen.
    vec4 kaleido(vec2 p, float t) {
      float r = length(p);
      float seg = 6.2831853 / u_segments;
      float a = mod(atan(p.y, p.x) + u_angle, seg);
      a = abs(a - seg * 0.5);
      vec2 q = vec2(cos(a), sin(a)) * r;
      vec2 s = q * (2.4 + u_bass * 0.9) - vec2(t * 0.25, 0.0);
      float f = fbm(s + fbm(s * 1.7 + t * 0.1) * (1.2 + u_mid * 1.6));
      float rings = 0.5 + 0.5 * sin(r * 18.0 - t * 2.0 - u_kick * 4.0);
      vec3 col = vivid(pal(f * 1.4 + r * 0.6 + rings * 0.15));
      col *= 0.55 + 0.6 * smoothstep(0.25, 0.75, f) + 0.3 * rings * u_high;
      float alpha = smoothstep(0.18, 0.55, f) * (0.75 + 0.25 * rings);
      return vec4(col, alpha * mix(1.0, smoothstep(1.2, 0.2, r), 0.5));
    }

    // Vier Rosenkurven, abwechselnd links- und rechtsherum, je an einem Band.
    vec4 mandala(vec2 p, float t) {
      float r = length(p);
      float a = atan(p.y, p.x);
      vec3 acc = vec3(0.0); float total = 0.0;
      for (int i = 0; i < 4; i++) {
        float k = float(i);
        float petals = floor(u_segments) + k * 2.0;
        float dir = mod(k, 2.0) < 0.5 ? 1.0 : -1.0;
        float level = i == 0 ? u_bass : (i == 1 ? u_mid : (i == 2 ? u_high : u_kick));
        float radius = 0.1 + k * 0.11 + 0.06 * level + 0.05 * u_kick;
        float shape = radius * (0.72 + 0.28 * cos(petals * (a + u_angle * (1.0 + k * 0.35) * dir) + t * 0.2 * k));
        float line = exp(-abs(r - shape) * (70.0 - k * 10.0)) * (0.7 + 0.5 * level);
        float fill = smoothstep(0.012, -0.012, r - shape) * (0.12 + 0.1 * level);
        float w = line + fill;
        acc += vivid(pal(k * 0.27 + r * 0.8 + t * 0.03)) * w;
        total += w;
      }
      float core = exp(-r * 14.0) * (0.4 + 0.8 * u_bass);
      acc += vivid(pal(t * 0.05 + 0.5)) * core;
      total += core;
      return vec4(acc / max(total, 0.001), clamp(total, 0.0, 1.0));
    }

    // Ringe kommen auf den Betrachter zu; die Mitte ist die dunkle Ferne.
    vec4 tunnel(vec2 p, float t) {
      float r = max(length(p), 0.001);
      float a = atan(p.y, p.x) / 6.2831853 + u_angle * 0.16;
      float depth = 0.32 / r + t * (0.7 + u_bass * 0.9);
      float twist = a + 0.05 / r * sin(t * 0.3);
      float stripes = 0.5 + 0.5 * sin((twist * u_segments + depth * 0.5) * 6.2831853);
      float rings = 0.5 + 0.5 * sin(depth * 6.2831853 * 2.0);
      float pattern = mix(rings, stripes, 0.45 + 0.3 * u_mid);
      vec3 col = vivid(pal(depth * 0.12 + stripes * 0.2));
      col *= 0.4 + 0.8 * pattern + 0.3 * u_high * rings;
      float alpha = smoothstep(0.0, 0.35, r) * (0.45 + 0.55 * pattern);
      return vec4(col, alpha);
    }

    // Julia-Menge, c wandert auf einem Kreis - die Figur morpht dabei laufend.
    vec4 fractal(vec2 p, float t) {
      float ang = t * 0.15 + u_mid * 0.25;
      vec2 c = (0.7885 + 0.025 * u_kick) * vec2(cos(ang), sin(ang));
      vec2 z = turn(p, u_angle * 0.3) * (2.6 - 0.5 * u_kick - 0.3 * u_bass);
      float n = 0.0; float gone = 0.0;
      for (int i = 0; i < 96; i++) {
        if (i >= u_steps) break;
        z = vec2(z.x * z.x - z.y * z.y, 2.0 * z.x * z.y) + c;
        if (dot(z, z) > 16.0) { gone = 1.0; break; }
        n += 1.0;
      }
      float x = gone > 0.5 ? (n - log2(log2(dot(z, z))) + 4.0) / float(u_steps) : 0.0;
      vec3 col = vivid(pal(x * 3.0 + t * 0.05));
      col *= 0.6 + 0.6 * u_high * x;
      // Innen dunkel, der Rand leuchtet.
      float alpha = gone > 0.5 ? smoothstep(0.02, 0.3, x) : 0.15 + 0.3 * u_bass;
      return vec4(gone > 0.5 ? col : vivid(pal(t * 0.04)) * 0.5, alpha);
    }

    // Klassisches Plasma: Sinuswellen, die sich ueberlagern.
    vec4 plasma(vec2 p, float t) {
      vec2 q = turn(p, u_angle * 0.2) * (3.0 + u_bass * 1.2);
      float v = sin(q.x + t) + sin((q.y + t) * 0.8) + sin((q.x + q.y + t * 1.3) * 0.7)
        + sin(length(q + vec2(sin(t * 0.33), cos(t * 0.5)) * 2.0) * 1.5 + t);
      v += u_mid * sin(length(q) * 3.0 - t * 4.0);
      vec3 col = vivid(pal(v * 0.25 + 0.5 + t * 0.04));
      col *= 0.75 + 0.35 * sin(v * 3.14159 + u_kick * 2.0);
      return vec4(col, 0.55 + 0.45 * abs(sin(v * 1.5708)));
    }

    void main() {
      vec2 uv = gl_FragCoord.xy / u_res;
      vec2 p = (uv - 0.5) * vec2(u_res.x / u_res.y, 1.0);
      // Schlag: kurzer Stoss nach innen.
      p *= 1.0 - u_zoom;
      float t = u_time;
      vec4 shape;
      if (u_form == 0) shape = lava(p, t);
      else if (u_form == 1) shape = blobs(p, t);
      else if (u_form == 2) shape = ink(p, t);
      else if (u_form == 3) shape = kaleido(p, t);
      else if (u_form == 4) shape = mandala(p, t);
      else if (u_form == 5) shape = tunnel(p, t);
      else if (u_form == 6) shape = fractal(p, t);
      else shape = plasma(p, t);
      // Ohne Bild: satter und heller, auf Schwarz soll es leuchten.
      // Die Staerke dimmt dort auch die Helligkeit, nicht nur die Deckung.
      vec3 col = mix(shape.rgb, (shape.rgb * 1.3 + 0.05) * (0.35 + 0.65 * u_strength), u_bare);
      col *= 1.0 + u_flash;
      float alpha = clamp(shape.a * u_strength * (1.0 + u_flash * 0.5), 0.0, 1.0);
      // Auf Schwarz dunkelt geringe Deckung die Farbe nur ab - dort hochziehen.
      alpha = mix(alpha, sqrt(alpha), u_bare);
      gl_FragColor = vec4(clamp(col, 0.0, 1.0) * alpha, alpha);
    }`;

  let root = null;
  let canvas = null;
  let gl = null;
  let program = null;
  let uniforms = {};
  let settings = { ...DEFAULT };
  let bare = false;
  let mode = 'screensaver';
  let open = false;
  let frame = 0;
  let last = 0;
  let time = 0;
  let scene = '';
  const level = { bass: 0, mid: 0, high: 0 };
  const average = { bass: 0.2, mid: 0.2, high: 0.2 };
  // Takt: Schlag-Huellkurve, Fluss-Mittel, Zeit seit dem letzten Schlag.
  const pulse = { kick: 0, flux: 0.05, since: 1, beats: 0, lastBeat: -1 };
  // Sekunden, die der Analyser trotz Wiedergabe nur Stille liefert.
  let deaf = 0;
  // Drehung, Palettendreh und Keilzahl - stetig, damit nichts springt, wo es nicht soll.
  const motion = { angle: 0, spin: 1, shift: 0, shiftTarget: 0, segments: 6 };
  let colors = [[0.38, 0.65, 0.98], [0.6, 0.78, 1], [0.35, 0.3, 0.85]];
  let target = colors;

  function player() { return window.journlScreensaverPlayer; }
  function effects() { return window.journlScreensaverPlayerEffects; }
  function simulated() { return Boolean(player()?.volumeLocked); }
  function active() { return open && settings.enabled && Boolean(gl); }

  // ─── WebGL ────────────────────────────────────────────────────────

  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || 'Shader');
    return shader;
  }

  function setup() {
    gl = canvas.getContext('webgl', { premultipliedAlpha: true, alpha: true, antialias: false, powerPreference: 'low-power' });
    if (!gl) return false;
    try {
      program = gl.createProgram();
      gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX));
      gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) || 'Link');
    } catch (error) {
      console.warn('Klangbild aus:', error);
      gl = null;
      return false;
    }
    gl.useProgram(program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, 'a_position');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    uniforms = Object.fromEntries(['u_res', 'u_time', 'u_bass', 'u_mid', 'u_high', 'u_strength', 'u_bare',
      'u_kick', 'u_zoom', 'u_flash', 'u_shift', 'u_angle', 'u_segments',
      'u_c1', 'u_c2', 'u_c3', 'u_form', 'u_octaves', 'u_steps'].map(name => [name, gl.getUniformLocation(program, name)]));
    return true;
  }

  function mount(element) {
    if (canvas && root === element) return;
    root = element;
    canvas = document.createElement('canvas');
    canvas.dataset.visual = '';
    canvas.setAttribute('aria-hidden', 'true');
    canvas.className = 'absolute inset-0 h-full w-full pointer-events-none';
    canvas.style.transition = 'opacity 1.2s ease';
    canvas.style.opacity = '0';
    canvas.addEventListener('webglcontextlost', (event) => { event.preventDefault(); stop(); gl = null; });
    canvas.addEventListener('webglcontextrestored', () => { if (setup()) apply(); });
    place();
    setup();
  }

  // Diashow: ueber der Abdunklung. Kulisse/Fragen: darunter, unter dem Text.
  function place() {
    const dim = root?.querySelector('[data-dim]');
    if (!canvas || !dim) return;
    if (mode === 'screensaver') dim.after(canvas);
    else dim.before(canvas);
  }

  function resize() {
    if (!canvas || !gl) return;
    const quality = QUALITY[settings.quality] || QUALITY.balanced;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5) * quality.scale;
    const width = Math.max(64, Math.round(window.innerWidth * ratio));
    const height = Math.max(64, Math.round(window.innerHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
      gl.viewport(0, 0, width, height);
    }
  }

  // ─── Ton ──────────────────────────────────────────────────────────

  function reaction() {
    return REACTIONS[settings.reaction] || REACTIONS.beat;
  }

  /* Ein Takt um 96 BPM mit leichtem Schwanken - genug, damit es nach Musik
     aussieht, ohne so zu tun, als hoere es zu. Laeuft auf der Uhr, nicht auf
     `time`: die beschleunigt mit dem Bass und wuerde den Takt verziehen. */
  function simulate(now) {
    const t = now / 1000;
    const beat = t * 1.6;
    const kick = Math.pow(1 - (beat % 1), 3);
    const count = Math.floor(beat);
    const onset = count !== pulse.lastBeat ? 1 : 0;
    pulse.lastBeat = count;
    return {
      bass: kick * (0.75 + 0.25 * Math.sin(t * 0.7)),
      mid: 0.45 + 0.25 * Math.sin(t * 1.3) + 0.15 * Math.sin(t * 2.9),
      high: 0.35 + 0.3 * Math.sin(t * 5.1) * Math.sin(t * 1.7),
      onset,
    };
  }

  /* Schlag: der Fluss liegt deutlich ueber seinem eigenen Mittel. Das Mittel
     zieht langsam nach, damit laute und leise Stuecke gleich gut zaehlen.
     Simulierte Schlaege (onset 1) kommen immer durch. */
  function detect(onsetValue, dt) {
    pulse.since += dt;
    pulse.kick *= Math.exp(-dt * 9);
    const threshold = Math.max(0.025, pulse.flux * 1.7);
    pulse.flux += (onsetValue - pulse.flux) * Math.min(1, dt * 1.5);
    if (onsetValue <= threshold || pulse.since < REFRACTORY) return;
    pulse.since = 0;
    pulse.beats += 1;
    pulse.kick = Math.max(pulse.kick, Math.min(1, 0.6 + (onsetValue / threshold - 1) * 0.8));
    if (!reaction().jumps) return;
    // Wild: jede Palette dreht weiter, alle 8 Schlaege andere Keile, alle 16 andere Richtung.
    motion.shiftTarget += 1 / 3;
    if (pulse.beats % 8 === 0) motion.segments = SEGMENTS[(SEGMENTS.indexOf(motion.segments) + 1) % SEGMENTS.length];
    if (pulse.beats % 16 === 0) motion.spin = -motion.spin;
  }

  /* Jedes Band gegen seinen eigenen gleitenden Mittelwert: leise Stuecke
     bewegen sich so genauso deutlich wie laute. Sanft: schnell hoch, langsam
     runter - Puls ohne Zappeln. Im Takt und wild: die ungeglaetteten Baender
     und kaum Nachlauf. */
  /* Frei: kein Ton. Das Tempo schwankt aus drei langsamen Wellen mit
     krummen Perioden (≈ 2-4 min) - wiederholt sich praktisch nie und wirkt
     zufaellig, ohne zu ruckeln. Die Baender atmen ebenso. */
  function drift(now) {
    const t = now / 1000;
    const wave = (Math.sin(t * 0.041) + Math.sin(t * 0.027 + 1.7) * 0.7 + Math.sin(t * 0.0173 + 4.1) * 0.5) / 2.2;
    return {
      speed: 0.6 + 0.45 * wave,
      bass: 0.3 + 0.2 * Math.sin(t * 0.21) * Math.sin(t * 0.067 + 2),
      mid: 0.35 + 0.25 * Math.sin(t * 0.13 + 1),
      high: 0.2 + 0.15 * Math.sin(t * 0.31 + 3),
    };
  }

  function listen(dt, now) {
    const react = reaction();
    if (react.free) {
      const calm = drift(now);
      pulse.kick = 0;
      Object.keys(level).forEach((band) => { level[band] += (calm[band] - level[band]) * Math.min(1, dt * 2); });
      return calm.speed;
    }
    const playing = Boolean(player()?.isPlaying?.());
    let raw = null;
    let onsetValue = 0;
    if (playing && !simulated()) {
      let heard = effects()?.levels?.();
      /* WebKit (Safari, Mac-App) liefert fuer Live-Streams aus einem
         MediaElementSource nur Nullen. Echte Stille dauert in Musik selten
         1,5 s - dann lieber den simulierten Takt als ein stehendes Bild. */
      const silent = heard && !(heard.bass || heard.mid || heard.high || heard.onset);
      deaf = silent ? deaf + dt : 0;
      if (deaf > 1.5) heard = null;
      if (heard) {
        const source = react.quick && heard.quick ? heard.quick : heard;
        onsetValue = heard.onset || 0;
        raw = {};
        Object.keys(level).forEach((band) => {
          average[band] = average[band] * 0.985 + source[band] * 0.015;
          raw[band] = Math.max(0, Math.min(1, (source[band] - average[band] * 0.7) / Math.max(average[band], 0.04)));
        });
      }
    }
    if (playing && !raw) {
      raw = simulate(now);
      onsetValue = raw.onset;
    }
    if (!playing) raw = { bass: 0, mid: 0, high: 0 };
    if (playing) detect(onsetValue, dt);
    else pulse.kick *= Math.exp(-dt * 9);
    Object.keys(level).forEach((band) => {
      const wanted = Math.min(1.6, raw[band] * react.gain);
      const rate = wanted > level[band] ? react.attack : react.release;
      level[band] += (wanted - level[band]) * Math.min(1, rate * dt * 60);
    });
    return playing;
  }

  // ─── Farbe ────────────────────────────────────────────────────────

  function hue(h, s, l) {
    const k = n => (n + h * 12) % 12;
    const a = s * Math.min(l, 1 - l);
    return [0, 8, 4].map(n => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1))));
  }

  // Ohne Bild wandert die Palette langsam durch den Farbkreis.
  function wander() {
    const h = (0.6 + time * 0.006) % 1;
    target = [hue(h, 0.9, 0.58), hue((h + 0.14) % 1, 0.95, 0.62), hue((h + 0.78) % 1, 0.85, 0.5)];
  }

  function pickColors() {
    if (bare) return;
    const fx = effects();
    if (!fx?.palette) return;
    const wanted = scene;
    fx.palette(scene).then((rgb) => {
      if (wanted === scene) target = rgb.map(color => color.map(v => v / 255));
    });
  }

  // ─── Lauf ─────────────────────────────────────────────────────────

  function draw(now) {
    frame = requestAnimationFrame(draw);
    const quality = QUALITY[settings.quality] || QUALITY.balanced;
    const elapsed = now - last;
    if (elapsed < 1000 / quality.fps - 2) return;
    const dt = Math.min(0.1, elapsed / 1000);
    last = now;
    const heard = listen(dt, now);
    const react = reaction();
    const kick = pulse.kick;
    // Ohne Musik ruhiger, mit Bass etwas schneller - die Zeit selbst atmet.
    // Im Takt springt sie bei jedem Schlag ein Stueck vor. Frei: eigenes Tempo.
    const pace = react.free ? heard : (heard ? 0.8 + level.bass * 0.6 : 0.35);
    time += dt * (pace + kick * react.jolt * 6);
    motion.angle += dt * motion.spin * (0.12 + kick * react.spin);
    motion.shift += (motion.shiftTarget - motion.shift) * Math.min(1, dt * 14);
    if (motion.shift > 1) { motion.shift -= 1; motion.shiftTarget -= 1; }
    if (bare) wander();
    colors = colors.map((color, index) => color.map((v, channel) => v + (target[index][channel] - v) * Math.min(1, dt * (bare ? 3 : 0.8))));
    resize();
    gl.uniform2f(uniforms.u_res, canvas.width, canvas.height);
    gl.uniform1f(uniforms.u_time, time);
    gl.uniform1f(uniforms.u_bass, level.bass);
    gl.uniform1f(uniforms.u_mid, level.mid);
    gl.uniform1f(uniforms.u_high, level.high);
    gl.uniform1f(uniforms.u_kick, react.zoom ? kick : 0);
    gl.uniform1f(uniforms.u_zoom, kick * react.zoom);
    gl.uniform1f(uniforms.u_flash, kick * react.flash * (reduced?.matches ? 0.5 : 1));
    gl.uniform1f(uniforms.u_shift, motion.shift);
    gl.uniform1f(uniforms.u_angle, motion.angle);
    gl.uniform1f(uniforms.u_segments, motion.segments);
    const strength = settings.strength / 100;
    // Ohne Bild gilt der Regler genauso (frueher fest ≥ 85 % - er wirkte dort nicht).
    gl.uniform1f(uniforms.u_strength, bare ? strength : strength * (settings.blend === 'bold' ? 1.15 : 1));
    gl.uniform1f(uniforms.u_bare, bare ? 1 : 0);
    gl.uniform3fv(uniforms.u_c1, colors[0]);
    gl.uniform3fv(uniforms.u_c2, colors[1]);
    gl.uniform3fv(uniforms.u_c3, colors[2]);
    gl.uniform1i(uniforms.u_form, Math.max(0, FORMS.indexOf(settings.form)));
    gl.uniform1i(uniforms.u_octaves, quality.octaves);
    gl.uniform1i(uniforms.u_steps, quality.steps);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  function start() {
    if (frame) return;
    last = 0;
    frame = requestAnimationFrame(draw);
  }

  function stop() {
    cancelAnimationFrame(frame);
    frame = 0;
  }

  function apply() {
    const on = active();
    // Echter Ton nur, wo er die Wiedergabe nicht gefaehrdet.
    effects()?.demand?.('visual', on && !simulated() && !reaction().free);
    if (!canvas) return;
    // Auf Schwarz zeigt „Farbschleier“ nichts - ohne Bild leuchtet es immer.
    canvas.style.mixBlendMode = bare ? 'screen' : (BLENDS[settings.blend] || 'screen');
    canvas.style.opacity = on ? '1' : '0';
    place();
    if (on) { pickColors(); start(); }
    else setTimeout(() => { if (!active()) stop(); }, 1300);
  }

  function configure(screensaverSettings) {
    settings = { ...DEFAULT, ...(screensaverSettings?.visual || {}) };
    // Ohne Farbspruenge kehren Palette und Keile in die Ruhelage zurueck.
    if (!reaction().jumps) {
      motion.shiftTarget = Math.round(motion.shift * 3) / 3;
      motion.segments = SEGMENTS[0];
      motion.spin = 1;
    }
    const sources = screensaverSettings?.sources || [];
    bare = sources.length === 1 && sources[0] === 'none';
    apply();
  }

  // ─── Kuerzel: alle Formen reihum, dann aus ─────────────────────────

  async function cycle() {
    const current = await window.apiFetch('/api/screensaver/settings', { cache: 'no-store' }).then(r => r.json()).catch(() => null);
    if (!current?.settings) return;
    const visual = { ...DEFAULT, ...(current.settings.visual || {}) };
    const index = visual.enabled ? FORMS.indexOf(visual.form) : -1;
    const next = index + 1 < FORMS.length ? { enabled: true, form: FORMS[index + 1] } : { enabled: false };
    const label = next.enabled ? `Klangbild: ${FORM_LABELS[next.form]}` : 'Klangbild aus';
    try {
      const response = await window.apiFetch('/api/screensaver/settings', {
        method: 'POST', body: JSON.stringify({ ...current.settings, visual: { ...visual, ...next } }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Nicht gespeichert');
      document.dispatchEvent(new CustomEvent('journl:screensaver-settings', { detail: data.settings }));
      window.journlScreensaver?.flashHint?.(label);
    } catch (error) {
      window.journlScreensaver?.flashHint?.(error.message || 'Nicht gespeichert');
    }
  }

  function handleKey(event) {
    const shortcut = player()?.settings?.()?.shortcuts?.visual;
    if (!shortcut || player()?.combo?.(event) !== shortcut) return false;
    event.preventDefault();
    cycle();
    return true;
  }

  // ─── Schoner ──────────────────────────────────────────────────────

  document.addEventListener('journl:screensaver-opened', (event) => {
    if (event.detail?.root) mount(event.detail.root);
    open = true;
    if (event.detail?.settings) configure(event.detail.settings);
    else apply();
  });
  document.addEventListener('journl:screensaver-closed', () => { open = false; apply(); });
  document.addEventListener('journl:screensaver-mode', (event) => { mode = event.detail?.mode || 'screensaver'; place(); });
  document.addEventListener('journl:screensaver-settings', event => configure(event.detail));
  document.addEventListener('journl:screensaver-image', (event) => { scene = event.detail?.src || ''; if (active()) pickColors(); });

  return { handleKey, cycle, forms: FORM_LABELS };
}());
