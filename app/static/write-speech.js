/* Vorlesen (TTS) im Schreiben-Tab, wiederverwendbar:
 *   window.journlSpeak(text, {voice, model}) -> Audio
 *   window.journlVoice.stop() / .isSpeaking()
 * Damit koennen weitere Features Text vorlesen, ohne diese Datei zu kennen.
 * Dazu gehoert der Eintrag „Text vorlesen“ im „⋯“-Menue. Laedt nach
 * write-voice.js und haengt sich an dessen window.journlVoice an.
 */
(function () {
  const field = document.getElementById('simple-input');
  if (!field) return;
  const toast = (message, isError) => window.showToast?.(message, !!isError);
  const icon = (paths, cls = 'h-4 w-4') => `<svg aria-hidden="true" viewBox="0 0 24 24" class="${cls}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;
  const ICON_SPEAKER = icon('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>');
  const ICON_CLOSE = icon('<path d="M6 6l12 12M18 6 6 18"/>', 'h-3.5 w-3.5');
  const iconButton = 'grid h-7 w-7 place-items-center rounded-md text-gray-500 hover:bg-gray-800 hover:text-gray-200';

  // ─── Vorlesen (wiederverwendbar) ──────────────────────────────────
  // Ein kleiner Player statt unsichtbarer Wiedergabe: Safari/iOS gibt ein
  // Audio nur direkt nach einer Nutzergeste frei, und nach dem
  // mehrsekuendigen TTS-Abruf ist die Geste verbraucht (5.3.22: „The
  // operation is not supported“; Web Audio blieb in 5.3.23 stumm, etwa
  // beim Stumm-Schalter). Scheitert der automatische Start, tippt man im
  // Player selbst auf Play - das ist immer eine echte Geste.
  const player = document.createElement('div');
  player.id = 'journl-speech-player';
  player.className = 'fixed left-1/2 z-[70] hidden w-[22rem] max-w-[94vw] -translate-x-1/2 rounded-xl border border-gray-800 bg-gray-900/95 px-3 py-2 text-gray-200 shadow-2xl backdrop-blur';
  player.style.top = 'calc(env(safe-area-inset-top, 0px) + 0.75rem)';
  player.innerHTML = `
    <div class="flex items-center gap-2 text-xs text-gray-400">
      ${ICON_SPEAKER}
      <span data-player-status class="min-w-0 flex-1 truncate" role="status" aria-live="polite">Vorlesen</span>
      <button type="button" data-player-close class="${iconButton} h-6 w-6" aria-label="Player schließen" title="Schließen">${ICON_CLOSE}</button>
    </div>
    <audio data-player-audio controls preload="auto" class="mt-1.5 h-9 w-full"></audio>`;
  document.body.appendChild(player);
  const playerAudio = player.querySelector('[data-player-audio]');
  const playerStatus = player.querySelector('[data-player-status]');
  let playerUrl = '';
  let currentAudio = null;

  const setPlayerStatus = (text) => { playerStatus.textContent = text; };
  playerAudio.addEventListener('play', () => {
    currentAudio = playerAudio;
    setPlayerStatus('Liest vor…');
    window.dispatchEvent(new Event('journl:speak-start'));
  });
  const onPlaybackEnd = (status) => () => {
    if (currentAudio !== playerAudio) return;
    currentAudio = null;
    setPlayerStatus(status);
    window.dispatchEvent(new Event('journl:speak-end'));
  };
  playerAudio.addEventListener('pause', onPlaybackEnd('Angehalten'));
  playerAudio.addEventListener('ended', () => { onPlaybackEnd('Fertig')(); setPlayerStatus('Fertig'); });
  playerAudio.addEventListener('error', () => {
    onPlaybackEnd('')();
    if (playerUrl) setPlayerStatus('Dieses Audio kann der Browser nicht abspielen.');
  });

  function stopSpeaking() {
    try { playerAudio.pause(); } catch (_) { /* schon angehalten */ }
  }
  function closePlayer() {
    stopSpeaking();
    playerAudio.removeAttribute('src');
    try { playerAudio.load(); } catch (_) { /* leer */ }
    if (playerUrl) URL.revokeObjectURL(playerUrl);
    playerUrl = '';
    player.classList.add('hidden');
  }
  player.querySelector('[data-player-close]').addEventListener('click', closePlayer);

  async function speak(text, options) {
    const value = String(text || '').trim();
    if (!value) throw new Error('Es gibt keinen Text zum Vorlesen.');
    stopSpeaking();
    player.classList.remove('hidden');
    setPlayerStatus('Wird geladen…');
    let blob;
    try {
      const response = await window.apiFetch('/api/voice/speak', {
        method: 'POST', body: JSON.stringify({ text: value, ...(options || {}) }), timeoutMs: 180000,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'Vorlesen fehlgeschlagen.');
      }
      blob = await response.blob();
    } catch (error) {
      closePlayer();
      throw error;
    }
    if (playerUrl) URL.revokeObjectURL(playerUrl);
    playerUrl = URL.createObjectURL(blob);
    playerAudio.src = playerUrl;
    setPlayerStatus('Bereit');
    try {
      await playerAudio.play();
    } catch (_) {
      // Kein Fehler fuer den Nutzer: der Player steht da, ein Tipp startet.
      setPlayerStatus('Zum Abspielen auf Play tippen');
    }
    return playerAudio;
  }
  // Vorlesen ist ein eigener Eintrag im „⋯“-Menue, nicht Teil der Aufnahme:
  // es liest das Schreibfeld, egal woher der Text kommt.
  const menuElement = window.journlWriteMediaMenu?.element;
  const readEntry = document.createElement('button');
  readEntry.type = 'button';
  readEntry.setAttribute('data-voice-read', '');
  readEntry.className = 'flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800';
  readEntry.innerHTML = `${ICON_SPEAKER}<span data-voice-read-label>Text vorlesen</span>`;
  const readLabel = readEntry.querySelector('[data-voice-read-label]');
  const undoEntry = menuElement?.querySelector('[data-undo]');
  if (undoEntry) undoEntry.insertAdjacentElement('afterend', readEntry);
  else menuElement?.appendChild(readEntry);
  const syncReadEntry = () => { readLabel.textContent = currentAudio ? 'Vorlesen stoppen' : 'Text vorlesen'; };
  window.addEventListener('journl:speak-end', syncReadEntry);
  window.addEventListener('journl:speak-start', syncReadEntry);
  readEntry.addEventListener('click', async () => {
    window.journlWriteMediaMenu?.close?.();
    if (currentAudio) { stopSpeaking(); return; }
    const text = field.value.trim() || window.journlVoice?.lastText?.() || '';
    readLabel.textContent = 'Wird geladen…';
    // Anbieter, Modell und Stimme waehlt der Server aus den Vorlese-
    // Einstellungen (Burger → Schreiben → Vorlesen).
    try {
      await speak(text);
    } catch (error) { toast(error.message, true); }
    syncReadEntry();
  });

  window.journlSpeak = speak;
  window.journlVoice = Object.assign(window.journlVoice || {}, {
    speak,
    stop: stopSpeaking,
    isSpeaking: () => Boolean(currentAudio),
  });
})();
