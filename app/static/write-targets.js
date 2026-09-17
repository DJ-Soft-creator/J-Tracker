// AI maintenance contract: classic script, shared by both app shells.
// Owns every "where does this submission go?" control of the Schreiben tab:
// the Brain project assignment and the Familie shopping-list picker. Both were
// duplicated per shell before; keep exactly one implementation here.
// Contract with *-app-core.js: writeTargetsRender(template) on template change
// (null in AI mode) and writeTargetsPayload() when building the submit body.

    // ─── Projektzuordnung (Brain) ───────────────────────────────────
    function renderProjectSelector(template) {
      const container = document.getElementById('project-selector-container');
      if (!container) return;
      const previousValue = document.getElementById('project-assignment')?.value || '';
      if (!template || !template.project_selector) {
        container.innerHTML = '';
        container.classList.add('hidden');
        return;
      }
      const projects = typeof window.brainProjectOptions === 'function' ? window.brainProjectOptions() : [];
      if (!projects.length && typeof window.loadBrainProjects === 'function') window.loadBrainProjects();
      let options = '<option value="">Ohne Projekt</option>';
      for (const project of projects) {
        options += `<option value="${escapeHtmlAttr(project.path || '')}">${escapeHtml(project.title || project.path || '')}</option>`;
      }
      container.innerHTML = `<label class="block text-xs text-gray-400 mb-1" for="project-assignment">Projektzuordnung</label><select id="project-assignment" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500">${options}</select>`;
      const selector = document.getElementById('project-assignment');
      if (selector && Array.from(selector.options).some(option => option.value === previousValue)) {
        selector.value = previousValue;
      }
      container.classList.remove('hidden');
    }

    window.refreshBrainProjectSelector = () => {
      const selectedId = document.getElementById('template-select')?.value;
      if (config && selectedId) renderProjectSelector(config.templates.find(t => t.id === selectedId));
    };

    // ─── Einkaufsliste: Zielliste wählen ────────────────────────────
    let writeShoppingTargets = null;

    async function renderShoppingTarget(active) {
      const container = document.getElementById('shopping-target-container');
      if (!container) return;
      if (!active) {
        container.innerHTML = '';
        container.classList.add('hidden');
        return;
      }
      if (writeShoppingTargets === null) {
        try {
          const res = await apiFetch('/api/family/shopping-lists');
          writeShoppingTargets = res.ok ? ((await res.json()).items || []) : [];
        } catch {
          writeShoppingTargets = [];
        }
      }
      // Ohne Auswahl schreibt der Server weiter in die bisherige Liste aus dem
      // Template. Der Eintrag bleibt deshalb sichtbar, statt zu verschwinden.
      let options = '<option value="">Standard-Einkaufsliste</option>';
      for (const item of writeShoppingTargets) {
        options += `<option value="${escapeHtmlAttr(item.id)}">${escapeHtml(item.title || item.id)}</option>`;
      }
      const previousValue = document.getElementById('shopping-target')?.value || '';
      container.innerHTML = `<label class="block text-xs text-gray-400 mb-1" for="shopping-target">Einkaufsliste</label>`
        + `<select id="shopping-target" class="w-full rounded-lg bg-gray-800 border border-gray-700 px-3 py-2 text-sm text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500">${options}</select>`;
      const selector = document.getElementById('shopping-target');
      if (selector && Array.from(selector.options).some(option => option.value === previousValue)) {
        selector.value = previousValue;
      }
      container.classList.remove('hidden');
    }

    // Angelegte, umbenannte oder gelöschte Listen sollen beim nächsten Öffnen
    // der Auswahl stimmen; family-board.js ruft das nach jedem Board-Laden auf.
    window.writeTargetsResetShoppingLists = () => { writeShoppingTargets = null; };

    // ─── Gemeinsamer Vertrag mit *-app-core.js ──────────────────────
    window.writeTargetsRender = (template) => {
      renderProjectSelector(template);
      renderShoppingTarget(Boolean(template) && template.id === 'einkaufsliste');
    };

    window.writeTargetsPayload = () => {
      const payload = {};
      const projectAssignment = document.getElementById('project-assignment');
      if (projectAssignment && projectAssignment.value) payload.project_assignment = projectAssignment.value;
      const shoppingTarget = document.getElementById('shopping-target');
      if (shoppingTarget && shoppingTarget.value) payload.family_target_project = shoppingTarget.value;
      return payload;
    };
