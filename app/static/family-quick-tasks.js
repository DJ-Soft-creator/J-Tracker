/* Schnell-Erfassung im Aufgaben-Editor des Familien-Tabs.
 *
 * Jede Zeile im Feld „Mehrere Aufgaben“ wird eine eigene Aufgabe - mit eigener
 * Person, eigenem Datum und eigenem Status, statt dass alle Zeilen dieselbe
 * gemeinsame Vorgabe bekommen. Kurzformen in der Zeile (Gross/klein und
 * ae/ä egal):
 *
 *   @Kim              Person (Anfang des Namens genuegt)
 *   heute, morgen, uebermorgen
 *   fr / freitag      naechster solcher Tag, nie heute (dafuer „heute“)
 *   naechsten/kommenden/diesen/am freitag · uebernaechsten freitag
 *   naechste woche (Montag) · naechsten monat (1.) · ende der woche (Freitag)
 *   ende des monats / monatsende
 *   (am/ab) 12.10. · 12.10.2026 · 2026-10-12 · in 3 tagen · in einer woche · in 2 monaten
 *   [x] am Anfang     schon erledigt   ([ ] und „- “ werden einfach entfernt)
 *
 * Wiederholungen werden kein Projekt-Eintrag, sondern ein Eintrag im Planer
 * (Sammelprojekt „Wiederkehrend“) - genau die Rhythmen, die der Planer kennt:
 *
 *   taeglich / jeden tag · jeden freitag / freitags · woechentlich
 *   alle 2 wochen / zweiwoechentlich · monatlich / jeden monatsersten
 *
 * Ein Datum in derselben Zeile ist dann der Start. Der Planer braucht eine
 * Person; ohne @Name und ohne Vorgabe zeigt die Vorschau das rot.
 *
 * Ohne eigene Angabe gelten Person und Datum aus der Vorgabe darunter. Eine
 * Uhrzeit gibt es bewusst nicht: target_date ist ein Datum, die Faelligkeit
 * vergleicht es als Text mit dem heutigen Tag. Keine KI: die Regeln sind
 * sofort fertig und liefern fuer dieselbe Zeile immer dasselbe.
 *
 * Die Personen liest das Modul aus dem vorhandenen #editor-bulk-user (value =
 * Kennung, Text = Anzeigename) - so kennt es die Feature-Dateien nicht.
 * Beide Shells rufen ``journlFamilyQuickTasks.parse`` beim Speichern und
 * ``createPlans`` fuer die Wiederholungen.
 */
