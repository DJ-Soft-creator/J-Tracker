/* Deep Write Mode: ein reiner Ruhe-Modus fuer den Schreiben-Tab. Header, Tabs
   und alle Bedienelemente um den Editor herum verlieren Saettigung und
   Praesenz, der Text bleibt unangetastet. Es aendert sich ausschliesslich die
   Darstellung - kein Datenpfad, kein Endpoint, keine Tastenbelegung.

   Der Schalter haengt im "⋯"-Menue aus write-session.js; dieses Skript wird
   danach geladen und ergaenzt die Zeile, damit write-session.js nicht weiter
   waechst. */
(function () {
  const STORAGE_KEY = 'journl.write.deep-mode';
  const menu = document.getElementById('write-media-menu');
  if (!menu) return;

  const toggle = document.createElement('button');
  toggle.type = 'button';
  toggle.setAttribute('data-deep-write', '');
  toggle.setAttribute('aria-pressed', 'false');
  toggle.className = 'flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm text-gray-200 hover:bg-gray-800';
  toggle.innerHTML = '<span>Deep Write Mode</span><span data-deep-write-state class="text-xs text-gray-500">Aus</span>';
  const stateLabel = toggle.querySelector('[data-deep-write-state]');
  // Direkt unter der Schriftgroesse: beide Schalter aendern nur, wie sich das
  // Schreiben anfuehlt, nicht was gespeichert wird.
  const fontSection = menu.querySelector('[data-font-section]');
  if (fontSection) fontSection.insertAdjacentElement('afterend', toggle);
  else menu.appendChild(toggle);

  function storedChoice() {
    try { return window.localStorage.getItem(STORAGE_KEY) === '1'; }
    catch (_) { return false; }
  }

  function apply(enabled, persist) {
    document.documentElement.classList.toggle('deep-write', enabled);
    toggle.setAttribute('aria-pressed', enabled ? 'true' : 'false');
    stateLabel.textContent = enabled ? 'An' : 'Aus';
    if (persist) {
      try { window.localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0'); }
      catch (_) { /* Private-Mode behaelt die Wahl nur fuer diese Seite. */ }
    }
    return enabled;
  }

  let active = apply(storedChoice(), false);
  // Das Menue bleibt bewusst offen: die Wirkung ist direkt daneben zu sehen.
  toggle.addEventListener('click', () => { active = apply(!active, true); });
}());
