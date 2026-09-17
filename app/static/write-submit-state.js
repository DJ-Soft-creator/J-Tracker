/* Shared, recoverable state for the Schreiben submit/save button. */
(function () {
  let active = null;
  let serial = 0;

  function button() { return document.getElementById('submit-btn'); }
  function recordingBlockReason() {
    return window.writeSessionSubmissionBlockReason?.() || '';
  }
  function show(message, error = false) {
    if (typeof window.showToast === 'function') window.showToast(message, error);
  }

  function applyReadyState(entry = active) {
    const submit = button();
    if (!submit || (entry && submit.dataset.writeSubmitOwner !== String(entry.id))) return;
    const recordingReason = recordingBlockReason();
    submit.disabled = Boolean(recordingReason);
    submit.classList.toggle('opacity-60', Boolean(recordingReason));
    submit.classList.toggle('cursor-not-allowed', Boolean(recordingReason));
    if (entry && submit.textContent === entry.busyText) submit.textContent = entry.readyText;
    if (entry) {
      submit.title = recordingReason || entry.activeMessage;
      submit.setAttribute('aria-busy', 'true');
    } else {
      submit.title = recordingReason;
      submit.setAttribute('aria-busy', 'false');
      delete submit.dataset.writeSubmitOwner;
    }
  }

  function finish(token, { timedOut = false } = {}) {
    if (!active || active.token !== token) return false;
    const entry = active;
    active = null;
    clearTimeout(entry.unlockTimer);
    clearTimeout(entry.watchdogTimer);
    applyReadyState(entry);
    const submit = button();
    if (submit?.dataset.writeSubmitOwner === String(entry.id)) {
      delete submit.dataset.writeSubmitOwner;
      applyReadyState(null);
    }
    if (timedOut) {
      entry.onTimeout?.();
      show(entry.timeoutMessage, true);
    }
    return true;
  }

  function begin(options = {}) {
    const submit = button();
    if (!submit) return null;
    const recordingReason = recordingBlockReason();
    if (recordingReason) {
      applyReadyState(null);
      show(recordingReason, true);
      return null;
    }
    if (active) {
      const supersedeScopes = Array.isArray(options.supersedeScopes) ? options.supersedeScopes : [];
      if (supersedeScopes.includes(active.scope)) finish(active.token);
      else {
        show(active.activeMessage);
        return null;
      }
    }

    const token = {};
    const id = ++serial;
    const entry = {
      id,
      token,
      scope: options.scope || 'write',
      readyText: options.readyText || submit.textContent,
      busyText: options.busyText || '…',
      activeMessage: options.activeMessage || 'Der Vorgang läuft bereits – dein Text bleibt erhalten.',
      timeoutMessage: options.timeoutMessage || 'Der Vorgang wurde automatisch freigegeben. Bitte erneut versuchen.',
      onTimeout: options.onTimeout,
      unlockTimer: null,
      watchdogTimer: null,
    };
    active = entry;
    submit.dataset.writeSubmitOwner = String(id);
    submit.disabled = true;
    submit.textContent = entry.busyText;
    submit.title = 'Kurzer Schutz vor versehentlichem Doppeltippen';
    submit.classList.add('opacity-60', 'cursor-not-allowed');
    submit.setAttribute('aria-busy', 'true');

    const lockMs = Number(options.lockMs) || 1000;
    const timeoutMs = Number(options.timeoutMs) || 60000;
    entry.unlockTimer = setTimeout(() => {
      if (active?.token === token) applyReadyState(entry);
    }, lockMs);
    entry.watchdogTimer = setTimeout(() => finish(token, { timedOut: true }), timeoutMs);
    return token;
  }

  function refresh() {
    if (active) applyReadyState(active);
    else applyReadyState(null);
  }

  window.writeSubmitState = { begin, finish, refresh, isActive: () => Boolean(active) };
  window.addEventListener('pageshow', refresh);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh();
  });
})();