(function () {
  // Index wie Date.getDay(): 0 = Sonntag.
  const WEEKDAYS = [
    ['so', 'sonntag'], ['mo', 'montag'], ['di', 'dienstag'], ['mi', 'mittwoch'],
    ['do', 'donnerstag'], ['fr', 'freitag'], ['sa', 'samstag'],
  ];
  const WEEKLY = ['weekly_sunday', 'weekly_monday', 'weekly_tuesday', 'weekly_wednesday',
    'weekly_thursday', 'weekly_friday', 'weekly_saturday'];
  // Wie scheduling.RECURRENCE_LABELS.
  const RECURRENCE_LABELS = {
    daily: 'Jeden Tag', weekly_monday: 'Jeden Montag', weekly_tuesday: 'Jeden Dienstag',
    weekly_wednesday: 'Jeden Mittwoch', weekly_thursday: 'Jeden Donnerstag', weekly_friday: 'Jeden Freitag',
    weekly_saturday: 'Jeden Samstag', weekly_sunday: 'Jeden Sonntag', biweekly: 'Alle 2 Wochen',
    monthly_first: 'Am 1. jedes Monats',
  };
  const NUMBERS = { ein: 1, eine: 1, einem: 1, einer: 1, einen: 1, zwei: 2, drei: 3, vier: 4, fuenf: 5, sechs: 6 };
  const NEXT = ['naechsten', 'naechster', 'naechste', 'kommenden', 'kommender', 'kommende', 'diesen', 'dieser', 'am', 'ab'];
  const AFTER_NEXT = ['uebernaechsten', 'uebernaechster', 'uebernaechste'];

  const pad = (value) => String(value).padStart(2, '0');
  const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const norm = (word) => String(word || '').toLowerCase().replace(/ä/g, 'ae').replace(/ö/g, 'oe')
    .replace(/ü/g, 'ue').replace(/ß/g, 'ss').replace(/[.,;:!?]+$/, '');
  function addDays(date, days) {
    const copy = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    copy.setDate(copy.getDate() + days);
    return copy;
  }
  function addMonths(date, months) {
    const target = new Date(date.getFullYear(), date.getMonth() + months, 1);
    const last = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
    return new Date(target.getFullYear(), target.getMonth(), Math.min(date.getDate(), last));
  }
  function validDate(year, month, day) {
    const date = new Date(year, month - 1, day);
    return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day ? date : null;
  }
  const weekdayOf = (word) => WEEKDAYS.findIndex((names) => names.includes(word));
  // Naechster solcher Wochentag, nie heute: „mittwoch“ am Mittwoch meint die naechste Woche.
  const nextWeekday = (today, weekday) => addDays(today, ((weekday - today.getDay() + 7) % 7) || 7);

  // Wiederholung ab words[index]. Liefert {recurrence, length} oder null;
  // 'weekly' haengt am Wochentag des Starts und wird nach der Zeile aufgeloest.
  function matchRecurrence(words, index) {
    const [a, b, c, d] = [0, 1, 2, 3].map((offset) => norm(words[index + offset]));
    if (a === 'taeglich' || a === 'tagtaeglich') return { recurrence: 'daily', length: 1 };
    if ((a === 'jeden' || a === 'jede') && b === 'tag') return { recurrence: 'daily', length: 2 };
    if (['jeden', 'jede', 'immer'].includes(a) && weekdayOf(b) >= 0) return { recurrence: WEEKLY[weekdayOf(b)], length: 2 };
    if (a.length > 3 && a.endsWith('s') && WEEKDAYS.some((names) => names[1] === a.slice(0, -1))) {
      return { recurrence: WEEKLY[weekdayOf(a.slice(0, -1))], length: 1 };
    }
    if (a === 'woechentlich' || ((a === 'jede' || a === 'jeden') && b === 'woche')) {
      return { recurrence: 'weekly', length: a === 'woechentlich' ? 1 : 2 };
    }
    if (a === 'alle' && (b === '2' || b === 'zwei') && c === 'wochen') return { recurrence: 'biweekly', length: 3 };
    if (['zweiwoechentlich', '14-taegig', 'vierzehntaegig'].includes(a)) return { recurrence: 'biweekly', length: 1 };
    if (a === 'monatlich' || a === 'monatsersten') return { recurrence: 'monthly_first', length: 1 };
    if (['jeden', 'am', 'zum'].includes(a) && b === 'monatsersten') return { recurrence: 'monthly_first', length: 2 };
    if (a === 'jeden' && b === 'monat') return { recurrence: 'monthly_first', length: 2 };
    if ((a === 'am' || a === 'jeden') && b === '1' && ['jedes', 'jeden', 'im'].includes(c) && d === 'monats') {
      return { recurrence: 'monthly_first', length: 4 };
    }
    return null;
  }

  // Ein Datum ab words[index] (ein bis drei Woerter). Liefert {date, length}.
  function matchDate(words, index, today) {
    const raw = String(words[index] || '').toLowerCase();
    const [a, b, c] = [0, 1, 2].map((offset) => norm(words[index + offset]));
    if (a === 'heute') return { date: today, length: 1 };
    if (a === 'morgen') return { date: addDays(today, 1), length: 1 };
    if (a === 'uebermorgen') return { date: addDays(today, 2), length: 1 };
    if (weekdayOf(a) >= 0) return { date: nextWeekday(today, weekdayOf(a)), length: 1 };
    if (NEXT.includes(a) && weekdayOf(b) >= 0) return { date: nextWeekday(today, weekdayOf(b)), length: 2 };
    if (AFTER_NEXT.includes(a) && weekdayOf(b) >= 0) return { date: addDays(nextWeekday(today, weekdayOf(b)), 7), length: 2 };
    if (['naechste', 'kommende'].includes(a) && b === 'woche') return { date: nextWeekday(today, 1), length: 2 };
    if (AFTER_NEXT.includes(a) && b === 'woche') return { date: addDays(nextWeekday(today, 1), 7), length: 2 };
    if (['naechsten', 'kommenden'].includes(a) && b === 'monat') {
      return { date: new Date(today.getFullYear(), today.getMonth() + 1, 1), length: 2 };
    }
    if (a === 'ende' && ['der', 'dieser'].includes(b) && c === 'woche') {
      return { date: addDays(today, (5 - today.getDay() + 7) % 7), length: 3 };
    }
    const monthEnd = new Date(today.getFullYear(), today.getMonth() + 1, 0);
    if (a === 'monatsende') return { date: monthEnd, length: 1 };
    if (a === 'ende' && ['des', 'dieses'].includes(b) && c === 'monats') return { date: monthEnd, length: 3 };
    if ((a === 'am' || a === 'ab') && words[index + 1]) {
      const hit = matchNumericDate(String(words[index + 1]).toLowerCase(), today);
      if (hit) return { date: hit, length: 2 };
    }
    const numeric = matchNumericDate(raw, today);
    if (numeric) return { date: numeric, length: 1 };
    if (a === 'in' && b && c) {
      const amount = NUMBERS[b] || Number(b);
      if (Number.isInteger(amount) && amount > 0 && amount < 400) {
        if (/^tage?n?$/.test(c)) return { date: addDays(today, amount), length: 3 };
        if (/^wochen?$/.test(c)) return { date: addDays(today, amount * 7), length: 3 };
        if (/^monate?n?$/.test(c) && amount < 60) return { date: addMonths(today, amount), length: 3 };
      }
    }
    return null;
  }

  function matchNumericDate(word, today) {
    let match = /^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?[,;]?$/.exec(word);
    if (match) {
      let year = match[3] ? Number(match[3]) : today.getFullYear();
      if (year < 100) year += 2000;
      let date = validDate(year, Number(match[2]), Number(match[1]));
      // Ohne Jahr meint ein vergangener Tag das naechste Jahr.
      if (date && !match[3] && date < today) date = validDate(year + 1, Number(match[2]), Number(match[1]));
      return date;
    }
    match = /^(\d{4})-(\d{2})-(\d{2})[.,;]?$/.exec(word);
    return match ? validDate(Number(match[1]), Number(match[2]), Number(match[3])) : null;
  }

  function matchUser(name, users) {
    const wanted = name.toLowerCase();
    return users.find((user) => user.display.toLowerCase() === wanted || String(user.id).toLowerCase() === wanted)
      || users.find((user) => user.display.toLowerCase().startsWith(wanted));
  }

  /**
   * Zeilen in Aufgaben zerlegen.
   * options: {users: [{id, display}], defaultUser, defaultDate, full, today}
   * Rueckgabe: [{title, completed, user, target_date, unknownUser, recurrence?, recurrenceLabel?, planError?}]
   */
  function parse(text, options) {
    const settings = options || {};
    const users = settings.users || [];
    const today = settings.today ? new Date(settings.today) : new Date();
    const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    const tasks = [];
    for (const raw of String(text || '').split('\n')) {
      let line = raw.trim().replace(/^[-*•]\s+/, '');
      if (!line) continue;
      let completed = false;
      const box = /^\[( |x|X)\]\s*/.exec(line);
      if (box) {
        completed = box[1].toLowerCase() === 'x';
        line = line.slice(box[0].length);
      }
      if (!settings.full) {
        if (line) tasks.push({ title: line, completed });
        continue;
      }
      const words = line.split(/\s+/);
      const kept = [];
      let user = null;
      let unknownUser = '';
      let date = null;
      let recurrence = '';
      for (let index = 0; index < words.length; index += 1) {
        const word = words[index];
        const mention = /^@([\wÄÖÜäöüß.-]+)$/.exec(word.replace(/[,;]+$/, ''));
        if (mention && !user) {
          const found = matchUser(mention[1], users);
          if (found) user = found;
          else unknownUser = mention[1];
          if (found) continue;
        }
        if (!recurrence) {
          const repeat = matchRecurrence(words, index);
          if (repeat) {
            recurrence = repeat.recurrence;
            index += repeat.length - 1;
            continue;
          }
        }
        if (!date) {
          const hit = matchDate(words, index, start);
          if (hit) {
            date = hit.date;
            index += hit.length - 1;
            continue;
          }
        }
        kept.push(word);
      }
      const title = kept.join(' ').trim();
      if (!title) continue;
      const task = {
        title,
        completed,
        user: user ? user.id : (settings.defaultUser || ''),
        target_date: date ? iso(date) : (settings.defaultDate || ''),
        unknownUser,
      };
      if (recurrence) {
        // „woechentlich“ haengt am Wochentag des Starts (sonst heute).
        if (recurrence === 'weekly') {
          recurrence = WEEKLY[(task.target_date ? new Date(`${task.target_date}T12:00:00`) : start).getDay()];
        }
        task.recurrence = recurrence;
        task.recurrenceLabel = RECURRENCE_LABELS[recurrence];
        task.target_date = task.target_date || iso(start);
        if (!task.user) task.planError = 'Person fehlt';
      }
      tasks.push(task);
    }
    return tasks;
  }

  // Wiederholungen als Planer-Eintraege anlegen. Rueckgabe: Titel, die nicht
  // angelegt werden konnten, mit Grund.
  async function createPlans(items) {
    const failed = [];
    for (const item of items || []) {
      if (item.planError) { failed.push(`${item.title}: ${item.planError}`); continue; }
      try {
        const response = await window.apiFetch('/api/family/planner', {
          method: 'POST',
          body: JSON.stringify({ title: item.title, user: item.user, recurrence: item.recurrence,
            start_date: item.target_date }),
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          failed.push(`${item.title}: ${data.error || response.status}`);
        }
      } catch (_) {
        failed.push(`${item.title}: Netzwerkfehler`);
      }
    }
    return failed;
  }

  // ─── Vorschau im Editor ─────────────────────────────────────────────
  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  function usersFromSelect() {
    const select = document.getElementById('editor-bulk-user');
    return select ? Array.from(select.options).filter((option) => option.value)
      .map((option) => ({ id: option.value, display: option.textContent.trim() })) : [];
  }
  function isFullMode() {
    const extra = document.getElementById('editor-bulk-extra');
    return Boolean(extra && !extra.classList.contains('hidden'));
  }
  function shortDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '');
    return match ? `${match[3]}.${match[2]}.` : value;
  }

  function renderPreview() {
    const area = document.getElementById('editor-bulk');
    if (!area) return;
    let box = document.getElementById('editor-bulk-preview');
    if (!box) {
      box = document.createElement('div');
      box.id = 'editor-bulk-preview';
      box.className = 'mt-2 space-y-1';
      area.insertAdjacentElement('afterend', box);
    }
    if (!isFullMode() || !area.value.trim()) { box.innerHTML = ''; return; }
    const users = usersFromSelect();
    const names = Object.fromEntries(users.map((user) => [user.id, user.display]));
    const tasks = parse(area.value, {
      users, full: true,
      defaultUser: document.getElementById('editor-bulk-user')?.value || '',
      defaultDate: document.getElementById('editor-bulk-date')?.value || '',
    });
    const chip = (text, tone) => `<span class="rounded-full px-1.5 py-0.5 text-[10px] ${tone}">${escapeHtml(text)}</span>`;
    box.innerHTML = `<p class="text-[11px] text-gray-500">Wird angelegt (${tasks.length}):</p>` + tasks.map((task) => `
      <div class="flex flex-wrap items-center gap-1.5 rounded-md border border-gray-800 px-2 py-1 text-xs">
        <span class="${task.completed ? 'text-gray-500 line-through' : 'text-gray-200'}">${escapeHtml(task.title)}</span>
        ${task.user ? chip(names[task.user] || task.user, 'bg-blue-900/40 text-blue-200') : ''}
        ${task.recurrence ? chip(`${task.recurrenceLabel} · Planer`, 'bg-purple-900/40 text-purple-200') : ''}
        ${task.target_date ? chip((task.recurrence ? 'ab ' : '') + shortDate(task.target_date), 'bg-amber-900/30 text-amber-200') : ''}
        ${task.planError ? chip(task.planError, 'bg-red-900/40 text-red-300') : ''}
        ${task.completed ? chip('erledigt', 'bg-green-900/40 text-green-300') : ''}
        ${task.unknownUser ? chip(`@${task.unknownUser} unbekannt`, 'bg-red-900/40 text-red-300') : ''}
      </div>`).join('');
  }

  document.addEventListener('input', (event) => {
    if (event.target.id === 'editor-bulk') renderPreview();
  });
  document.addEventListener('change', (event) => {
    if (event.target.id === 'editor-bulk-user' || event.target.id === 'editor-bulk-date') renderPreview();
  });

  // Beispieltext fuer das Feld, mit echten Personen des Projekts statt
  // erfundener Namen - so zeigt das Beispiel, was @… hier wirklich trifft.
  function placeholder(users) {
    const names = (users || []).map((user) => (/^[\wÄÖÜäöüß.-]+/.exec(String(user.display || '').trim()) || [])[0])
      .filter(Boolean);
    const [first = 'Kim', second] = names;
    return [`Müll rausbringen @${first} morgen`,
      second ? `Steuer abgeben @${second} 12.10.` : 'Steuer abgeben 12.10.',
      `Blumen gießen @${first} jeden Freitag`,
      '[x] Arzttermin vereinbart'].join('\n');
  }

  window.journlFamilyQuickTasks = { parse, renderPreview, placeholder, createPlans };
})();
