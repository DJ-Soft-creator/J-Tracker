/* Bildschirmschoner-Modus „Fragen“: Fragen ueber der laufenden Bildershow.

   Zwei Antwortarten (Einstellung `answer`):

   * **Impuls** — die Frage steht als grosser Hintergrundtext ueber den Bildern,
     aber unter dem Schreibfeld der Kulisse. Geschrieben wird frei; auf Wunsch
     (`heading`) steht die Frage als Ueberschrift im Text und verschwindet
     wieder, wenn bis zur naechsten Frage nichts darunter geschrieben wurde.
   * **Fragen-Session** — je Frage ein Textfeld oder feste Antwortknoepfe
     (Klick oder Taste 1–9). Jede Antwort wandert als Block ins Schreibfeld;
     gesendet wird wie immer mit Strg/Cmd+Enter.

   Ablaeufe: `manual` (keine Zeit), `auto` (wechselt nach Zeit), `quiz`
   (sichtbarer Countdown), `sequence` (Set der Reihe nach, jede Frage mit
   eigener Zeit). Jeder Ablauf endet nach der letzten Frage des Sets, statt
   von vorn zu beginnen - ausser Frei und Automatisch mit `loop`, die dann neu
   (ggf. neu gemischt) anfangen. Zurueck geht es nur in Frei und Automatisch;
   Quiz und Sequenz laufen bewusst nur vorwaerts. Der dezente Schieberegler
   oben stellt die Zeit je Frage und wird gespeichert.

   Was bei Ablauf der Zeit passiert (`on_timeout`): `keep` — weiter, eine leere
   Antwort steht als „–“; `skip` — weiter, leere Fragen fallen weg; `wait` —
   ohne Antwort geht es nicht weiter. Fragen mit `required` warten immer.

   screensaver.js ruft `start(ctx)` beim Wechsel in den Modus und `stop()`
   beim Verlassen; Tasten reicht es ueber `handleKey(event)` durch. */
