/* Einstellungen des Bildschirmschoners im Einstellungen-Modal.

   Die Felder entstehen hier statt im Template: die Quellenliste kommt ohnehin
   erst vom Server (Themen, private und gemeinsame Ordner, eigene
   Journalbilder), und beide Templates haetten das Markup sonst doppelt.

   Gespeichert wird sofort bei jeder Aenderung - fuer diese Handvoll Felder
   waere ein Speichern-Knopf nur ein zusaetzlicher Klick. Jede Antwort wird als
   `journl:screensaver-settings` weitergereicht, damit eine offene Diashow den
   neuen Stand ohne Neustart uebernimmt. */
(function () {
  const container = document.getElementById('settings-screensaver');
  if (!container) return;

  const INTERVALS = [[10, '10 Sekunden'], [20, '20 Sekunden'], [30, '30 Sekunden'], [45, '45 Sekunden'],
    [60, '1 Minute'], [120, '2 Minuten'], [300, '5 Minuten'], [600, '10 Minuten']];
  const INFO = [['full', 'Uhrzeit, Aufnahme und Bildnachweis'], ['clock', 'Nur Uhrzeit und Aufnahme'],
    ['image', 'Nur Bildnachweis'], ['off', 'Aus']];
  const TRANSITIONS = [['fade', 'Überblenden'], ['cut', 'Harter Schnitt'], ['kenburns', 'Langsamer Zoom']];
  const MODES = [['screensaver', 'Diashow (jede Taste beendet)'], ['focus', 'Kulisse (Schreibfeld über den Bildern)'],
    ['questions', 'Fragen (Impuls oder Fragen-Session über den Bildern)']];
  const ORDERS = [['shuffle', 'Zufällig gemischt'], ['spread', 'Quellen gleichmäßig abwechselnd'],
    ['sequential', 'Der Reihe nach (Journalbilder nach Datum)']];
  const VISUAL_FORMS = [['lava', 'Lavalampe – ein Klecks'], ['blobs', 'Kleckse, die verschmelzen'], ['ink', 'Tinte – fließende Schlieren'],
    ['kaleido', 'Kaleidoskop – gespiegelte Keile'], ['mandala', 'Mandala – drehende Blütenfigur'], ['tunnel', 'Tunnel – Ringe fliegen heran'],
    ['fractal', 'Fraktal – Julia-Menge, die morpht'], ['plasma', 'Plasma – Farbwellen über alles']];
  const VISUAL_REACTIONS = [['free', 'Frei – ohne Musik, Tempo schwankt sanft'], ['soft', 'Sanft – wabert mit'], ['beat', 'Im Takt – stößt bei jedem Schlag'],
    ['wild', 'Wild – Stöße, Farbsprünge, Richtungswechsel']];
  const VISUAL_BLENDS = [['glow', 'Leuchten – Motiv bleibt erkennbar'], ['tint', 'Farbschleier – tönt ein'], ['bold', 'Kräftig – Bild wird Kulisse']];
  const VISUAL_QUALITIES = [['saver', 'Sparsam (24 fps, weich)'], ['balanced', 'Ausgewogen (30 fps)'], ['full', 'Voll (60 fps, scharf)']];
  const VISUAL_DEFAULT = { enabled: false, form: 'lava', blend: 'glow', strength: 70, quality: 'balanced', reaction: 'beat' };

  const LABEL = 'block text-xs text-gray-400 mb-1';
  const SELECT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500';

  let settings = null;
  let loaded = false;
  let lastData = null;

  function options(entries, current) {
    return entries.map(([value, label]) =>
      `<option value="${value}"${String(value) === String(current) ? ' selected' : ''}>${label}</option>`).join('');
  }

  function seconds(value) {
    return `${Number(value).toFixed(1).replace('.', ',')} s`;
  }

  /* Eine Überblendung darf länger dauern als ein Bild steht - dann geht das
     eine Bild ins andere über, ohne je still zu stehen. Gewollt ist das
     selten, deshalb sagt die Zeile es, statt den Regler zu begrenzen. */
  function fadeNote() {
    const row = container.querySelector('[data-fade-row]');
    if (!row) return;
    const transition = container.querySelector('#screensaver-transition').value;
    const fade = Number(container.querySelector('#screensaver-fade').value);
    const interval = Number(container.querySelector('#screensaver-interval').value);
    row.classList.toggle('hidden', transition === 'cut');
    container.querySelector('[data-fade-value]').textContent = seconds(fade);
    container.querySelector('[data-fade-note]').textContent = fade >= interval
      ? 'Länger als die Standzeit: die Bilder blenden ohne Pause ineinander.'
      : '';
  }

  /* Klangbild: Farbklecks zur Musik (screensaver-visual.js). Die Hinweise
     sagen, was auf diesem Geraet gilt. */
  function visualSection(visual) {
    const ios = Boolean(window.journlScreensaverPlayer?.volumeLocked);
    const shortcut = window.journlScreensaverPlayer?.settings?.()?.shortcuts?.visual;
    const key = shortcut ? window.journlScreensaverPlayer.formatShortcut(shortcut) : '';
    return `
        <div class="rounded-lg border border-gray-700 px-3 py-2.5 space-y-3" data-visual>
          <label class="flex items-center justify-between gap-3 text-sm text-gray-200">
            <span><span class="block font-medium">Klangbild</span>
              <span class="block text-xs text-gray-500">Farbeffekt über dem Bild, der sich zur Musik des Players bewegt</span></span>
            <input id="screensaver-visual-enabled" type="checkbox" class="h-5 w-5 accent-green-500"${visual.enabled ? ' checked' : ''} />
          </label>
          <div data-visual-options class="space-y-3${visual.enabled ? '' : ' hidden'}">
            <div>
              <label class="${LABEL}" for="screensaver-visual-form">Form</label>
              <select id="screensaver-visual-form" class="${SELECT}">${options(VISUAL_FORMS, visual.form)}</select>
            </div>
            <div>
              <label class="${LABEL}" for="screensaver-visual-reaction">Reaktion auf die Musik</label>
              <select id="screensaver-visual-reaction" class="${SELECT}">${options(VISUAL_REACTIONS, visual.reaction)}</select>
            </div>
            <div>
              <label class="${LABEL}" for="screensaver-visual-blend">Über dem Bild</label>
              <select id="screensaver-visual-blend" class="${SELECT}">${options(VISUAL_BLENDS, visual.blend)}</select>
            </div>
            <div>
              <label class="${LABEL}" for="screensaver-visual-strength">Intensität <span data-visual-strength class="text-gray-500">${visual.strength}%</span></label>
              <input id="screensaver-visual-strength" type="range" min="10" max="100" step="5" value="${visual.strength}" class="w-full accent-green-500" />
            </div>
            <div>
              <label class="${LABEL}" for="screensaver-visual-quality">Qualität</label>
              <select id="screensaver-visual-quality" class="${SELECT}">${options(VISUAL_QUALITIES, visual.quality)}</select>
            </div>
            <p class="text-xs text-gray-500">${ios
              ? 'Auf diesem Gerät bewegt es sich im simulierten Takt: eine echte Tonanalyse würde die Musik bei gesperrtem Bildschirm anhalten.'
              : 'Ohne Musik atmet es ruhig weiter. In Kulisse und Fragen liegt es unter dem Text.'}${key ? ` ${escape(key)} schaltet im Schoner die Form reihum.` : ''}</p>
          </div>
        </div>`;
  }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function render(data) {
    lastData = data;
    settings = data.settings;
    const chosen = new Set(settings.sources || []);
    const sources = (data.sources || []).map(source => `
      <label class="flex items-center justify-between gap-3 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-200">
        <span>
          <span class="block font-medium">${source.label}</span>
          <span class="block text-xs text-gray-500">${source.kind === 'none' ? 'Nur das Klangbild auf Schwarz'
            : source.kind === 'theme' ? 'Wikimedia Commons'
            : source.kind === 'journal' ? `Eigene Medien · ${source.count} Bilder`
            : source.kind === 'user' ? `Privater Ordner · ${source.count} Bilder`
            : `Gemeinsamer Ordner · ${source.count} Bilder`}</span>
        </span>
        <input type="checkbox" data-source="${source.id}" class="h-5 w-5 accent-green-500"${chosen.has(source.id) ? ' checked' : ''} />
      </label>`).join('');

    container.innerHTML = `
      <p class="text-xs text-gray-500 mb-3">Die Bilder holt der Server und legt sie zwischen — das Endgerät spricht nie mit einem fremden Host, und die Diashow läuft auch ohne Verbindung weiter.</p>
      <div class="space-y-3">
        <div>
          <label class="${LABEL}" for="screensaver-interval">Bild wechseln alle</label>
          <select id="screensaver-interval" class="${SELECT}">${options(INTERVALS, settings.interval)}</select>
        </div>
        <div>
          <span class="${LABEL}">Bildquellen</span>
          <div class="space-y-2" data-sources>${sources || '<p class="text-xs text-gray-500">Keine Quellen gefunden.</p>'}</div>
          <p class="text-xs text-gray-500 mt-1.5">Private Sammlungen legst du unten unter „Meine Ordner“ an; Unterordner unter <code class="text-gray-400">DATA_DIR/screensaver/</code> sehen alle Konten.</p>
        </div>
        <div>
          <label class="${LABEL}" for="screensaver-order">Reihenfolge</label>
          <select id="screensaver-order" class="${SELECT}">${options(ORDERS, settings.order)}</select>
        </div>
        <div>
          <label class="${LABEL}" for="screensaver-info">Info unten links</label>
          <select id="screensaver-info" class="${SELECT}">${options(INFO, settings.info)}</select>
          <p class="text-xs text-gray-500 mt-1.5">Bei „Aus“ bleibt für Bilder unter freier Lizenz ein sehr leiser Namensnachweis stehen — das verlangt die Lizenz.</p>
          <label class="mt-2 flex items-center justify-between gap-3 text-sm text-gray-200">
            <span>Mehr über das Bild <span class="block text-xs text-gray-500">Beschreibung und Aufnahmedatum, soweit bekannt</span></span>
            <input id="screensaver-details" type="checkbox" class="h-5 w-5 accent-green-500"${settings.details ? ' checked' : ''} />
          </label>
        </div>
        <div>
          <label class="${LABEL}" for="screensaver-transition">Übergang</label>
          <select id="screensaver-transition" class="${SELECT}">${options(TRANSITIONS, settings.transition)}</select>
        </div>
        <div data-fade-row class="${settings.transition === 'cut' ? 'hidden' : ''}">
          <label class="${LABEL}" for="screensaver-fade">Dauer des Übergangs <span data-fade-value class="text-gray-500">${seconds(settings.fade_seconds)}</span></label>
          <input id="screensaver-fade" type="range" min="0" max="10" step="0.1" value="${settings.fade_seconds}" class="w-full accent-green-500" />
          <p data-fade-note class="text-xs text-gray-500 mt-1.5"></p>
        </div>
        <div>
          <label class="${LABEL}" for="screensaver-dim">Abdunkelung <span data-dim-value class="text-gray-500">${settings.dim}%</span></label>
          <input id="screensaver-dim" type="range" min="0" max="80" step="5" value="${settings.dim}" class="w-full accent-green-500" />
        </div>
        ${visualSection({ ...VISUAL_DEFAULT, ...(settings.visual || {}) })}
        <div>
          <label class="${LABEL}" for="screensaver-mode">Beim Öffnen starten als</label>
          <select id="screensaver-mode" class="${SELECT}">${options(MODES, settings.mode)}</select>
        </div>
        <div class="flex items-center justify-between gap-3 pt-1">
          <button type="button" data-preview class="bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold py-1.5 px-3 rounded transition">Jetzt starten</button>
          <span data-status class="text-xs text-gray-500"></span>
        </div>
      </div>`;

    container.querySelector('[data-preview]').addEventListener('click', () => {
      window.closeSettings?.();
      window.journlScreensaver?.open();
    });
    container.querySelector('#screensaver-dim').addEventListener('input', (event) => {
      container.querySelector('[data-dim-value]').textContent = `${event.target.value}%`;
    });
    container.querySelector('#screensaver-visual-strength').addEventListener('input', (event) => {
      container.querySelector('[data-visual-strength]').textContent = `${event.target.value}%`;
    });
    container.querySelector('#screensaver-visual-enabled').addEventListener('change', (event) => {
      container.querySelector('[data-visual-options]').classList.toggle('hidden', !event.target.checked);
    });
    // „Keine Bilder“ schliesst die echten Quellen aus und umgekehrt.
    container.querySelector('[data-sources]').addEventListener('change', (event) => {
      const box = event.target.closest('[data-source]');
      if (!box?.checked) return;
      container.querySelectorAll('[data-source]').forEach((other) => {
        if (other !== box && (box.dataset.source === 'none' || other.dataset.source === 'none')) other.checked = false;
      });
    }, true);
    // Am Regler zieht man durch viele Zwischenwerte: die Zahl folgt sofort,
    // gespeichert wird erst beim Loslassen ueber das change-Ereignis.
    container.querySelector('#screensaver-fade').addEventListener('input', fadeNote);
    container.querySelector('#screensaver-transition').addEventListener('change', fadeNote);
    container.querySelector('#screensaver-interval').addEventListener('change', fadeNote);
    fadeNote();
  }

  function collect() {
    return {
      interval: Number(container.querySelector('#screensaver-interval').value),
      sources: Array.from(container.querySelectorAll('[data-source]:checked')).map(box => box.dataset.source),
      info: container.querySelector('#screensaver-info').value,
      transition: container.querySelector('#screensaver-transition').value,
      fade_seconds: Number(container.querySelector('#screensaver-fade').value),
      dim: Number(container.querySelector('#screensaver-dim').value),
      mode: container.querySelector('#screensaver-mode').value,
      order: container.querySelector('#screensaver-order').value,
      details: container.querySelector('#screensaver-details').checked,
      visual: {
        enabled: container.querySelector('#screensaver-visual-enabled').checked,
        form: container.querySelector('#screensaver-visual-form').value,
        blend: container.querySelector('#screensaver-visual-blend').value,
        strength: Number(container.querySelector('#screensaver-visual-strength').value),
        quality: container.querySelector('#screensaver-visual-quality').value,
        reaction: container.querySelector('#screensaver-visual-reaction').value,
      },
    };
  }

  async function save() {
    const status = container.querySelector('[data-status]');
    if (status) status.textContent = 'Wird gespeichert…';
    try {
      const response = await window.apiFetch('/api/screensaver/settings', {
        method: 'POST', body: JSON.stringify(collect())
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Nicht gespeichert.');
      settings = data.settings;
      // Ohne Quelle faellt der Server auf die Voreinstellung zurueck; die
      // Haekchen muessen das zeigen, sonst steht die Maske falsch da.
      container.querySelectorAll('[data-source]').forEach(box => {
        box.checked = settings.sources.includes(box.dataset.source);
      });
      // Der Server rastert die Uebergangsdauer auf Zehntel - der Regler zeigt
      // danach genau das, was gespeichert wurde.
      container.querySelector('#screensaver-fade').value = settings.fade_seconds;
      fadeNote();
      if (status) status.textContent = 'Gespeichert';
      document.dispatchEvent(new CustomEvent('journl:screensaver-settings', { detail: settings }));
    } catch (error) {
      if (status) status.textContent = error.message || 'nicht gespeichert';
    }
  }

  // Einmal am Container, nicht je render() - sonst speicherte jede Aenderung
  // so oft, wie die Maske schon neu gezeichnet wurde.
  container.addEventListener('change', save);

  async function load() {
    try {
      const response = await window.apiFetch('/api/screensaver/settings', { cache: 'no-store' });
      render(await response.json());
      loaded = true;
    } catch (_) {
      container.innerHTML = '<p class="text-xs text-gray-500">Einstellungen nicht erreichbar.</p>';
    }
  }

  // Ordner angelegt, umbenannt, befuellt: Zaehler und Namen neu holen. Die
  // Auswahl zieht der Server beim Umbenennen und Loeschen selbst mit.
  document.addEventListener('journl:screensaver-folders-changed', () => {
    if (loaded) load();
  });
  // Das Kuerzel im Schoner hat das Klangbild umgeschaltet: die Maske zieht
  // nach, sonst ueberschriebe die naechste Aenderung hier den neuen Stand.
  document.addEventListener('journl:screensaver-settings', (event) => {
    if (!loaded || !lastData || !event.detail || event.detail === settings) return;
    render({ ...lastData, settings: event.detail });
  });
  // Ein geladenes Preset hat die Einstellung ersetzt - die Maske zeigt sie.
  document.addEventListener('journl:screensaver-preset-applied', () => {
    if (loaded) load();
  });

  // Die Quellenliste scannt Ordner - sie wird erst geholt, wenn die Sektion
  // wirklich sichtbar wird, und danach nur auf Wunsch neu.
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'screensaver') return;
    if (!loaded) load();
  });
}());
