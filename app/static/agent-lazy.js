/* Lazy-Loader fuer die Agentenmodule.
 *
 * AI maintenance contract: hier steht nur, WANN ein Agentenmodul geladen wird,
 * nie was es tut. Die Module selbst bleiben Eigentuemer ihrer Oberflaeche.
 *
 * Die vier Agentendateien lagen bisher als ausfuehrbare <script>-Tags in beiden
 * Shells und kosteten bei jedem Start Parsezeit und eine Anfrage — auch wenn
 * niemand einen Agenten oeffnete. Jetzt stehen sie als data-Bloecke
 * (`type="text/journl-lazy"`) im Template; der Browser laedt sie nicht, und
 * dieses Modul injiziert die echte Datei erst beim ersten Aufruf. Der ?v=-Teil
 * der Quell-URL bleibt der Cache-Buster.
 *
 * Weil die Inline-Handler (`onclick="openShoppingAgent(...)"`) und die
 * Dashboard-/Brain-Knoepfe die Funktionen synchron erwarten, legt der Loader
 * fuer jeden Einstieg einen Stub an, der das Modul nachlaedt und dann an die
 * echte Funktion weiterreicht. Das Schreib-Agent-Menue im geteilten
 * "..."-Menue entsteht ebenfalls hier: der Eintrag muss sichtbar sein, bevor
 * das Modul geladen ist.
 */
(function () {
  const registry = {};
  document.querySelectorAll('script[type="text/journl-lazy"][data-lazy]').forEach((tag) => {
    if (tag.dataset.lazy && tag.src) registry[tag.dataset.lazy] = tag.src;
  });

  // Ein Agentenfenster braucht die visualViewport-Regel; sie kommt vor ihm.
  const dependencies = {
    'write-agent.js': ['agent-viewport.js'],
    'shopping-agent.js': ['agent-viewport.js'],
  };

  const pending = {};

  function inject(name) {
    if (pending[name]) return pending[name];
    const src = registry[name];
    if (!src) {
      return Promise.reject(new Error(`Unbekanntes Agentenmodul: ${name}`));
    }
    pending[name] = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = false;
      script.onload = () => resolve();
      script.onerror = () => {
        delete pending[name];
        reject(new Error(`Agentenmodul ${name} konnte nicht geladen werden.`));
      };
      document.head.appendChild(script);
    });
    return pending[name];
  }

  function load(name) {
    const deps = dependencies[name] || [];
    return Promise.all(deps.map(inject)).then(() => inject(name));
  }

  function fail(error) {
    if (typeof window.showToast === 'function') window.showToast(error.message, true);
  }

  // Einstiege, die von aussen synchron gerufen werden, aber ein Modul brauchen.
  window.openWriteAgentFromDashboard = function () {
    const args = arguments;
    load('write-agent.js').then(
      () => window.openWriteAgentFromDashboard.apply(window, args),
      fail,
    );
  };
  window.openWriteAgentFromBrain = function () {
    const args = arguments;
    load('write-agent.js').then(
      () => window.openWriteAgentFromBrain.apply(window, args),
      fail,
    );
  };
  window.openShoppingAgent = function () {
    const args = arguments;
    load('shopping-agent.js').then(
      () => window.openShoppingAgent.apply(window, args),
      fail,
    );
  };

  /* Der Eintrag steht in der obersten Menueebene, oberhalb von "An Agent
     senden" — beide schicken Text weg, nur dieser gibt ihn als Vorschlag
     zurueck. Er gehoert hierher, weil er sichtbar sein muss, bevor das Modul
     geladen ist; write-agent.js selbst haengt nichts mehr ins Menue. */
  function addWriteAgentEntry() {
    const menu = document.getElementById('write-media-menu');
    if (!menu) {
      window.setTimeout(addWriteAgentEntry, 50);
      return;
    }
    if (menu.querySelector('[data-write-agent-entry]')) return;
    const entry = document.createElement('button');
    entry.type = 'button';
    entry.setAttribute('data-write-agent-entry', '');
    entry.className = 'block w-full px-3 py-2 text-left text-sm text-green-300 hover:bg-green-950/50';
    entry.textContent = 'Schreib-Agent öffnen';
    const assistant = menu.querySelector('[data-job-action="assistant"]');
    if (assistant) assistant.insertAdjacentElement('beforebegin', entry);
    else menu.prepend(entry);
    entry.addEventListener('click', () => {
      menu.classList.add('hidden');
      load('write-agent.js').then(
        () => window.openWriteAgentFromWriteTab(),
        fail,
      );
    });
  }

  addWriteAgentEntry();

  window.journlAgentModules = { load };
})();
