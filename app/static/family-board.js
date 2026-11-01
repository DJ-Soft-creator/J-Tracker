// AI maintenance contract: classic script, shared by both app shells.
// Owns the Familie tab board: the Aufgaben/Projekte/Notizen subtabs, the
// personal and unassigned aggregate buckets, per-user pinning and the
// create/rename/assign/delete dialog. Loaded after the shell features file, so
// its loadFamily/renderFamily/toggleFamilyProject replace the older versions.
//
// Vocabulary: the UI says "Bucket", the API stays /api/family/project* — see
// docs/project/Anforderung-Familie-Tab-Neuaufbau.md, section 2.

    const familyBoardScopes = [
      { key: 'aufgaben', label: 'Aufgaben', type: 'bucket', empty: 'Noch keine Aufgaben-Buckets.' },
      { key: 'projekte', label: 'Projekte', type: 'projekt', empty: 'Noch keine Projekte.' },
      { key: 'notizen', label: 'Notizen', type: 'notiz', empty: 'Noch keine Notizen.' },
      // Kein `type`: das Archiv ist kein Dokumenttyp, sondern die Lesesicht auf
      // `family/archive/*.md`. Die Schleifen ueber die Scopes laufen deshalb ins
      // Leere (`data['archiv']` gibt es nicht) - genau richtig.
      { key: 'archiv', label: 'Archiv', readOnly: true, empty: 'Noch nichts archiviert.' },
    ];

    const familyBoard = {
      data: null,
      scope: 'aufgaben',
      expanded: new Set(),
      settings: { id: null, mode: 'edit' },
      archive: null,
    };

    function familyBoardScope(key) {
      return familyBoardScopes.find(item => item.key === key) || familyBoardScopes[0];
    }

    function familyBoardDocuments(scope) {
      const data = familyBoard.data;
      if (!data) return [];
      const documents = [];
      if (scope === 'aufgaben') {
        if (data.personal) documents.push(data.personal);
        if (data.unassigned) documents.push(data.unassigned);
      }
      return documents.concat(data[scope] || []);
    }

    // Both app shells look documents up through this helper instead of keeping
    // their own copy of the board data.
    function familyDocument(documentId) {
      for (const scope of familyBoardScopes) {
        const hit = familyBoardDocuments(scope.key).find(item => item.id === documentId);
        if (hit) return hit;
      }
      return null;
    }
    window.familyDocument = familyDocument;

    // Gestrichelter Benachrichtigungs-Indikator unter dem Familie-Tab.
    // Ein Muster aus festen Pixeln lief nie symmetrisch aus: bei 48 px (Desktop)
    // und 24 px (Mobil) endete die 12-px-Periode jeweils mit einer Lücke, die
    // Linie begann also mit einem Strich und hörte mit Leerraum auf.
    // Prozentwerte binden das Muster an die Elementbreite: bei n Strichen gibt
    // es n-1 Lücken, also 2n-1 gleich große Einheiten — damit beginnt und endet
    // die Linie in jeder Breite mit einem Strich.
    const FAMILY_NOTIFY_DASHES = 4;

    function familyNotifyDashPattern(color) {
      const unit = 100 / (FAMILY_NOTIFY_DASHES * 2 - 1);
      return `repeating-linear-gradient(to right, ${color} 0, ${color} ${unit}%,`
        + ` transparent ${unit}%, transparent ${unit * 2}%)`;
    }
    window.familyNotifyDashPattern = familyNotifyDashPattern;

    // A task assigned to me appears twice: in its source document and in my
    // personal bucket. Both copies are updated so the board stays consistent
    // without a full reload after every checkbox.
    function familyBoardApplyTaskState(taskId, completed, data) {
      if (!familyBoard.data) return;
      for (const scope of familyBoardScopes) {
        for (const document_ of familyBoardDocuments(scope.key)) {
          const task = (document_.tasks || []).find(item => item.id === taskId);
          if (!task) continue;
          task.completed = completed;
          task.completed_at = (data && data.completed_at) || null;
          task.completed_by = (data && data.completed_by) || null;
        }
      }
    }
    window.familyBoardApplyTaskState = familyBoardApplyTaskState;

    async function loadFamily() {
      const container = document.getElementById('family-container');
      if (!container) return;
      container.innerHTML = '<p class="text-gray-500 text-center mt-8">Lade…</p>';
      try {
        const res = await apiFetch('/api/family/board');
        if (res.status === 401) { showLogin(); return; }
        if (!res.ok) {
          const error = await res.json().catch(() => ({}));
          container.innerHTML = `<p class="text-red-400 text-center mt-8">${escapeHtml(error.error || 'Fehler beim Laden.')}</p>`;
          return;
        }
        familyBoard.data = await res.json();
        // Angelegte, umbenannte oder gelöschte Listen sollen im Schreiben-Tab
        // beim nächsten Öffnen der Zielauswahl bereits stimmen.
        window.writeTargetsResetShoppingLists?.();
        renderFamilySubtabs();
        renderFamily();
      } catch (err) {
        container.innerHTML = `<p class="text-red-400 text-center mt-8">Fehler: ${escapeHtml(err.message)}</p>`;
      }
    }

    function renderFamilySubtabs() {
      const bar = document.getElementById('family-subtabs');
      if (!bar) return;
      let html = '';
      for (const scope of familyBoardScopes) {
        const active = scope.key === familyBoard.scope;
        // Die Leiste ist der meistgenutzte Umschalter im Familien-Tab und wurde
        // mit reiner Textbreite auf dem Telefon regelmaessig verfehlt. Padding
        // statt groesserer Schrift: die Trefferflaeche waechst, die Zeile
        // bleibt einzeilig.
        const classes = active
          ? 'px-1.5 py-1.5 text-[15px] font-medium text-green-400 border-b-2 border-green-500'
          : 'px-1.5 py-1.5 text-[15px] text-gray-500 hover:text-gray-300 border-b-2 border-transparent';
        html += `<button type="button" onclick="familyBoardSwitch('${scope.key}')" class="${classes}">${escapeHtml(scope.label)}</button>`;
      }
      bar.innerHTML = html;
      const scope = familyBoardScope(familyBoard.scope);
      const newButton = document.getElementById('family-new');
      if (newButton) {
        // Im Archiv gibt es nichts anzulegen; der Knopf verschwindet, statt eine
        // Aktion anzubieten, die ins Leere laeuft.
        newButton.hidden = !!scope.readOnly;
        newButton.title = 'Neu anlegen: ' + scope.label;
      }
    }

    function familyBoardSwitch(scopeKey) {
      familyBoard.scope = familyBoardScope(scopeKey).key;
      renderFamilySubtabs();
      renderFamily();
      if (familyBoard.scope === 'archiv' && familyBoard.archive === null) loadFamilyArchive();
    }

    function familyBoardMatches(document_, query) {
      if (!query) return true;
      if ((document_.title || '').toLowerCase().includes(query)) return true;
      if (familyBoardNotesText(document_).toLowerCase().includes(query)) return true;
      for (const task of (document_.tasks || [])) {
        if ((task.title || '').toLowerCase().includes(query)) return true;
        if ((task.origin_title || '').toLowerCase().includes(query)) return true;
        if ((task.user_display || '').toLowerCase().includes(query)) return true;
      }
      return false;
    }

    function familyBoardIsShopping(document_) {
      return document_.template_id === 'einkaufsliste'
        || (document_.target_file || '').endsWith('einkaufsliste.md');
    }

    // Projekte und Notizen sind Fliesstext mit Aufgaben darin: sie werden als
    // Markdown im Schreiben-Tab bearbeitet, nicht im Aufgaben-Editor. Nur bei
    // ihnen oeffnet deshalb auch ein Tipp in die Karte den Editor.
    function familyBoardUsesMarkdownEditor(document_) {
      if (document_.aggregate || document_.personal) return false;
      if (document_.id === 'wiederkehrende-aufgaben') return false;
      if (familyBoardIsShopping(document_)) return false;
      return document_.type === 'projekt' || document_.type === 'notiz';
    }

    function familyBoardText(value) {
      const text = String(value || '');
      if (!text) return '';
      return window.renderJournalText ? window.renderJournalText(text) : escapeHtml(text);
    }

    function familyBoardSortTasks(tasks) {
      return [...(tasks || [])].sort((a, b) => {
        const aOrder = Number.isFinite(Number(a.sort_index)) ? Number(a.sort_index) : Number.MAX_SAFE_INTEGER;
        const bOrder = Number.isFinite(Number(b.sort_index)) ? Number(b.sort_index) : Number.MAX_SAFE_INTEGER;
        if (aOrder !== bOrder) return aOrder - bOrder;
        if (Boolean(a.completed) !== Boolean(b.completed)) return a.completed ? 1 : -1;
        return (a.target_date || '9999-12-31').localeCompare(b.target_date || '9999-12-31');
      });
    }

    function familyBoardActions(document_) {
      const id = escapeHtmlAttr(document_.id || '');
      const isShopping = familyBoardIsShopping(document_);
      const isRecurring = document_.id === 'wiederkehrende-aufgaben';
      if (document_.aggregate) return '';
      if (isRecurring) {
        return '<button type="button" onclick="openPlanner()" class="text-green-500 hover:text-green-400 text-xs">Pläne verwalten</button>';
      }
      const actions = [];
      if (!document_.personal) {
        const pinned = document_.pinned ? 'text-green-400' : 'text-gray-500 hover:text-gray-300';
        const pinTitle = document_.pinned ? 'Nicht mehr anheften' : 'Oben anheften';
        actions.push(`<button type="button" onclick="familyBoardTogglePin('${id}')" class="${pinned} text-xs" title="${pinTitle}">${window.journlIcon?.('pin', 'h-4 w-4') || ''}</button>`);
      }
      if (isShopping) {
        actions.push(`<button type="button" data-menu-label="Agent öffnen" onclick="openShoppingAgent('${id}')" class="text-gray-400 hover:text-green-400 text-xs" title="Liste von der KI sortieren oder ändern">${window.journlIcon?.('agent', 'h-4 w-4') || ''}</button>`);
        actions.push(`<button type="button" data-menu-label="Liste aufräumen" onclick="cleanShoppingList('${id}')" class="text-gray-400 hover:text-red-400 text-xs" title="Abgehakte Artikel entfernen oder leere Liste">${window.journlIcon?.('listClean', 'h-4 w-4') || ''}</button>`);
      }
      const useMarkdownEditor = familyBoardUsesMarkdownEditor(document_);
      actions.push(useMarkdownEditor
        ? `<button type="button" data-menu-label="Text bearbeiten" onclick="openFamilyWriteEditor('${id}')" class="text-green-500 hover:text-green-400 text-xs" title="Als Markdown im Schreiben-Tab bearbeiten">${window.journlIcon?.('pencil', 'h-4 w-4') || ''}</button>`
        : `<button type="button" onclick="openEditor('${id}','${isShopping ? 'compact' : 'full'}')" class="text-green-500 hover:text-green-400 text-xs" title="Inhalt bearbeiten">${window.journlIcon?.('pencil', 'h-4 w-4') || ''}</button>`);
      if (useMarkdownEditor) {
        // Im Markdown-Editor ist eine Aufgabe nur eine Zeile: sie bekommt
        // weder Person noch Termin und landet damit in "Nicht zugeordnet".
        // Der Aufgaben-Editor bleibt deshalb als zweiter Weg erreichbar; er
        // laesst den freien Notiztext (`notes`) unangetastet.
        actions.push(`<button type="button" data-menu-label="Aufgaben verwalten" onclick="openEditor('${id}','full')" class="text-gray-400 hover:text-green-400 text-xs" title="Aufgaben verwalten: Person und Termin zuordnen">${window.journlIcon?.('checkbox', 'h-4 w-4') || ''}</button>`);
      }
      if (!document_.personal) {
        actions.push(`<button type="button" data-menu-label="Einstellungen" onclick="familyBoardOpenSettings('${id}')" class="text-gray-400 hover:text-gray-200 text-xs" title="Bearbeiten: Titel, Typ, Berechtigung">${window.journlIcon?.('gear', 'h-4 w-4') || ''}</button>`);
      }
      if (document_.type === 'projekt') {
        actions.push(`<button type="button" data-menu-label="Projekt abschließen" onclick="familyBoardComplete('${id}')" class="text-gray-400 hover:text-green-400 text-xs" title="Projekt abschließen und archivieren">${window.journlIcon?.('checkCircle', 'h-4 w-4') || ''}</button>`);
      }
      if (document_.can_delete) {
        actions.push(`<button type="button" data-menu-variant="danger" onclick="familyBoardDelete('${id}')" class="text-gray-500 hover:text-red-400 text-xs" title="Löschen">${window.journlIcon?.('trash', 'h-4 w-4') || ''}</button>`);
      }
      return (window.actionMenu || ((parts) => parts.join('')))(actions, { label: 'Aktionen' });
    }

    function familyBoardTitle(document_) {
      const title = escapeHtml(document_.title || '');
      if (document_.aggregate) {
        return `<h3 class="text-sm font-semibold text-gray-300">${title}</h3>`;
      }
      if (familyBoardIsShopping(document_)) {
        return `<button type="button" onclick="openShopping('${escapeHtmlAttr(document_.id || '')}')" class="min-w-0 max-w-full text-left text-sm font-semibold text-gray-200 hover:text-green-400 break-words">${title}</button>`;
      }
      return `<h3 class="text-sm font-semibold text-gray-200 min-w-0 break-words">${title}</h3>`;
    }

    function familyBoardTaskRow(document_, task, today) {
      const originId = task.origin_id || document_.id;
      const checked = task.completed ? 'checked' : '';
      const strike = task.completed ? 'line-through text-gray-500' : 'text-gray-200';
      const dateClass = !task.completed && task.target_date && task.target_date <= today ? 'text-red-400' : 'text-gray-600';
      const dateStr = task.target_date ? ` · ${escapeHtml(task.target_date)}` : '';
      const userStr = task.user_display ? ` · <span class="inline-flex items-center gap-1 align-middle">${window.journlIcon?.('user', 'h-3 w-3') || ''}${escapeHtml(task.user_display)}</span>` : '';
      const recurrenceStr = task.source === 'recurring' ? ` · ${escapeHtml(task.recurrence_label || 'Wiederkehrend')}` : '';
      // Herkunft nur zeigen, wenn die Aufgabe aus einem anderen Dokument kommt.
      // Ist das Dokument nicht freigegeben, bleibt es beim Titel ohne Zugriff.
      let originStr = '';
      if (task.origin_title && originId !== document_.id) {
        const lock = task.origin_accessible === false ? ' ' + (window.journlIcon?.('lock', 'h-3 w-3 inline align-middle') || '') : '';
        originStr = ` · <span class="inline-flex items-center gap-1 align-middle">${window.journlIcon?.('folder', 'h-3 w-3') || ''}${escapeHtml(task.origin_title)}${lock}</span>`;
      }
      const box = `<input type="checkbox" ${checked} onchange="checkFamilyTask('${escapeHtmlAttr(originId || '')}','${escapeHtmlAttr(task.id || '')}',this)" class="h-5 w-5 accent-green-500 disabled:opacity-60" />`;
      const text = `<span class="flex-1 min-w-0 break-words">${familyBoardText(task.title)}<span class="${dateClass} text-xs">${dateStr}${userStr}${recurrenceStr}${originStr}</span></span>`;
      // Abgehakt wird ausschliesslich ueber das Kaestchen: beim Scrollen durch
      // eine lange Aufgabenliste traf ein Tipp auf den Text sonst die ganze
      // Zeile und erledigte versehentlich eine Aufgabe. Das Label umschliesst
      // nur die Checkbox und vergroessert deren Trefferflaeche.
      return `<div class="flex items-start gap-2 text-sm ${strike}">
        <label class="-my-0.5 flex shrink-0 cursor-pointer p-1" aria-label="Erledigt">${box}</label>
        ${text}
      </div>`;
    }

    // Der freie Notiztext einer Notiz oder eines Projekts. `notes` steht in der
    // Datei ueber der Aufgabenliste, `notes_after` darunter - die Karte haelt
    // diese Reihenfolge ein, sonst laese sich die Vorschau anders als das
    // Dokument.
    function familyBoardNotes(document_, position, expanded) {
      const text = String((position === 'after' ? document_.notes_after : document_.notes) || '').trim();
      if (!text) return '';
      const clamp = expanded ? '' : ' family-notes-clamp';
      const spacing = position === 'after'
        ? 'mt-2 border-t border-gray-800/60 pt-2'
        : 'mb-2 border-b border-gray-800/60 pb-2';
      return `<div class="family-notes${clamp} ${spacing} text-sm text-gray-400 whitespace-pre-wrap break-words">${familyBoardText(text)}</div>`;
    }

    // Was unter "## Kommentare" steht, ist in Notizen fast immer schlicht
    // weitergeschriebener Text: der Editor zeigt die Ueberschrift unten, man
    // tippt dort weiter. Auf der Karte gehoert er deshalb zum Inhalt und nicht
    // hinter den Aufgaben-Editor versteckt. Verfasser und Zeitpunkt bleiben
    // dort, wo Kommentare gepflegt werden.
    function familyBoardCommentText(document_) {
      return (document_.comments || [])
        .map(comment => String(comment.text || '').trim())
        .filter(Boolean)
        .join('\n');
    }

    function familyBoardComments(document_, expanded) {
      const text = familyBoardCommentText(document_);
      if (!text) return '';
      const clamp = expanded ? '' : ' family-notes-clamp';
      return `<div class="family-notes${clamp} mt-2 border-t border-gray-800/60 pt-2 text-sm text-gray-400 whitespace-pre-wrap break-words">${familyBoardText(text)}</div>`;
    }

    function familyBoardNotesText(document_) {
      return [document_.notes, document_.notes_after, familyBoardCommentText(document_)]
        .map(value => String(value || '').trim())
        .filter(Boolean)
        .join('\n');
    }

    function familyBoardNotesAreLong(document_) {
      const notes = familyBoardNotesText(document_);
      return notes.length > 220 || notes.split('\n').length > 4;
    }

    function familyBoardCard(document_, query) {
      const id = escapeHtmlAttr(document_.id || '');
      const badges = [];
      if (document_.pinned) badges.push('<span class="text-[10px] text-green-500">angeheftet</span>');
      if ((document_.assigned_users || []).length) badges.push('<span class="text-[10px] text-gray-500">eingeschränkt</span>');
      // Ein Tipp in den Inhaltsbereich oeffnet denselben Markdown-Editor wie
      // "Text bearbeiten" im ⋯-Menue; das Menue bleibt als zweiter Weg.
      const tapOpensEditor = familyBoardUsesMarkdownEditor(document_);
      const bodyAttributes = tapOpensEditor
        ? ` data-family-edit="${id}" class="space-y-1.5 family-card-body" title="Zum Bearbeiten tippen"`
        : ' class="space-y-1.5"';
      let html = `<div class="bg-gray-900 rounded-xl p-4 border border-gray-800">
        <div class="flex items-start justify-between gap-2 mb-3">
          <div class="min-w-0 flex items-center gap-2 flex-wrap">${familyBoardTitle(document_)}${badges.join('')}</div>
          <div class="flex items-center gap-2 shrink-0">${familyBoardActions(document_)}</div>
        </div>
        <div${bodyAttributes}>`;
      let tasks = familyBoardSortTasks(document_.tasks);
      // Trifft schon der Titel des Buckets, bleibt sein ganzer Inhalt stehen —
      // sonst stünde ein gefundener Bucket ohne Aufgaben da.
      if (query && !(document_.title || '').toLowerCase().includes(query)) {
        tasks = tasks.filter(task => (task.title || '').toLowerCase().includes(query));
      }
      if (!tasks.length && !familyBoardNotesText(document_)) {
        html += `<p class="text-gray-600 text-xs">${document_.type === 'notiz' ? 'Keine Einträge.' : 'Keine Aufgaben.'}</p>`;
      }
      const MAX_TASKS = 5;
      const expanded = familyBoard.expanded.has(document_.id);
      html += familyBoardNotes(document_, 'before', expanded);
      const shown = expanded ? tasks : tasks.slice(0, MAX_TASKS);
      const today = new Date().toLocaleDateString('sv-SE');
      let lastGroup = '__none__';
      for (const task of shown) {
        const group = task.gruppe || '';
        if (group && group !== lastGroup) {
          html += `<div class="flex items-center gap-2 text-xs font-semibold text-gray-500 pt-1">${window.journlGroupDot?.(group) || ''}<span>${escapeHtml(group)}</span></div>`;
          lastGroup = group;
        } else if (!group && lastGroup !== '__none__') {
          lastGroup = '__none__';
        }
        html += familyBoardTaskRow(document_, task, today);
      }
      html += familyBoardNotes(document_, 'after', expanded);
      html += familyBoardComments(document_, expanded);
      const hiddenTasks = tasks.length - MAX_TASKS;
      if (hiddenTasks > 0 || familyBoardNotesAreLong(document_)) {
        const label = expanded
          ? 'Weniger anzeigen'
          : (hiddenTasks > 0 ? `+${hiddenTasks} weitere anzeigen` : 'Mehr anzeigen');
        html += `<button type="button" onclick="toggleFamilyProject('${id}')" class="text-xs text-green-500 hover:text-green-400 mt-1">${label}</button>`;
      }
      html += '</div></div>';
      return html;
    }

    function renderFamily() {
      const container = document.getElementById('family-container');
      if (!container) return;
      if (!familyBoard.data) return;
      const query = (document.getElementById('family-search')?.value || '').trim().toLowerCase();
      const scope = familyBoardScope(familyBoard.scope);
      if (scope.key === 'archiv') { renderFamilyArchive(container, scope, query); return; }
      let documents = familyBoardDocuments(scope.key);
      if (query) documents = documents.filter(item => familyBoardMatches(item, query));
      if (!documents.length) {
        const message = query ? 'Keine Treffer.' : scope.empty;
        container.innerHTML = `<p class="text-gray-500 text-center mt-8">${escapeHtml(message)}</p>`;
        return;
      }
      container.innerHTML = documents.map(item => familyBoardCard(item, query)).join('');
    }

    // ─── Archiv (Lesesicht) ─────────────────────────────────────────
    // Erledigte Aufgaben wandern nach sieben Tagen automatisch in
    // `family/archive/` und waren bis 4.7.16 nirgends mehr sichtbar: die Daten
    // sammelten sich an, ohne dass jemand sie wieder aufmachen konnte. Diese
    // Sicht liest sie nur - archiviert wird weiterhin ausschliesslich serverseitig.
    async function loadFamilyArchive() {
      try {
        const res = await apiFetch('/api/family/archive');
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Archiv konnte nicht geladen werden', true); return; }
        familyBoard.archive = Array.isArray(data.items) ? data.items : [];
      } catch {
        showToast('Netzwerkfehler beim Laden des Archivs', true);
        familyBoard.archive = [];
      }
      if (familyBoard.scope === 'archiv') renderFamily();
    }

    function familyArchiveProjectTitle(item) {
      const known = familyDocument(item.project_id);
      if (known && known.title) return known.title;
      if (item.project_id === 'wiederkehrende-aufgaben') return 'Wiederkehrende Aufgaben';
      return item.project_id || '';
    }

    function familyArchiveDay(value) {
      if (!value) return '';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
      return date.toLocaleDateString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric' });
    }

    function familyArchiveMatches(item, query) {
      if (!query) return true;
      const person = item.completed_by_display || item.user_display || '';
      return [item.title, familyArchiveProjectTitle(item), person]
        .some(value => (value || '').toLowerCase().includes(query));
    }

    // Erledigt, aber noch nicht archiviert (bis zu sieben Tage): oben im
    // Archiv mit „Zurücksetzen“. Kommt mit dem Board vom Server und
    // uebersteht damit Tab-Wechsel und Neustart.
    function familyRecentlyCompletedHtml(query) {
      const items = (familyBoard.data?.recently_completed || []).filter(item => familyArchiveMatches(
        { title: item.title, project_id: item.origin_id, completed_by_display: item.completed_by_display }, query));
      if (!items.length) return '';
      const rows = items.map(item => {
        const meta = [item.origin_title, item.completed_by_display, familyArchiveDay(item.completed_at)]
          .filter(Boolean).map(escapeHtml).join(' · ');
        return `<div class="flex items-start gap-2 px-3 py-2">
          <div class="min-w-0 flex-1">
            <div class="text-[15px] text-gray-300 line-through decoration-gray-600 break-words">${escapeHtml(item.title)}</div>
            <div class="mt-0.5 text-xs text-gray-500">${meta}</div>
          </div>
          <button type="button" onclick="familyBoardReopenTask('${escapeHtmlAttr(item.origin_id || '')}','${escapeHtmlAttr(item.id || '')}',this)"
                  class="shrink-0 rounded-md border border-gray-700 px-2 py-1 text-xs text-gray-300 hover:border-green-600 hover:text-green-400 disabled:opacity-50">Zurücksetzen</button>
        </div>`;
      }).join('');
      return `<section class="mb-4">
        <h3 class="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Zuletzt erledigt</h3>
        <div class="divide-y divide-gray-800 rounded-xl border border-gray-800 bg-gray-900">${rows}</div>
      </section>`;
    }

    async function familyBoardReopenTask(projectId, taskId, button) {
      if (button) button.disabled = true;
      try {
        const res = await apiFetch('/api/family/task/check', {
          method: 'POST',
          body: JSON.stringify({ project_id: projectId, task_id: taskId, completed: false })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || 'Zurücksetzen fehlgeschlagen');
        showToast('Wieder offen');
        await loadFamily();
        window.checkFamilyNotifications?.();
      } catch (error) {
        if (button) button.disabled = false;
        showToast(error.message || 'Netzwerkfehler', true);
      }
    }
    window.familyBoardReopenTask = familyBoardReopenTask;

    function renderFamilyArchive(container, scope, query) {
      const recent = familyRecentlyCompletedHtml(query);
      if (familyBoard.archive === null) {
        container.innerHTML = recent + '<p class="text-gray-500 text-center mt-8">Lade…</p>';
        return;
      }
      // Neueste zuerst: ein Archiv liest man von hinten.
      const items = familyBoard.archive
        .filter(item => familyArchiveMatches(item, query))
        .slice()
        .sort((a, b) => String(b.completed_at || '').localeCompare(String(a.completed_at || '')));
      if (!items.length) {
        container.innerHTML = recent + `<p class="text-gray-500 text-center mt-8">${escapeHtml(query ? 'Keine Treffer.' : scope.empty)}</p>`;
        return;
      }
      const icon = (name, size) => window.journlIcon?.(name, size) || '';
      container.innerHTML = recent + items.map(item => {
        const person = item.completed_by_display || item.user_display || '';
        const meta = [];
        meta.push(`<span class="inline-flex items-center gap-1 align-middle">${icon('folder', 'h-3 w-3')}${escapeHtml(familyArchiveProjectTitle(item))}</span>`);
        if (person) meta.push(`<span class="inline-flex items-center gap-1 align-middle">${icon('user', 'h-3 w-3')}${escapeHtml(person)}</span>`);
        const done = familyArchiveDay(item.completed_at);
        if (done) meta.push(`<span class="inline-flex items-center gap-1 align-middle">${icon('checkCircle', 'h-3 w-3')}${escapeHtml(done)}</span>`);
        return `<div class="bg-gray-900 border border-gray-800 rounded-xl px-3 py-2">
          <div class="text-[15px] text-gray-300 line-through decoration-gray-600">${escapeHtml(item.title || '')}</div>
          <div class="mt-1 text-xs text-gray-500 flex flex-wrap items-center gap-x-2 gap-y-1">${meta.join('<span class="text-gray-700">·</span>')}</div>
        </div>`;
      }).join('');
    }

    function toggleFamilyProject(documentId) {
      if (familyBoard.expanded.has(documentId)) familyBoard.expanded.delete(documentId);
      else familyBoard.expanded.add(documentId);
      renderFamily();
    }

    // ─── Anheften ───────────────────────────────────────────────────
    async function familyBoardTogglePin(documentId) {
      const scope = familyBoard.scope;
      const current = familyBoardDocuments(scope)
        .filter(item => item.pinned && !item.personal && !item.aggregate)
        .map(item => item.id);
      const next = current.includes(documentId)
        ? current.filter(id => id !== documentId)
        : current.concat(documentId);
      try {
        const res = await apiFetch('/api/family/pins', {
          method: 'PUT',
          body: JSON.stringify({ scope, ids: next }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Anheften fehlgeschlagen', true); return; }
        loadFamily();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    // ─── Anlegen / Bearbeiten ───────────────────────────────────────
    function familyBoardSettingsModal() {
      let modal = document.getElementById('family-settings-modal');
      if (modal) return modal;
      modal = document.createElement('div');
      modal.id = 'family-settings-modal';
      modal.className = 'fixed inset-0 z-50 hidden items-center justify-center px-4 bg-black/50';
      modal.innerHTML = `<div class="w-full max-w-md bg-gray-900 border border-gray-800 rounded-xl shadow-xl max-h-[90vh] overflow-y-auto">
        <div class="flex items-center justify-between px-5 py-3 border-b border-gray-800">
          <h2 id="family-settings-title" class="text-lg font-semibold text-gray-100">Bearbeiten</h2>
          <button type="button" id="family-settings-close" class="text-gray-500 hover:text-white text-xl leading-none">×</button>
        </div>
        <div class="p-5 space-y-4">
          <div>
            <label for="family-settings-name" class="block text-xs text-gray-400 mb-1">Titel</label>
            <input id="family-settings-name" type="text" maxlength="200" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500" />
          </div>
          <div>
            <label for="family-settings-type" class="block text-xs text-gray-400 mb-1">Typ</label>
            <select id="family-settings-type" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500">
              <option value="aufgabe">Aufgabe (einzelner Eintrag)</option>
              <option value="bucket">Aufgaben-Bucket</option>
              <option value="projekt">Projekt (hat ein Ende)</option>
              <option value="notiz">Notiz (bleibt dauerhaft)</option>
            </select>
          </div>
          <div id="family-settings-task-fields" class="hidden space-y-4">
            <div>
              <label for="family-settings-task-target" class="block text-xs text-gray-400 mb-1">Wohin</label>
              <select id="family-settings-task-target" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500"></select>
            </div>
            <div>
              <label for="family-settings-task-user" class="block text-xs text-gray-400 mb-1">Zuständig</label>
              <select id="family-settings-task-user" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500"></select>
            </div>
            <div>
              <label for="family-settings-task-date" class="block text-xs text-gray-400 mb-1">Fällig am (optional)</label>
              <input type="date" id="family-settings-task-date" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500" />
            </div>
          </div>
          <div id="family-settings-document-fields" class="space-y-4">
            <label class="flex items-start gap-2 text-sm text-gray-300">
              <input type="checkbox" id="family-settings-exclude" class="mt-0.5 accent-green-500" />
              <span>Aufgaben nicht in „Meine Aufgaben“ sammeln
                <span class="block text-[11px] text-gray-600">Für Einkaufslisten voreingestellt: Artikel bleiben in ihrer Liste.</span>
              </span>
            </label>
            <div>
              <span class="block text-xs text-gray-400 mb-1">Sichtbar für</span>
              <p id="family-settings-visibility-hint" class="text-[11px] text-gray-600 mb-1"></p>
              <div id="family-settings-users" class="space-y-1 max-h-40 overflow-y-auto"></div>
            </div>
          </div>
        </div>
        <div class="flex justify-end gap-2 px-5 py-3 border-t border-gray-800">
          <button type="button" id="family-settings-cancel" class="px-3 py-2 text-sm text-gray-400 hover:text-gray-200">Abbrechen</button>
          <button type="button" id="family-settings-save" class="px-3 py-2 text-sm font-semibold rounded-lg bg-green-600 hover:bg-green-500 text-white">Speichern</button>
        </div>
      </div>`;
      document.body.appendChild(modal);
      modal.querySelector('#family-settings-close').addEventListener('click', familyBoardCloseSettings);
      modal.querySelector('#family-settings-cancel').addEventListener('click', familyBoardCloseSettings);
      modal.querySelector('#family-settings-save').addEventListener('click', familyBoardSaveSettings);
      modal.querySelector('#family-settings-type').addEventListener('change', familyBoardApplyDialogMode);
      modal.addEventListener('click', (event) => {
        if (event.target === modal) familyBoardCloseSettings();
      });
      return modal;
    }

    // Dokumente, in die eine einzelne Aufgabe geschrieben werden darf. Der
    // Planner-Bucket fehlt bewusst: dessen Aufgaben erzeugt der Scheduler.
    function familyBoardTaskTargets() {
      const data = familyBoard.data;
      if (!data) return [];
      const targets = [];
      if (data.personal) targets.push({ id: data.personal.id, label: 'Meine Aufgaben' });
      for (const scope of familyBoardScopes) {
        for (const document_ of (data[scope.key] || [])) {
          if (document_.aggregate || document_.id === 'wiederkehrende-aufgaben') continue;
          targets.push({ id: document_.id, label: `${scope.label.replace(/n$/, '')}: ${document_.title || document_.id}` });
        }
      }
      return targets;
    }

    function familyBoardApplyDialogMode() {
      const isTask = document.getElementById('family-settings-type').value === 'aufgabe';
      document.getElementById('family-settings-task-fields').classList.toggle('hidden', !isTask);
      document.getElementById('family-settings-document-fields').classList.toggle('hidden', isTask);
      document.querySelector('label[for="family-settings-name"]').textContent = isTask ? 'Aufgabe' : 'Titel';
      if (!isTask) return;
      const targetSelect = document.getElementById('family-settings-task-target');
      const previous = targetSelect.value;
      targetSelect.innerHTML = familyBoardTaskTargets()
        .map(target => `<option value="${escapeHtmlAttr(target.id)}">${escapeHtml(target.label)}</option>`)
        .join('');
      if (Array.from(targetSelect.options).some(option => option.value === previous)) {
        targetSelect.value = previous;
      }
      const userSelect = document.getElementById('family-settings-task-user');
      const users = (familyBoard.data && familyBoard.data.users) || [];
      userSelect.innerHTML = '<option value="">— niemand —</option>'
        + users.map(user => `<option value="${escapeHtmlAttr(user.id)}">${escapeHtml(user.display || user.id)}</option>`).join('');
    }

    function familyBoardRenderUserChecks(selected, enabled) {
      const host = document.getElementById('family-settings-users');
      const users = (familyBoard.data && familyBoard.data.users) || [];
      const chosen = new Set(selected || []);
      let html = '';
      for (const user of users) {
        const checked = chosen.has(user.id) ? ' checked' : '';
        const disabled = enabled ? '' : ' disabled';
        html += `<label class="flex items-center gap-2 text-sm text-gray-300">
          <input type="checkbox" value="${escapeHtmlAttr(user.id)}" class="family-settings-user accent-green-500"${checked}${disabled} />
          <span>${escapeHtml(user.display || user.id)}</span>
        </label>`;
      }
      host.innerHTML = html || '<p class="text-xs text-gray-600">Keine Benutzer gefunden.</p>';
      document.getElementById('family-settings-visibility-hint').textContent = enabled
        ? 'Niemand ausgewählt = für alle sichtbar.'
        : 'Die Zuordnung ändert nur ein Administrator.';
    }

    function familyBoardOpenSettings(documentId) {
      const document_ = familyDocument(documentId);
      if (!document_) return;
      const modal = familyBoardSettingsModal();
      familyBoard.settings = { id: documentId, mode: 'edit' };
      document.getElementById('family-settings-title').textContent = 'Bearbeiten';
      document.getElementById('family-settings-name').value = document_.title || '';
      document.getElementById('family-settings-type').value = document_.type || 'bucket';
      document.getElementById('family-settings-exclude').checked = Boolean(document_.exclude_from_personal);
      document.querySelector('#family-settings-type option[value="aufgabe"]').disabled = true;
      familyBoardApplyDialogMode();
      familyBoardRenderUserChecks(document_.assigned_users, Boolean(familyBoard.data?.is_admin));
      modal.classList.remove('hidden');
      modal.classList.add('flex');
    }

    function familyBoardNew() {
      if (!familyBoard.data) return;
      const modal = familyBoardSettingsModal();
      familyBoard.settings = { id: null, mode: 'create' };
      const scope = familyBoardScope(familyBoard.scope);
      document.getElementById('family-settings-title').textContent = 'Neu';
      document.getElementById('family-settings-name').value = '';
      document.querySelector('#family-settings-type option[value="aufgabe"]').disabled = false;
      document.getElementById('family-settings-type').value =
        scope.key === 'aufgaben' ? 'aufgabe' : scope.type;
      document.getElementById('family-settings-exclude').checked = false;
      document.getElementById('family-settings-task-date').value = '';
      familyBoardApplyDialogMode();
      familyBoardRenderUserChecks([], Boolean(familyBoard.data.is_admin));
      modal.classList.remove('hidden');
      modal.classList.add('flex');
      document.getElementById('family-settings-name').focus();
    }

    function familyBoardCloseSettings() {
      const modal = document.getElementById('family-settings-modal');
      if (!modal) return;
      modal.classList.add('hidden');
      modal.classList.remove('flex');
      familyBoard.settings = { id: null, mode: 'edit' };
    }

    async function familyBoardSaveSettings() {
      const title = document.getElementById('family-settings-name').value.trim();
      const type = document.getElementById('family-settings-type').value;
      if (!title) {
        showToast(type === 'aufgabe' ? 'Bitte eine Aufgabe eingeben' : 'Bitte einen Titel eingeben', true);
        return;
      }
      if (type === 'aufgabe') { familyBoardSaveTask(title); return; }
      const isAdmin = Boolean(familyBoard.data?.is_admin);
      const assigned = Array.from(document.querySelectorAll('.family-settings-user'))
        .filter(input => input.checked)
        .map(input => input.value);
      const creating = familyBoard.settings.mode === 'create';
      const payload = {
        title,
        type,
        exclude_from_personal: document.getElementById('family-settings-exclude').checked,
      };
      if (isAdmin) payload.assigned_users = assigned;
      if (creating) payload.template_id = type;
      try {
        const res = await apiFetch(
          creating ? '/api/family/project' : '/api/family/project/' + familyBoard.settings.id,
          { method: creating ? 'POST' : 'PUT', body: JSON.stringify(payload) }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Speichern fehlgeschlagen', true); return; }
        showToast(creating ? 'Angelegt' : 'Gespeichert');
        familyBoardCloseSettings();
        familyBoard.scope = familyBoardScope(
          familyBoardScopes.find(item => item.type === type)?.key || familyBoard.scope
        ).key;
        loadFamily();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    async function familyBoardSaveTask(title) {
      const targetId = document.getElementById('family-settings-task-target').value;
      if (!targetId) { showToast('Bitte ein Ziel wählen', true); return; }
      try {
        const res = await apiFetch('/api/family/project/' + targetId + '/task', {
          method: 'POST',
          body: JSON.stringify({
            title,
            user: document.getElementById('family-settings-task-user').value || '',
            target_date: document.getElementById('family-settings-task-date').value || '',
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Aufgabe konnte nicht gespeichert werden', true); return; }
        showToast('Aufgabe angelegt');
        familyBoardCloseSettings();
        familyBoard.scope = 'aufgaben';
        loadFamily();
        checkFamilyNotifications();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    async function familyBoardDelete(documentId) {
      const document_ = familyDocument(documentId);
      if (!document_) return;
      const label = document_.type === 'notiz' ? 'Notiz' : (document_.type === 'projekt' ? 'Projekt' : 'Bucket');
      if (!window.confirm(`${label} „${document_.title || ''}“ endgültig löschen? Alle enthaltenen Aufgaben gehen verloren.`)) return;
      try {
        const res = await apiFetch('/api/family/project/' + documentId, { method: 'DELETE' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Löschen fehlgeschlagen', true); return; }
        showToast('Gelöscht');
        loadFamily();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    async function familyBoardComplete(documentId) {
      const document_ = familyDocument(documentId);
      if (!document_) return;
      if (!window.confirm(`Projekt „${document_.title || ''}“ abschließen? Es wandert ins Archiv und bleibt über die Brain-Suche auffindbar.`)) return;
      try {
        const res = await apiFetch('/api/family/project/' + documentId + '/complete', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { showToast(data.error || 'Abschließen fehlgeschlagen', true); return; }
        showToast('Projekt abgeschlossen');
        loadFamily();
      } catch {
        showToast('Netzwerkfehler', true);
      }
    }

    document.getElementById('family-search')?.addEventListener('input', renderFamily);

    // Delegiert, weil `renderFamily` den Inhalt des Containers komplett ersetzt.
    // Bedienelemente und Links behalten ihre eigene Wirkung; alles andere im
    // Inhaltsbereich einer Projekt-/Notizkarte oeffnet den Markdown-Editor.
    document.getElementById('family-container')?.addEventListener('click', (event) => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      const body = target.closest('[data-family-edit]');
      if (!body) return;
      if (target.closest('a, button, input, label, select, textarea')) return;
      if (window.getSelection && String(window.getSelection()).trim()) return;
      const documentId = body.getAttribute('data-family-edit');
      if (documentId) window.openFamilyWriteEditor?.(documentId);
    });
    document.getElementById('family-new')?.addEventListener('click', familyBoardNew);
