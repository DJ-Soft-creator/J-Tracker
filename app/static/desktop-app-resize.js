// AI maintenance contract: classic scripts share one global app shell.
// Preserve template load order; move whole top-level functions, never partial bodies.
    // ─── settings: my templates ──────────────────────────────────────
    // Anzeige und Verwaltung leben in template-manager.js, damit beide
    // App-Shells eine Implementierung teilen. Quelle ist dort die rohe
    // config.json ueber /api/settings/templates, nicht das env-injizierte
    // /api/config.
    function loadSettingsTemplates() {
      const list = document.getElementById('settings-templates-list');
      if (!list) return;
      if (window.journlTemplates) { window.journlTemplates.render(); return; }
      list.innerHTML = '<p class="text-gray-500">Vorlagenverwaltung ist nicht geladen.</p>';
    }

    // ─── settings: my snippets ────────────────────────────────────────
    // Anzeige und Verwaltung leben in snippet-manager.js, gleiches Muster
    // wie bei den Templates.
    function loadSettingsSnippets() {
      const list = document.getElementById('settings-snippets-list');
      if (!list) return;
      if (window.journlSnippets) { window.journlSnippets.render(); return; }
      list.innerHTML = '<p class="text-gray-500">Abkürzungsverwaltung ist nicht geladen.</p>';
    }

    // ─── ESC handler ─────────────────────────────────────────────────
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeShopping();
        closeEditor();
        closeSettings();
        if (burgerMenu) burgerMenu.classList.add('hidden');
      }
    });

    // ─── clock (server time via API) ──────────────────────────────
    async function updateClock() {
      try {
        const res = await apiFetch('/api/time');
        if (res.ok) {
          const data = await res.json();
          document.getElementById('clock').textContent = data.time;
          return;
        }
      } catch {
        // ignore
      }
      // Fallback to local time
      const now = new Date();
      const h = String(now.getHours()).padStart(2, '0');
      const m = String(now.getMinutes()).padStart(2, '0');
      document.getElementById('clock').textContent = `${h}:${m}`;
    }
    updateClock();
    setInterval(updateClock, 60000);

    // ─── centered desktop input-panel resizing ──────────────────────
    const WRITE_PANEL_STORAGE_KEY = 'journl.desktop.write-panel-size.v1';
    const WRITE_PANEL_MIN_HEIGHT = 160;
    const writePanelHandles = Array.from(document.querySelectorAll('.input-resize-handle'));
    let desiredWritePanelSize = readStoredWritePanelSize();
    let renderedWritePanelSize = { width: null, height: null };
    let activeWritePanelResize = null;
    let writePanelResizeFrame = 0;

    function isValidPanelDimension(value) {
      return typeof value === 'number' && Number.isFinite(value) && value > 0;
    }

    function readStoredWritePanelSize() {
      try {
        const raw = localStorage.getItem(WRITE_PANEL_STORAGE_KEY);
        if (!raw) return { width: null, height: null };
        const stored = JSON.parse(raw);
        return {
          width: isValidPanelDimension(stored && stored.width) ? stored.width : null,
          height: isValidPanelDimension(stored && stored.height) ? stored.height : null,
        };
      } catch {
        return { width: null, height: null };
      }
    }

    function persistDesiredWritePanelSize() {
      if (!isValidPanelDimension(desiredWritePanelSize.width) || !isValidPanelDimension(desiredWritePanelSize.height)) return;
      try {
        localStorage.setItem(WRITE_PANEL_STORAGE_KEY, JSON.stringify(desiredWritePanelSize));
      } catch {
        // Resizing still works when storage is blocked or full.
      }
    }

    function clampPanelDimension(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function getWritePanelBounds() {
      const app = document.getElementById('app');
      const writeTab = document.getElementById('tab-write');
      const main = document.querySelector('main');
      if (!inputArea || !app || !writeTab || !main || app.classList.contains('hidden')) return null;

      const visualViewport = window.visualViewport;
      const viewportWidth = visualViewport && isValidPanelDimension(visualViewport.width)
        ? visualViewport.width
        : window.innerWidth;
      const viewportHeight = visualViewport && isValidPanelDimension(visualViewport.height)
        ? visualViewport.height
        : window.innerHeight;
      const viewportLeft = visualViewport && Number.isFinite(visualViewport.offsetLeft)
        ? visualViewport.offsetLeft
        : 0;
      const viewportTop = visualViewport && Number.isFinite(visualViewport.offsetTop)
        ? visualViewport.offsetTop
        : 0;
      const boundsElement = writeTab.clientWidth > 0 && writeTab.clientHeight > 0 ? writeTab : main;
      const boundsRect = boundsElement.getBoundingClientRect();
      let availableWidth = boundsElement.clientWidth || boundsRect.width || viewportWidth;
      let availableHeight = boundsElement.clientHeight || boundsRect.height || viewportHeight;

      if (visualViewport) {
        const visibleLeft = Math.max(boundsRect.left, viewportLeft);
        const visibleRight = Math.min(boundsRect.right, viewportLeft + viewportWidth);
        const visibleTop = Math.max(boundsRect.top, viewportTop);
        const visibleBottom = Math.min(boundsRect.bottom, viewportTop + viewportHeight);
        const visibleWidth = Math.max(0, visibleRight - visibleLeft);
        const visibleHeight = Math.max(0, visibleBottom - visibleTop);
        const centerX = boundsRect.left + (boundsRect.width / 2);
        const centeredHalfWidth = Math.min(centerX - visibleLeft, visibleRight - centerX);

        availableWidth = Math.min(
          availableWidth,
          centeredHalfWidth > 0 ? centeredHalfWidth * 2 : (visibleWidth || viewportWidth)
        );
        availableHeight = Math.min(availableHeight, visibleHeight || viewportHeight);
      }

      availableWidth = Math.max(1, Math.floor(availableWidth));
      availableHeight = Math.max(1, Math.floor(availableHeight));

      const sideMargin = Math.min(24, Math.max(8, availableWidth * 0.02));
      const maxWidth = Math.max(1, availableWidth - (sideMargin * 2));
      const minWidth = Math.min(280, maxWidth);
      const defaultWidth = clampPanelDimension(availableWidth - 100, minWidth, maxWidth);

      const controlsReserve = Math.min(140, Math.max(64, availableHeight * 0.2));
      const preferredMaxHeight = Math.min(
        availableHeight - controlsReserve,
        viewportHeight * 0.75
      );
      // On very short viewports the panel deliberately overflows the scrollable
      // Write tab so the editor keeps usable space above the 50px handle row.
      const minHeight = WRITE_PANEL_MIN_HEIGHT;
      const maxHeight = Math.max(minHeight, Math.round(preferredMaxHeight));
      const defaultHeight = clampPanelDimension(availableHeight * 0.35, minHeight, maxHeight);

      return { minWidth, maxWidth, minHeight, maxHeight, defaultWidth, defaultHeight };
    }

    function renderWritePanelSize(width, height) {
      const bounds = getWritePanelBounds();
      if (!bounds) return null;

      const nextWidth = isValidPanelDimension(width) ? width : bounds.defaultWidth;
      const nextHeight = isValidPanelDimension(height) ? height : bounds.defaultHeight;
      renderedWritePanelSize = {
        width: Math.round(clampPanelDimension(nextWidth, bounds.minWidth, bounds.maxWidth)),
        height: Math.round(clampPanelDimension(nextHeight, bounds.minHeight, bounds.maxHeight)),
      };
      inputArea.style.width = renderedWritePanelSize.width + 'px';
      inputArea.style.height = renderedWritePanelSize.height + 'px';
      return renderedWritePanelSize;
    }

    function renderDesiredWritePanelSize() {
      return renderWritePanelSize(desiredWritePanelSize.width, desiredWritePanelSize.height);
    }

    function commitRenderedWritePanelSize(changedWidth, changedHeight) {
      if (!changedWidth && !changedHeight) return;
      if (!isValidPanelDimension(renderedWritePanelSize.width) || !isValidPanelDimension(renderedWritePanelSize.height)) return;

      desiredWritePanelSize = {
        width: changedWidth || !isValidPanelDimension(desiredWritePanelSize.width)
          ? renderedWritePanelSize.width
          : desiredWritePanelSize.width,
        height: changedHeight || !isValidPanelDimension(desiredWritePanelSize.height)
          ? renderedWritePanelSize.height
          : desiredWritePanelSize.height,
      };
      persistDesiredWritePanelSize();
    }

    function scheduleWritePanelClamp() {
      if (writePanelResizeFrame) cancelAnimationFrame(writePanelResizeFrame);
      writePanelResizeFrame = requestAnimationFrame(() => {
        writePanelResizeFrame = 0;
        renderDesiredWritePanelSize();
      });
    }

    function finishWritePanelResize(event) {
      if (!activeWritePanelResize) return;
      if (event && event.pointerId !== undefined && event.pointerId !== activeWritePanelResize.pointerId) return;

      const resize = activeWritePanelResize;
      activeWritePanelResize = null;
      resize.handle.classList.remove('dragging');
      inputArea.classList.remove('resizing');
      document.body.style.userSelect = resize.previousUserSelect;
      document.body.style.cursor = resize.previousCursor;
      try {
        if (resize.handle.hasPointerCapture(resize.pointerId)) {
          resize.handle.releasePointerCapture(resize.pointerId);
        }
      } catch {
        // Capture can already be gone after pointercancel or window blur.
      }

      if (event && event.type === 'pointerup') {
        commitRenderedWritePanelSize(resize.changedWidth, resize.changedHeight);
      } else {
        renderDesiredWritePanelSize();
      }
    }

    function startWritePanelResize(event) {
      if (!event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return;
      event.preventDefault();
      finishWritePanelResize();

      const handle = event.currentTarget;
      const rect = inputArea.getBoundingClientRect();
      activeWritePanelResize = {
        handle,
        pointerId: event.pointerId,
        axis: handle.dataset.resizeAxis,
        startX: event.clientX,
        startY: event.clientY,
        startWidth: rect.width,
        startHeight: rect.height,
        changedWidth: false,
        changedHeight: false,
        previousUserSelect: document.body.style.userSelect,
        previousCursor: document.body.style.cursor,
      };

      try {
        handle.setPointerCapture(event.pointerId);
      } catch {
        finishWritePanelResize();
        return;
      }
      handle.classList.add('dragging');
      inputArea.classList.add('resizing');
      document.body.style.userSelect = 'none';
      document.body.style.cursor = getComputedStyle(handle).cursor;
    }

    function moveWritePanelResize(event) {
      const resize = activeWritePanelResize;
      if (!resize || event.pointerId !== resize.pointerId) return;
      event.preventDefault();

      let width = resize.startWidth;
      let height = resize.startHeight;
      if (resize.axis.includes('x')) {
        // X and XY sit on the right; doubling the delta moves both centered edges
        // symmetrically while the right edge follows the pointer distance.
        width += (event.clientX - resize.startX) * 2;
      }
      if (resize.axis.includes('y')) height += event.clientY - resize.startY;
      const renderedSize = renderWritePanelSize(width, height);
      if (!renderedSize) return;
      resize.changedWidth = resize.axis.includes('x') && renderedSize.width !== Math.round(resize.startWidth);
      resize.changedHeight = resize.axis.includes('y') && renderedSize.height !== Math.round(resize.startHeight);
    }

    function resizeWritePanelWithKeyboard(event) {
      const axis = event.currentTarget.dataset.resizeAxis;
      const step = event.shiftKey ? 40 : 12;
      const currentSize = isValidPanelDimension(renderedWritePanelSize.width)
        && isValidPanelDimension(renderedWritePanelSize.height)
        ? renderedWritePanelSize
        : renderDesiredWritePanelSize();
      if (!currentSize) return;

      let width = currentSize.width;
      let height = currentSize.height;
      let changesWidth = false;
      let changesHeight = false;

      if (axis.includes('x') && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        width += event.key === 'ArrowRight' ? step : -step;
        changesWidth = true;
      }
      if (axis.includes('y') && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        height += event.key === 'ArrowDown' ? step : -step;
        changesHeight = true;
      }
      if (!changesWidth && !changesHeight) return;
      event.preventDefault();
      const renderedSize = renderWritePanelSize(width, height);
      if (!renderedSize) return;
      commitRenderedWritePanelSize(
        changesWidth && renderedSize.width !== currentSize.width,
        changesHeight && renderedSize.height !== currentSize.height
      );
    }

    writePanelHandles.forEach(handle => {
      handle.addEventListener('pointerdown', startWritePanelResize);
      handle.addEventListener('pointermove', moveWritePanelResize);
      handle.addEventListener('pointerup', finishWritePanelResize);
      handle.addEventListener('pointercancel', finishWritePanelResize);
      handle.addEventListener('lostpointercapture', finishWritePanelResize);
      handle.addEventListener('keydown', resizeWritePanelWithKeyboard);
    });

    function handleWritePanelViewportResize() {
      finishWritePanelResize();
      scheduleWritePanelClamp();
    }

    window.addEventListener('blur', finishWritePanelResize);
    window.addEventListener('resize', handleWritePanelViewportResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleWritePanelViewportResize);
      window.visualViewport.addEventListener('scroll', handleWritePanelViewportResize);
    }
    scheduleWritePanelClamp();
