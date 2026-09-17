/* In-app image frame shared by Today and Brain journal cards.

   The mobile shell pins the viewport (`user-scalable=no`), so the browser's own
   pinch-zoom never reaches a photo. The viewer therefore brings its own
   gestures: pinch, drag and double-tap, all anchored on the point being
   touched, so a photo can actually be read on a phone. */
(function () {
  const MAX_SCALE = 6;
  const DOUBLE_TAP_SCALE = 2.5;

  let viewer = null;
  let stage = null;
  let image = null;
  let previousFocus = null;

  let scale = 1;
  let translateX = 0;
  let translateY = 0;
  const pointers = new Map();
  let gesture = null;
  let panned = false;
  let lastTapAt = 0;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /* Keep the photo inside the stage: at scale 1 it stays centred, and zoomed in
     it may only travel as far as the part hidden beyond the edge. */
  function clampTranslation() {
    if (!stage || !image) return;
    const frame = stage.getBoundingClientRect();
    const overflowX = Math.max(0, (image.offsetWidth * scale - frame.width) / 2);
    const overflowY = Math.max(0, (image.offsetHeight * scale - frame.height) / 2);
    translateX = clamp(translateX, -overflowX, overflowX);
    translateY = clamp(translateY, -overflowY, overflowY);
  }

  function applyTransform() {
    clampTranslation();
    image.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
    image.style.cursor = scale > 1 ? 'grab' : 'zoom-in';
    stage.dataset.zoomed = scale > 1 ? 'true' : 'false';
  }

  function resetTransform() {
    scale = 1;
    translateX = 0;
    translateY = 0;
    if (image) applyTransform();
  }

  // Point relative to the stage centre, which is also the transform origin.
  function stagePoint(clientX, clientY) {
    const frame = stage.getBoundingClientRect();
    return { x: clientX - frame.left - frame.width / 2, y: clientY - frame.top - frame.height / 2 };
  }

  function zoomTo(nextScale, anchor) {
    const target = clamp(nextScale, 1, MAX_SCALE);
    // The content under the anchor has to stay under it while the scale changes.
    const contentX = (anchor.x - translateX) / scale;
    const contentY = (anchor.y - translateY) / scale;
    scale = target;
    translateX = anchor.x - contentX * target;
    translateY = anchor.y - contentY * target;
    if (target === 1) { translateX = 0; translateY = 0; }
    applyTransform();
  }

  function midpoint() {
    const [first, second] = [...pointers.values()];
    return stagePoint((first.x + second.x) / 2, (first.y + second.y) / 2);
  }

  function spread() {
    const [first, second] = [...pointers.values()];
    return Math.hypot(first.x - second.x, first.y - second.y) || 1;
  }

  function beginGesture() {
    gesture = pointers.size >= 2
      ? { kind: 'pinch', spread: spread(), anchor: midpoint(), scale, translateX, translateY }
      : { kind: 'pan', point: [...pointers.values()][0], translateX, translateY };
  }

  function onPointerDown(event) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    stage.setPointerCapture?.(event.pointerId);
    panned = false;
    beginGesture();
  }

  function onPointerMove(event) {
    if (!pointers.has(event.pointerId) || !gesture) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (gesture.kind === 'pinch' && pointers.size >= 2) {
      event.preventDefault();
      panned = true;
      const anchor = midpoint();
      const next = clamp(gesture.scale * (spread() / gesture.spread), 1, MAX_SCALE);
      const contentX = (gesture.anchor.x - gesture.translateX) / gesture.scale;
      const contentY = (gesture.anchor.y - gesture.translateY) / gesture.scale;
      scale = next;
      translateX = anchor.x - contentX * next;
      translateY = anchor.y - contentY * next;
      applyTransform();
      return;
    }
    if (gesture.kind !== 'pan' || scale <= 1) return;
    event.preventDefault();
    const current = [...pointers.values()][0];
    const dx = current.x - gesture.point.x;
    const dy = current.y - gesture.point.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) panned = true;
    translateX = gesture.translateX + dx;
    translateY = gesture.translateY + dy;
    applyTransform();
  }

  function onPointerUp(event) {
    pointers.delete(event.pointerId);
    stage.releasePointerCapture?.(event.pointerId);
    if (pointers.size) { beginGesture(); return; }
    gesture = null;
    if (scale === 1) { translateX = 0; translateY = 0; applyTransform(); }
  }

  function onTap(event) {
    if (panned) return;
    const now = Date.now();
    const isDoubleTap = now - lastTapAt < 320;
    // Restart the counter after a hit, so a third tap is a fresh first one
    // instead of toggling the zoom again.
    lastTapAt = isDoubleTap ? 0 : now;
    if (!isDoubleTap) return;
    event.preventDefault();
    zoomTo(scale > 1 ? 1 : DOUBLE_TAP_SCALE, stagePoint(event.clientX, event.clientY));
  }

  function onWheel(event) {
    event.preventDefault();
    zoomTo(scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15), stagePoint(event.clientX, event.clientY));
  }

  function closeImageViewer() {
    if (!viewer || viewer.classList.contains('hidden')) return;
    viewer.classList.add('hidden');
    image?.removeAttribute('src');
    pointers.clear();
    gesture = null;
    resetTransform();
    previousFocus?.focus?.({ preventScroll: true });
    previousFocus = null;
  }

  function ensureViewer() {
    if (viewer) return viewer;
    viewer = document.createElement('div');
    viewer.id = 'journal-image-viewer';
    viewer.className = 'hidden fixed inset-0 z-[60] flex items-center justify-center bg-black/75 p-3 sm:p-6';
    viewer.setAttribute('role', 'dialog');
    viewer.setAttribute('aria-modal', 'true');
    viewer.setAttribute('aria-label', 'Bildansicht');
    viewer.innerHTML = '<div class="relative flex max-h-full max-w-full flex-col rounded-xl border border-gray-700 bg-gray-950 p-2 shadow-2xl">'
      + '<button type="button" data-journal-image-close class="absolute right-3 top-3 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-gray-900/90 text-xl leading-none text-gray-200 shadow hover:bg-gray-700 hover:text-white" aria-label="Bild schließen" title="Schließen">×</button>'
      // The stage owns the gestures, so the browser must not claim them itself.
      + '<div data-journal-image-stage class="relative overflow-hidden rounded-lg" style="touch-action:none;">'
      /* Die Hoehe laesst oben und unten je einen Safe-Area-Streifen plus Rand
         frei. Der Rahmen ist vertikal zentriert, also rutscht der
         Schliessen-Button dadurch unter Notch/Statusleiste der Fullscreen-App. */
      + '<img data-journal-image class="select-none rounded-lg object-contain" style="transform-origin:50% 50%; will-change:transform;'
      + ' max-height:calc(100vh - 2 * env(safe-area-inset-top, 0px) - 4.5rem);'
      + ' max-height:calc(100dvh - 2 * env(safe-area-inset-top, 0px) - 4.5rem);'
      + ' max-width:calc(100vw - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px) - 2.5rem);'
      + ' max-width:calc(100dvw - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px) - 2.5rem);" draggable="false" alt="">'
      + '</div>'
      + '<p class="pointer-events-none pt-1 text-center text-[11px] text-gray-500">Doppeltippen, Mausrad oder zwei Finger zum Zoomen</p>'
      + '</div>';
    stage = viewer.querySelector('[data-journal-image-stage]');
    image = viewer.querySelector('[data-journal-image]');

    stage.addEventListener('pointerdown', onPointerDown);
    stage.addEventListener('pointermove', onPointerMove, { passive: false });
    stage.addEventListener('pointerup', onPointerUp);
    stage.addEventListener('pointercancel', onPointerUp);
    stage.addEventListener('click', onTap);
    stage.addEventListener('wheel', onWheel, { passive: false });
    stage.addEventListener('dblclick', (event) => event.preventDefault());
    viewer.addEventListener('click', (event) => {
      // A zoomed photo keeps the backdrop inert; otherwise panning would close it.
      if (event.target.closest('[data-journal-image-close]')) return closeImageViewer();
      if (event.target === viewer && scale === 1) closeImageViewer();
    });
    document.body.appendChild(viewer);
    return viewer;
  }

  function openImageViewer(src, alt, trigger) {
    if (!src) return;
    const dialog = ensureViewer();
    previousFocus = trigger || document.activeElement;
    resetTransform();
    image.src = src;
    image.alt = alt || 'Foto';
    dialog.classList.remove('hidden');
    dialog.querySelector('[data-journal-image-close]').focus({ preventScroll: true });
  }

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-journal-image-viewer]');
    if (!trigger) return;
    event.preventDefault();
    openImageViewer(trigger.dataset.journalImageViewer, trigger.dataset.journalImageAlt, trigger);
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeImageViewer();
  });

}());
