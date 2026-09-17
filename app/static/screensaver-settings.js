/* Einstellungen des Bildschirmschoners im Einstellungen-Modal.

   Die Felder entstehen hier statt im Template: die Quellenliste kommt ohnehin
   erst vom Server (Themen, eigene Ordner unter DATA_DIR/screensaver/, eigene
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
  const MODES = [['screensaver', 'Diashow (jede Taste beendet)'], ['focus', 'Kulisse (Schreibfeld über den Bildern)']];

  const LABEL = 'block text-xs text-gray-400 mb-1';
  const SELECT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500';

  let settings = null;
  let loaded = false;

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

  function render(data) {
    settings = data.settings;
    const chosen = new Set(settings.sources || []);
    const sources = (data.sources || []).map(source => `
      <label class="flex items-center justify-between gap-3 rounded-lg border border-gray-700 px-3 py-2 text-sm text-gray-200">
        <span>
          <span class="block font-medium">${source.label}</span>
          <span class="block text-xs text-gray-500">${source.kind === 'theme' ? 'Wikimedia Commons'
            : source.kind === 'journal' ? `Eigene Medien · ${source.count} Bilder`
            : `Eigener Ordner · ${source.count} Bilder`}</span>
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
          <p class="text-xs text-gray-500 mt-1.5">Eigene Sammlungen: Unterordner unter <code class="text-gray-400">DATA_DIR/screensaver/</code> anlegen und Bilder hineinlegen.</p>
        </div>
        <div>
          <label class="${LABEL}" for="screensaver-info">Info unten links</label>
          <select id="screensaver-info" class="${SELECT}">${options(INFO, settings.info)}</select>
          <p class="text-xs text-gray-500 mt-1.5">Bei „Aus“ bleibt für Bilder unter freier Lizenz ein sehr leiser Namensnachweis stehen — das verlangt die Lizenz.</p>
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
    // Am Regler zieht man durch viele Zwischenwerte: die Zahl folgt sofort,
    // gespeichert wird erst beim Loslassen ueber das change-Ereignis.
    container.querySelector('#screensaver-fade').addEventListener('input', fadeNote);
    container.querySelector('#screensaver-transition').addEventListener('change', fadeNote);
    container.querySelector('#screensaver-interval').addEventListener('change', fadeNote);
    container.addEventListener('change', save);
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

  async function load() {
    try {
      const response = await window.apiFetch('/api/screensaver/settings', { cache: 'no-store' });
      render(await response.json());
      loaded = true;
    } catch (_) {
      container.innerHTML = '<p class="text-xs text-gray-500">Einstellungen nicht erreichbar.</p>';
    }
  }

  // Die Quellenliste scannt Ordner - sie wird erst geholt, wenn die Sektion
  // wirklich sichtbar wird, und danach nur auf Wunsch neu.
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'screensaver') return;
    if (!loaded) load();
  });
}());
