/* AI maintenance contract: classic script, shared by both app shells.
 *
 * Sammelmappen: die Tagesablage (was unbenutzt liegen blieb) und die
 * Themenmappen (tagesuebergreifende Behaelter fuer ein Vorhaben).
 *
 * Ein zugeordneter Journalabschnitt wird als **Verweis** gespeichert, nie als
 * Kopie — das Tagesjournal bleibt die massgebliche Quelle.
 *
 * Vertrag mit *-app-core.js: window.journlBuckets.assign(entryId) haengt den
 * Eintrag aus dem "⋯"-Menue des Heute-Tabs an eine Mappe. Zum Zurueckspringen
 * nutzt die Ansicht switchTab() und chooseDashboardJournalDate() derselben
 * Shell und sucht danach den Anker `#entry-<anchor>` im Heute-Tab.
 */
(function () {

  function escapeHtml(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttr(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  async function request(url, options) {
    const response = await window.apiFetch(url, { timeoutMs: 15000, ...(options || {}) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Aktion fehlgeschlagen');
    return data;
  }

  // ─── Zuordnung aus dem Heute-Tab ──────────────────────────────────
  // Der Dateiname eines Eintrags traegt das Datum; daraus entsteht der Verweis.
  function entryDate(entryId) {
    const element = document.getElementById(`entry-${entryId}`);
    const name = element?.dataset.filename || '';
    const match = name.match(/(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[1]}-${match[2]}-${match[3]}` : '';
  }

  // Die Notiz ist nur die Beschriftung des Verweises in der Markdown-Datei —
  // der Text selbst wird beim Anzeigen live aus dem Journal geholt.
  function entryLabel(entryId) {
    const content = document.getElementById(`entry-${entryId}`)?.dataset.content || '';
    const firstLine = content.split('\n').find((line) => line.trim()) || '';
    return firstLine.trim().slice(0, 120);
  }

  // Mappe auswaehlen oder neu anlegen; liefert den Slug oder null.
  async function pickBucket() {
    let list;
    try {
      list = (await request('/api/buckets')).items || [];
    } catch (error) {
      window.showToast?.(error.message);
      return null;
    }
    const names = list.map((item, index) => `${index + 1}. ${item.title}`).join('\n');
    const answer = window.prompt(
      `Zu welcher Sammelmappe?\n${names}\n\nNummer eingeben oder neuen Namen tippen:`, '');
    if (answer === null || !answer.trim()) return null;
    const chosen = Number.parseInt(answer.trim(), 10);
    if (Number.isInteger(chosen) && list[chosen - 1]) return list[chosen - 1].slug;
    try {
      return (await request('/api/buckets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: answer.trim() }),
      })).slug;
    } catch (error) {
      window.showToast?.(error.message);
      return null;
    }
  }

  async function addReference(payload) {
    const slug = await pickBucket();
    if (!slug) return;
    try {
      const data = await request(`/api/buckets/${encodeURIComponent(slug)}/items`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      window.showToast?.(data.duplicate
        ? 'War schon in der Mappe.' : `Zur Mappe hinzugefügt (${data.items}).`);
    } catch (error) {
      window.showToast?.(error.message);
    }
  }

  async function assign(entryId) {
    const day = entryDate(entryId);
    if (!day) {
      window.showToast?.('Zu diesem Eintrag lässt sich kein Datum bestimmen.');
      return;
    }
    await addReference({ date: day, anchor: entryId, note: entryLabel(entryId) });
  }

  // Der Brain-Treffer wird ueber Dokument und Block adressiert — dieselbe
  // Kennung, mit der die Karte auch ihren Volltext holt.
  async function assignBrain(docId, fingerprint, path, text) {
    if (!docId || !fingerprint) {
      window.showToast?.('Zu diesem Treffer fehlt die Kennung.');
      return;
    }
    const firstLine = String(text || '').split('\n').find((line) => line.trim()) || '';
    await addReference({
      kind: 'brain', doc_id: docId, fingerprint, path: path || '',
      note: firstLine.trim().slice(0, 120),
    });
  }

  // ─── Zurueck zur Journalstelle ────────────────────────────────────
  function waitForSelector(selector, timeout = 6000) {
    return new Promise((resolve) => {
      const started = Date.now();
      (function look() {
        const found = document.querySelector(selector);
        if (found || Date.now() - started > timeout) {
          resolve(found);
          return;
        }
        window.setTimeout(look, 120);
      }());
    });
  }

  function highlight(target) {
    target.scrollIntoView({ block: 'center', behavior: 'smooth' });
    target.classList.add('ring-2', 'ring-green-500');
    window.setTimeout(() => target.classList.remove('ring-2', 'ring-green-500'), 2500);
  }

  // Der Brain-Tab kennt keinen Direktlink auf einen Block. Der Verweis kommt
  // deshalb ueber die Suche zurueck: markante Woerter des Blocks eintippen und
  // die Karte mit demselben Fingerprint hervorheben.
  function searchPhrase(text) {
    return String(text || '')
      .replace(/[#*_`>\[\]()-]/g, ' ')
      .split(/\s+/).filter((word) => word.length > 2).slice(0, 6).join(' ');
  }

  async function jumpToBrain(fingerprint, hint) {
    const input = document.getElementById('brain-query');
    if (typeof window.switchTab !== 'function' || !input) {
      window.showToast?.('In dieser Ansicht lässt sich der Block nicht öffnen.');
      return;
    }
    window.closeSettings?.();
    window.switchTab('brain');
    window.setBrainMode?.('search');
    input.value = searchPhrase(hint);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const card = fingerprint
      ? await waitForSelector(`[data-brain-card][data-fingerprint="${CSS.escape(fingerprint)}"]`)
      : null;
    if (!card) {
      window.showToast?.('Brain durchsucht — der Block steht nicht unter den Treffern.');
      return;
    }
    highlight(card);
  }

  async function jumpToEntry(day, anchor) {
    if (typeof window.switchTab !== 'function'
      || typeof window.chooseDashboardJournalDate !== 'function') {
      window.showToast?.('In dieser Ansicht lässt sich der Eintrag nicht öffnen.');
      return;
    }
    window.closeSettings?.();
    // Erst das Datum, dann den Tab: beide Aufrufe laden das Dashboard, nach
    // dieser Reihenfolge aber beide denselben Tag — welcher Lauf zuerst fertig
    // wird, spielt dann keine Rolle mehr.
    window.chooseDashboardJournalDate(day);
    window.switchTab('dashboard');
    const target = anchor ? await waitForSelector(`#${CSS.escape(`entry-${anchor}`)}`) : null;
    if (!target) {
      window.showToast?.(`${formatDay(day)} geöffnet — der Eintrag selbst ist dort nicht mehr auffindbar.`);
      return;
    }
    highlight(target);
  }

  // ─── Ansicht in den Einstellungen ─────────────────────────────────
  function formatDay(day) {
    const match = String(day || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return match ? `${match[3]}.${match[2]}.${match[1]}` : String(day || '');
  }

  function formatTime(value) {
    const match = String(value || '').match(/T(\d{2}:\d{2})/);
    return match ? `${match[1]} Uhr` : '';
  }

  function countLabel(count, one, many) {
    return `${count} ${count === 1 ? one : many}`;
  }

  function bucketRow(item) {
    return `<li class="rounded-lg border border-gray-800 px-3 py-2">
      <div class="flex items-start justify-between gap-2">
        <button type="button" data-bucket-open="${escapeAttr(item.slug)}"
          class="min-w-0 flex-1 text-left">
          <p class="text-sm text-gray-200">${escapeHtml(item.title)}</p>
          <p class="text-xs text-gray-500"><span data-bucket-count="${escapeAttr(item.slug)}">${
            countLabel(item.items, 'Eintrag', 'Einträge')}</span> · zuletzt
            ${escapeHtml(formatDay(String(item.updated_at || '').slice(0, 10)))}</p>
        </button>
        <button type="button" data-bucket-delete="${escapeAttr(item.slug)}"
          data-bucket-title="${escapeAttr(item.title)}"
          class="shrink-0 rounded p-1 text-red-500 hover:bg-red-950 hover:text-red-400"
          title="Mappe löschen" aria-label="Mappe löschen">${
            window.journlIcon?.('trash', 'h-4 w-4') || ''}</button>
      </div>
      <div data-bucket-body="${escapeAttr(item.slug)}" class="hidden mt-2"></div>
    </li>`;
  }

  function dayRow(day) {
    return `<li class="rounded-lg border border-gray-800 px-3 py-2">
      <button type="button" data-day-open="${escapeAttr(day.date)}" class="text-left w-full">
        <p class="text-sm text-gray-200">${escapeHtml(formatDay(day.date))}</p>
        <p class="text-xs text-gray-500">${countLabel(day.items, 'Medium', 'Medien')}</p>
      </button>
      <div data-day-body="${escapeAttr(day.date)}" class="hidden mt-2"></div>
    </li>`;
  }

  // Der Vorschautext kommt bei jedem Oeffnen frisch aus der Quelle — die Mappe
  // haelt nur den Verweis, nie den Text.
  function referenceCard(entry, slug) {
    const brain = entry.kind === 'brain';
    const source = brain
      ? (entry.path || entry.file || entry.doc_id || 'Brain-Block')
      : formatDay(entry.date) + (entry.file ? ' · ' + entry.file : '');
    const actions = `<div class="flex shrink-0 items-center gap-1">
      <button type="button" data-entry-jump="${escapeAttr(entry.anchor || '')}"
        data-entry-date="${escapeAttr(entry.date || '')}"
        data-entry-kind="${brain ? 'brain' : 'journal'}"
        data-entry-fingerprint="${escapeAttr(entry.fingerprint || '')}"
        data-entry-hint="${escapeAttr(entry.excerpt || entry.note || '')}"
        class="inline-flex items-center gap-1 rounded border border-gray-700 px-2 py-1
          text-xs text-gray-300 hover:border-green-600 hover:text-green-400"
        title="${brain ? 'Im Brain-Tab suchen' : 'Im Heute-Tab öffnen'}">${
          window.journlIcon?.('link', 'h-3.5 w-3.5') || ''}Öffnen</button>
      <button type="button" data-entry-remove="${escapeAttr(entry.id || '')}"
        data-bucket-slug="${escapeAttr(slug)}"
        class="rounded p-1 text-gray-500 hover:bg-red-950 hover:text-red-400"
        title="Verweis entfernen" aria-label="Verweis entfernen">${
          window.journlIcon?.('close', 'h-3.5 w-3.5') || ''}</button>
    </div>`;
    const body = entry.found
      ? `<p class="mt-1 whitespace-pre-wrap break-words text-xs text-gray-300">${
          escapeHtml(entry.excerpt)}${entry.truncated ? ' …' : ''}</p>`
        + (entry.media
          ? `<p class="mt-1 text-[11px] text-gray-500">${
              countLabel(entry.media, 'Medium', 'Medien')} am Eintrag</p>` : '')
      : `<p class="mt-1 text-xs text-amber-500/80">Der Eintrag ist nicht mehr
           auffindbar — er wurde bearbeitet oder gelöscht.</p>`
        + (entry.note ? `<p class="mt-1 text-xs text-gray-500">Notiz damals: ${
            escapeHtml(entry.note)}</p>` : '');
    return `<li class="rounded border border-gray-800 bg-gray-950/60 p-2">
      <div class="flex items-start justify-between gap-2">
        <div class="min-w-0 flex-1">
          <p class="truncate text-[11px] text-gray-500">${
            brain ? 'Brain · ' : ''}${escapeHtml(source)}</p>
          ${body}
        </div>
        ${actions}
      </div>
    </li>`;
  }

  function mediaCard(day, item) {
    const base = `/api/buckets/tagesablage/${encodeURIComponent(day)}/${encodeURIComponent(item.id)}/preview`;
    const caption = `<p class="truncate text-[11px] text-gray-500" title="${
      escapeAttr(item.original_filename || '')}">${
      escapeHtml(formatTime(item.captured_at) || formatTime(item.collected_at) || '—')}</p>`;
    const viewable = (item.mime_type || '').startsWith('image/') || item.type === 'image';
    const image = viewable && (item.has_preview || item.has_file)
      ? `<button type="button" data-journal-image-viewer="${escapeAttr(base + '?full=1')}"
           data-journal-image-alt="${escapeAttr(item.original_filename || 'Medium')}"
           class="block w-full cursor-zoom-in" aria-label="Medium in der App öffnen">
           <img src="${escapeAttr(base)}" loading="lazy" alt="Vorschau"
             class="h-24 w-full rounded object-cover"></button>`
      : `<div class="flex h-24 w-full items-center justify-center rounded border border-gray-800
           text-gray-600">${window.journlIcon?.('note', 'h-5 w-5') || ''}</div>`;
    return `<figure class="space-y-1">${image}${caption}</figure>`;
  }

  async function load() {
    const host = document.getElementById('settings-buckets');
    if (!host) return;
    host.innerHTML = '<p class="text-gray-500 text-sm">Lade…</p>';
    try {
      const [buckets, days] = await Promise.all([
        request('/api/buckets'),
        request('/api/buckets/tagesablage'),
      ]);
      const mappen = (buckets.items || []).length
        ? `<ul class="space-y-2">${buckets.items.map(bucketRow).join('')}</ul>`
        : '<p class="text-gray-500 text-sm">Noch keine Themenmappe. Lege eine über das '
          + '⋯-Menü eines Eintrags im Heute-Tab an.</p>';
      const ablage = (days.days || []).length
        ? `<ul class="space-y-2">${days.days.map(dayRow).join('')}</ul>`
        : '<p class="text-gray-500 text-sm">Nichts abgelegt. Unbenutzte Screenshots wandern '
          + 'nach Mitternacht automatisch hierher.</p>';
      host.innerHTML = `<h4 class="text-xs font-semibold text-gray-400 mb-1">Themenmappen</h4>
        ${mappen}
        <h4 class="text-xs font-semibold text-gray-400 mt-4 mb-1">Tagesablage</h4>
        ${ablage}`;
    } catch (error) {
      host.innerHTML = `<p class="text-red-400 text-sm">${escapeHtml(error.message)}</p>`;
    }
  }

  function toggle(target) {
    if (!target.classList.contains('hidden')) {
      target.classList.add('hidden');
      return false;
    }
    target.classList.remove('hidden');
    target.innerHTML = '<p class="text-xs text-gray-500">Lade…</p>';
    return true;
  }

  async function openBucket(slug) {
    const target = document.querySelector(`[data-bucket-body="${CSS.escape(slug)}"]`);
    if (!target || !toggle(target)) return;
    try {
      const data = await request(`/api/buckets/${encodeURIComponent(slug)}`);
      const entries = (data.entries || []).slice().reverse();
      target.innerHTML = entries.length
        ? `<ul class="space-y-2">${entries.map((entry) => referenceCard(entry, slug)).join('')}</ul>`
        : '<p class="text-xs text-gray-500">Noch nichts zugeordnet.</p>';
    } catch (error) {
      target.innerHTML = `<p class="text-xs text-red-400">${escapeHtml(error.message)}</p>`;
    }
  }

  async function openDay(day) {
    const target = document.querySelector(`[data-day-body="${CSS.escape(day)}"]`);
    if (!target || !toggle(target)) return;
    try {
      const data = await request(`/api/buckets/tagesablage/${encodeURIComponent(day)}`);
      const items = data.items || [];
      target.innerHTML = items.length
        ? `<div class="grid grid-cols-3 gap-2 sm:grid-cols-4">${
            items.map((item) => mediaCard(day, item)).join('')}</div>`
        : '<p class="text-xs text-gray-500">Nichts an diesem Tag.</p>';
    } catch (error) {
      target.innerHTML = `<p class="text-xs text-red-400">${escapeHtml(error.message)}</p>`;
    }
  }

  // Nur die Karte verschwindet: ein Neuaufbau der Liste wuerde die gerade
  // geoeffnete Mappe wieder zuklappen.
  async function removeReference(slug, entryId, card) {
    try {
      const data = await request(
        `/api/buckets/${encodeURIComponent(slug)}/items/${encodeURIComponent(entryId)}`,
        { method: 'DELETE' });
      const list = card?.parentElement;
      card?.remove();
      if (list && !list.children.length) {
        list.outerHTML = '<p class="text-xs text-gray-500">Noch nichts zugeordnet.</p>';
      }
      const count = document.querySelector(`[data-bucket-count="${CSS.escape(slug)}"]`);
      if (count) count.textContent = countLabel(data.items || 0, 'Eintrag', 'Einträge');
      window.showToast?.('Verweis entfernt. Der Eintrag selbst bleibt bestehen.');
    } catch (error) {
      window.showToast?.(error.message);
    }
  }

  async function removeBucket(slug, title) {
    if (!window.confirm(`Sammelmappe „${title}“ wirklich löschen? `
      + 'Die verwiesenen Einträge und Medien bleiben erhalten.')) return;
    try {
      await request(`/api/buckets/${encodeURIComponent(slug)}`, { method: 'DELETE' });
      window.showToast?.('Mappe gelöscht.');
      load();
    } catch (error) {
      window.showToast?.(error.message);
    }
  }

  document.addEventListener('click', (event) => {
    const remove = event.target.closest('[data-entry-remove]');
    if (remove) {
      event.preventDefault();
      removeReference(remove.dataset.bucketSlug, remove.dataset.entryRemove,
        remove.closest('li'));
      return;
    }
    const drop = event.target.closest('[data-bucket-delete]');
    if (drop) {
      event.preventDefault();
      removeBucket(drop.dataset.bucketDelete, drop.dataset.bucketTitle);
      return;
    }
    const jump = event.target.closest('[data-entry-jump]');
    if (jump) {
      event.preventDefault();
      if (jump.dataset.entryKind === 'brain') {
        jumpToBrain(jump.dataset.entryFingerprint, jump.dataset.entryHint);
      } else {
        jumpToEntry(jump.dataset.entryDate, jump.dataset.entryJump);
      }
      return;
    }
    const bucket = event.target.closest('[data-bucket-open]');
    if (bucket) {
      event.preventDefault();
      openBucket(bucket.dataset.bucketOpen);
      return;
    }
    const day = event.target.closest('[data-day-open]');
    if (day) {
      event.preventDefault();
      openDay(day.dataset.dayOpen);
    }
  });

  document.addEventListener('journl:settings-opened', (event) => {
    if (!event.detail?.group || event.detail.group === 'buckets') load();
  });

  window.journlBuckets = { assign, assignBrain, reload: load };
})();
