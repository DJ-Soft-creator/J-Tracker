// Journl Aktionsmenü: bündelt die kleinen Icon-Buttons oben rechts auf einer
// Karte (Bearbeiten, Anheften, Löschen, Verwalten …) hinter einem dezenten
// "⋯"-Button. Die Aufrufer behalten ihre bestehenden Buttons — sie werden
// unsichtbar in den Wrapper gerendert, die Menüzeilen leiten den Klick an sie
// weiter. Dadurch bleiben onclick-Handler, data-Attribute und die Position im
// DOM (wichtig für closest()) unverändert.
(function () {
  const TRIGGER_CLASS = 'inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-transparent '
    + 'text-gray-500 transition hover:border-gray-700 hover:bg-gray-800 hover:text-gray-200 '
    + 'focus:outline-none focus-visible:ring-1 focus-visible:ring-gray-600';
  const TRIGGER_OPEN_CLASS = ['border-gray-700', 'bg-gray-800', 'text-gray-200'];
  const POPUP_CLASS = 'fixed z-[60] min-w-[12rem] max-w-[17rem] rounded-xl border border-gray-700 bg-gray-900 p-1 '
    + 'shadow-xl shadow-black/50 origin-top-right transition duration-100 ease-out';
  const ROW_CLASS = 'w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-[15px] text-gray-300 '
    + 'transition hover:bg-gray-800 hover:text-white focus:outline-none focus-visible:bg-gray-800 disabled:opacity-40';
  const ROW_DANGER_CLASS = 'w-full flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left text-[15px] text-gray-300 '
    + 'transition hover:bg-red-950 hover:text-red-300 focus:outline-none focus-visible:bg-red-950 disabled:opacity-40';

  // Zeigergeräte öffnen das Menü schon beim Hovern. Auf Touch gibt es kein
  // Hovern, dort bleibt es beim Tippen.
  const HOVER_QUERY = window.matchMedia?.('(hover: hover) and (pointer: fine)');
  const HOVER_OPEN_DELAY = 120;
  const HOVER_CLOSE_DELAY = 280;
  // Die Hoverzone ist bewusst größer als das Menü: Der Weg vom "⋯"-Knopf zur
  // gewünschten Zeile führt oft knapp an der Kante vorbei, und ein Menü, das
  // dabei zuklappt, ist unbenutzbar.
  const HOVER_PADDING = 28;

  let openMenu = null;
  let openedByHover = false;
  let hoverOpenTimer = null;
  let hoverCloseTimer = null;
  let pendingTrigger = null;

  function escapeAttr(text) {
    return String(text == null ? '' : text)
      .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /**
   * Baut den Menü-Wrapper. `parts` sind fertige Button-HTML-Schnipsel.
   * Bei höchstens einer Aktion bleibt der Button unverändert stehen — ein
   * Menü mit einem einzigen Eintrag wäre nur ein zusätzlicher Klick.
   */
  window.actionMenu = function actionMenu(parts, options) {
    const settings = options || {};
    const items = (Array.isArray(parts) ? parts : [parts])
      .filter((part) => typeof part === 'string' && part.trim());
    if (items.length <= 1) return items[0] || '';
    const label = escapeAttr(settings.label || 'Aktionen');
    return `<div class="relative inline-flex shrink-0" data-action-menu>`
      + `<button type="button" data-action-menu-trigger class="${TRIGGER_CLASS}" aria-haspopup="menu" aria-expanded="false" title="${label}" aria-label="${label}">`
      + `<svg viewBox="0 0 24 24" class="h-[18px] w-[18px] pointer-events-none" fill="currentColor" aria-hidden="true">`
      + `<circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/></svg>`
      + `</button>`
      + `<div data-action-menu-items class="hidden">${items.join('')}</div>`
      + `</div>`;
  };

  function sourceLabel(source) {
    return source.dataset.menuLabel
      || source.getAttribute('title')
      || source.getAttribute('aria-label')
      || source.textContent.trim()
      || 'Aktion';
  }

  function sourceIcon(source) {
    const svg = source.querySelector('svg');
    if (svg) return svg.outerHTML;
    const hidden = source.querySelector('[aria-hidden="true"]');
    if (hidden) return hidden.innerHTML;
    const text = source.textContent.trim();
    // Emoji-Buttons tragen ihr Icon als Text; längere Beschriftungen sind
    // bereits das Label und werden nicht doppelt gezeigt.
    return Array.from(text).length <= 2 ? text : '';
  }

  function buildRow(source) {
    const row = document.createElement('button');
    row.type = 'button';
    row.setAttribute('role', 'menuitem');
    row.className = source.dataset.menuVariant === 'danger' ? ROW_DANGER_CLASS : ROW_CLASS;
    if (source.disabled) row.disabled = true;
    const icon = document.createElement('span');
    icon.className = 'inline-flex h-4 w-4 shrink-0 items-center justify-center text-sm leading-none';
    icon.setAttribute('aria-hidden', 'true');
    icon.innerHTML = sourceIcon(source);
    const text = document.createElement('span');
    text.className = 'min-w-0 flex-1 break-words';
    text.textContent = sourceLabel(source);
    row.append(icon, text);
    row.addEventListener('click', () => {
      closeActionMenu();
      // Erst schließen, dann klicken: Der Originalbutton hängt weiterhin in
      // seiner Karte, damit closest()/parentElement wie gewohnt greifen.
      source.click();
    });
    return row;
  }

  function placePopup(popup, trigger) {
    const rect = trigger.getBoundingClientRect();
    const size = popup.getBoundingClientRect();
    const margin = 8;
    let left = rect.right - size.width;
    left = Math.min(Math.max(left, margin), Math.max(margin, window.innerWidth - size.width - margin));
    let top = rect.bottom + 6;
    if (top + size.height > window.innerHeight - margin) {
      const above = rect.top - size.height - 6;
      top = above >= margin ? above : Math.max(margin, window.innerHeight - size.height - margin);
    }
    popup.style.left = `${Math.round(left)}px`;
    popup.style.top = `${Math.round(top)}px`;
  }

  function hoverEnabled() {
    return Boolean(HOVER_QUERY && HOVER_QUERY.matches);
  }

  function cancelHoverOpen() {
    if (hoverOpenTimer) clearTimeout(hoverOpenTimer);
    hoverOpenTimer = null;
    pendingTrigger = null;
  }

  function cancelHoverClose() {
    if (hoverCloseTimer) clearTimeout(hoverCloseTimer);
    hoverCloseTimer = null;
  }

  function scheduleHoverClose() {
    if (!openedByHover || hoverCloseTimer) return;
    hoverCloseTimer = window.setTimeout(() => {
      hoverCloseTimer = null;
      closeActionMenu();
    }, HOVER_CLOSE_DELAY);
  }

  /** Liegt der Zeiger im erweiterten Bereich um Knopf oder Menü? */
  function pointerInHoverZone(event) {
    if (!openMenu) return false;
    return [openMenu.popup, openMenu.trigger].some((element) => {
      if (!element.isConnected) return false;
      const rect = element.getBoundingClientRect();
      return event.clientX >= rect.left - HOVER_PADDING && event.clientX <= rect.right + HOVER_PADDING
        && event.clientY >= rect.top - HOVER_PADDING && event.clientY <= rect.bottom + HOVER_PADDING;
    });
  }

  function closeActionMenu() {
    cancelHoverOpen();
    cancelHoverClose();
    openedByHover = false;
    if (!openMenu) return;
    const { trigger, popup, observer } = openMenu;
    openMenu = null;
    if (observer) observer.disconnect();
    popup.remove();
    if (trigger.isConnected) {
      trigger.setAttribute('aria-expanded', 'false');
      trigger.classList.remove(...TRIGGER_OPEN_CLASS);
    }
  }

  function openActionMenu(trigger, options) {
    const wrapper = trigger.closest('[data-action-menu]');
    const holder = wrapper && wrapper.querySelector('[data-action-menu-items]');
    if (!holder) return;
    // Versteckte Quellen bleiben draussen: ein Aufrufer, der Eintraege je nach
    // Zustand ein- und ausblendet, soll sie nicht trotzdem im Menue sehen.
    const sources = Array.from(holder.querySelectorAll(':scope > button, :scope > a'))
      .filter((source) => !source.hidden && !source.classList.contains('hidden'));
    if (!sources.length) return;

    const popup = document.createElement('div');
    popup.className = `${POPUP_CLASS} opacity-0 scale-95`;
    popup.setAttribute('role', 'menu');
    popup.dataset.actionMenuPopup = '';
    sources.forEach((source) => popup.appendChild(buildRow(source)));
    document.body.appendChild(popup);
    placePopup(popup, trigger);
    requestAnimationFrame(() => {
      popup.classList.remove('opacity-0', 'scale-95');
      popup.classList.add('opacity-100', 'scale-100');
    });

    trigger.setAttribute('aria-expanded', 'true');
    trigger.classList.add(...TRIGGER_OPEN_CLASS);

    // Wird die Karte neu gerendert (Familie/Brain/Heute laden neu), verliert
    // der Trigger seinen Platz im DOM — dann darf kein Menü stehen bleiben.
    const observer = new MutationObserver(() => {
      if (!trigger.isConnected) closeActionMenu();
    });
    observer.observe(document.body, { childList: true, subtree: true });

    openMenu = { trigger, popup, observer };
    // Beim Hovern nicht fokussieren: Das würde die Seite springen lassen und
    // die Tastaturposition des Nutzers ohne dessen Zutun verschieben.
    if (!options || options.focus !== false) popup.querySelector('button:not([disabled])')?.focus();
  }

  function focusRow(offset) {
    if (!openMenu) return;
    const rows = Array.from(openMenu.popup.querySelectorAll('button:not([disabled])'));
    if (!rows.length) return;
    const current = rows.indexOf(document.activeElement);
    const next = (current + offset + rows.length + (current === -1 ? 1 : 0)) % rows.length;
    rows[next].focus();
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest?.('[data-action-menu-trigger]');
    if (trigger) {
      event.preventDefault();
      event.stopPropagation();
      const wasOpen = openMenu && openMenu.trigger === trigger;
      // Ein per Hover geöffnetes Menü darf der Klick nicht wieder zuklappen —
      // er übernimmt es und verankert es, bis geklickt oder weggeklickt wird.
      const claimHovered = wasOpen && openedByHover;
      if (claimHovered) {
        cancelHoverClose();
        openedByHover = false;
        openMenu.popup.querySelector('button:not([disabled])')?.focus();
        return;
      }
      closeActionMenu();
      if (!wasOpen) openActionMenu(trigger);
      return;
    }
    if (openMenu && !event.target.closest?.('[data-action-menu-popup]')) closeActionMenu();
  });

  document.addEventListener('keydown', (event) => {
    if (!openMenu) return;
    if (event.key === 'Escape') {
      const { trigger } = openMenu;
      closeActionMenu();
      if (trigger.isConnected) trigger.focus();
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      focusRow(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      focusRow(-1);
    }
  });

  document.addEventListener('pointerover', (event) => {
    if (!hoverEnabled() || (event.pointerType && event.pointerType !== 'mouse')) return;
    // Der Wrapper zaehlt mit: er traegt einen schmalen Rand um den Knopf
    // (CSS), damit das Menue schon knapp neben der Kartenecke aufzieht.
    const wrapper = event.target.closest?.('[data-action-menu]');
    const trigger = event.target.closest?.('[data-action-menu-trigger]')
      || wrapper?.querySelector(':scope > [data-action-menu-trigger]');
    if (trigger) {
      cancelHoverClose();
      if ((openMenu && openMenu.trigger === trigger) || pendingTrigger === trigger) return;
      cancelHoverOpen();
      pendingTrigger = trigger;
      hoverOpenTimer = window.setTimeout(() => {
        hoverOpenTimer = null;
        pendingTrigger = null;
        if (!trigger.isConnected) return;
        closeActionMenu();
        openedByHover = true;
        openActionMenu(trigger, { focus: false });
      }, HOVER_OPEN_DELAY);
      return;
    }
    // Der Zeiger hat den Knopf verlassen, bevor das Menü überhaupt erschien.
    if (pendingTrigger && !event.target.closest?.('[data-action-menu-popup]')) cancelHoverOpen();
  });

  document.addEventListener('pointermove', (event) => {
    if (!openedByHover || !openMenu) return;
    if (pointerInHoverZone(event)) cancelHoverClose();
    else scheduleHoverClose();
  });

  // Verlässt der Zeiger das Fenster, kommt kein pointermove mehr - ohne das
  // hier bliebe ein per Hover geöffnetes Menü stehen.
  document.documentElement.addEventListener('pointerleave', scheduleHoverClose);

  window.addEventListener('scroll', () => closeActionMenu(), true);
  window.addEventListener('resize', () => closeActionMenu());
  window.closeActionMenu = closeActionMenu;
})();
