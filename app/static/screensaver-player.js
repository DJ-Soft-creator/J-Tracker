/* Player des Bildschirmschoners: eigene Musik aus „Meine Ordner“ und den
   gemeinsamen Ordnern, Podcasts und Internetradio.

   Die Quelle (`audio`: music, podcast, radio) waehlt der Server aus; /tracks
   liefert fuer alle dieselbe Form. Episoden bringen ihre Position und
   „gehoert“ mit, Sender sind `live` (keine Dauer, kein Spulen, beim Pausieren
   wird die Verbindung getrennt, damit der Proxy-Thread frei wird).

   Er lebt neben dem Schoner-Overlay direkt im <body> und laeuft deshalb ueber
   Tabwechsel und das Oeffnen/Schliessen des Schoners hinweg weiter. Wo er
   erscheint, sagt „Anzeigen in“ (`display`): nur im Schoner, im Schreiben-Tab
   (der Schoner zaehlt als dessen Kulisse mit) oder ueberall. Ausserhalb dieses
   Bereichs pausiert der Ton und setzt beim Zurueckkehren wieder ein.

   * Zwei <audio>-Decks: das aktive spielt, das andere blendet beim Crossfade
     ein. Die Rampen laufen ueber Timer statt requestAnimationFrame, damit ein
     Crossfade auch in einem Hintergrund-Tab zu Ende kommt.
   * Drei Sichtbarkeitsstufen: volle Leiste, leuchtende Mini-Pille (Hover oder
     Tippen klappt auf) und unsichtbar (nur Tastatur und Medientasten).
   * Kuerzel kommen aus den Einstellungen (`shortcuts`, Format
     `Ctrl+Alt+Shift+<KeyboardEvent.code>`, Cmd zaehlt wie Strg). Im offenen
     Schoner fragt screensaver.js ueber `handleKey` nach, bevor eine Taste ihn
     weckt.
   * Diktat: `journl:recording` aus write-session.js pausiert oder senkt den
     Pegel; nach der Aufnahme geht es weiter wie vorher.
   * Fortsetzen: Titel und Position gehen alle paar Sekunden an den Server.
     Nach einem Reload spielt nichts von selbst - die Pille bietet
     „Fortsetzen“ an, weil Browser ohne Geste ohnehin keinen Ton erlauben.

   iOS laesst `audio.volume` nur lesen (immer 1). Dort gibt es deshalb keinen
   Crossfade (beide Titel liefen sonst voll uebereinander), „Leiser stellen“
   beim Diktat wird zu „Pausieren“, der Sleep-Timer stoppt ohne Ausblenden,
   Stumm geht ueber `muted` und die Lautstaerke ueber die Geraetetasten. Der
   Weg ueber Web Audio wuerde die Wiedergabe im Hintergrund gefaehrden.

   Markup und Darstellung liegen in screensaver-player-view.js, die
   Lichteffekte in screensaver-player-effects.js; dieses Modul besitzt
   Wiedergabe, Bereich, Diktat und Kuerzel und ruft dort `render*` bzw.
   meldet Cover, Wiedergabe und Einstellungen an die Effekte.

   Einstellungen aendert screensaver-player-settings.js und meldet sie als
   `journl:player-settings`; Lautstaerke, Stumm, Shuffle und Wiederholen
   speichert der Player selbst und meldet sie mit `origin: 'player'`. */
