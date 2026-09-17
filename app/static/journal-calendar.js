/* Shared content-free journal calendar for Today and Brain date filters. */
(function () {
  const monthNames = [
    'Januar', 'Februar', 'März', 'April', 'Mai', 'Juni',
    'Juli', 'August', 'September', 'Oktober', 'November', 'Dezember',
  ];
  const weekdayNames = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
  const dateCache = new Map();
  let renderSequence = 0;
  let state = null;

  const overlay = document.createElement('div');
  overlay.id = 'journal-calendar-overlay';
  overlay.className = 'fixed inset-0 hidden items-center justify-center bg-black/60 px-4';
  overlay.style.zIndex = '70';
  overlay.innerHTML = `
    <section role="dialog" aria-modal="true" aria-labelledby="journal-calendar-title" class="w-full max-w-xs rounded-2xl border border-gray-700 bg-gray-900 p-4 shadow-2xl">
      <div class="mb-3 flex items-center justify-between gap-3">
        <button type="button" data-calendar-previous class="grid h-9 w-9 place-items-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white" aria-label="Vorheriger Monat">‹</button>
        <h2 id="journal-calendar-title" class="text-sm font-semibold text-gray-200"></h2>
        <button type="button" data-calendar-next class="grid h-9 w-9 place-items-center rounded-lg text-gray-400 hover:bg-gray-800 hover:text-white" aria-label="Nächster Monat">›</button>
      </div>
      <div data-calendar-weekdays class="grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-gray-600"></div>
      <div data-calendar-days class="mt-1 grid grid-cols-7 gap-1"></div>
      <div class="mt-3 flex min-h-5 items-center justify-between gap-3 text-[11px] text-gray-500">
        <span data-calendar-status></span>
        <button type="button" data-calendar-close class="rounded px-2 py-1 text-gray-500 hover:bg-gray-800 hover:text-gray-200">Schließen</button>
      </div>
    </section>`;
  document.body.appendChild(overlay);

  const title = overlay.querySelector('#journal-calendar-title');
  const daysGrid = overlay.querySelector('[data-calendar-days]');
  const status = overlay.querySelector('[data-calendar-status]');
  const previousButton = overlay.querySelector('[data-calendar-previous]');
  const nextButton = overlay.querySelector('[data-calendar-next]');
  overlay.querySelector('[data-calendar-weekdays]').innerHTML = weekdayNames
    .map((name) => `<span class="py-1">${name}</span>`).join('');

  function localToday() {
    return new Date().toLocaleDateString('sv-SE');
  }

  function parseIsoDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    if (!match) return null;
    const parsed = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }

  function isoDate(year, monthIndex, day) {
    return `${String(year).padStart(4, '0')}-${String(monthIndex + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  }

  function monthKey(date) {
    return `${String(date.getFullYear()).padStart(4, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}`;
  }

  async function journalDates(month) {
    if (!dateCache.has(month)) {
      dateCache.set(month, apiFetch(`/api/journals/dates?month=${encodeURIComponent(month)}`)
        .then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.error || 'Journaltage konnten nicht geladen werden.');
          return new Set((data.dates || []).filter((value) => /^\d{4}-\d{2}-\d{2}$/.test(value)));
        })
        .catch((error) => {
          dateCache.delete(month);
          throw error;
        }));
    }
    return dateCache.get(month);
  }

  function brainRange() {
    return {
      start: document.getElementById('brain-range-start')?.value || '',
      end: document.getElementById('brain-range-end')?.value || '',
    };
  }

  function renderDays(journals, focusSelection) {
    const year = state.month.getFullYear();
    const monthIndex = state.month.getMonth();
    const firstOffset = (new Date(year, monthIndex, 1).getDay() + 6) % 7;
    const dayCount = new Date(year, monthIndex + 1, 0).getDate();
    const today = localToday();
    const range = brainRange();
    const fragment = document.createDocumentFragment();

    for (let index = 0; index < firstOffset; index += 1) {
      const spacer = document.createElement('span');
      spacer.className = 'h-10';
      spacer.setAttribute('aria-hidden', 'true');
      fragment.appendChild(spacer);
    }
    for (let day = 1; day <= dayCount; day += 1) {
      const iso = isoDate(year, monthIndex, day);
      const selected = iso === state.selected;
      const inRange = range.start && range.end && iso >= range.start && iso <= range.end;
      const isToday = iso === today;
      const disabled = Boolean(state.max && iso > state.max);
      const hasJournal = journals.has(iso);
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.calendarDate = iso;
      button.disabled = disabled;
      button.className = `relative grid h-10 place-items-center rounded-lg text-sm transition ${
        selected ? 'bg-green-600 text-white' : inRange ? 'bg-green-950/60 text-green-100' : isToday
          ? 'ring-1 ring-green-700/70 text-gray-100 hover:bg-gray-800' : disabled
            ? 'cursor-not-allowed text-gray-700' : 'text-gray-300 hover:bg-gray-800 hover:text-white'
      }`;
      const spoken = new Intl.DateTimeFormat('de-DE', { dateStyle: 'full' })
        .format(new Date(year, monthIndex, day));
      button.setAttribute('aria-label', `${spoken}${hasJournal ? ', Journal vorhanden' : ''}`);
      if (selected) button.setAttribute('aria-current', 'date');
      button.textContent = String(day);
      if (hasJournal) {
        const marker = document.createElement('span');
        marker.className = `absolute bottom-1 h-1.5 w-1.5 rounded-full ${selected ? 'bg-white' : 'bg-green-400'}`;
        marker.setAttribute('aria-hidden', 'true');
        button.appendChild(marker);
      }
      fragment.appendChild(button);
    }
    daysGrid.replaceChildren(fragment);
    if (focusSelection) {
      const preferred = daysGrid.querySelector(`[data-calendar-date="${state.selected}"]:not(:disabled)`)
        || daysGrid.querySelector('[data-calendar-date]:not(:disabled)');
      preferred?.focus({ preventScroll: true });
    }
  }

  async function renderMonth() {
    const sequence = ++renderSequence;
    const month = monthKey(state.month);
    title.textContent = `${monthNames[state.month.getMonth()]} ${state.month.getFullYear()}`;
    status.textContent = 'Journaltage werden geladen…';
    renderDays(new Set(), true);
    const currentMonth = monthKey(new Date());
    nextButton.disabled = state.kind === 'dashboard' && month >= currentMonth;
    nextButton.classList.toggle('opacity-30', nextButton.disabled);
    try {
      const journals = await journalDates(month);
      if (!state || sequence !== renderSequence || month !== monthKey(state.month)) return;
      renderDays(journals, false);
      status.innerHTML = '<span class="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-green-400"></span>Journal vorhanden';
    } catch (error) {
      if (state && sequence === renderSequence) status.textContent = error.message;
    }
  }

  function closeCalendar(restoreFocus = true) {
    const trigger = state?.trigger;
    state = null;
    renderSequence += 1;
    dateCache.clear();
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    if (restoreFocus && trigger?.isConnected) trigger.focus({ preventScroll: true });
  }

  function selectDate(value) {
    if (!state) return;
    const kind = state.kind;
    closeCalendar(false);
    if (kind === 'dashboard') {
      window.chooseDashboardJournalDate?.(value);
      return;
    }
    const input = document.getElementById(kind === 'brain-start' ? 'brain-range-start' : 'brain-range-end');
    const counterpart = document.getElementById(kind === 'brain-start' ? 'brain-range-end' : 'brain-range-start');
    if (!input) return;
    input.value = value;
    if (kind === 'brain-start' && counterpart?.value && counterpart.value < value) counterpart.value = '';
    if (kind === 'brain-end' && counterpart?.value && counterpart.value > value) counterpart.value = '';
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function openCalendar(trigger) {
    const kind = trigger.dataset.journalCalendar;
    const range = brainRange();
    const selected = kind === 'dashboard' ? trigger.dataset.date
      : kind === 'brain-start' ? range.start : range.end;
    const initial = parseIsoDate(selected || range.start || range.end || localToday()) || new Date();
    state = {
      kind,
      trigger,
      selected: selected || '',
      max: kind === 'dashboard' ? (trigger.dataset.max || localToday()) : '',
      month: new Date(initial.getFullYear(), initial.getMonth(), 1),
    };
    overlay.classList.remove('hidden');
    overlay.classList.add('flex');
    renderMonth();
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-journal-calendar]');
    if (trigger) {
      event.preventDefault();
      openCalendar(trigger);
    }
  });
  daysGrid.addEventListener('click', (event) => {
    const button = event.target.closest('[data-calendar-date]');
    if (button && !button.disabled) selectDate(button.dataset.calendarDate);
  });
  previousButton.addEventListener('click', () => {
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1);
    renderMonth();
  });
  nextButton.addEventListener('click', () => {
    if (nextButton.disabled) return;
    state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1);
    renderMonth();
  });
  overlay.querySelector('[data-calendar-close]').addEventListener('click', () => closeCalendar());
  overlay.addEventListener('click', (event) => { if (event.target === overlay) closeCalendar(); });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && state) closeCalendar();
  });
}());
