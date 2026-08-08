let plannerState = {
  items: [],
  users: [],
  recurrences: [],
  today: '',
  editingId: null,
};


function plannerElement(id) {
  return document.getElementById(id);
}


function plannerDate(value) {
  if (!value) return '';
  const parsed = new Date(value + 'T12:00:00');
  return parsed.toLocaleDateString('de-DE', {
    weekday: 'short',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}


function plannerIsoDate(value) {
  return value.toISOString().slice(0, 10);
}


function calculatePlannerNextDate(recurrence, startValue, fromValue) {
  if (!recurrence || !startValue || !fromValue) return null;
  const start = new Date(startValue + 'T12:00:00Z');
  const from = new Date(fromValue + 'T12:00:00Z');
  let candidate = start > from ? new Date(start) : new Date(from);

  if (recurrence === 'once') return start >= from ? plannerIsoDate(start) : null;
  if (recurrence === 'daily') return plannerIsoDate(candidate);

  const weekdays = {
    weekly_sunday: 0,
    weekly_monday: 1,
    weekly_tuesday: 2,
    weekly_wednesday: 3,
    weekly_thursday: 4,
    weekly_friday: 5,
    weekly_saturday: 6,
  };
  if (Object.prototype.hasOwnProperty.call(weekdays, recurrence)) {
    const offset = (weekdays[recurrence] - candidate.getUTCDay() + 7) % 7;
    candidate.setUTCDate(candidate.getUTCDate() + offset);
    return plannerIsoDate(candidate);
  }
  if (recurrence === 'biweekly') {
    const elapsed = Math.max(0, Math.floor((candidate - start) / 86400000));
    const cycles = Math.ceil(elapsed / 14);
    start.setUTCDate(start.getUTCDate() + cycles * 14);
    return plannerIsoDate(start);
  }
  if (recurrence === 'monthly_first') {
    candidate = new Date(Date.UTC(candidate.getUTCFullYear(), candidate.getUTCMonth(), 1, 12));
    const lowerBound = start > from ? start : from;
    if (candidate < lowerBound) candidate.setUTCMonth(candidate.getUTCMonth() + 1);
    return plannerIsoDate(candidate);
  }
  return null;
}


function updatePlannerPreview() {
  const preview = plannerElement('planner-preview');
  if (!preview) return;
  if (!plannerElement('planner-active').checked) {
    preview.textContent = 'Plan ist pausiert';
    preview.className = 'text-xs text-amber-400';
    return;
  }
  const next = calculatePlannerNextDate(
    plannerElement('planner-recurrence').value,
    plannerElement('planner-start-date').value,
    plannerState.today,
  );
  preview.className = 'text-xs text-green-400';
  if (!next) {
    preview.textContent = 'Kein weiterer Termin';
  } else if (next === plannerState.today) {
    preview.textContent = 'Nächster Termin: heute';
  } else {
    preview.textContent = 'Nächster Termin: ' + plannerDate(next);
  }
}


function fillPlannerSelects() {
  const userSelect = plannerElement('planner-user');
  const recurrenceSelect = plannerElement('planner-recurrence');
  userSelect.innerHTML = '<option value="">Person wählen</option>' + plannerState.users.map((user) =>
    `<option value="${escapeHtmlAttr(user.id || '')}">${escapeHtml(user.display || '')}</option>`
  ).join('');
  recurrenceSelect.innerHTML = plannerState.recurrences.map((item) =>
    `<option value="${escapeHtmlAttr(item.value || '')}">${escapeHtml(item.label || '')}</option>`
  ).join('');
}


function resetPlannerForm() {
  plannerState.editingId = null;
  plannerElement('planner-form-title').textContent = 'Neue Aufgabe planen';
  plannerElement('planner-form-reset').classList.add('hidden');
  plannerElement('planner-save').textContent = 'Plan speichern';
  plannerElement('planner-task-title').value = '';
  const ownUser = plannerState.users.find((user) => user.id === currentUserId);
  plannerElement('planner-user').value = ownUser ? ownUser.id : '';
  plannerElement('planner-recurrence').value = 'once';
  plannerElement('planner-start-date').value = plannerState.today;
  plannerElement('planner-active').checked = true;
  updatePlannerPreview();
}


function renderPlannerList(schedulerStatus) {
  const list = plannerElement('planner-list');
  plannerElement('planner-count').textContent = `(${plannerState.items.length})`;
  if (schedulerStatus && schedulerStatus.error) {
    plannerElement('planner-status').textContent = 'Letzte Prüfung fehlgeschlagen.';
    plannerElement('planner-status').className = 'text-xs text-red-400 mt-0.5';
  } else {
    const added = schedulerStatus ? Number(schedulerStatus.added || 0) : 0;
    plannerElement('planner-status').textContent = added
      ? `${added} fällige Aufgabe${added === 1 ? '' : 'n'} neu angelegt.`
      : 'Alle fälligen Aufgaben sind angelegt.';
    plannerElement('planner-status').className = 'text-xs text-gray-500 mt-0.5';
  }

  if (!plannerState.items.length) {
    list.innerHTML = `<div class="border border-dashed border-gray-700 rounded-xl px-4 py-8 text-center">
      <p class="text-sm text-gray-400">Noch keine Aufgaben geplant.</p>
      <p class="text-xs text-gray-600 mt-1">Lege oben den ersten Termin an.</p>
    </div>`;
    return;
  }

  list.innerHTML = plannerState.items.map((item) => {
    let dueText = 'Kein weiterer Termin';
    let dueClass = 'text-gray-500';
    if (!item.active) {
      dueText = 'Pausiert';
      dueClass = 'text-amber-400';
    } else if (item.next_due_date === plannerState.today) {
      dueText = 'Heute fällig';
      dueClass = 'text-red-400';
    } else if (item.next_due_date) {
      dueText = 'Nächster Termin: ' + plannerDate(item.next_due_date);
      dueClass = 'text-green-400';
    }
    return `<article data-plan-id="${escapeHtmlAttr(item.id || '')}" class="min-w-0 max-w-full overflow-hidden bg-gray-950/60 border ${item.active ? 'border-gray-800' : 'border-gray-800/60'} rounded-xl px-4 py-3">
      <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-3 min-w-0 max-w-full">
        <div class="min-w-0">
          <div class="flex flex-wrap items-center gap-2">
            <h4 class="min-w-0 max-w-full text-sm font-semibold ${item.active ? 'text-gray-100' : 'text-gray-500'} break-words">${escapeHtml(item.title || '')}</h4>
            <span class="max-w-full text-[11px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-400 whitespace-normal break-words">${escapeHtml(item.recurrence_label || '')}</span>
          </div>
          <p class="text-xs text-gray-500 mt-1 break-words">${escapeHtml(item.user_display || '')} · Start ${escapeHtml(plannerDate(item.start_date))}</p>
          <p class="text-xs ${dueClass} mt-1 break-words">${escapeHtml(dueText)}</p>
        </div>
        <div class="flex flex-wrap items-center gap-1 max-w-full shrink-0">
          <button type="button" data-planner-action="edit" class="text-xs text-green-500 hover:text-green-400 px-2 py-1.5 rounded hover:bg-gray-800">Bearbeiten</button>
          <button type="button" data-planner-action="toggle" class="text-xs text-gray-400 hover:text-white px-2 py-1.5 rounded hover:bg-gray-800">${item.active ? 'Pausieren' : 'Aktivieren'}</button>
          <button type="button" data-planner-action="delete" class="text-xs text-red-500 hover:text-red-400 px-2 py-1.5 rounded hover:bg-gray-800" aria-label="Plan löschen">Löschen</button>
        </div>
      </div>
    </article>`;
  }).join('');
}


async function loadPlanner() {
  const list = plannerElement('planner-list');
  list.innerHTML = '<p class="text-gray-500 text-sm text-center py-6">Lade Pläne…</p>';
  try {
    const response = await apiFetch('/api/family/planner');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      list.innerHTML = `<p class="text-red-400 text-sm text-center py-6">${escapeHtml(data.error || 'Planung konnte nicht geladen werden.')}</p>`;
      return;
    }
    plannerState.items = data.items || [];
    plannerState.users = data.users || [];
    plannerState.recurrences = data.recurrences || [];
    plannerState.today = data.today || new Date().toISOString().slice(0, 10);
    fillPlannerSelects();
    resetPlannerForm();
    renderPlannerList(data.scheduler || {});
  } catch (error) {
    list.innerHTML = '<p class="text-red-400 text-sm text-center py-6">Netzwerkfehler beim Laden.</p>';
  }
}


function openPlanner() {
  const modal = plannerElement('planner-modal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
  loadPlanner().then(() => {
    const titleInput = plannerElement('planner-task-title');
    if (typeof focusMobileControl === 'function') focusMobileControl(titleInput);
    else titleInput.focus();
  });
}


function closePlanner() {
  const modal = plannerElement('planner-modal');
  if (!modal || modal.classList.contains('hidden')) return;
  modal.classList.add('hidden');
  modal.classList.remove('flex');
  plannerState.editingId = null;
}


function editPlannerItem(planId) {
  const item = plannerState.items.find((candidate) => candidate.id === planId);
  if (!item) return;
  plannerState.editingId = item.id;
  plannerElement('planner-form-title').textContent = 'Plan bearbeiten';
  plannerElement('planner-form-reset').classList.remove('hidden');
  plannerElement('planner-save').textContent = 'Änderungen speichern';
  plannerElement('planner-task-title').value = item.title || '';
  plannerElement('planner-user').value = item.user || '';
  plannerElement('planner-recurrence').value = item.recurrence || '';
  plannerElement('planner-start-date').value = item.start_date || plannerState.today;
  plannerElement('planner-active').checked = Boolean(item.active);
  updatePlannerPreview();
  const titleInput = plannerElement('planner-task-title');
  if (typeof focusMobileControl === 'function') focusMobileControl(titleInput);
  else titleInput.focus();
}


async function savePlanner(event) {
  event.preventDefault();
  const payload = {
    title: plannerElement('planner-task-title').value.trim(),
    user: plannerElement('planner-user').value,
    recurrence: plannerElement('planner-recurrence').value,
    start_date: plannerElement('planner-start-date').value,
    active: plannerElement('planner-active').checked,
  };
  if (!payload.title || !payload.user || !payload.recurrence || !payload.start_date) {
    showToast('Bitte alle Felder ausfüllen', true);
    return;
  }

  const button = plannerElement('planner-save');
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = 'Speichert…';
  try {
    const editing = plannerState.editingId;
    const response = await apiFetch(
      editing ? '/api/family/planner/' + encodeURIComponent(editing) : '/api/family/planner',
      { method: editing ? 'PUT' : 'POST', body: JSON.stringify(payload) },
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Plan konnte nicht gespeichert werden', true);
      return;
    }
    showToast(editing ? 'Plan aktualisiert' : 'Aufgabe geplant');
    await loadPlanner();
    if (typeof loadFamily === 'function') loadFamily();
    if (typeof checkFamilyNotifications === 'function') checkFamilyNotifications();
  } catch (error) {
    showToast('Netzwerkfehler', true);
  } finally {
    button.disabled = false;
    button.textContent = plannerState.editingId ? originalText : 'Plan speichern';
  }
}


async function togglePlannerItem(planId) {
  const item = plannerState.items.find((candidate) => candidate.id === planId);
  if (!item) return;
  try {
    const response = await apiFetch('/api/family/planner/' + encodeURIComponent(planId), {
      method: 'PUT',
      body: JSON.stringify({ active: !item.active }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Status konnte nicht geändert werden', true);
      return;
    }
    showToast(item.active ? 'Plan pausiert' : 'Plan aktiviert');
    await loadPlanner();
    if (typeof loadFamily === 'function') loadFamily();
  } catch (error) {
    showToast('Netzwerkfehler', true);
  }
}


async function deletePlannerItem(planId) {
  const item = plannerState.items.find((candidate) => candidate.id === planId);
  if (!item || !window.confirm(`Plan „${item.title}“ wirklich löschen? Bereits erzeugte Aufgaben bleiben erhalten.`)) return;
  try {
    const response = await apiFetch('/api/family/planner/' + encodeURIComponent(planId), { method: 'DELETE' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Plan konnte nicht gelöscht werden', true);
      return;
    }
    showToast('Plan gelöscht');
    await loadPlanner();
  } catch (error) {
    showToast('Netzwerkfehler', true);
  }
}


async function runPlannerNow() {
  const button = plannerElement('planner-run');
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = 'Prüft…';
  try {
    const response = await apiFetch('/api/family/planner/run', {
      method: 'POST',
      body: JSON.stringify({}),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showToast(data.error || 'Scheduler fehlgeschlagen', true);
      return;
    }
    showToast(data.added ? `${data.added} Aufgabe${data.added === 1 ? '' : 'n'} angelegt` : 'Alles aktuell');
    await loadPlanner();
    if (typeof loadFamily === 'function') loadFamily();
    if (typeof checkFamilyNotifications === 'function') checkFamilyNotifications();
  } catch (error) {
    showToast('Netzwerkfehler', true);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}


plannerElement('planner-close').addEventListener('click', closePlanner);
plannerElement('planner-form-reset').addEventListener('click', resetPlannerForm);
plannerElement('planner-form').addEventListener('submit', savePlanner);
plannerElement('planner-run').addEventListener('click', runPlannerNow);
plannerElement('planner-recurrence').addEventListener('change', updatePlannerPreview);
plannerElement('planner-start-date').addEventListener('change', updatePlannerPreview);
plannerElement('planner-active').addEventListener('change', updatePlannerPreview);
plannerElement('planner-modal').addEventListener('click', (event) => {
  if (event.target.id === 'planner-modal') closePlanner();
});
plannerElement('planner-list').addEventListener('click', (event) => {
  const button = event.target.closest('[data-planner-action]');
  const row = event.target.closest('[data-plan-id]');
  if (!button || !row) return;
  const planId = row.getAttribute('data-plan-id');
  const action = button.getAttribute('data-planner-action');
  if (action === 'edit') editPlannerItem(planId);
  if (action === 'toggle') togglePlannerItem(planId);
  if (action === 'delete') deletePlannerItem(planId);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closePlanner();
});
