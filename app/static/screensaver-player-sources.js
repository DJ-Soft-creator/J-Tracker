/* Podcast-Abos und Radiosender in den Player-Einstellungen.

   screensaver-player-settings.js baut die Maske und ruft hier `markup(settings)`
   fuer den Bereich der gewaehlten Quelle. Die Auswahl (welcher Podcast, welcher
   Sender) sind Radio-Buttons `player-podcast`/`player-station` - die liest und
   speichert die Maske selbst. Dieses Modul besitzt nur Abonnieren, Erneuern,
   Abbestellen und eigene Sender; Eingabefelder tragen `data-no-save`, damit
   ein Tippen dort nicht als Einstellungsaenderung zaehlt.

   Nach jeder Aenderung geht `journl:player-sources-changed` raus: der Player
   holt die Liste neu, die Maske waehlt mit `pick` ein frisches erstes Abo
   gleich aus. */
window.journlScreensaverPlayerSources = (function () {
  const BASE = '/api/screensaver/player';
  const LABEL = 'block text-xs text-gray-400 mb-1';
  const INPUT = 'w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500';
  const ADD = 'shrink-0 rounded-lg bg-blue-600/80 px-3 py-2 text-sm text-white hover:bg-blue-500 transition disabled:opacity-40';
  const ICON_BUTTON = 'h-8 w-8 shrink-0 inline-flex items-center justify-center rounded-md text-gray-500 hover:text-white hover:bg-gray-700 transition';
  const ICONS = {
    refresh: '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v3.5H16"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    mic: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>',
    radio: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
  };

  const data = { feeds: null, stations: null, busy: false, status: '', error: false, draft: {} };
  let settings = null;
  let loading = null;

  function icon(name, size = 'h-4 w-4') {
    return `<svg viewBox="0 0 24 24" class="${size} pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function cover(url, fallback) {
    return `<span class="relative inline-flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-md bg-gray-800 text-gray-500">
      ${icon(fallback)}${url ? `<img src="${escape(url)}" alt="" class="absolute inset-0 h-full w-full object-cover" onerror="this.remove()" />` : ''}</span>`;
  }

  async function request(url, options = {}) {
    const response = await window.apiFetch(url, { cache: 'no-store', ...options });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || 'Aktion fehlgeschlagen.');
    return body;
  }

  function status() {
    return `<p data-sources-status class="min-h-4 text-xs ${data.error ? 'text-red-400' : 'text-gray-500'}">${escape(data.status)}</p>`;
  }

  // ─── Podcasts ─────────────────────────────────────────────────────

  function podcastMarkup() {
    if (data.feeds === null) return '<p class="text-xs text-gray-500">Lade Podcasts…</p>';
    const rows = data.feeds.map(feed => `
      <div class="flex items-center gap-2 rounded-lg border ${settings.podcast === feed.id ? 'border-blue-500/50 bg-blue-500/5' : 'border-gray-700'} px-2 py-1.5">
        <label class="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5">
          <input type="radio" name="player-podcast" value="${escape(feed.id)}" class="h-4 w-4 accent-green-500"${settings.podcast === feed.id ? ' checked' : ''} />
          ${cover(feed.cover, 'mic')}
          <span class="min-w-0">
            <span class="block truncate text-sm text-gray-200">${escape(feed.title || feed.url)}</span>
            <span class="block truncate text-xs text-gray-500">${[feed.author, `${feed.count} Episoden`, feed.unheard ? `${feed.unheard} ungehört` : 'alles gehört'].filter(Boolean).map(escape).join(' · ')}</span>
          </span>
        </label>
        <button type="button" data-podcast-refresh="${escape(feed.id)}" class="${ICON_BUTTON}" title="Jetzt erneuern" aria-label="Jetzt erneuern">${icon('refresh')}</button>
        <button type="button" data-podcast-remove="${escape(feed.id)}" class="${ICON_BUTTON} hover:!text-red-300" title="Abo beenden" aria-label="Abo beenden">${icon('trash')}</button>
      </div>`).join('');
    return `
      <div class="space-y-2">
        <span class="${LABEL}">Podcasts</span>
        ${rows || '<p class="text-xs text-gray-500">Noch keine Abos. Füge unten die RSS-Adresse eines Podcasts ein.</p>'}
        <div class="flex gap-2 pt-1">
          <input data-no-save data-podcast-url type="url" inputmode="url" placeholder="RSS-Adresse, z. B. https://…/feed.xml" value="${escape(data.draft.podcast || '')}" class="${INPUT}" />
          <button type="button" data-podcast-add class="${ADD}"${data.busy ? ' disabled' : ''}>Abonnieren</button>
        </div>
        <p class="text-xs text-gray-500">Der Server liest den Feed und reicht die Episoden durch, dein Gerät spricht nie mit dem Podcast-Host. Tempo und Position merkt sich der Player je Podcast bzw. Episode.</p>
        ${status()}
      </div>`;
  }

  // ─── Radio ────────────────────────────────────────────────────────

  function radioMarkup() {
    if (data.stations === null) return '<p class="text-xs text-gray-500">Lade Sender…</p>';
    const chosen = settings.station || data.stations[0]?.id;
    const rows = data.stations.map(station => `
      <div class="flex items-center gap-2 rounded-lg border ${chosen === station.id ? 'border-blue-500/50 bg-blue-500/5' : 'border-gray-700'} px-2 py-1.5">
        <label class="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5">
          <input type="radio" name="player-station" value="${escape(station.id)}" class="h-4 w-4 accent-green-500"${chosen === station.id ? ' checked' : ''} />
          ${cover(station.cover, 'radio')}
          <span class="min-w-0">
            <span class="block truncate text-sm text-gray-200">${escape(station.label)}</span>
            <span class="block truncate text-xs text-gray-500">${escape([station.genre, station.provider].filter(Boolean).join(' · '))}</span>
          </span>
        </label>
        ${station.custom ? `<button type="button" data-station-remove="${escape(station.id)}" class="${ICON_BUTTON} hover:!text-red-300" title="Sender löschen" aria-label="Sender löschen">${icon('trash')}</button>` : ''}
      </div>`).join('');
    return `
      <div class="space-y-2">
        <span class="${LABEL}">Sender</span>
        <div class="space-y-1.5">${rows}</div>
        <details class="pt-1"${data.draft.station ? ' open' : ''}>
          <summary class="cursor-pointer list-none text-xs text-blue-400 hover:text-blue-300">Eigenen Sender hinzufügen</summary>
          <div class="mt-2 space-y-2">
            <input data-no-save data-station-label type="text" maxlength="60" placeholder="Name" value="${escape(data.draft.station?.label || '')}" class="${INPUT}" />
            <input data-no-save data-station-url type="url" inputmode="url" placeholder="Stream-Adresse (MP3/AAC/Ogg, auch .m3u/.pls)" value="${escape(data.draft.station?.url || '')}" class="${INPUT}" />
            <input data-no-save data-station-logo type="url" inputmode="url" placeholder="Logo-Adresse (optional)" value="${escape(data.draft.station?.logo || '')}" class="${INPUT}" />
            <button type="button" data-station-add class="${ADD}"${data.busy ? ' disabled' : ''}>Sender anlegen</button>
          </div>
        </details>
        <p class="text-xs text-gray-500">Die Wiedergabe läuft über den Server. HLS-Streams (.m3u8) gehen nicht.</p>
        ${status()}
      </div>`;
  }

  // ─── Laden und Aktionen ───────────────────────────────────────────

  function markup(current) {
    settings = current;
    if (current.audio === 'podcast') { if (data.feeds === null) load(); return podcastMarkup(); }
    if (current.audio === 'radio') { if (data.stations === null) load(); return radioMarkup(); }
    return '';
  }

  function box() {
    return document.querySelector('#settings-screensaver-player [data-audio-sources]');
  }

  function refresh() {
    const target = box();
    if (target && settings) target.innerHTML = markup(settings);
  }

  function load() {
    if (loading) return loading;
    loading = Promise.all([
      request(`${BASE}/podcasts`).then((body) => { data.feeds = body.feeds || []; }),
      request(`${BASE}/radio`).then((body) => { data.stations = body.stations || []; }),
    ]).catch((error) => {
      data.feeds = data.feeds || [];
      data.stations = data.stations || [];
      setStatus(error.message, true);
    }).finally(() => { loading = null; refresh(); });
    return loading;
  }

  function setStatus(text, error = false) {
    data.status = text || '';
    data.error = error;
    const line = box()?.querySelector('[data-sources-status]');
    if (line) {
      line.textContent = data.status;
      line.classList.toggle('text-red-400', error);
      line.classList.toggle('text-gray-500', !error);
    }
  }

  function changed(pick) {
    document.dispatchEvent(new CustomEvent('journl:player-sources-changed', { detail: { pick } }));
  }

  async function busy(label, work) {
    data.busy = true;
    setStatus(label);
    refresh();
    try {
      await work();
    } catch (error) {
      data.busy = false;
      setStatus(error.message || 'Aktion fehlgeschlagen.', true);
      refresh();
      return;
    }
    data.busy = false;
    refresh();
  }

  async function subscribe() {
    const field = box()?.querySelector('[data-podcast-url]');
    const url = field?.value.trim();
    if (!url) { setStatus('Bitte die RSS-Adresse einfügen.', true); return; }
    data.draft.podcast = url;
    await busy('Feed wird gelesen…', async () => {
      const body = await request(`${BASE}/podcasts`, { method: 'POST', body: JSON.stringify({ url }) });
      delete data.draft.podcast;
      data.feeds = data.feeds.concat(body.feed);
      setStatus(`„${body.feed.title}“ abonniert`);
      changed(settings.podcast ? null : { podcast: body.feed.id });
    });
  }

  async function addStation() {
    const target = box();
    const value = name => target?.querySelector(`[data-station-${name}]`)?.value.trim() || '';
    // Vor busy() auslesen: das zeichnet die Maske neu und leert die Felder.
    // Der Entwurf haelt sie, bis der Sender wirklich angelegt ist.
    const station = { label: value('label'), url: value('url'), logo: value('logo') };
    if (!station.url) { setStatus('Bitte die Stream-Adresse einfügen.', true); return; }
    data.draft.station = station;
    await busy('Stream wird geprüft…', async () => {
      const body = await request(`${BASE}/radio`, { method: 'POST', body: JSON.stringify(station) });
      delete data.draft.station;
      data.stations = data.stations.concat(body.station);
      setStatus(`„${body.station.label}“ angelegt`);
      changed({ station: body.station.id });
    });
  }

  document.addEventListener('click', async (event) => {
    if (!event.target.closest('#settings-screensaver-player [data-audio-sources]')) return;
    if (event.target.closest('[data-podcast-add]')) { subscribe(); return; }
    if (event.target.closest('[data-station-add]')) { addStation(); return; }
    const refreshId = event.target.closest('[data-podcast-refresh]')?.dataset.podcastRefresh;
    if (refreshId) {
      await busy('Wird erneuert…', async () => {
        const body = await request(`${BASE}/podcasts/${refreshId}/refresh`, { method: 'POST' });
        data.feeds = data.feeds.map(feed => (feed.id === refreshId ? body.feed : feed));
        setStatus('Erneuert');
        changed();
      });
      return;
    }
    const removeId = event.target.closest('[data-podcast-remove]')?.dataset.podcastRemove;
    if (removeId) {
      const feed = data.feeds.find(entry => entry.id === removeId);
      if (!window.confirm(`Abo „${feed?.title || 'Podcast'}“ beenden?`)) return;
      await busy('', async () => {
        await request(`${BASE}/podcasts/${removeId}`, { method: 'DELETE' });
        data.feeds = data.feeds.filter(entry => entry.id !== removeId);
        setStatus('Abo beendet');
        changed(settings.podcast === removeId ? { podcast: data.feeds[0]?.id || '' } : null);
      });
      return;
    }
    const stationId = event.target.closest('[data-station-remove]')?.dataset.stationRemove;
    if (stationId) {
      const station = data.stations.find(entry => entry.id === stationId);
      if (!window.confirm(`Sender „${station?.label || ''}“ löschen?`)) return;
      await busy('', async () => {
        await request(`${BASE}/radio/${stationId}`, { method: 'DELETE' });
        data.stations = data.stations.filter(entry => entry.id !== stationId);
        setStatus('Sender gelöscht');
        changed(settings.station === stationId ? { station: data.stations[0]?.id || '' } : null);
      });
    }
  });

  // Enter im Adressfeld heisst „Abonnieren“ bzw. „Anlegen“.
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || !event.target.matches?.('#settings-screensaver-player [data-no-save]')) return;
    event.preventDefault();
    if (event.target.matches('[data-podcast-url]')) subscribe();
    else addStation();
  });

  // Neu laden, sobald die Gruppe geoeffnet wird - der Zaehler „ungehoert“ wandert.
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'player') { data.feeds = null; data.stations = null; }
  });

  return { markup, load };
}());
