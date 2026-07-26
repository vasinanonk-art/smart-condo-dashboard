(() => {
  'use strict';
  if (window.HouseholdUI) return;

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const qualityLabels = {confirmed:'Confirmed', assumed:'Assumed', unknown:'Unknown'};
  const statusLabels = {online:'Online', offline:'Offline', unknown:'Unknown'};

  function statusBadge(value, label, id='') {
    const normalized = ['online','offline'].includes(value) ? value : 'unknown';
    return `<span class="household-badge household-status-badge household-status-${normalized}" role="status" aria-live="polite"${id ? ` id="${safe(id)}"` : ''}>${safe(label || statusLabels[normalized])}</span>`;
  }

  function stateQualityBadge(value) {
    const normalized = qualityLabels[value] ? value : 'unknown';
    return `<span class="household-badge household-quality-badge household-quality-${normalized}">${safe(qualityLabels[normalized])} state</span>`;
  }

  function deviceHeader({title, room, status='unknown', statusLabel, titleId='', statusId=''}) {
    return `<header class="household-device-header">
      <div class="household-device-identity">
        <h3 class="household-device-title"${titleId ? ` id="${safe(titleId)}"` : ''}>${safe(title)}</h3>
        ${room ? `<span class="household-device-room">${safe(room)}</span>` : ''}
      </div>
      ${statusBadge(status, statusLabel, statusId)}
    </header>`;
  }

  function warningBox(message, id='') {
    if (!message) return '';
    return `<div class="household-warning-box" role="note"${id ? ` id="${safe(id)}"` : ''}>${safe(message)}</div>`;
  }

  function actionButton({label, disabled=false, reason='', attributes='', className=''}) {
    const reasonText = reason ? ` aria-label="${safe(`${label}. ${reason}`)}" title="${safe(reason)}"` : ` aria-label="${safe(label)}"`;
    return `<button type="button" class="household-action-button${className ? ` ${safe(className)}` : ''}"${disabled ? ' disabled' : ''}${reasonText}${attributes ? ` ${attributes}` : ''}>${safe(label)}</button>`;
  }

  function actionGrid(content, label='Device controls') {
    return `<div class="household-action-grid" role="group" aria-label="${safe(label)}">${content}</div>`;
  }

  function deviceDetails({summary='Device Details', content='', open=false, attributes=''}) {
    return `<details class="household-device-details"${open ? ' open' : ''}${attributes ? ` ${attributes}` : ''}>
      <summary>${safe(summary)}</summary>
      <div class="household-device-details-content">${content}</div>
    </details>`;
  }

  function deviceCard({id='', title, room='', status='unknown', statusLabel, quality='unknown', state='', warning='', actions='', details=''}) {
    const titleId = id ? `${id}-title` : '';
    return `<article class="household-device-card"${titleId ? ` aria-labelledby="${safe(titleId)}"` : ''}>
      ${deviceHeader({title, room, status, statusLabel, titleId})}
      <div class="household-device-summary">
        ${stateQualityBadge(quality)}
        ${state ? `<span class="household-device-state">${safe(state)}</span>` : ''}
      </div>
      ${warningBox(warning, id ? `${id}-warning` : '')}
      ${actions ? actionGrid(actions, `${title} controls`) : ''}
      ${details}
    </article>`;
  }

  function ensureToast() {
    let host = document.getElementById('householdCommandToast');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'householdCommandToast';
    host.className = 'household-toast';
    host.setAttribute('role', 'status');
    host.setAttribute('aria-live', 'polite');
    host.hidden = true;
    document.body.appendChild(host);
    return host;
  }

  function toast(message, type='success') {
    const host = ensureToast();
    host.textContent = String(message || '');
    host.className = `household-toast household-toast-${type === 'error' ? 'error' : 'success'}`;
    host.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { host.hidden = true; }, 3500);
  }

  window.HouseholdUI = Object.freeze({
    safe, statusBadge, stateQualityBadge, deviceHeader, warningBox,
    actionButton, actionGrid, deviceDetails, deviceCard, toast,
  });
})();
