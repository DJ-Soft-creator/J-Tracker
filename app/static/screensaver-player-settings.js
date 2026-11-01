/* Einstellungen des Players im Einstellungen-Modal (Gruppe `player`).

   Wie beim Schoner entstehen die Felder hier, weil die Musikordner erst vom
   Server kommen, und gespeichert wird sofort bei jeder Aenderung. Podcast-Abos
   und Sender verwaltet screensaver-player-sources.js im Bereich
   `[data-audio-sources]`; ausgewaehlt wird ueber Radio-Buttons, die hier
   mitgelesen werden. Jede Antwort
   geht als `journl:player-settings` (origin `settings`) an den Player; was der
   Player selbst aendert (Lautstaerke, Shuffle, Wiederholen, Minimieren),
   kommt mit origin `player` zurueck und wird hier nur nachgezogen.

   Kuerzel werden per Tastendruck aufgenommen: Klick auf die Belegung, dann
   die Kombination druecken (Esc bricht ab, Entf/Ruecktaste macht die Aktion
   unbelegt). Ohne Strg/Cmd oder Alt nimmt der Server sie nicht an - sonst
   waere es eine Tipp-Taste. Die Preset-Tasten (`preset_1` bis `preset_9`)
   werden hier mit aufgenommen; ausgeloest werden sie in
   screensaver-presets.js. */
