/* Zeitplan des Whisper-Sidecars im Einstellungen-Modal.

   Der Sidecar laeuft einmal pro Instanz, der Zeitplan gilt deshalb fuer alle
   Konten: lesen darf ihn jeder, aendern nur ein Administrator. Gespeichert wird
   sofort bei der Auswahl - ein eigener Speichern-Knopf waere fuer zwei Felder
   nur ein zusaetzlicher Klick. */
(function () {
  const mode = document.getElementById('settings-transcribe-mode');
  const hourSelect = document.getElementById('settings-transcribe-hour');
  const note = document.getElementById('settings-transcribe-schedule-note');
  const status = document.getElementById('settings-transcribe-schedule-status');
  if (!mode || !hourSelect) return;

  for (let hour = 0; hour < 24; hour += 1) {
    const option = document.createElement('option');
    option.value = String(hour);
    option.textContent = `${String(hour).padStart(2, '0')}:00 Uhr`;
    hourSelect.appendChild(option);
  }

  async function request(options) {
    const response = await window.apiFetch('/api/write-sessions/transcriptions/schedule', options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Zeitplan nicht verfügbar.');
    return data;
  }

  function render(data) {
    mode.value = data.mode || 'daily';
    hourSelect.value = String(typeof data.hour === 'number' ? data.hour : 11);
    hourSelect.classList.toggle('hidden', mode.value !== 'daily');
    const editable = data.can_edit !== false;
    mode.disabled = !editable;
    hourSelect.disabled = !editable;
    if (!note) return;
    const time = `${String(hourSelect.value).padStart(2, '0')}:00 Uhr`;
    note.textContent = mode.value === 'hourly'
      ? 'Offene Aufnahmen werden stündlich lokal transkribiert.'
      : mode.value === 'off'
        ? 'Offene Aufnahmen werden nur über „Jetzt transkribieren“ verarbeitet.'
        : `Offene Aufnahmen werden täglich um ${time} lokal transkribiert.`;
  }

  async function save() {
    if (status) status.textContent = 'Wird gespeichert…';
    try {
      render(await request({
        method: 'POST',
        body: JSON.stringify({ mode: mode.value, hour: Number(hourSelect.value) })
      }));
      if (status) status.textContent = 'Gespeichert';
    } catch (error) {
      if (status) status.textContent = error.message || 'nicht gespeichert';
    }
  }

  mode.addEventListener('change', () => {
    hourSelect.classList.toggle('hidden', mode.value !== 'daily');
    save();
  });
  hourSelect.addEventListener('change', save);

  (async function load() {
    try { render(await request({ cache: 'no-store' })); }
    catch (_) { /* Ohne Antwort bleibt die Voreinstellung im Markup stehen. */ }
  })();
})();
