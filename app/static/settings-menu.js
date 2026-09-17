/* Burger menu entries open the settings modal on a single group of sections. */
(function () {
  const modal = document.getElementById('settings-modal');
  const menu = document.getElementById('burger-menu');
  if (!modal || !menu) return;

  const title = document.getElementById('settings-modal-title');
  const body = document.getElementById('settings-body');
  const sections = Array.from(modal.querySelectorAll('[data-settings-group]'));

  function showGroup(group) {
    const inGroup = sections.filter(section => section.dataset.settingsGroup === group);
    sections.forEach(section => section.classList.toggle('hidden', !inGroup.includes(section)));
    // A group with a single section is already named by the modal title.
    inGroup.forEach(section => section.querySelector('h3')?.classList.toggle('hidden', inGroup.length === 1));
  }

  window.openSettingsGroup = function openSettingsGroup(group, label) {
    showGroup(group);
    if (title) title.textContent = label || 'Einstellungen';
    window.openSettings?.();
    body?.parentElement?.scrollTo({ top: 0 });
    document.dispatchEvent(new CustomEvent('journl:settings-opened', { detail: { group } }));
  };

  menu.addEventListener('click', (event) => {
    const entry = event.target.closest('[data-settings-open]');
    if (!entry) return;
    menu.classList.add('hidden');
    window.openSettingsGroup(entry.dataset.settingsOpen, entry.dataset.settingsLabel);
  });
})();