(function () {
  const ENDPOINT = '/api/screensaver/player';
  const SAVE_EVERY = 15000;
  const PRESET_FADE = 1.5;
  const SKIP_FADE = 1.5;
  const SLEEP_FADE = 8;
  const VOLUME_STEP = 5;
  const DICTATION_REWIND = 3;
  const REPEAT_NEXT = { off: 'folder', folder: 'track', track: 'off' };
  const RATES = [0.8, 1, 1.1, 1.2, 1.3, 1.5, 1.75, 2];
  const volumeLocked = (() => {
    try {
      const probe = new Audio();
      probe.volume = 0.5;
      return Math.abs(probe.volume - 0.5) > 0.01;
    } catch (_) { return false; }
  })();
  const SLEEP_STEPS = [0, 15, 30, 60, 'end'];

  const state = {
    settings: null,
    tracks: [],
    order: [],
    cursor: -1,
    missing: [],
    wanted: false,         // der Nutzer will Ton - auch wenn gerade Bereich/Diktat pausiert
    areaPaused: false,
    dictationPaused: false,
    ducked: false,
    duck: 1,
    needsGesture: false,
    resumable: false,
    expanded: false,
    autoHidden: false,
    keysOpen: false,
    lastSaved: 0,
    seeking: false,
    typing: false,         // mobil: Bildschirmtastatur offen
    rate: 1,
    problem: '',
    listOpen: false,
    sleep: { choice: 0, until: 0, timer: null },
    reconnects: 0,
  };
  const decks = [createDeck(), createDeck()];
  const effects = window.journlScreensaverPlayerEffects;
  let active = 0;
  const { ACTION_LABELS, REPEAT_LABELS, formatShortcut } = window.journlScreensaverPlayerView;
  const view = window.journlScreensaverPlayerView.create({
    state, volumeLocked, current, playing, inArea, screensaverOpen, run, play, seek, setVolume,
    toggleShuffle, cycleRepeat, toggleMute, persist, updateSession,
    mode, live, playItem, cycleRate, cycleSleep, hasSource,
  });
  let started = false;

  // ─── Decks und Pegel ──────────────────────────────────────────────

  function createDeck() {
    const audio = new Audio();
    audio.preload = 'metadata';
    const deck = { audio, gain: 1, track: null, timer: null, handedOver: false };
    audio.addEventListener('timeupdate', () => onTime(deck));
    audio.addEventListener('ended', () => onEnded(deck));
    ['play', 'pause', 'loadedmetadata', 'durationchange'].forEach(name => audio.addEventListener(name, () => {
      if (deck === current()) { view.renderProgress(); view.renderState(); updateSession(); }
      effects?.setPlaying(playing());
    }));
    audio.addEventListener('error', () => {
      if (deck !== current() || !deck.track) return;
      if (!deck.audio.getAttribute('src')) return;  // getrennter Sender, kein Fehler
      view.flash(deck.track.live ? `„${deck.track.title}“ ist gerade nicht erreichbar`
        : `„${deck.track.title}“ lässt sich nicht abspielen`);
      // Ein kaputter Titel soll die Reihe nicht anhalten - bei Sendern und
      // Episoden waere das Weiterspringen aber eine Ueberraschung.
      if (state.wanted && mode() === 'music' && state.order.length > 1) next({ fade: 0, play: true });
    });
    return deck;
  }

  function current() { return decks[active]; }
  function other() { return decks[1 - active]; }
  function playing() { return Boolean(current().track) && !current().audio.paused; }
  function mode() { return state.settings?.audio || 'music'; }
  function live() { return Boolean(current().track?.live); }

  function hasSource() {
    const settings = state.settings;
    if (!settings) return false;
    if (settings.audio === 'podcast') return Boolean(settings.podcast);
    if (settings.audio === 'radio') return true;
    return settings.sources.length > 0;
  }

  function level() {
    const settings = state.settings;
    if (!settings || settings.muted) return 0;
    return (settings.volume / 100) * state.duck;
  }

  function applyVolume() {
    decks.forEach((deck) => {
      deck.audio.volume = Math.max(0, Math.min(1, level() * deck.gain));
      // `muted` greift auch dort, wo `volume` nur lesbar ist (iOS).
      deck.audio.muted = Boolean(state.settings?.muted);
    });
  }

  function crossfade() {
    return volumeLocked ? 0 : (state.settings?.crossfade || 0);
  }

  // Lineare Rampe ueber Zeitstempel: ein gedrosselter Hintergrund-Tab
  // ueberspringt Zwischenschritte, kommt aber zur rechten Zeit am Ziel an.
  function ramp(set, from, to, seconds, slot) {
    clearInterval(slot.timer);
    if (!(seconds > 0)) { set(to); return Promise.resolve(); }
    const begin = Date.now();
    return new Promise((resolve) => {
      slot.timer = setInterval(() => {
        const progress = Math.min(1, (Date.now() - begin) / (seconds * 1000));
        set(from + (to - from) * progress);
        if (progress >= 1) { clearInterval(slot.timer); slot.timer = null; resolve(); }
      }, 40);
    });
  }

  function fadeDeck(deck, to, seconds) {
    return ramp((value) => { deck.gain = value; applyVolume(); }, deck.gain, to, seconds, deck);
  }

  const duckSlot = { timer: null };
  function fadeDuck(to, seconds) {
    return ramp((value) => { state.duck = value; applyVolume(); }, state.duck, to, seconds, duckSlot);
  }

  function disconnect(deck) {
    deck.audio.removeAttribute('src');
    deck.audio.load();
  }

  function release(deck) {
    clearInterval(deck.timer);
    deck.timer = null;
    deck.audio.pause();
    disconnect(deck);
    deck.track = null;
    deck.gain = 1;
    deck.handedOver = false;
  }

  function applyRate(deck) {
    const rate = deck.track && !deck.track.live && mode() === 'podcast' ? state.rate : 1;
    deck.audio.defaultPlaybackRate = rate;
    deck.audio.playbackRate = rate;
  }

  /* Eigene Musik liegt lokal, da kostet die Vorschau nichts. Episoden und
     Sender laufen ueber den Proxy - jede offene Vorschau hielte dort einen
     Thread, deshalb verbindet erst `begin`. */
  function prepare(deck, item, position) {
    deck.track = item;
    deck.handedOver = false;
    deck.audio.loop = mode() === 'music' && state.settings?.repeat === 'track';
    deck.audio.preload = mode() === 'music' ? 'metadata' : 'none';
    if (item.live) disconnect(deck);
    else deck.audio.src = item.src;
    applyRate(deck);
    if (position > 0) {
      deck.audio.addEventListener('loadedmetadata', () => {
        const duration = deck.audio.duration;
        deck.audio.currentTime = Number.isFinite(duration) ? Math.min(position, Math.max(0, duration - 2)) : position;
      }, { once: true });
    }
  }

  async function begin(deck) {
    if (deck.track?.live && !deck.audio.getAttribute('src')) deck.audio.src = deck.track.src;
    try {
      await deck.audio.play();
      state.needsGesture = false;
      view.wake();
      return true;
    } catch (error) {
      // Ohne vorherige Geste (Deep Link, Reload) verweigert der Browser den
      // Ton - dann bietet die Pille „Tippen zum Abspielen“ an.
      if (error?.name === 'NotAllowedError') {
        state.needsGesture = true;
        state.wanted = true;
      }
      return false;
    } finally {
      view.render();
    }
  }

  // ─── Reihenfolge ──────────────────────────────────────────────────

  function buildOrder(first = -1) {
    const indices = state.tracks.map((_, index) => index);
    if (mode() === 'music' && state.settings?.shuffle) {
      for (let index = indices.length - 1; index > 0; index -= 1) {
        const swap = Math.floor(Math.random() * (index + 1));
        [indices[index], indices[swap]] = [indices[swap], indices[index]];
      }
      if (first >= 0) indices.splice(0, 0, ...indices.splice(indices.indexOf(first), 1));
    }
    state.order = indices;
    state.cursor = first >= 0 ? state.order.indexOf(first) : 0;
  }

  function trackIndex(item) {
    return item ? state.tracks.findIndex(entry => entry.id === item.id) : -1;
  }

  function hasNext() {
    if (mode() !== 'music') return state.cursor < state.order.length - 1;
    return state.cursor < state.order.length - 1 || state.settings?.repeat === 'folder';
  }

  // Wo eine Episode weitergeht: an ihrer Stelle, gehoerte von vorn.
  function startAt(item) {
    return mode() === 'podcast' && item && !item.done ? (item.position || 0) : 0;
  }

  /* Wo die Wiedergabe nach dem Laden steht: beim zuletzt gespielten Titel,
     im Radio beim gewaehlten Sender, bei Podcasts sonst bei der neuesten
     ungehoerten Episode. */
  function startIndex(stored) {
    let index = state.tracks.findIndex(entry => entry.id === stored?.track);
    if (index < 0 && mode() === 'podcast') index = state.tracks.findIndex(entry => !entry.done);
    return index;
  }

  // ─── Wiedergabe ───────────────────────────────────────────────────

  async function switchTo(cursor, options = {}) {
    const item = state.tracks[state.order[cursor]];
    if (!item) return;
    const play = options.play ?? playing();
    const fade = volumeLocked ? 0 : (options.fade || 0);
    const outgoing = current();
    const position = options.position ?? startAt(item);
    state.cursor = cursor;
    state.resumable = false;
    state.reconnects = 0;
    if (fade > 0 && play && !outgoing.audio.paused) {
      // Crossfade: das freie Deck blendet ein, das alte aus und wird danach frei.
      release(other());
      active = 1 - active;
      const incoming = current();
      incoming.gain = 0;
      prepare(incoming, item, position);
      applyVolume();
      outgoing.handedOver = true;
      const ok = await begin(incoming);
      fadeDeck(incoming, 1, fade);
      fadeDeck(outgoing, 0, ok ? fade : 0).then(() => { if (outgoing !== current()) release(outgoing); });
    } else {
      release(other());
      outgoing.audio.pause();
      prepare(outgoing, item, position);
      outgoing.gain = 1;
      applyVolume();
      if (play) await begin(outgoing);
    }
    // Im Radio ist der Sender die Einstellung - so startet ein Preset dort.
    if (mode() === 'radio' && state.settings.station !== item.id) persist({ station: item.id });
    updateSession();
    view.renderTrack();
    view.renderList();
    saveState(true);
  }

  function playItem(index) {
    const cursor = state.order.indexOf(index);
    if (cursor < 0) return;
    state.wanted = true;
    switchTo(cursor, { play: true, fade: Math.min(crossfade(), SKIP_FADE) });
  }

  function emptyHint() {
    if (mode() === 'podcast') return state.problem || (state.settings?.podcast ? 'Der Podcast hat keine Episoden' : 'Kein Podcast gewählt – Player-Einstellungen');
    if (mode() === 'radio') return 'Keine Sender verfügbar';
    return state.settings?.sources?.length ? 'In den gewählten Ordnern liegt keine Musik' : 'Keine Musik gewählt – Player-Einstellungen';
  }

  async function play() {
    if (!state.tracks.length) {
      view.flash(emptyHint());
      return;
    }
    state.wanted = true;
    state.areaPaused = false;
    state.dictationPaused = false;
    // Meist mit Geste: der Moment, in dem der Analyser anlaufen darf.
    effects?.wake();
    if (!current().track) {
      await switchTo(Math.max(0, state.cursor), { play: true });
      return;
    }
    current().gain = 1;
    applyVolume();
    await begin(current());
    state.resumable = false;
  }

  function pause(options = {}) {
    release(other());
    current().audio.pause();
    // Live gibt es nichts zu puffern: Verbindung trennen, beim Weiterspielen
    // neu verbinden - dann ist es auch wieder live statt verspaetet.
    if (live()) disconnect(current());
    if (!options.keepWanted) {
      state.wanted = false;
      state.areaPaused = false;
      state.dictationPaused = false;
    }
    saveState(true);
    view.render();
  }

  function togglePlay() {
    if (playing()) pause();
    else play();
  }

  function next(options = {}) {
    if (!state.order.length) return;
    let cursor = state.cursor + 1;
    if (cursor >= state.order.length) {
      if (state.settings?.shuffle) buildOrder();
      cursor = 0;
    }
    switchTo(cursor, { fade: options.fade ?? Math.min(crossfade(), SKIP_FADE), play: options.play });
  }

  // Wie bei jedem Player: nach drei Sekunden Laufzeit erst an den Anfang.
  function previous() {
    if (!state.order.length) return;
    if (current().track && current().audio.currentTime > 3) { seek(0); return; }
    const cursor = state.cursor <= 0 ? state.order.length - 1 : state.cursor - 1;
    switchTo(cursor, { fade: Math.min(crossfade(), SKIP_FADE) });
  }

  function seek(seconds) {
    const { audio } = current();
    if (!current().track || live()) return;
    const duration = Number.isFinite(audio.duration) ? audio.duration : Infinity;
    audio.currentTime = Math.max(0, Math.min(duration - 0.25, seconds));
    current().handedOver = false;
    view.renderProgress();
    saveState(true);
  }

  function skip(delta) {
    if (!current().track) return;
    if (live()) { view.flash('Live – hier lässt sich nicht spulen'); return; }
    seek(current().audio.currentTime + delta);
    view.flash(`${delta > 0 ? '+' : '−'}${Math.abs(delta)} s`);
  }

  function setVolume(volume, options = {}) {
    if (volumeLocked) { if (options.announce) view.flash('Lautstärke über die Tasten des Geräts'); return; }
    const value = Math.max(0, Math.min(100, Math.round(volume)));
    persist({ volume: value, muted: value === 0 ? state.settings.muted : false });
    applyVolume();
    view.renderVolume();
    if (options.announce) view.flash(`Lautstärke ${value} %`);
  }

  function toggleMute() {
    persist({ muted: !state.settings.muted });
    applyVolume();
    view.renderVolume();
  }

  function toggleShuffle() {
    persist({ shuffle: !state.settings.shuffle });
    buildOrder(trackIndex(current().track));
    view.renderModes();
    view.flash(state.settings.shuffle ? 'Zufällige Reihenfolge' : 'Der Reihe nach');
  }

  function cycleRepeat() {
    persist({ repeat: REPEAT_NEXT[state.settings.repeat] || 'folder' });
    decks.forEach((deck) => { deck.audio.loop = mode() === 'music' && state.settings.repeat === 'track'; });
    view.renderModes();
    view.flash(REPEAT_LABELS[state.settings.repeat]);
  }

  // ─── Tempo und Sleep-Timer ────────────────────────────────────────

  let rateTimer = null;
  function cycleRate() {
    if (mode() !== 'podcast') return;
    const index = RATES.findIndex(rate => rate >= state.rate - 0.001);
    const rate = RATES[(index + 1) % RATES.length] || 1;
    state.rate = rate;
    decks.forEach(applyRate);
    view.renderModes();
    view.flash(`Tempo ${view.formatRate(rate)}`);
    // Pro Podcast gemerkt, nicht in den Einstellungen.
    clearTimeout(rateTimer);
    const feed = state.settings.podcast;
    rateTimer = setTimeout(() => {
      request(`${ENDPOINT}/podcasts/${feed}`, { method: 'PATCH', body: JSON.stringify({ rate }) }).catch(() => {});
    }, 600);
  }

  function cycleSleep() {
    const steps = live() ? SLEEP_STEPS.filter(step => step !== 'end') : SLEEP_STEPS;
    const index = steps.indexOf(state.sleep.choice);
    setSleep(steps[(index + 1) % steps.length]);
  }

  function setSleep(choice) {
    clearTimeout(state.sleep.timer);
    const minutes = typeof choice === 'number' ? choice : 0;
    state.sleep = { choice, until: minutes ? Date.now() + minutes * 60000 : 0, timer: null };
    if (minutes) state.sleep.timer = setTimeout(() => sleepNow(), minutes * 60000);
    view.renderModes();
    if (!choice) view.flash('Sleep-Timer aus');
    else if (choice === 'end') view.flash(mode() === 'podcast' ? 'Stopp am Ende der Episode' : 'Stopp am Ende des Titels');
    else view.flash(`Stopp in ${minutes} min`);
  }

  // Leise ausblenden statt hart abschneiden - man schlaeft ja vielleicht schon.
  async function sleepNow() {
    clearTimeout(state.sleep.timer);
    state.sleep = { choice: 0, until: 0, timer: null };
    view.renderModes();
    if (!playing()) return;
    const deck = current();
    await fadeDeck(deck, 0, volumeLocked ? 0 : SLEEP_FADE);
    if (deck !== current()) return;
    pause();
    deck.gain = 1;
    applyVolume();
  }

  function onTime(deck) {
    if (deck !== current()) return;
    const { audio } = deck;
    const fade = crossfade();
    const duration = audio.duration;
    // Der Crossfade beginnt so, dass der neue Titel voll da ist, wenn der alte
    // endet - nur bei Musik und nicht, wenn der Sleep-Timer dort anhaelt.
    if (fade > 0 && mode() === 'music' && state.sleep.choice !== 'end' && !audio.loop && !deck.handedOver
        && Number.isFinite(duration) && duration > fade * 2 && duration - audio.currentTime <= fade && hasNext()) {
      deck.handedOver = true;
      next({ fade, play: true });
      return;
    }
    if (!state.seeking) view.renderProgress();
    if (!audio.paused) saveState(false);
    positionState();
  }

  function onEnded(deck) {
    if (deck !== current() || deck.handedOver) return;
    if (deck.track?.live) { reconnect(deck); return; }
    if (mode() === 'podcast' && deck.track) {
      deck.track.done = true;
      deck.track.position = 0;
      saveState(true, deck.audio.duration || deck.track.duration || 0);
      view.renderList();
    }
    if (state.sleep.choice === 'end') {
      state.sleep = { choice: 0, until: 0, timer: null };
      pause();
      view.flash('Sleep-Timer: angehalten');
      return;
    }
    if (mode() === 'podcast') {
      // Weiter mit der naechsten ungehoerten Episode, gehoerte ueberspringen.
      const cursor = state.order.findIndex((index, position) => position > state.cursor && !state.tracks[index].done);
      if (cursor >= 0) switchTo(cursor, { play: true });
      else pause();
      return;
    }
    if (hasNext()) {
      next({ fade: 0, play: true });
      return;
    }
    // Ende der Liste ohne Wiederholen: zurueck an den Anfang, angehalten.
    state.wanted = false;
    switchTo(0, { play: false });
  }

  // Ein Sender, der abbricht, bekommt drei neue Versuche.
  function reconnect(deck) {
    if (!state.wanted || state.reconnects >= 3) { pause(); view.flash('Verbindung zum Sender verloren'); return; }
    state.reconnects += 1;
    setTimeout(() => {
      if (deck !== current() || !state.wanted || !deck.track?.live) return;
      disconnect(deck);
      begin(deck);
    }, 1500 * state.reconnects);
  }

  // ─── Laden ────────────────────────────────────────────────────────

  async function request(url, options = {}) {
    const response = await window.apiFetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Player nicht erreichbar.');
    return data;
  }

  async function fetchTracks() {
    const data = await request(`${ENDPOINT}/tracks`);
    state.tracks = Array.isArray(data.tracks) ? data.tracks : [];
    state.missing = data.missing || [];
    state.problem = data.error || '';
    state.rate = Number(data.rate) || 1;
    return data;
  }

  /* Neue Quellen: der laufende Titel spielt weiter, wenn er auch in der neuen
     Auswahl liegt; sonst beginnt die neue Auswahl - ueber `fade` per
     Crossfade, wie beim Wechsel des Presets im laufenden Schoner. */
  async function reloadTracks(options = {}) {
    const wasPlaying = playing();
    let data;
    try {
      data = await fetchTracks();
    } catch (_) {
      return;
    }
    if (!state.tracks.length) {
      release(other());
      release(current());
      state.order = [];
      state.cursor = -1;
      state.wanted = false;
      view.render();
      return;
    }
    const keep = trackIndex(current().track);
    const wanted = startIndex(data.state);
    // Im Radio gilt der gewaehlte Sender, auch wenn der laufende in der Liste bleibt.
    if (keep >= 0 && !(mode() === 'radio' && wanted >= 0 && wanted !== keep)) {
      current().track = state.tracks[keep];
      buildOrder(keep);
      decks.forEach(applyRate);
      if (options.start && !wasPlaying) play();
      view.render();
      return;
    }
    buildOrder(wanted);
    const cursor = Math.max(0, state.cursor);
    if (wasPlaying || options.start) await switchTo(cursor, { fade: wasPlaying ? (options.fade || 0) : 0, play: true });
    else { release(current()); view.render(); }
  }

  async function init() {
    if (started) return;
    started = true;
    try {
      state.settings = (await request(`${ENDPOINT}/settings`)).settings;
    } catch (_) {
      started = false;
      return;
    }
    view.build();
    effects?.mount(document.getElementById('journl-player'), decks.map(deck => deck.audio));
    configureEffects();
    bindSession();
    watchArea();
    applyVolume();
    if (hasSource()) {
      try {
        const data = await fetchTracks();
        const stored = data.state || {};
        const index = startIndex(stored);
        buildOrder(index);
        if (index >= 0 && state.tracks[index].id === stored.track) {
          // Vorbereitet, nicht gestartet: das „Fortsetzen“ bleibt ein Klick.
          const item = state.tracks[index];
          prepare(current(), item, mode() === 'podcast' ? startAt(item) : (stored.position || 0));
          state.resumable = true;
          updateSession();
        }
      } catch (_) { /* ohne Liste bleibt der Player still */ }
    }
    view.render();
  }

  // Audio-reaktiv braucht Web Audio - auf iOS bliebe der Ton dann im
  // Hintergrund stehen, deshalb gibt es den Effekt dort nicht.
  function configureEffects() {
    const wanted = state.settings?.effects || {};
    effects?.configure({ ...wanted, reactive: Boolean(wanted.reactive) && !volumeLocked });
  }

  // ─── Speichern ────────────────────────────────────────────────────

  let settingsTimer = null;
  function persist(changes) {
    state.settings = { ...state.settings, ...changes };
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(async () => {
      try {
        const data = await request(`${ENDPOINT}/settings`, { method: 'POST', body: JSON.stringify(state.settings) });
        state.settings = { ...data.settings, volume: state.settings.volume, muted: state.settings.muted };
        document.dispatchEvent(new CustomEvent('journl:player-settings', { detail: { settings: data.settings, origin: 'player' } }));
      } catch (_) { /* naechste Aenderung versucht es erneut */ }
    }, 500);
  }

  function saveState(force, position) {
    const deck = current();
    if (!deck.track || typeof window.apiFetch !== 'function') return;
    const now = Date.now();
    if (!force && now - state.lastSaved < SAVE_EVERY) return;
    state.lastSaved = now;
    const at = deck.track.live ? 0 : (position ?? (deck.audio.currentTime || 0));
    const duration = Number.isFinite(deck.audio.duration) ? deck.audio.duration : (deck.track.duration || 0);
    if (mode() === 'podcast' && position === undefined) deck.track.position = at;
    // keepalive: auch beim Schliessen des Tabs kommt der Stand noch an.
    window.apiFetch(`${ENDPOINT}/state`, {
      method: 'PUT', keepalive: true,
      body: JSON.stringify({ mode: mode(), track: deck.track.id, position: at, duration }),
    }).catch(() => {});
  }

  window.addEventListener('pagehide', () => saveState(true));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') saveState(true); });

  // ─── Bereich: Schoner, Schreiben-Tab, ueberall ────────────────────

  function screensaverOpen() { return Boolean(window.journlScreensaver?.isOpen?.()); }

  function inArea() {
    const app = document.getElementById('app');
    if (!state.settings || !app || app.classList.contains('hidden')) return false;
    const display = state.settings.display;
    if (display === 'everywhere') return true;
    if (screensaverOpen()) return true;
    if (display === 'screensaver') return false;
    const write = document.getElementById('tab-write');
    return Boolean(write && !write.classList.contains('hidden'));
  }

  function checkArea() {
    const inside = inArea();
    if (!inside && playing()) {
      pause({ keepWanted: true });
      state.areaPaused = true;
    } else if (inside && state.areaPaused && !state.dictationPaused) {
      play();
    }
    view.render();
  }

  document.addEventListener('journl:screensaver-opened', () => { view.place(); checkArea(); });
  document.addEventListener('journl:screensaver-closed', () => { view.place(); checkArea(); });

  function watchArea() {
    const observer = new MutationObserver(checkArea);
    ['app', 'tab-write'].forEach((id) => {
      const element = document.getElementById(id);
      if (element) observer.observe(element, { attributes: true, attributeFilter: ['class'] });
    });
  }

  // ─── Diktat ───────────────────────────────────────────────────────

  document.addEventListener('journl:recording', (event) => {
    if (!state.settings) return;
    if (event.detail?.recording) {
      if (!playing()) return;
      if (state.settings.dictation === 'duck' && !volumeLocked) {
        state.ducked = true;
        fadeDuck(state.settings.duck_level / 100, 0.6);
      } else {
        pause({ keepWanted: true });
        state.dictationPaused = true;
        // Bei gesprochenem Wort fehlt sonst der halbe Satz vor der Pause.
        if (mode() === 'podcast') seek(Math.max(0, current().audio.currentTime - DICTATION_REWIND));
      }
      return;
    }
    if (state.ducked) {
      state.ducked = false;
      fadeDuck(1, 1.2);
    }
    if (state.dictationPaused) {
      state.dictationPaused = false;
      if (inArea()) play();
    }
  });

  // ─── Media Session und Kuerzel ────────────────────────────────────

  function updateSession() {
    effects?.setCover(current().track?.cover || '');
    const session = navigator.mediaSession;
    if (!session) return;
    const item = current().track;
    try {
      session.metadata = item && window.MediaMetadata ? new MediaMetadata({
        title: item.title,
        artist: item.artist || item.source,
        album: item.album || item.source,
        artwork: item.cover ? [{ src: new URL(item.cover, window.location.href).href }] : [],
      }) : null;
      session.playbackState = playing() ? 'playing' : (item ? 'paused' : 'none');
    } catch (_) { /* aeltere Browser kennen nicht alle Felder */ }
  }

  function positionState() {
    const session = navigator.mediaSession;
    const { audio } = current();
    if (!session?.setPositionState || !Number.isFinite(audio.duration) || !audio.duration) return;
    try { session.setPositionState({ duration: audio.duration, position: Math.min(audio.currentTime, audio.duration), playbackRate: audio.playbackRate || 1 }); }
    catch (_) { /* ungueltige Zwischenstaende beim Laden */ }
  }

  function bindSession() {
    const session = navigator.mediaSession;
    if (!session) return;
    const handlers = {
      play: () => { if (inArea()) play(); },
      pause: () => pause(),
      stop: () => pause(),
      previoustrack: () => previous(),
      nexttrack: () => next(),
      seekbackward: details => skip(-(details?.seekOffset || state.settings?.skip_back || 10)),
      seekforward: details => skip(details?.seekOffset || state.settings?.skip_forward || 30),
      seekto: (details) => { if (typeof details?.seekTime === 'number') seek(details.seekTime); },
    };
    Object.entries(handlers).forEach(([action, handler]) => {
      try { session.setActionHandler(action, handler); } catch (_) { /* nicht unterstuetzt */ }
    });
  }

  /* Strg+Alt meldet Windows auch fuer AltGr - wie bei den Presets zaehlt die
     Kombination deshalb nur ohne AltGraph. Cmd zaehlt wie Strg. */
  function combo(event) {
    if (event.getModifierState?.('AltGraph')) return '';
    if (!event.code || /^(Control|Alt|Shift|Meta|OS)/.test(event.code)) return '';
    const parts = [];
    if (event.ctrlKey || event.metaKey) parts.push('Ctrl');
    if (event.altKey) parts.push('Alt');
    if (event.shiftKey) parts.push('Shift');
    return [...parts, event.code].join('+');
  }

  function run(action) {
    const settings = state.settings;
    const actions = {
      play_pause: togglePlay,
      back: () => skip(-settings.skip_back),
      forward: () => skip(settings.skip_forward),
      previous,
      next: () => next(),
      volume_up: () => setVolume(settings.volume + VOLUME_STEP, { announce: true }),
      volume_down: () => setVolume(settings.volume - VOLUME_STEP, { announce: true }),
    };
    actions[action]?.();
  }

  function handleKey(event) {
    if (!state.settings || !inArea()) return false;
    const pressed = combo(event);
    if (!pressed) return false;
    // preset_1 bis preset_9 liegen hier nur gespeichert; ausgeloest werden
    // sie in screensaver-presets.js, auch ausserhalb von „Anzeigen in“.
    const action = Object.keys(ACTION_LABELS).find(name => state.settings.shortcuts?.[name] === pressed);
    if (!action) return false;
    event.preventDefault();
    run(action);
    return true;
  }

  // Im offenen Schoner fragt screensaver.js selbst nach, bevor eine Taste ihn weckt.
  document.addEventListener('keydown', (event) => {
    if (screensaverOpen()) return;
    handleKey(event);
  });

  // ─── Einstellungen von aussen ─────────────────────────────────────

  document.addEventListener('journl:player-settings', (event) => {
    const { settings, origin, start, preset } = event.detail || {};
    if (!settings || origin === 'player') return;
    if (!state.settings) { state.settings = settings; init(); return; }
    const sourcesChanged = sourceKey(settings) !== sourceKey(state.settings);
    const shuffleChanged = settings.shuffle !== state.settings.shuffle;
    state.settings = settings;
    view.build();
    configureEffects();
    decks.forEach((deck) => { deck.audio.loop = mode() === 'music' && settings.repeat === 'track'; });
    applyVolume();
    if (sourcesChanged) {
      const fade = preset ? Math.max(settings.crossfade || 0, PRESET_FADE) : 0;
      if (state.sleep.choice === 'end') setSleep(0);
      reloadTracks({ fade, start: start && hasSource() });
    } else {
      if (shuffleChanged) buildOrder(trackIndex(current().track));
      if (start && preset && hasSource() && !playing()) play();
    }
    checkArea();
  });

  // Was zur gewaehlten Quelle gehoert; aendert es sich, kommt eine neue Liste.
  function sourceKey(settings) {
    const { audio = 'music' } = settings || {};
    return JSON.stringify([audio, audio === 'music' ? settings.sources : audio === 'podcast' ? settings.podcast : settings.station]);
  }

  // Menue: ein- und ausblenden, ohne in die Einstellungen zu muessen.
  // Einblenden fuehrt zur Pille; wer „Immer sichtbar“ will, stellt das dort ein.
  document.addEventListener('click', (event) => {
    if (!event.target.closest('[data-player-toggle]')) return;
    document.getElementById('burger-menu')?.classList.add('hidden');
    if (!state.settings) return;
    if (!hasSource()) { window.openSettingsGroup?.('player', 'Player'); return; }
    const show = state.settings.visibility === 'hidden';
    persist({ visibility: show ? 'minimal' : 'hidden' });
    view.render();
    if (!show) view.flash('Player ausgeblendet – Kürzel und Sperrbildschirm bleiben');
    else if (!inArea()) view.flash(`Player eingeblendet – zu sehen ${state.settings.display === 'screensaver' ? 'im Bildschirmschoner' : 'im Schreiben-Tab'}`);
  });

  // Abo erneuert, Sender angelegt oder geloescht: die Liste neu holen.
  document.addEventListener('journl:player-sources-changed', () => { if (state.settings) reloadTracks(); });

  // ─── Start ────────────────────────────────────────────────────────

  const app = document.getElementById('app');
  function whenVisible() {
    if (!app || app.classList.contains('hidden')) return;
    observer.disconnect();
    init();
  }
  const observer = new MutationObserver(whenVisible);
  if (app) observer.observe(app, { attributes: true, attributeFilter: ['class'] });
  whenVisible();

  window.journlScreensaverPlayer = {
    handleKey, play, pause, togglePlay, next, previous,
    isPlaying: playing,
    settings: () => state.settings,
    volumeLocked,
    formatShortcut, combo,
    actions: ACTION_LABELS,
  };
}());