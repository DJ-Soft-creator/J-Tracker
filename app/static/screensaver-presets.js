/* Presets des Bildschirmschoners: ein komplettes Setup unter einem Namen.

   Ein Preset entsteht nur aus dem aktuellen Stand („Als Preset speichern“) und
   wird nur auf ausdruecklichen Klick ueberschrieben. Laden macht es zur
   aktuellen Einstellung; spaetere Aenderungen bleiben dort, ein Punkt am Namen
   zeigt solange „abweichend“. Speicherung und Abgleich besitzt der Server
   (`screensaver_presets.py`), dieses Modul nur die vier Einstiege:

   * Einstellungen (`#settings-screensaver-presets`): speichern, laden,
     ueberschreiben, umbenennen, sortieren, loeschen, Link kopieren.
   * Chips im Burger-Menue (`[data-screensaver-presets-menu]`): ein Klick
     startet den Schoner mit dem Preset.
   * Umschalter im laufenden Schoner: wechselt ohne Neustart - die neue
     Auswahl blendet ueber, wie bei einer geaenderten Einstellung.
   * Strg/Cmd+Alt+1 bis 9 (Reihenfolge der Liste) und `?screensaver=<id>`.
     Die Tasten sind umbelegbar; die Belegung liegt bei den Player-Kuerzeln
     (`preset_1` bis `preset_9`), weil dort schon die Aufnahme und die
     Doppelt-Pruefung leben.

   Nach dem Laden gehen zwei Ereignisse raus: `journl:screensaver-settings`
   (der offene Schoner uebernimmt den Stand) und
   `journl:screensaver-preset-applied` (die Einstellungsmasken laden neu). */
