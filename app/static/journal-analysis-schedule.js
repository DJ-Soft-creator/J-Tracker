/* Zeitplan- und Zeitraum-Bausteine der Journal-Analyse: Beschriftung,
 * Formularfelder und Auslesen des benutzerdefinierten Zeitplans (Wochentage
 * oder Monatstage) sowie die Zeitraum-Auswahl.
 *
 * Ausgelagert aus `journal-analysis.js` (1000-Zeilen-Grenze); muss vor dieser
 * Datei geladen werden. Klassisches Script, stellt
 * `window.JournlAnalysisSchedule` bereit.
 */
(function () {
  function escapeAttr(text) {
    return String(text == null ? '' : text).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  const WEEKDAYS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
  const DATE_RANGE_LABELS = {
    previous_day: 'Vortag',
    last_days: 'Letzte Tage (inkl. heute)',
    current_week: 'Aktuelle Woche',
    last_week: 'Letzte Woche',
    current_month: 'Aktueller Monat',
    last_month: 'Letzter Monat',
    custom: 'Eigener Zeitraum',
  };

  function rangeLabel(job) {
    if (job.date_range_mode === 'last_days') return `letzte ${job.range_days} Tage inkl. heute`;
    return DATE_RANGE_LABELS[job.date_range_mode] || job.date_range_mode;
  }

  function rangeFields(job, input) {
    const mode = job?.date_range_mode || 'previous_day';
    const options = Object.entries(DATE_RANGE_LABELS).map(([value, label]) =>
      `<option value="${value}"${value === mode ? ' selected' : ''}>${label}</option>`).join('');
    return `<select data-field="date_range_mode" class="${input}">${options}</select>
      <label data-range-days class="mt-2 block text-xs text-gray-400 ${mode === 'last_days' ? '' : 'hidden'}">Anzahl Tage (inkl. heute)
        <input type="number" min="1" max="62" data-field="range_days" class="${input}" value="${escapeAttr(job?.range_days || 2)}" /></label>
      <div data-custom-range class="mt-2 grid grid-cols-2 gap-2 ${mode === 'custom' ? '' : 'hidden'}">
        <label class="text-xs text-gray-400">Von
          <input type="date" data-field="custom_start" class="${input}" value="${escapeAttr(job?.custom_start || '')}" /></label>
        <label class="text-xs text-gray-400">Bis
          <input type="date" data-field="custom_end" class="${input}" value="${escapeAttr(job?.custom_end || '')}" /></label>
      </div>`;
  }

  function customScheduleLabel(job) {
    const rule = job.schedule_rule || {};
    if (rule.kind === 'weekly') {
      return `${(rule.weekdays || []).map((day) => WEEKDAYS[day]).join(', ')} ${job.schedule_time}`;
    }
    if (rule.kind === 'monthly') {
      return `am ${(rule.days || []).map((day) => `${day}.`).join(', ')} ${job.schedule_time}`;
    }
    return 'benutzerdefiniert';
  }

  function scheduleLabel(job) {
    if (job.schedule_mode === 'manual') return 'manuell';
    if (job.schedule_mode === 'interval') return `alle ${job.schedule_interval_hours} h`;
    if (job.schedule_mode === 'custom') return customScheduleLabel(job);
    return `täglich ${job.schedule_time}`;
  }

  function customScheduleFields(job, hidden, input) {
    const rule = job?.schedule_rule || { kind: 'weekly', weekdays: [] };
    const weekly = rule.kind !== 'monthly';
    const weekdays = new Set(rule.weekdays || []);
    const boxes = WEEKDAYS.map((label, index) => `<label class="inline-flex items-center gap-1 text-xs text-gray-300">
        <input type="checkbox" data-schedule-weekday value="${index}"${weekdays.has(index) ? ' checked' : ''} /> ${label}</label>`).join('');
    return `<div data-schedule-custom class="space-y-2 ${hidden ? 'hidden' : ''}">
      <label class="block text-xs text-gray-400">Wiederholung
        <select data-schedule-rule-kind class="${input}">
          <option value="weekly"${weekly ? ' selected' : ''}>wöchentlich an Wochentagen</option>
          <option value="monthly"${weekly ? '' : ' selected'}>monatlich an Tagen</option>
        </select></label>
      <div data-schedule-weekly class="flex flex-wrap gap-x-3 gap-y-1 ${weekly ? '' : 'hidden'}">${boxes}</div>
      <label data-schedule-monthly class="block text-xs text-gray-400 ${weekly ? 'hidden' : ''}">Tage im Monat (kommagetrennt, 31 = Monatsletzter)
        <input data-schedule-month-days placeholder="1" class="${input}" value="${escapeAttr((rule.days || []).join(', '))}" /></label>
    </div>`;
  }

  function readScheduleRule(form) {
    const kind = form.querySelector('[data-schedule-rule-kind]')?.value || 'weekly';
    if (kind === 'monthly') {
      const days = (form.querySelector('[data-schedule-month-days]')?.value || '')
        .split(/[\s,;]+/).filter(Boolean).map(Number);
      return { kind, days };
    }
    const weekdays = [...form.querySelectorAll('[data-schedule-weekday]:checked')].map((box) => Number(box.value));
    return { kind, weekdays };
  }

  document.addEventListener('change', (event) => {
    const range = event.target.closest('[data-field="date_range_mode"]');
    if (range) {
      const form = range.closest('form');
      form?.querySelector('[data-custom-range]')?.classList.toggle('hidden', range.value !== 'custom');
      form?.querySelector('[data-range-days]')?.classList.toggle('hidden', range.value !== 'last_days');
    }
    const ruleKind = event.target.closest('[data-schedule-rule-kind]');
    if (!ruleKind) return;
    const form = ruleKind.closest('form');
    form?.querySelector('[data-schedule-weekly]')?.classList.toggle('hidden', ruleKind.value !== 'weekly');
    form?.querySelector('[data-schedule-monthly]')?.classList.toggle('hidden', ruleKind.value !== 'monthly');
  });

  window.JournlAnalysisSchedule = {
    scheduleLabel, customScheduleFields, readScheduleRule, rangeLabel, rangeFields,
  };
})();
