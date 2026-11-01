/* Private Ordner des Bildschirmschoners im Einstellungen-Modal.

   Jeder Ordner liegt offen unter `<user>/screensaver/ordner/<name>/` - was
   per Sync dort landet, ist genauso Quelle wie ein Upload von hier. Ein
   Ordner traegt Bilder und Musik nebeneinander. Dieses Modul legt Ordner an,
   benennt sie um, loescht sie und laedt Dateien hoch; die Auswahl als Bild-
   bzw. Musikquelle bleibt bei screensaver-settings.js und
   screensaver-player-settings.js. Nach jeder
   Aenderung geht `journl:screensaver-folders-changed` raus, damit die
   Quellenliste dort ihre Zaehler und Namen neu holt.

   Hochgeladen wird eine Datei je Anfrage: so gibt es einen echten
   Fortschritt, und der Server kann zu grosse Dateien abweisen, bevor er sie
   annimmt. */
(function () {
  const container = document.getElementById('settings-screensaver-folders');
  if (!container) return;

  const PREVIEW_LIMIT = 48;
  const INPUT = 'min-w-0 flex-1 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500';
  const ICON_BUTTON = 'h-8 w-8 shrink-0 inline-flex items-center justify-center rounded-md text-gray-400 hover:text-white hover:bg-gray-700 transition';
  const ICONS = {
    folder: '<path d="M3 7.5A1.5 1.5 0 0 1 4.5 6h4l2 2h9A1.5 1.5 0 0 1 21 9.5v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    note: '<path d="M9 17.5V6.2l10-2v11.3"/><circle cx="6.6" cy="17.6" r="2.4"/><circle cx="16.6" cy="15.6" r="2.4"/>',
  };
  const AUDIO = /\.(mp3|m4a|aac|ogg|oga|opus|flac|wav)$/i;
  const ACCEPT = 'image/jpeg,image/png,image/webp,image/avif,image/gif,audio/*,.mp3,.m4a,.aac,.ogg,.oga,.opus,.flac,.wav';

  let folders = [];
  let maxBytes = 0;
  let maxAudioBytes = 0;
  let open = null;      // Name des aufgeklappten Ordners
  let files = [];
  let showAll = false;
  let loaded = false;

  function icon(name, size = 'h-4 w-4') {
    return `<svg viewBox="0 0 24 24" class="${size} pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  function size(bytes) {
    if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 ** 3).toFixed(1).replace('.', ',')} GB`;
    if (bytes >= 1024 * 1024) return `${(bytes / 1024 ** 2).toFixed(1).replace('.', ',')} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }

  function folderUrl(name, suffix = '') {
    return `/api/screensaver/folders/${encodeURIComponent(name)}${suffix}`;
  }

  function fileUrl(folder, relative) {
    return folderUrl(folder, `/files/${relative.split('/').map(encodeURIComponent).join('/')}`);
  }

  function setStatus(text, error = false) {
    const status = container.querySelector('[data-folder-status]');
    if (!status) return;
    status.textContent = text || '';
    status.classList.toggle('text-red-400', error);
    status.classList.toggle('text-gray-500', !error);
  }

  function changed() {
    document.dispatchEvent(new CustomEvent('journl:screensaver-folders-changed'));
  }

  async function request(url, options = {}) {
    const response = await window.apiFetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Aktion fehlgeschlagen.');
    return data;
  }

  function counts(folder) {
    const parts = [];
    if (folder.count || !folder.audio) parts.push(`${folder.count} ${folder.count === 1 ? 'Bild' : 'Bilder'}`);
    if (folder.audio) parts.push(`${folder.audio} Titel`);
    return parts.join(' · ');
  }

  function audioList(tracks) {
    if (!tracks.length) return '';
    return `<ul class="mt-2 divide-y divide-gray-700/50 rounded-md ring-1 ring-gray-700/60">${tracks.map(file => `
      <li class="group flex items-center gap-2 px-2 py-1.5 text-xs text-gray-300">
        <span class="text-blue-300/70">${icon('note', 'h-3.5 w-3.5')}</span>
        <span class="min-w-0 flex-1 truncate" title="${escape(file.name)}">${escape(file.name)}</span>
        <span class="shrink-0 text-gray-500">${size(file.bytes)}</span>
        <button type="button" data-delete-file="${escape(file.name)}" aria-label="${escape(file.name)} löschen" title="Löschen"
                class="h-6 w-6 shrink-0 inline-flex items-center justify-center rounded-full text-gray-500 hover:bg-red-600 hover:text-white transition">${icon('close', 'h-3.5 w-3.5')}</button>
      </li>`).join('')}</ul>`;
  }

  function fileGrid() {
    if (!files.length) {
      return '<p class="text-xs text-gray-500">Noch keine Dateien in diesem Ordner.</p>';
    }
    const tracks = files.filter(file => file.kind === 'audio');
    const images = files.filter(file => file.kind !== 'audio');
    if (!images.length) return audioList(tracks);
    const visible = showAll ? images : images.slice(0, PREVIEW_LIMIT);
    const tiles = visible.map(file => `
      <figure class="group relative aspect-square overflow-hidden rounded-md bg-gray-800 ring-1 ring-gray-700/60" title="${escape(file.name)} · ${size(file.bytes)}">
        <img src="${fileUrl(open, file.name)}" alt="" loading="lazy" decoding="async" class="h-full w-full object-cover transition duration-300 group-hover:scale-105" />
        <button type="button" data-delete-file="${escape(file.name)}" aria-label="${escape(file.name)} löschen" title="Löschen"
                class="absolute right-1 top-1 h-6 w-6 inline-flex items-center justify-center rounded-full bg-black/60 text-gray-200 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:bg-red-600 transition">${icon('close', 'h-3.5 w-3.5')}</button>
      </figure>`).join('');
    const more = !showAll && images.length > PREVIEW_LIMIT
      ? `<button type="button" data-show-all class="mt-2 text-xs text-blue-400 hover:text-blue-300">Alle ${images.length} Bilder anzeigen</button>`
      : '';
    return `<div class="grid grid-cols-4 sm:grid-cols-6 gap-1.5">${tiles}</div>${more}${audioList(tracks)}`;
  }

  function folderRow(folder) {
    const expanded = folder.name === open;
    const name = escape(folder.name);
    return `
      <li class="rounded-lg border ${expanded ? 'border-blue-500/50 bg-gray-800/40' : 'border-gray-700'} transition" data-folder="${name}">
        <div class="flex items-center gap-2 px-2 py-1.5">
          <button type="button" data-toggle class="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-gray-800/60 transition" aria-expanded="${expanded}">
            <span class="text-gray-500 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}">${icon('chevron')}</span>
            <span class="text-blue-300/80">${icon('folder')}</span>
            <span class="min-w-0">
              <span data-name class="block truncate text-sm font-medium text-gray-200">${name}</span>
              <span class="block text-xs text-gray-500">${counts(folder)}${folder.bytes ? ` · ${size(folder.bytes)}` : ''}</span>
            </span>
          </button>
          <button type="button" data-rename class="${ICON_BUTTON}" aria-label="Umbenennen" title="Umbenennen">${icon('pencil')}</button>
          <button type="button" data-delete class="${ICON_BUTTON} hover:!text-red-400" aria-label="Ordner löschen" title="Ordner löschen">${icon('trash')}</button>
        </div>
        ${expanded ? `
        <div class="space-y-2 border-t border-gray-700/60 px-3 py-3">
          <label data-dropzone class="flex cursor-pointer flex-col items-center gap-1 rounded-lg border border-dashed border-gray-600 px-3 py-4 text-center text-xs text-gray-400 hover:border-blue-500/70 hover:text-gray-200 transition">
            <span class="text-gray-400">${icon('upload', 'h-5 w-5')}</span>
            <span>Bilder oder Musik hierher ziehen oder <span class="text-blue-400">auswählen</span></span>
            <span class="text-gray-500">JPG, PNG, WebP, AVIF, GIF bis ${size(maxBytes)} · MP3, M4A, OGG/OPUS, FLAC, WAV bis ${size(maxAudioBytes)} · im Original</span>
            <input type="file" data-upload multiple accept="${ACCEPT}" class="sr-only" />
          </label>
          <div data-files>${fileGrid()}</div>
        </div>` : ''}
      </li>`;
  }

  function render() {
    container.innerHTML = `
      <div class="mt-4 border-t border-gray-800 pt-3">
        <span class="block text-xs text-gray-400 mb-1">Meine Ordner</span>
        <p class="text-xs text-gray-500 mb-2">Nur für dich sichtbar, für Bilder und Musik. Was du per Sync unter <code class="text-gray-400">DATA_DIR/&lt;dein Konto&gt;/screensaver/ordner/</code> ablegst, erscheint hier automatisch.</p>
        <form data-create class="flex gap-2 mb-2">
          <input data-new-name type="text" maxlength="80" placeholder="Neuer Ordner, z. B. Urlaub 2026" class="${INPUT}" />
          <button type="submit" class="shrink-0 bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold py-1.5 px-3 rounded transition">Anlegen</button>
        </form>
        <ul class="space-y-2">${folders.map(folderRow).join('') || '<li class="text-xs text-gray-500">Noch keine eigenen Ordner.</li>'}</ul>
        <p data-folder-status class="mt-1.5 min-h-4 text-xs text-gray-500"></p>
      </div>`;
  }

  async function loadFolders() {
    const data = await request('/api/screensaver/folders', { cache: 'no-store' });
    folders = data.folders || [];
    maxBytes = data.max_upload_bytes || 0;
    maxAudioBytes = data.max_audio_bytes || maxBytes;
    if (open && !folders.some(folder => folder.name === open)) open = null;
  }

  async function loadFiles() {
    if (!open) { files = []; return; }
    const data = await request(folderUrl(open), { cache: 'no-store' });
    files = data.files || [];
  }

  async function refresh(status) {
    try {
      await loadFolders();
      await loadFiles();
      render();
      if (status) setStatus(status);
    } catch (error) {
      render();
      setStatus(error.message, true);
    }
  }

  async function upload(list) {
    const chosen = Array.from(list || []);
    if (!chosen.length || !open) return;
    const folder = open;
    const problems = [];
    let done = 0;
    for (const file of chosen) {
      setStatus(`Lade ${done + 1} von ${chosen.length} hoch…`);
      const limit = AUDIO.test(file.name) ? maxAudioBytes : maxBytes;
      if (limit && file.size > limit) {
        problems.push(`${file.name}: größer als ${size(limit)}`);
        continue;
      }
      const body = new FormData();
      body.append('files', file);
      try {
        await request(folderUrl(folder, '/files'), { method: 'POST', body });
        done += 1;
      } catch (error) {
        problems.push(`${file.name}: ${error.message}`);
      }
    }
    await refresh();
    changed();
    setStatus(problems.length
      ? `${done} hochgeladen, ${problems.length} abgelehnt – ${problems.join('; ')}`
      : `${done} ${done === 1 ? 'Datei' : 'Dateien'} hochgeladen.`, problems.length > 0);
  }

  function startRename(row) {
    // Ein Eingabefeld darf nicht im Aufklapp-Knopf stecken - es ersetzt ihn.
    const toggle = row.querySelector('[data-toggle]');
    const current = row.dataset.folder;
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 80;
    input.value = current;
    input.setAttribute('aria-label', 'Neuer Ordnername');
    input.className = 'min-w-0 flex-1 rounded-md bg-gray-900 border border-blue-500/60 px-2 py-1.5 text-sm text-gray-100 focus:outline-none';
    toggle.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const name = input.value.trim();
      if (!save || !name || name === current) { render(); return; }
      try {
        const data = await request(folderUrl(current), { method: 'PATCH', body: JSON.stringify({ name }) });
        if (open === current) open = data.folder.name;
        await refresh('Umbenannt.');
        changed();
      } catch (error) {
        render();
        setStatus(error.message, true);
      }
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      // Esc bricht nur das Umbenennen ab, nicht das ganze Einstellungen-Modal.
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
    input.addEventListener('click', event => event.stopPropagation());
  }

  container.addEventListener('submit', async (event) => {
    if (!event.target.matches('[data-create]')) return;
    event.preventDefault();
    const field = container.querySelector('[data-new-name]');
    const name = field.value.trim();
    if (!name) return;
    try {
      const data = await request('/api/screensaver/folders', { method: 'POST', body: JSON.stringify({ name }) });
      open = data.folder.name;
      await refresh(`„${data.folder.name}“ angelegt – jetzt Bilder oder Musik hinzufügen.`);
      changed();
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  container.addEventListener('click', async (event) => {
    const row = event.target.closest('[data-folder]');
    if (event.target.closest('[data-show-all]')) {
      showAll = true;
      container.querySelector('[data-files]').innerHTML = fileGrid();
      return;
    }
    if (!row) return;
    const name = row.dataset.folder;
    if (event.target.closest('[data-toggle]')) {
      open = open === name ? null : name;
      showAll = false;
      files = [];
      await refresh();
    } else if (event.target.closest('[data-rename]')) {
      startRename(row);
    } else if (event.target.closest('[data-delete]')) {
      const folder = folders.find(entry => entry.name === name);
      const count = (folder?.count || 0) + (folder?.audio || 0);
      const question = count
        ? `Ordner „${name}“ mit ${count} ${count === 1 ? 'Datei' : 'Dateien'} (${counts(folder)}) endgültig löschen?`
        : `Leeren Ordner „${name}“ löschen?`;
      if (!window.confirm(question)) return;
      try {
        await request(folderUrl(name), { method: 'DELETE' });
        if (open === name) open = null;
        await refresh('Ordner gelöscht.');
        changed();
      } catch (error) {
        setStatus(error.message, true);
      }
    } else if (event.target.closest('[data-delete-file]')) {
      const relative = event.target.closest('[data-delete-file]').dataset.deleteFile;
      if (!window.confirm(`„${relative}“ löschen?`)) return;
      try {
        await request(fileUrl(name, relative), { method: 'DELETE' });
        await refresh('Datei gelöscht.');
        changed();
      } catch (error) {
        setStatus(error.message, true);
      }
    }
  });

  container.addEventListener('change', (event) => {
    if (!event.target.matches('[data-upload]')) return;
    upload(event.target.files);
    event.target.value = '';
  });

  // Ziehen und Ablegen: die Zone leuchtet, solange etwas darueber schwebt.
  container.addEventListener('dragover', (event) => {
    const zone = event.target.closest('[data-dropzone]');
    if (!zone) return;
    event.preventDefault();
    zone.classList.add('border-blue-500', 'bg-blue-500/10');
  });
  container.addEventListener('dragleave', (event) => {
    const zone = event.target.closest('[data-dropzone]');
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove('border-blue-500', 'bg-blue-500/10');
  });
  container.addEventListener('drop', (event) => {
    const zone = event.target.closest('[data-dropzone]');
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove('border-blue-500', 'bg-blue-500/10');
    upload(event.dataTransfer?.files);
  });

  // Wie die Quellenliste: erst laden, wenn die Sektion sichtbar wird.
  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group !== 'screensaver' || loaded) return;
    loaded = true;
    refresh();
  });
}());