(function () {
  const container = document.getElementById('settings-screensaver-player');
  if (!container) return;

  const ENDPOINT = '/api/screensaver/player/settings';
  const LABEL = 'block text-xs text-gray-400 mb-1';
  const SELECT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500';
  const ROW = 'flex items-center justify-between gap-3 text-sm text-gray-200';
  const CHECK = 'h-5 w-5 accent-green-500';
  const AUDIO = [['music', 'Eigene Musik'], ['podcast', 'Podcast'], ['radio', 'Internetradio']];
  const EFFECTS = [
    ['color', 'Farbe aus dem Bild', 'Akzent und Glow folgen dem Schoner-Bild bzw. dem Cover'],
    ['reactive', 'Audio-reaktiv', 'Aurora-Leuchten im Takt der Musik'],
    ['glass', 'Glas-Optik', 'Mattglas mit wandernder Lichtkante'],
    ['breathe', 'Ruhiges Atmen', 'Langsames Pulsieren während der Wiedergabe'],
  ];
  const REPEAT = [['off', 'Aus'], ['folder', 'Ordner wiederholen'], ['track', 'Titel wiederholen']];
  const VISIBILITY = [['visible', 'Sichtbar – volle Leiste'], ['minimal', 'Minimal – leuchtende Pille, klappt bei Hover/Tippen auf'],
    ['hidden', 'Unsichtbar – nur Tastatur und Medientasten']];
  const DISPLAY = [['everywhere', 'Überall'], ['write', 'Nur im Schreiben-Tab (mit Bildschirmschoner)'],
    ['screensaver', 'Nur im Bildschirmschoner']];
  const DICTATION = [['pause', 'Pausieren'], ['duck', 'Leiser stellen']];
  const BACK = [5, 10, 15, 20, 30];
  const FORWARD = [10, 15, 20, 30, 45, 60];
  const PLAYER_KEYS = ['play_pause', 'back', 'forward', 'previous', 'next', 'volume_up', 'volume_down'];
  const PRESET_KEYS = Array.from({ length: 9 }, (_, index) => `preset_${index + 1}`);
  const ALL_KEYS = PLAYER_KEYS.concat(['visual'], PRESET_KEYS);
  const DEFAULT_SHORTCUTS = {
    play_pause: 'Ctrl+Alt+Space', back: 'Ctrl+Alt+ArrowLeft', forward: 'Ctrl+Alt+ArrowRight',
    previous: 'Ctrl+Alt+Shift+ArrowLeft', next: 'Ctrl+Alt+Shift+ArrowRight',
    volume_up: 'Ctrl+Alt+ArrowUp', volume_down: 'Ctrl+Alt+ArrowDown', visual: 'Ctrl+Alt+KeyV',
    ...Object.fromEntries(PRESET_KEYS.map((action, index) => [action, `Ctrl+Alt+Digit${index + 1}`])),
  };
  const RESERVED = /^Ctrl\+Alt\+(KeyB|KeyK)$/;

  // iOS: die Werte gelten fuers Konto, greifen auf diesem Geraet aber nicht.
  const LOCKED_HINT = 'Auf diesem Gerät lässt der Browser die Lautstärke nicht steuern: kein Crossfade, Lautstärke über die Tasten des Geräts. Die Werte gelten auf deinen anderen Geräten.';
  let settings = null;
  let sources = [];
  let loaded = false;

  function player() { return window.journlScreensaverPlayer; }
  function locked() { return Boolean(player()?.volumeLocked); }
  function format(combo) { return player()?.formatShortcut(combo) || combo; }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function options(entries, current) {
    return entries.map(([value, label]) =>
      `<option value="${value}"${String(value) === String(current) ? ' selected' : ''}>${label}</option>`).join('');
  }

  function seconds(entries) { return entries.map(value => [value, `${value} Sekunden`]); }

  function sourceList() {
    const chosen = new Set(settings.sources || []);
    // Gewaehlte Ordner ohne Musik (geleert, per Sync entfernt) bleiben sichtbar,
    // damit man sie abwaehlen kann.
    const missing = (settings.sources || []).filter(id => !sources.some(source => source.id === id))
      .map(id => ({ id, label: id.split(':').slice(1).join(':'), kind: id.split(':')[0], count: 0 }));
    const rows = sources.concat(missing).map(source => `
      <label class="flex items-center justify-between gap-3 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-200">
        <span>
          <span class="block font-medium">${escape(source.label)}</span>
          <span class="block text-xs text-gray-500">${source.kind === 'user' ? 'Privater Ordner' : 'Gemeinsamer Ordner'} · ${source.count ? `${source.count} Titel` : 'keine Musik gefunden'}</span>
        </span>
        <input type="checkbox" data-source="${escape(source.id)}" class="${CHECK}"${chosen.has(source.id) ? ' checked' : ''} />
      </label>`).join('');
    return rows || `<p class="text-xs text-gray-500">Noch keine Ordner mit Musik. Lade MP3, M4A, OGG/OPUS, FLAC oder WAV unter <button type="button" data-open-folders class="text-blue-400 hover:text-blue-300">Bildschirmschoner → Meine Ordner</button> hoch oder lege sie per Sync dort ab.</p>`;
  }

  function actionLabel(action) {
    if (action === 'visual') return 'Klangbild im Schoner umschalten';
    const preset = /^preset_(\d)$/.exec(action);
    if (!preset) return player()?.actions?.[action] || action;
    const entry = window.journlScreensaverPresets?.list?.()[Number(preset[1]) - 1];
    return `Preset ${preset[1]}${entry ? ` · ${entry.label}` : ''}`;
  }

  function shortcutRow(action) {
    const combo = settings.shortcuts[action];
    return `
      <div class="${ROW}">
        <span class="min-w-0 truncate text-gray-300">${escape(actionLabel(action))}</span>
        <button type="button" data-shortcut="${action}" class="min-w-[9rem] rounded-md border border-gray-700 bg-gray-800 px-2.5 py-1 text-xs ${combo ? 'text-gray-200' : 'text-gray-500'} hover:border-blue-500/60 transition">${combo ? escape(format(combo)) : 'unbelegt'}</button>
      </div>`;
  }

  function shortcutRows() {
    return `${PLAYER_KEYS.map(shortcutRow).join('')}${shortcutRow('visual')}
      <details class="group pt-1">
        <summary class="cursor-pointer list-none text-xs text-gray-400 hover:text-gray-200">Presets starten <span class="text-gray-500">(Reihenfolge wie in der Preset-Liste)</span></summary>
        <div class="mt-1.5 space-y-1.5">${PRESET_KEYS.map(shortcutRow).join('')}</div>
      </details>`;
  }

  function musicSection() {
    return `
      <div>
        <span class="${LABEL}">Musikquellen</span>
        <div class="space-y-2" data-sources>${sourceList()}</div>
        <p class="text-xs text-gray-500 mt-1.5">Mehrere Ordner werden gemischt. Titel und Cover kommen aus den Tags der Dateien, sonst aus Dateiname und <code class="text-gray-400">cover.jpg</code>.</p>
      </div>`;
  }

  function render() {
    const music = settings.audio === 'music';
    const sources = window.journlScreensaverPlayerSources;
    container.innerHTML = `
      <p class="text-xs text-gray-500 mb-3">Eigene Musik, Podcasts oder Internetradio. Der Player läuft unabhängig vom Schoner weiter, auch über Tabwechsel.</p>
      <div class="space-y-4">
        <div><label class="${LABEL}" for="player-audio">Quelle</label><select id="player-audio" class="${SELECT}">${options(AUDIO, settings.audio)}</select></div>
        <div data-audio-sources>${music ? musicSection() : (sources?.markup(settings) || '')}</div>
        <div class="space-y-2">
          <span class="${LABEL}">Wiedergabe</span>
          <div class="space-y-2${music ? '' : ' hidden'}">
            <label class="${ROW}"><span>Zufällige Reihenfolge</span><input id="player-shuffle" type="checkbox" class="${CHECK}"${settings.shuffle ? ' checked' : ''} /></label>
            <div><label class="${LABEL}" for="player-repeat">Wiederholen</label><select id="player-repeat" class="${SELECT}">${options(REPEAT, settings.repeat)}</select></div>
          </div>
          <div>
            <label class="${LABEL}" for="player-crossfade">Crossfade <span data-crossfade-value class="text-gray-500">${settings.crossfade ? `${settings.crossfade} s` : 'aus'}</span></label>
            <input id="player-crossfade" type="range" min="0" max="12" step="1" value="${settings.crossfade}" class="w-full accent-green-500" />
          </div>
          <div>
            <label class="${LABEL}" for="player-volume">Lautstärke <span data-volume-value class="text-gray-500">${settings.volume} %</span></label>
            <input id="player-volume" type="range" min="0" max="100" step="5" value="${settings.volume}" class="w-full accent-green-500" />
          </div>
          ${locked() ? `<p class="text-xs text-amber-300/80">${LOCKED_HINT}</p>` : ''}
          <div class="grid grid-cols-2 gap-2">
            <div><label class="${LABEL}" for="player-skip-back">Zurückspulen</label><select id="player-skip-back" class="${SELECT}">${options(seconds(BACK), settings.skip_back)}</select></div>
            <div><label class="${LABEL}" for="player-skip-forward">Vorspulen</label><select id="player-skip-forward" class="${SELECT}">${options(seconds(FORWARD), settings.skip_forward)}</select></div>
          </div>
        </div>
        <div class="space-y-2">
          <span class="${LABEL}">Darstellung</span>
          <div><label class="${LABEL}" for="player-visibility">Sichtbarkeit</label><select id="player-visibility" class="${SELECT}">${options(VISIBILITY, settings.visibility)}</select></div>
          <label class="${ROW}"><span>Leiste nach 8 s ohne Bewegung zur Pille <span class="block text-xs text-gray-500">Nur bei „Sichtbar“, solange Musik läuft</span></span><input id="player-autohide" type="checkbox" class="${CHECK}"${settings.autohide ? ' checked' : ''} /></label>
          <label class="${ROW}"><span>Titel anzeigen</span><input id="player-show-title" type="checkbox" class="${CHECK}"${settings.show_title ? ' checked' : ''} /></label>
          <label class="${ROW}"><span>Cover anzeigen</span><input id="player-show-cover" type="checkbox" class="${CHECK}"${settings.show_cover ? ' checked' : ''} /></label>
          <div>
            <label class="${LABEL}" for="player-display">Anzeigen in</label><select id="player-display" class="${SELECT}">${options(DISPLAY, settings.display)}</select>
            <p class="text-xs text-gray-500 mt-1.5">Außerhalb dieses Bereichs pausiert der Ton und spielt beim Zurückkommen weiter.</p>
          </div>
        </div>
        <div class="space-y-2">
          <span class="${LABEL}">Lichteffekte</span>
          ${EFFECTS.map(([key, label, hint]) => `<label class="${ROW}"><span>${label}<span class="block text-xs text-gray-500">${key === 'reactive' && locked() ? 'Auf diesem Gerät nicht verfügbar – der Ton liefe sonst im Hintergrund nicht weiter' : hint}</span></span><input data-effect="${key}" type="checkbox" class="${CHECK}"${settings.effects?.[key] ? ' checked' : ''} /></label>`).join('')}
        </div>
        <div class="space-y-2">
          <span class="${LABEL}">Beim Diktieren</span>
          <select id="player-dictation" class="${SELECT}">${options(DICTATION, settings.dictation)}</select>
          <div data-duck-row class="${settings.dictation === 'duck' ? '' : 'hidden'}">
            <label class="${LABEL}" for="player-duck">Pegel während der Aufnahme <span data-duck-value class="text-gray-500">${settings.duck_level} %</span></label>
            <input id="player-duck" type="range" min="0" max="60" step="5" value="${settings.duck_level}" class="w-full accent-green-500" />
          </div>
          <p class="text-xs text-gray-500">Nach dem Ende der Aufnahme läuft die Musik weiter wie vorher.${locked() ? ' Auf diesem Gerät wird immer pausiert.' : ''}</p>
        </div>
        <div class="space-y-1.5">
          <span class="${LABEL}">Tastenkürzel <span class="text-gray-500">– Klick auf die Belegung, dann neue Kombination drücken</span></span>
          ${shortcutRows()}
          <div class="flex items-center justify-between gap-3 pt-1">
            <button type="button" data-reset-shortcuts class="text-xs text-blue-400 hover:text-blue-300">Standard wiederherstellen</button>
            <span class="text-xs text-gray-500">Medientasten der Tastatur gehen immer.</span>
          </div>
        </div>
        <p data-status class="min-h-4 text-xs text-gray-500"></p>
      </div>`;
  }

  function collect() {
    const value = id => container.querySelector(`#${id}`);
    const picked = name => container.querySelector(`input[name="${name}"]:checked`)?.value;
    // Die Ordnerliste steht nur bei „Eigene Musik“ in der Maske.
    const folders = container.querySelector('[data-sources]')
      ? Array.from(container.querySelectorAll('[data-source]:checked')).map(box => box.dataset.source) : settings.sources;
    return {
      ...settings,
      audio: value('player-audio').value,
      sources: folders,
      podcast: picked('player-podcast') ?? settings.podcast,
      station: picked('player-station') ?? settings.station,
      shuffle: value('player-shuffle').checked,
      repeat: value('player-repeat').value,
      crossfade: Number(value('player-crossfade').value),
      volume: Number(value('player-volume').value),
      skip_back: Number(value('player-skip-back').value),
      skip_forward: Number(value('player-skip-forward').value),
      visibility: value('player-visibility').value,
      autohide: value('player-autohide').checked,
      show_title: value('player-show-title').checked,
      show_cover: value('player-show-cover').checked,
      display: value('player-display').value,
      effects: Object.fromEntries(EFFECTS.map(([key]) => [key, Boolean(container.querySelector(`[data-effect="${key}"]`)?.checked)])),
      dictation: value('player-dictation').value,
      duck_level: Number(value('player-duck').value),
    };
  }

  function setStatus(text, error = false) {
    const status = container.querySelector('[data-status]');
    if (!status) return;
    status.textContent = text || '';
    status.classList.toggle('text-red-400', error);
    status.classList.toggle('text-gray-500', !error);
  }

  async function save(next) {
    setStatus('Wird gespeichert…');
    try {
      const response = await window.apiFetch(ENDPOINT, { method: 'POST', body: JSON.stringify(next) });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Nicht gespeichert.');
      settings = data.settings;
      setStatus('Gespeichert');
      document.dispatchEvent(new CustomEvent('journl:player-settings', { detail: { settings, origin: 'settings' } }));
      return true;
    } catch (error) {
      setStatus(error.message || 'Nicht gespeichert.', true);
      return false;
    }
  }

  async function load() {
    try {
      const response = await window.apiFetch(`${ENDPOINT}?sources=1`, { cache: 'no-store' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error);
      settings = data.settings;
      sources = data.sources || [];
      loaded = true;
      render();
    } catch (_) {
      container.innerHTML = '<p class="text-xs text-gray-500">Player-Einstellungen nicht erreichbar.</p>';
    }
  }

  // ─── Tastenkuerzel aufnehmen ──────────────────────────────────────

  let recording = null;
  function stopRecording() {
    if (!recording) return;
    window.removeEventListener('keydown', recording.listener, true);
    recording = null;
  }

  function recordShortcut(button) {
    stopRecording();
    const action = button.dataset.shortcut;
    button.textContent = 'Kombination drücken…';
    button.classList.add('border-blue-500', 'text-blue-200');
    // Auf window und in der Capture-Phase: so sieht weder der Player noch der
    // Schoner noch ein Preset-Kuerzel die Taste, waehrend sie aufgenommen wird.
    const listener = async (event) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.key === 'Escape') { stopRecording(); render(); return; }
      if ((event.key === 'Backspace' || event.key === 'Delete') && !event.ctrlKey && !event.altKey && !event.metaKey) {
        stopRecording();
        const ok = await save({ ...collect(), shortcuts: { ...settings.shortcuts, [action]: '' } });
        render();
        if (ok) setStatus(`${actionLabel(action)}: unbelegt`);
        return;
      }
      const combo = player()?.combo(event) || '';
      if (!combo) return;  // nur ein Modifikator - weiter warten
      stopRecording();
      if (!/(^|\+)(Ctrl|Alt)\+/.test(combo)) { render(); setStatus('Bitte mit Strg/Cmd oder Alt kombinieren.', true); return; }
      if (RESERVED.test(combo)) { render(); setStatus(`${format(combo)} ist schon vergeben (Schoner oder Schreibmodus).`, true); return; }
      const taken = ALL_KEYS.find(name => name !== action && settings.shortcuts[name] === combo);
      if (taken) { render(); setStatus(`${format(combo)} liegt schon auf „${actionLabel(taken)}“.`, true); return; }
      const ok = await save({ ...collect(), shortcuts: { ...settings.shortcuts, [action]: combo } });
      render();
      if (ok) setStatus(`${actionLabel(action)}: ${format(combo)}`);
    };
    recording = { listener };
    window.addEventListener('keydown', listener, true);
    // Wegklicken oder Modal schliessen beendet die Aufnahme - sonst schluckte
    // sie den naechsten Tastendruck irgendwo in der App.
    button.addEventListener('blur', () => { if (recording?.listener === listener) { stopRecording(); render(); } }, { once: true });
  }

  container.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-shortcut]');
    if (button) { recordShortcut(button); return; }
    if (event.target.closest('[data-reset-shortcuts]')) {
      stopRecording();
      if (await save({ ...collect(), shortcuts: { ...DEFAULT_SHORTCUTS } })) { render(); setStatus('Standardkürzel wiederhergestellt.'); }
      return;
    }
    if (event.target.closest('[data-open-folders]')) {
      window.openSettingsGroup?.('screensaver', 'Bildschirmschoner');
      setTimeout(() => document.getElementById('settings-screensaver-folders')?.scrollIntoView({ block: 'start', behavior: 'smooth' }), 150);
    }
  });

  // Regler: die Zahl folgt sofort, gespeichert wird beim Loslassen (change).
  container.addEventListener('input', (event) => {
    const labels = { 'player-crossfade': ['[data-crossfade-value]', v => (Number(v) ? `${v} s` : 'aus')],
      'player-volume': ['[data-volume-value]', v => `${v} %`], 'player-duck': ['[data-duck-value]', v => `${v} %`] };
    const entry = labels[event.target.id];
    if (entry) container.querySelector(entry[0]).textContent = entry[1](event.target.value);
  });

  container.addEventListener('change', async (event) => {
    if (!event.target.closest('input, select') || event.target.closest('[data-no-save]')) return;
    if (event.target.id === 'player-dictation') {
      container.querySelector('[data-duck-row]').classList.toggle('hidden', event.target.value !== 'duck');
    }
    const ok = await save(collect());
    // Andere Quelle oder anderes Abo: die Maske zeigt die passenden Felder.
    if (ok && (event.target.id === 'player-audio' || event.target.name === 'player-podcast' || event.target.name === 'player-station')) render();
  });

  // Ein frisches erstes Abo oder ein neuer Sender wird gleich ausgewaehlt.
  document.addEventListener('journl:player-sources-changed', async (event) => {
    const pick = event.detail?.pick;
    if (!loaded || !pick) return;
    if (await save({ ...collect(), ...pick })) render();
  });

  // Der Player hat selbst etwas geaendert (Lautstaerke, Shuffle, ...) oder ein
  // Preset hat die Musik gewechselt: die Maske zeigt den neuen Stand.
  document.addEventListener('journl:player-settings', (event) => {
    const detail = event.detail || {};
    if (detail.origin === 'settings' || !detail.settings || !loaded || recording) return;
    settings = detail.settings;
    render();
  });
  document.addEventListener('journl:screensaver-folders-changed', () => { if (loaded) load(); });

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'player') load();
    else stopRecording();
  });
}());