(function () {
  const ENDPOINT = '/api/screensaver/questions';
  // Die letzte Stufe 0 heisst „Manuell“: keine Zeit, weiter geht es von Hand.
  const STEPS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1800, 0];

  let ctx = null;
  let data = null;
  let flow = null;
  let set = null;
  let queue = [];
  let position = -1;
  let current = null;
  let ticker = null;
  let startedAt = 0;
  let duration = 0;
  let expired = false;
  let finished = false;
  let selected = '';
  let headingMarker = '';
  let sessionHeaderWritten = false;
  // Was die Session ins Schreibfeld gehaengt hat - damit Zurueck es wieder abnehmen kann.
  let recorded = [];
  // Angefangene Antworten, die man per Zurueck verlassen hat - je Position.
  let drafts = {};
  let saveTimer = null;
  let el = null;

  const escapeHtml = (value) => String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const SHADOW = 'text-shadow:0 1px 3px rgba(0,0,0,.9), 0 0 10px rgba(0,0,0,.7), 0 2px 24px rgba(0,0,0,.6)';

  function formatSeconds(value) {
    if (!value) return 'Manuell';
    if (value < 60) return `${value} s`;
    const minutes = Math.floor(value / 60);
    const rest = value % 60;
    return rest ? `${minutes}:${String(rest).padStart(2, '0')} min` : `${minutes} min`;
  }
  function countdown(value) {
    const total = Math.max(0, Math.ceil(value));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  // ─── Markup ───────────────────────────────────────────────────────
  function build(root) {
    if (el && el.top.isConnected) return el;
    const top = document.createElement('div');
    top.className = 'absolute left-1/2 top-3 z-[3] flex -translate-x-1/2 items-center gap-2 rounded-full px-3 py-1 text-[11px] text-white opacity-30 transition-opacity duration-300 hover:opacity-100 focus-within:opacity-100';
    top.style.textShadow = '0 1px 3px rgba(0,0,0,.9)';
    top.innerHTML = `
      <span data-q-count class="tabular-nums text-white/80"></span>
      <label data-q-slider-wrap class="flex items-center gap-2"><span class="sr-only">Zeit je Frage</span>
        <input data-q-slider type="range" min="0" max="${STEPS.length - 1}" step="1" class="h-1 w-36 cursor-pointer accent-white">
        <span data-q-seconds class="w-14 tabular-nums text-white/80"></span></label>
      <button type="button" data-q-back class="rounded-full px-2 py-0.5 text-white/80 hover:bg-white/15" title="Vorige Frage (Alt+←)">‹ Zurück</button>
      <button type="button" data-q-skip class="rounded-full px-2 py-0.5 text-white/80 hover:bg-white/15" title="Nächste Frage (Alt+→)">Nächste ›</button>`;
    const bar = document.createElement('div');
    bar.className = 'absolute left-0 top-0 z-[3] h-0.5 bg-white/60';
    bar.style.width = '0';

    // Impuls: ueber Bild und Abdunkelung, unter dem Schreibfeld (das danach kommt).
    const impulse = document.createElement('div');
    impulse.className = 'pointer-events-none absolute inset-x-0 top-[9%] hidden px-8 text-center';
    impulse.innerHTML = `
      <p data-q-impulse class="mx-auto max-w-5xl text-3xl font-light leading-tight text-white/75 md:text-5xl" style="${SHADOW}"></p>
      <p data-q-impulse-timer class="mt-3 text-sm tabular-nums text-white/60" style="${SHADOW}"></p>`;

    const session = document.createElement('div');
    session.className = 'absolute inset-0 z-[2] hidden flex-col items-center justify-center px-6';
    session.style.background = 'radial-gradient(ellipse 65% 60% at center, rgba(0,0,0,.5), rgba(0,0,0,.2) 60%, transparent 85%)';
    session.innerHTML = `
      <p data-q-text class="max-w-4xl text-center text-3xl font-light leading-tight text-white md:text-4xl" style="${SHADOW}"></p>
      <p data-q-timer class="mt-3 h-5 text-sm tabular-nums text-white/70" style="${SHADOW}"></p>
      <textarea data-q-answer rows="4" spellcheck="false" placeholder="Antwort…"
        class="mt-6 w-full max-w-2xl resize-none border-b border-white/25 bg-transparent text-lg leading-relaxed text-white caret-white placeholder:text-white/40 focus:border-white/60 focus:outline-none"
        style="${SHADOW}"></textarea>
      <div data-q-choices class="mt-6 flex max-w-3xl flex-wrap justify-center gap-2"></div>
      <div class="mt-6 flex items-center gap-4 text-xs text-white/60" style="${SHADOW}">
        <button type="button" data-q-prev class="rounded-full px-3 py-1.5 text-sm text-white/70 hover:bg-white/10" title="Vorige Frage (Alt+←)">Zurück</button>
        <button type="button" data-q-next class="rounded-full border border-white/30 px-4 py-1.5 text-sm text-white/90 hover:bg-white/10">Weiter</button>
        <span data-q-hint></span>
      </div>`;

    const editorWrap = root.querySelector('[data-editor-wrap]');
    root.insertBefore(impulse, editorWrap);
    root.appendChild(session);
    root.appendChild(bar);
    root.appendChild(top);
    el = {
      top, bar, impulse, session,
      count: top.querySelector('[data-q-count]'),
      sliderWrap: top.querySelector('[data-q-slider-wrap]'),
      slider: top.querySelector('[data-q-slider]'),
      seconds: top.querySelector('[data-q-seconds]'),
      skip: top.querySelector('[data-q-skip]'),
      back: top.querySelector('[data-q-back]'),
      prev: session.querySelector('[data-q-prev]'),
      impulseText: impulse.querySelector('[data-q-impulse]'),
      impulseTimer: impulse.querySelector('[data-q-impulse-timer]'),
      text: session.querySelector('[data-q-text]'),
      timer: session.querySelector('[data-q-timer]'),
      answer: session.querySelector('[data-q-answer]'),
      choices: session.querySelector('[data-q-choices]'),
      next: session.querySelector('[data-q-next]'),
      hint: session.querySelector('[data-q-hint]'),
    };
    el.slider.addEventListener('input', () => {
      flow.seconds = STEPS[Number(el.slider.value)] ?? flow.seconds;
      el.seconds.textContent = formatSeconds(flow.seconds);
      // Die laufende Frage bekommt die neue Zeit sofort (ausser in Sequenzen).
      if (flow.flow !== 'sequence' && flow.flow !== 'manual' && current) restartTimer(flow.seconds);
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => persistFlow({ seconds: flow.seconds }), 800);
    });
    el.skip.addEventListener('click', () => advance('manual'));
    el.next.addEventListener('click', () => advance('manual'));
    el.back.addEventListener('click', back);
    el.prev.addEventListener('click', back);
    el.choices.addEventListener('click', (event) => {
      const button = event.target.closest('[data-q-choice]');
      if (button) choose(button.dataset.qChoice);
    });
    return el;
  }

  function persistFlow(patch) {
    window.apiFetch?.(ENDPOINT, { method: 'PUT', body: JSON.stringify({ flow: patch }) }).catch(() => {});
  }

  // ─── Schreibfeld ──────────────────────────────────────────────────
  function fieldValue() { return ctx.field()?.value || ''; }
  function setFieldValue(value) {
    const target = ctx.field();
    if (!target) return;
    target.value = value;
    target.dispatchEvent(new Event('input', { bubbles: true }));
    if (ctx.editor) ctx.editor.value = value;
  }
  function appendBlock(block) {
    const current = fieldValue();
    const separator = !current ? '' : current.endsWith('\n\n') ? '' : current.endsWith('\n') ? '\n' : '\n\n';
    setFieldValue(current + separator + block);
    return separator + block;
  }
  // Impuls: die Ueberschrift der vorigen Frage faellt weg, wenn nichts darunter steht.
  function dropEmptyHeading() {
    if (!headingMarker) return;
    const value = ctx.editor?.value ?? fieldValue();
    if (value.endsWith(headingMarker)) setFieldValue(value.slice(0, -headingMarker.length).replace(/\s+$/, ''));
    headingMarker = '';
  }

  // ─── Ablauf ───────────────────────────────────────────────────────
  function shuffle(list) {
    const copy = list.slice();
    for (let i = copy.length - 1; i > 0; i -= 1) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }
  function buildQueue() {
    const questions = set?.questions || [];
    queue = flow.flow !== 'sequence' && flow.shuffle ? shuffle(questions) : questions.slice();
    position = -1;
  }
  function isSession() { return flow.answer === 'session'; }
  // Quiz und Sequenz sind ein Durchgang auf Zeit - dort gibt es kein Zurueck und keinen Loop.
  function canGoBack() { return flow.flow === 'manual' || flow.flow === 'auto'; }
  function loops() { return Boolean(flow.loop) && canGoBack(); }
  function timeFor(question) {
    // „Manuell“ am Regler schaltet die Zeit ab - auch die eigene Zeit einer Frage in Sequenzen.
    if (flow.flow === 'manual' || !flow.seconds) return 0;
    if (flow.flow === 'sequence') return question.seconds || flow.seconds;
    return flow.seconds;
  }
  function mustAnswer(question) {
    return isSession() && (question.required || flow.on_timeout === 'wait');
  }

  function restartTimer(seconds) {
    clearInterval(ticker);
    duration = seconds;
    startedAt = Date.now();
    expired = false;
    if (!duration) { el.bar.style.width = '0'; renderTimer(); return; }
    ticker = setInterval(tick, 250);
    tick();
  }
  function tick() {
    const elapsed = (Date.now() - startedAt) / 1000;
    const left = Math.max(0, duration - elapsed);
    el.bar.style.width = `${Math.min(100, (elapsed / duration) * 100)}%`;
    renderTimer(left);
    if (left <= 0 && !expired) {
      expired = true;
      onTimeout();
    }
  }
  function renderTimer(left) {
    // Countdown nur dort gross, wo er Teil des Spiels ist: Quiz und Sequenz.
    const visible = duration && (flow.flow === 'quiz' || flow.flow === 'sequence');
    const text = visible ? (expired ? 'Zeit um' : countdown(left)) : '';
    el.timer.textContent = text;
    el.impulseTimer.textContent = text;
  }

  function onTimeout() {
    clearInterval(ticker);
    if (!isSession()) { advance('timeout'); return; }
    if (mustAnswer(current) && !answerValue()) {
      el.hint.textContent = 'Zeit um – diese Frage braucht eine Antwort.';
      return;
    }
    advance('timeout');
  }

  function answerValue() {
    if (!current) return '';
    return current.choices?.length ? selected : el.answer.value.trim();
  }

  function recordAnswer() {
    if (!current || !isSession()) return;
    const answer = answerValue();
    if (!answer && flow.on_timeout === 'skip') return;
    if (!sessionHeaderWritten) {
      const time = new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date());
      appendBlock(`## ${set.label} · ${time}`);
      sessionHeaderWritten = true;
    }
    recorded.push({ position, answer, text: appendBlock(`**${current.text}**\n${answer || '–'}`) });
  }

  function advance(reason) {
    if (finished) return;
    if (current && isSession()) {
      if (reason === 'manual' && mustAnswer(current) && !answerValue()) {
        el.hint.textContent = 'Bitte zuerst antworten.';
        return;
      }
      recordAnswer();
    }
    if (current && !isSession()) dropEmptyHeading();
    position += 1;
    // Einmal durch ist Schluss - auch bei Zeitablauf und Zufallsreihenfolge -,
    // es sei denn, Frei/Automatisch sollen im Loop laufen.
    if (queue.length && position >= queue.length) {
      if (!loops()) { finish(); return; }
      buildQueue();
      position = 0;
      recorded = [];
      drafts = {};
    }
    show(queue[position]);
  }

  function show(question) {
    current = question;
    selected = '';
    el.hint.textContent = '';
    if (!question) {
      el.impulseText.textContent = 'Dieses Set hat noch keine Fragen – in den Einstellungen anlegen oder generieren.';
      return;
    }
    el.count.textContent = `${set.label} · ${position + 1} / ${queue.length}`;
    if (isSession()) {
      el.text.textContent = question.text;
      const hasChoices = Boolean(question.choices?.length);
      el.answer.classList.toggle('hidden', hasChoices);
      el.answer.value = '';
      el.choices.innerHTML = hasChoices ? question.choices.map((choice, index) =>
        `<button type="button" data-q-choice="${escapeHtml(choice)}" class="rounded-full border border-white/35 px-4 py-2 text-base text-white/90 transition hover:bg-white/15" style="${SHADOW}"><span class="mr-2 text-xs text-white/50">${index + 1}</span>${escapeHtml(choice)}</button>`).join('') : '';
      el.choices.classList.toggle('hidden', !hasChoices);
      el.next.textContent = position + 1 >= queue.length && !loops() ? 'Abschließen' : 'Weiter';
      const draft = drafts[position];
      delete drafts[position];
      if (draft && hasChoices) markChoice(draft);
      else if (draft) el.answer.value = draft;
      if (!hasChoices) el.answer.focus({ preventScroll: true });
      else el.next.focus({ preventScroll: true });
    } else {
      el.impulseText.textContent = question.text;
      if (flow.heading) {
        const value = ctx.editor?.value ?? fieldValue();
        const separator = !value ? '' : value.endsWith('\n\n') ? '' : value.endsWith('\n') ? '\n' : '\n\n';
        headingMarker = `${separator}**${question.text}**\n`;
        setFieldValue(value + headingMarker);
        ctx.editor?.focus({ preventScroll: true });
        ctx.editor?.setSelectionRange?.(ctx.editor.value.length, ctx.editor.value.length);
      }
    }
    syncBack();
    restartTimer(timeFor(question));
  }

  function syncBack() {
    const visible = canGoBack();
    const enabled = visible && position > 0;
    [el.back, el.prev].forEach((button) => {
      button.classList.toggle('hidden', !visible);
      button.disabled = !enabled;
      button.classList.toggle('opacity-30', !enabled);
    });
    if (isSession()) el.back.classList.add('hidden');
  }

  /* Eine Frage zurueck. In der Session wird die zuletzt eingetragene Antwort
     wieder aus dem Schreibfeld genommen und vorbelegt - aber nur, wenn das
     Feld noch genau damit endet; hat jemand inzwischen darin geschrieben,
     bleibt der Text stehen und die neue Antwort kommt dazu. Nach dem Ende
     fuehrt Zurueck wieder in die letzte Frage. */
  function back() {
    if (!ctx || !flow || !canGoBack() || !queue.length) return;
    if (!finished && position <= 0) return;
    if (isSession() && current && !finished) drafts[position] = answerValue();
    if (finished) {
      finished = false;
      el.next.onclick = null;
      syncControls();
      position = queue.length;
    } else if (!isSession()) {
      dropEmptyHeading();
    }
    position -= 1;
    let previous = null;
    if (isSession() && recorded.length && recorded[recorded.length - 1].position === position) {
      previous = recorded.pop();
      const value = fieldValue();
      if (value.endsWith(previous.text)) setFieldValue(value.slice(0, -previous.text.length));
    }
    show(queue[position]);
    if (previous?.answer && isSession()) {
      if (current.choices?.length) markChoice(previous.answer);
      else el.answer.value = previous.answer;
    }
  }

  function markChoice(choice) {
    selected = choice;
    el.choices.querySelectorAll('[data-q-choice]').forEach((button) => {
      const active = button.dataset.qChoice === choice;
      button.classList.toggle('bg-white/25', active);
      button.classList.toggle('border-white', active);
    });
  }

  function choose(choice) {
    if (!current?.choices?.includes(choice)) return;
    markChoice(choice);
    // Eine Auswahl ist die Antwort: kurz zeigen, dann weiter.
    setTimeout(() => { if (current && selected === choice) advance('manual'); }, 350);
  }

  function finish() {
    finished = true;
    clearInterval(ticker);
    current = null;
    el.bar.style.width = '100%';
    el.skip.classList.add('hidden');
    el.sliderWrap.classList.add('hidden');
    // Zurueck bleibt: es fuehrt wieder in die letzte Frage.
    [el.back, el.prev].forEach((button) => { button.disabled = false; button.classList.remove('opacity-30'); });
    if (isSession()) {
      el.text.textContent = 'Fertig. Deine Antworten stehen im Schreibfeld.';
      el.answer.classList.add('hidden');
      el.choices.classList.add('hidden');
      el.timer.textContent = '';
      el.hint.textContent = `${/Mac|iPhone|iPad/.test(navigator.platform || '') ? '⌘' : 'Strg'}+Enter sendet · „Weiterschreiben“ öffnet das Schreibfeld`;
      el.next.textContent = 'Weiterschreiben';
      el.next.onclick = () => { el.next.onclick = null; ctx.setMode('focus'); };
    } else {
      dropEmptyHeading();
      el.impulseText.textContent = 'Alle Fragen durch.';
      el.impulseTimer.textContent = '';
    }
  }

  function syncControls() {
    const timed = flow.flow === 'auto' || flow.flow === 'quiz';
    el.sliderWrap.classList.toggle('hidden', !timed);
    el.skip.classList.toggle('hidden', isSession());
  }

  // ─── Lebenszyklus ─────────────────────────────────────────────────
  async function start(context) {
    ctx = context;
    build(ctx.root);
    finished = false;
    el.next.onclick = null;
    sessionHeaderWritten = false;
    recorded = [];
    drafts = {};
    headingMarker = '';
    current = null;
    try {
      const response = await window.apiFetch(ENDPOINT, { cache: 'no-store' });
      data = await response.json();
    } catch (_) {
      data = { sets: [], flow: {} };
    }
    if (!ctx) return; // in der Zwischenzeit verlassen
    flow = { seconds: 90, flow: 'auto', answer: 'impulse', on_timeout: 'keep', shuffle: true, heading: true, loop: false, ...(data.flow || {}) };
    set = (data.sets || []).find((item) => item.id === flow.set_id) || (data.sets || [])[0] || { label: 'Fragen', questions: [] };
    const session = isSession();
    // Das (in der Session unsichtbare) Kulissenfeld traegt den Stand des
    // Schreibfelds - sonst schriebe Strg+Enter am Ende einen alten Text zurueck.
    if (ctx.editor) ctx.editor.value = fieldValue();
    ctx.showEditor(!session);
    el.top.classList.remove('hidden');
    el.bar.classList.remove('hidden');
    el.impulse.classList.toggle('hidden', session);
    el.session.classList.toggle('hidden', !session);
    el.session.classList.toggle('flex', session);
    syncControls();
    const step = STEPS.reduce((best, value, index) => (Math.abs(value - flow.seconds) < Math.abs(STEPS[best] - flow.seconds) ? index : best), 0);
    el.slider.value = String(step);
    el.seconds.textContent = formatSeconds(flow.seconds);
    buildQueue();
    advance('start');
  }

  function stop() {
    clearInterval(ticker);
    clearTimeout(saveTimer);
    if (ctx && flow && !isSession()) dropEmptyHeading();
    if (el) {
      ['top', 'bar', 'impulse', 'session'].forEach((key) => el[key].classList.add('hidden'));
      el.session.classList.remove('flex');
    }
    ctx = null;
    current = null;
  }

  // true, wenn die Taste hier verbraucht wurde.
  function handleKey(event) {
    if (!ctx || !flow) return false;
    if (event.altKey && event.key === 'ArrowRight') {
      event.preventDefault();
      advance('manual');
      return true;
    }
    // Alt+← immer abfangen - sonst blaettert der Browser in seiner Historie zurueck.
    if (event.altKey && event.key === 'ArrowLeft') {
      event.preventDefault();
      back();
      return true;
    }
    if (!isSession()) return false;
    if (finished) return false; // Strg+Enter sendet dann wie gewohnt
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
      event.preventDefault();
      advance('manual');
      return true;
    }
    if (current?.choices?.length && /^[1-9]$/.test(event.key) && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const choice = current.choices[Number(event.key) - 1];
      if (choice) { event.preventDefault(); choose(choice); }
      return true;
    }
    return false;
  }

  window.journlScreensaverQuestions = { start, stop, handleKey, isSession: () => Boolean(flow && isSession() && !finished) };
}());
