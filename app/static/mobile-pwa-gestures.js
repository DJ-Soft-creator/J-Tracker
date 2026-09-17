/* Keep the installed mobile PWA inside its app shell. */
(function () {
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (!standalone) return;

  let edgeGesture = null;
  document.addEventListener('gesturestart', (event) => event.preventDefault(), { passive: false });
  document.addEventListener('touchstart', (event) => {
    if (event.touches.length !== 1) {
      edgeGesture = null;
      return;
    }
    const touch = event.touches[0];
    const edgeWidth = 28;
    edgeGesture = touch.clientX <= edgeWidth || touch.clientX >= window.innerWidth - edgeWidth
      ? { x: touch.clientX, y: touch.clientY }
      : null;
  }, { passive: true });
  document.addEventListener('touchmove', (event) => {
    if (event.touches.length > 1) {
      event.preventDefault();
      return;
    }
    if (!edgeGesture || event.touches.length !== 1) return;
    const touch = event.touches[0];
    const deltaX = touch.clientX - edgeGesture.x;
    const deltaY = touch.clientY - edgeGesture.y;
    if (Math.abs(deltaX) > 10 && Math.abs(deltaX) > Math.abs(deltaY) * 1.15) {
      event.preventDefault();
    }
  }, { passive: false });
  const clearEdgeGesture = () => { edgeGesture = null; };
  document.addEventListener('touchend', clearEdgeGesture, { passive: true });
  document.addEventListener('touchcancel', clearEdgeGesture, { passive: true });
})();