(function () {
  const ENDPOINT = '/api/screensaver/presets';
  const SHORTCUTS = 9;
  const ICON_BUTTON = 'h-8 w-8 shrink-0 inline-flex items-center justify-center rounded-md text-gray-400 hover:text-white hover:bg-gray-700 transition disabled:opacity-30 disabled:pointer-events-none';
  const ICONS = {
    play: '<path d="M8 5.5v13l10-6.5z"/>',
    capture: '<path d="M5 4h11l3 3v13H5z"/><path d="M8 4v5h7V4"/><path d="M8 20v-6h8v6"/>',
    pencil: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
    up: '<path d="m6 15 6-6 6 6"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.8"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5"/>',
    trash: '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    stack: '<path d="m12 4 8 4-8 4-8-4z"/><path d="m4 12 8 4 8-4"/><path d="m4 16 8 4 8-4"/>',
  };

  const state = { presets: [], active: '', modified: false, max: 12, loaded: false };
  let loading = null;
  const settingsBox = document.getElementById('settings-screensaver-presets');

  function icon(name, size = 'h-4 w-4') {
    return `<svg viewBox="0 0 24 24" class="${size} pointer-events-none" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  }

  function escape(value) {
    return String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  }

  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
  // Ohne geladene Player-Einstellungen gilt die Voreinstellung Strg+Alt+<Ziffer>.
  function combos() {
    const stored = window.journlScreensaverPlayer?.settings?.()?.shortcuts;
    return Array.from({ length: SHORTCUTS }, (_, index) =>
      (stored && typeof stored[`preset_${index + 1}`] === 'string' ? stored[`preset_${index + 1}`] : `Ctrl+Alt+Digit${index + 1}`));
  }

  function shortcut(index) {
    if (index >= SHORTCUTS) return '';
    const combo = combos()[index];
    const format = window.journlScreensaverPlayer?.formatShortcut;
    if (format) return format(combo);
    return combo ? `${mac ? '⌃⌥' : 'Strg+Alt+'}${index + 1}` : '';
  }

  async function request(url, options = {}) {
    const response = await window.apiFetch(url, { cache: 'no-store', ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Aktion fehlgeschlagen.');
    return data;
  }

  function take(data) {
    state.presets = data.presets || [];
    state.active = data.active || '';
    state.modified = Boolean(data.modified);
    state.max = data.max || state.max;
    state.loaded = true;
    renderAll();
  }

  function load() {
    loading = loading || request(ENDPOINT).then(take).catch(() => {}).finally(() => { loading = null; });
    return loading;
  }

  // Mehrere Aenderungen kurz hintereinander (Regler, Haekchen) fragen nur einmal nach.
  let refreshTimer = null;
  function refreshSoon() {
    if (!state.loaded) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(load, 400);
  }

  function label(id) {
    return state.presets.find(entry => entry.id === id)?.label || id;
  }

  // ─── Laden ────────────────────────────────────────────────────────

  async function apply(id, options = {}) {
    const screensaver = window.journlScreensaver;
    let data;
    try {
      data = await request(`${ENDPOINT}/${encodeURIComponent(id)}/apply`, { method: 'POST', body: '{}' });
    } catch (error) {
      if (screensaver?.isOpen()) screensaver.flashHint?.(error.message);
      else window.showToast?.(error.message, true);
      return;
    }
    take(data);
    document.dispatchEvent(new CustomEvent('journl:screensaver-settings', { detail: data.settings }));
    // Musik: neue Auswahl blendet per Crossfade ueber; gestartet wird sie mit
    // dem Schoner oder beim Wechsel darin. Ohne Geste zeigt die Pille den Hinweis.
    if (data.player) {
      document.dispatchEvent(new CustomEvent('journl:player-settings', {
        detail: { settings: data.player, origin: 'preset', preset: Boolean(data.player_from_preset),
          start: Boolean(options.start || screensaver?.isOpen()) },
      }));
    }
    document.dispatchEvent(new CustomEvent('journl:screensaver-preset-applied', { detail: data }));
    const missing = data.missing?.length ? ` · ${data.missing.length} Quelle${data.missing.length === 1 ? '' : 'n'} fehlt` : '';
    const message = `Preset „${label(id)}“${missing}`;
    if (screensaver?.isOpen()) {
      // Die Bilder blendet das Einstellungs-Ereignis ueber; der Modus wechselt
      // hier, damit etwa eine Fragen-Runde mit dem neuen Ablauf neu beginnt.
      screensaver.setMode?.(data.settings.mode);
      screensaver.flashHint?.(message);
    } else if (options.start) {
      await screensaver?.open('');
      screensaver?.flashHint?.(message);
    } else {
      setStatus(missing ? `${message} – fehlende Ordner ausgelassen.` : `${message} geladen.`);
    }
  }

  // ─── Einstellungen ────────────────────────────────────────────────

  function setStatus(text, error = false) {
    const status = settingsBox?.querySelector('[data-preset-status]');
    if (!status) return;
    status.textContent = text || '';
    status.classList.toggle('text-red-400', error);
    status.classList.toggle('text-gray-500', !error);
  }

  function presetRow(entry, index) {
    const active = entry.id === state.active;
    const note = active ? (state.modified ? 'aktiv · abweichend' : 'aktiv') : '';
    const keys = shortcut(index);
    return `
      <li data-preset="${escape(entry.id)}" class="flex items-center gap-1 rounded-lg border ${active ? 'border-blue-500/50 bg-blue-500/5' : 'border-gray-700'} px-2 py-1.5 transition">
        <button type="button" data-apply class="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left hover:bg-gray-800/60 transition" title="Laden">
          <span class="relative inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full ${active ? 'bg-blue-500/20 text-blue-200' : 'bg-gray-800 text-gray-400'} text-xs font-semibold tabular-nums">
            ${index < SHORTCUTS ? index + 1 : '·'}
            ${active && state.modified ? '<span class="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-amber-400 ring-2 ring-gray-900"></span>' : ''}
          </span>
          <span class="min-w-0">
            <span data-name class="block truncate text-sm font-medium text-gray-200">${escape(entry.label)}</span>
            <span class="block text-xs text-gray-500">${[note, keys].filter(Boolean).join(' · ') || '&nbsp;'}</span>
          </span>
        </button>
        <button type="button" data-capture class="${ICON_BUTTON}" title="Mit aktuellem Stand überschreiben" aria-label="Mit aktuellem Stand überschreiben">${icon('capture')}</button>
        <button type="button" data-rename class="${ICON_BUTTON}" title="Umbenennen" aria-label="Umbenennen">${icon('pencil')}</button>
        <button type="button" data-move="-1" class="${ICON_BUTTON}" title="Nach oben" aria-label="Nach oben"${index === 0 ? ' disabled' : ''}>${icon('up')}</button>
        <button type="button" data-move="1" class="${ICON_BUTTON}" title="Nach unten" aria-label="Nach unten"${index === state.presets.length - 1 ? ' disabled' : ''}>${icon('down')}</button>
        <button type="button" data-copy-link class="${ICON_BUTTON}" title="Link kopieren (Lesezeichen, Kurzbefehl)" aria-label="Link kopieren">${icon('link')}</button>
        <button type="button" data-delete class="${ICON_BUTTON} hover:!text-red-400" title="Löschen" aria-label="Löschen">${icon('trash')}</button>
      </li>`;
  }

  function renderSettings() {
    if (!settingsBox) return;
    const full = state.presets.length >= state.max;
    settingsBox.innerHTML = `
      <div class="mb-4 border-b border-gray-800 pb-3">
        <span class="block text-xs text-gray-400 mb-1">Presets</span>
        <p class="text-xs text-gray-500 mb-2">Ein Preset merkt sich das ganze Setup: Bildquellen, Takt, Übergang, Abdunkelung, Info, Startmodus, den Fragen-Ablauf und die Musik (Ordner, Zufall, Wiederholen, Crossfade, Lautstärke, Sichtbarkeit). Laden ändert das Preset nicht – überschrieben wird nur über das Speichern-Symbol.</p>
        <ul class="space-y-1.5 mb-2">${state.presets.map(presetRow).join('') || '<li class="text-xs text-gray-500">Noch keine Presets.</li>'}</ul>
        <form data-preset-create class="flex gap-2">
          <input data-preset-name type="text" maxlength="40" placeholder="${full ? `Höchstens ${state.max} Presets` : 'Aktuellen Stand speichern als …'}"${full ? ' disabled' : ''}
                 class="min-w-0 flex-1 rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50" />
          <button type="submit" class="shrink-0 bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold py-1.5 px-3 rounded transition disabled:opacity-50"${full ? ' disabled' : ''}>Speichern</button>
        </form>
        <p data-preset-status class="mt-1.5 min-h-4 text-xs text-gray-500"></p>
      </div>`;
  }

  function startRename(row) {
    const toggle = row.querySelector('[data-apply]');
    const id = row.dataset.preset;
    const input = document.createElement('input');
    input.type = 'text';
    input.maxLength = 40;
    input.value = label(id);
    input.setAttribute('aria-label', 'Neuer Name');
    input.className = 'min-w-0 flex-1 rounded-md bg-gray-900 border border-blue-500/60 px-2 py-1.5 text-sm text-gray-100 focus:outline-none';
    toggle.replaceWith(input);
    input.focus();
    input.select();
    let finished = false;
    const finish = async (save) => {
      if (finished) return;
      finished = true;
      const value = input.value.trim();
      if (!save || !value || value === label(id)) { renderSettings(); return; }
      try {
        take(await request(`${ENDPOINT}/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ label: value }) }));
        setStatus('Umbenannt.');
      } catch (error) {
        renderSettings();
        setStatus(error.message, true);
      }
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); finish(true); }
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false); }
    });
    input.addEventListener('blur', () => finish(true));
  }

  settingsBox?.addEventListener('submit', async (event) => {
    if (!event.target.matches('[data-preset-create]')) return;
    event.preventDefault();
    const name = settingsBox.querySelector('[data-preset-name]').value.trim();
    if (!name) return;
    try {
      take(await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ label: name }) }));
      setStatus(`„${name}“ gespeichert.`);
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  settingsBox?.addEventListener('click', async (event) => {
    const row = event.target.closest('[data-preset]');
    if (!row) return;
    const id = row.dataset.preset;
    const url = `${ENDPOINT}/${encodeURIComponent(id)}`;
    try {
      if (event.target.closest('[data-apply]')) {
        await apply(id);
      } else if (event.target.closest('[data-capture]')) {
        if (!window.confirm(`„${label(id)}“ mit dem aktuellen Stand überschreiben?`)) return;
        take(await request(url, { method: 'PATCH', body: JSON.stringify({ capture: true }) }));
        setStatus(`„${label(id)}“ überschrieben.`);
      } else if (event.target.closest('[data-rename]')) {
        startRename(row);
      } else if (event.target.closest('[data-move]')) {
        const ids = state.presets.map(entry => entry.id);
        const from = ids.indexOf(id);
        const to = from + Number(event.target.closest('[data-move]').dataset.move);
        if (to < 0 || to >= ids.length) return;
        [ids[from], ids[to]] = [ids[to], ids[from]];
        take(await request(`${ENDPOINT}/order`, { method: 'POST', body: JSON.stringify({ ids }) }));
      } else if (event.target.closest('[data-copy-link]')) {
        const link = `${window.location.origin}/?screensaver=${encodeURIComponent(id)}`;
        try { await navigator.clipboard.writeText(link); setStatus(`Link kopiert: ${link}`); }
        catch (_) { setStatus(link); }
      } else if (event.target.closest('[data-delete]')) {
        if (!window.confirm(`Preset „${label(id)}“ löschen? Die aktuelle Einstellung bleibt, wie sie ist.`)) return;
        take(await request(url, { method: 'DELETE' }));
        setStatus('Preset gelöscht.');
      }
    } catch (error) {
      setStatus(error.message, true);
    }
  });

  // ─── Chips im Burger-Menue ────────────────────────────────────────

  function renderMenus() {
    document.querySelectorAll('[data-screensaver-presets-menu]').forEach((menu) => {
      menu.classList.toggle('hidden', !state.presets.length);
      menu.innerHTML = state.presets.map((entry, index) => {
        const active = entry.id === state.active;
        return `<button type="button" data-screensaver-preset="${escape(entry.id)}" title="${escape(entry.label)}${shortcut(index) ? ` · ${shortcut(index)}` : ''}"
          class="max-w-full truncate rounded-full border px-2.5 py-0.5 text-xs transition ${active
            ? 'border-blue-500/60 bg-blue-500/10 text-blue-200 shadow-[0_0_10px_rgba(59,130,246,.25)]'
            : 'border-gray-700 text-gray-400 hover:border-blue-500/50 hover:text-white'}">${escape(entry.label)}</button>`;
      }).join('');
    });
  }

  document.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-screensaver-preset]');
    if (!chip) return;
    document.getElementById('burger-menu')?.classList.add('hidden');
    closeSwitcher();
    apply(chip.dataset.screensaverPreset, { start: true });
  });

  // ─── Umschalter im laufenden Schoner ──────────────────────────────

  let switcher = null;
  let popover = null;

  function closeSwitcher() {
    if (!popover) return;
    popover.classList.add('opacity-0', 'scale-95', 'pointer-events-none');
    switcher?.setAttribute('aria-expanded', 'false');
  }

  function renderSwitcher() {
    if (!switcher) return;
    switcher.classList.toggle('hidden', !state.presets.length);
    const name = switcher.querySelector('[data-switcher-label]');
    name.textContent = state.active ? label(state.active) : 'Preset';
    popover.innerHTML = state.presets.map((entry, index) => {
      const active = entry.id === state.active;
      return `<button type="button" data-screensaver-preset="${escape(entry.id)}"
        class="group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition ${active ? 'bg-white/10 text-white' : 'text-white/70 hover:bg-white/10 hover:text-white'}">
        <span class="h-1.5 w-1.5 shrink-0 rounded-full transition ${active ? 'bg-blue-300 shadow-[0_0_8px_2px_rgba(147,197,253,.7)]' : 'bg-white/20 group-hover:bg-white/50'}"></span>
        <span class="min-w-0 flex-1 truncate">${escape(entry.label)}</span>
        <span class="text-[11px] text-white/35 tabular-nums">${shortcut(index)}</span>
      </button>`;
    }).join('');
  }

  function mountSwitcher(root) {
    if (switcher || !root) return;
    const bar = root.querySelector('[data-settings]')?.parentElement;
    if (!bar) return;
    switcher = document.createElement('button');
    switcher.type = 'button';
    switcher.className = 'hidden items-center gap-1.5 rounded-lg px-2.5 py-2 text-xs font-medium text-white/25 hover:text-white/80 hover:bg-white/10 transition flex';
    switcher.setAttribute('aria-haspopup', 'true');
    switcher.setAttribute('aria-expanded', 'false');
    switcher.title = 'Preset wechseln';
    switcher.innerHTML = `${icon('stack', 'h-4 w-4')}<span data-switcher-label class="max-w-[10rem] truncate"></span>`;
    bar.insertBefore(switcher, bar.firstChild);

    popover = document.createElement('div');
    popover.dataset.screensaverKeep = '';
    popover.className = 'absolute right-4 top-16 z-10 w-64 origin-top-right rounded-xl bg-black/50 p-1.5 ring-1 ring-white/10 shadow-2xl backdrop-blur-md transition duration-200 ease-out opacity-0 scale-95 pointer-events-none';
    root.appendChild(popover);

    switcher.addEventListener('click', () => {
      const hidden = popover.classList.contains('opacity-0');
      if (hidden) {
        renderSwitcher();
        popover.classList.remove('opacity-0', 'scale-95', 'pointer-events-none');
        switcher.setAttribute('aria-expanded', 'true');
      } else {
        closeSwitcher();
      }
    });
    root.addEventListener('pointerdown', (event) => {
      if (!event.target.closest('[data-screensaver-keep]') && !switcher.contains(event.target)) closeSwitcher();
    });
    renderSwitcher();
  }

  document.addEventListener('journl:screensaver-opened', (event) => {
    mountSwitcher(event.detail?.root);
    closeSwitcher();
    if (!state.loaded) load();
  });

  function renderAll() {
    renderSettings();
    renderMenus();
    renderSwitcher();
  }

  // ─── Tasten ───────────────────────────────────────────────────────

  /* Strg+Alt meldet Windows auch fuer AltGr - und AltGr+7/8/9/0 tippt dort
     Klammern. Deshalb zaehlt die Kombination nur ohne AltGraph (das prueft
     `combo` des Players). */
  function handleKey(event) {
    const pressed = window.journlScreensaverPlayer?.combo?.(event);
    if (!pressed) return false;
    const index = combos().indexOf(pressed);
    if (index < 0) return false;
    event.preventDefault();
    const run = () => {
      const entry = state.presets[index];
      if (entry) apply(entry.id, { start: true });
      else if (window.journlScreensaver?.isOpen()) window.journlScreensaver.flashHint?.(`Kein Preset auf ${index + 1}`);
    };
    if (state.loaded) run();
    else load().then(run);
    return true;
  }

  // Im offenen Schoner fragt screensaver.js selbst nach, bevor eine Taste ihn weckt.
  document.addEventListener('keydown', (event) => {
    if (window.journlScreensaver?.isOpen()) return;
    handleKey(event);
  });

  // ─── Deep Link und Start ──────────────────────────────────────────

  const app = document.getElementById('app');
  const linked = new URLSearchParams(window.location.search).get('screensaver');
  let started = false;
  function whenVisible() {
    if (started || !app || app.classList.contains('hidden')) return;
    started = true;
    observer.disconnect();
    load().then(() => {
      if (linked === null) return;
      const url = new URL(window.location.href);
      url.searchParams.delete('screensaver');
      window.history.replaceState(null, '', url.pathname + (url.search || '') + url.hash);
      // Kurz warten wie deep-link.js: der Start waehlt selbst noch den Tab.
      setTimeout(() => {
        if (state.presets.some(entry => entry.id === linked)) apply(linked, { start: true });
        else window.journlScreensaver?.open('').then(() => {
          if (linked) window.journlScreensaver.flashHint?.(`Preset „${linked}“ gibt es nicht`);
        });
      }, 600);
    });
  }
  const observer = new MutationObserver(whenVisible);
  if (app) observer.observe(app, { attributes: true, attributeFilter: ['class'] });
  whenVisible();

  document.addEventListener('journl:settings-opened', (event) => {
    if (event.detail?.group === 'screensaver') load();
  });
  // Geaenderte Einstellungen oder Fragen-Ablauf: „abweichend“ neu bestimmen.
  document.addEventListener('journl:screensaver-settings', refreshSoon);
  document.addEventListener('journl:screensaver-questions-saved', refreshSoon);
  document.addEventListener('journl:player-settings', (event) => {
    if (event.detail?.origin !== 'preset') refreshSoon();
  });

  window.journlScreensaverPresets = { handleKey, apply, reload: load, list: () => state.presets };
}());
