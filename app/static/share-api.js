/* Share API key management and Settings entry point for AI workflow design. */
(function () {
  const status = document.getElementById('settings-share-token-status');
  const list = document.getElementById('settings-share-token-list');
  const label = document.getElementById('settings-share-token-label');
  const create = document.getElementById('settings-share-token-create');
  const reveal = document.getElementById('settings-share-token-reveal');
  const value = document.getElementById('settings-share-token-value');
  const endpoint = document.getElementById('settings-share-endpoint');
  const mcpEndpoint = document.getElementById('settings-mcp-endpoint');
  const mcpFamilyEndpoint = document.getElementById('settings-mcp-family-endpoint');
  const workflows = document.getElementById('settings-ai-workflows');
  if (!status) return;

  const SCOPE_LABELS = {
    upload: 'Upload',
    run_workflow: 'Workflow',
    write_journal: 'Journal',
    mcp: 'MCP privat',
    mcp_family: 'MCP Familie',
  };

  endpoint.value = `${window.location.origin}/api/share`;
  mcpEndpoint.value = `${window.location.origin}/mcp/privat`;
  mcpFamilyEndpoint.value = `${window.location.origin}/mcp/family`;

  async function jsonRequest(url, options = {}) {
    const response = await window.apiFetch(url, { timeoutMs: 15000, ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Share-API-Aktion fehlgeschlagen');
    return data;
  }

  function selectedScopes() {
    return [...document.querySelectorAll('[data-share-scope]:checked')].map((input) => input.value);
  }

  function describe(key) {
    const scopes = (key.scopes || []).map((scope) => SCOPE_LABELS[scope] || scope).join(', ');
    const used = key.last_used_at
      ? `zuletzt benutzt ${new Date(key.last_used_at).toLocaleDateString('de-DE')}`
      : 'noch nie benutzt';
    return `${scopes || 'keine Rechte'} · ${used}`;
  }

  // Each key is rendered from data rather than markup, so a label the user typed
  // can never reach the DOM as HTML.
  function renderKeys(keys) {
    list.textContent = '';
    keys.forEach((key) => {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-2 rounded bg-gray-800/60 px-2 py-1 text-xs text-gray-300';
      const text = document.createElement('div');
      text.className = 'min-w-0 flex-1';
      const name = document.createElement('div');
      name.className = 'truncate text-gray-200';
      name.textContent = key.label || 'Schlüssel';
      const meta = document.createElement('div');
      meta.className = 'truncate text-gray-500';
      meta.textContent = describe(key);
      text.append(name, meta);
      const revoke = document.createElement('button');
      revoke.type = 'button';
      revoke.className = 'rounded bg-red-950 px-2 py-1 text-red-200 hover:bg-red-900';
      revoke.textContent = 'Widerrufen';
      revoke.addEventListener('click', () => revokeKey(key));
      row.append(text, revoke);
      list.append(row);
    });
  }

  async function loadStatus() {
    try {
      const data = await jsonRequest('/api/share/token', { cache: 'no-store' });
      const keys = data.keys || [];
      status.textContent = keys.length
        ? `${keys.length} Schlüssel aktiv.`
        : 'Noch kein Share-API-Schlüssel eingerichtet.';
      renderKeys(keys);
    } catch (_) {
      status.textContent = 'Status nicht verfügbar.';
      list.textContent = '';
    }
  }

  async function revokeKey(key) {
    if (!window.confirm(`Schlüssel „${key.label || 'Schlüssel'}" wirklich widerrufen?`)) return;
    try {
      await jsonRequest('/api/share/token', { method: 'DELETE', body: JSON.stringify({ id: key.id }) });
      reveal.classList.add('hidden');
      value.value = '';
      await loadStatus();
    } catch (error) {
      window.showToast?.(error.message, true);
    }
  }

  create.addEventListener('click', async () => {
    const scopes = selectedScopes();
    if (!scopes.length) { window.showToast?.('Mindestens ein API-Recht auswählen.', true); return; }
    create.disabled = true;
    try {
      const data = await jsonRequest('/api/share/token', {
        method: 'POST', body: JSON.stringify({ scopes, label: label.value }),
      });
      value.value = data.token;
      reveal.classList.remove('hidden');
      label.value = '';
      await loadStatus();
    } catch (error) {
      window.showToast?.(error.message, true);
    } finally {
      create.disabled = false;
    }
  });

  document.querySelectorAll('[data-copy-share-value]').forEach((button) => button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copyShareValue);
    try { await navigator.clipboard.writeText(input.value); window.showToast?.('Kopiert.'); }
    catch (_) { input.select(); document.execCommand('copy'); window.showToast?.('Kopiert.'); }
  }));

  workflows?.addEventListener('click', () => document.getElementById('brain-tag-catalog')?.click());
  document.addEventListener('journl:settings-opened', (event) => {
    if (!event.detail?.group || event.detail.group === 'integrations') loadStatus();
  });
  loadStatus();
})();
