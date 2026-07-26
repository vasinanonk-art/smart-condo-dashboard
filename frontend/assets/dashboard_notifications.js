(() => {
  'use strict';
  if (window.__notificationCenterInstalled) return;
  window.__notificationCenterInstalled = true;

  const UI = window.HouseholdUI;
  const state = {items:[], unread:0, loaded:false, loading:false, loadPromise:null};
  const safe = UI.safe;
  const relative = timestamp => {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(timestamp || 0)));
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return `${Math.floor(seconds / 86400)} d ago`;
  };

  async function request(url, method='GET') {
    const response = await fetch(url, {method});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'notification_request_failed');
    return payload;
  }

  function close({restoreFocus=true}={}) {
    const panel = document.getElementById('notificationPanel');
    const button = document.getElementById('notificationButton');
    if (!panel || panel.hidden) return;
    panel.hidden = true;
    button?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) button?.focus();
  }

  async function open() {
    const panel = document.getElementById('notificationPanel');
    const button = document.getElementById('notificationButton');
    if (!panel) return;
    panel.hidden = false;
    button?.setAttribute('aria-expanded', 'true');
    if (!state.loaded) await load();
    render();
    panel.querySelector('[data-notification-close]')?.focus();
  }

  function install() {
    const row = document.querySelector('.topbar .status-row');
    if (!row) return;
    document.getElementById('notificationButton')?.remove();
    document.getElementById('notificationPanel')?.remove();
    const button = document.createElement('button');
    button.id = 'notificationButton';
    button.type = 'button';
    button.className = 'household-notification-trigger';
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'notificationPanel');
    button.innerHTML = 'Notifications <span id="notificationCount" class="household-notification-count" aria-label="Unread notifications" hidden>0</span>';
    row.prepend(button);
    const panel = document.createElement('aside');
    panel.id = 'notificationPanel';
    panel.className = 'household-notification-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'false');
    panel.setAttribute('aria-labelledby', 'notificationPanelTitle');
    panel.hidden = true;
    document.body.appendChild(panel);
    button.addEventListener('click', () => panel.hidden ? open() : close());
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') close();
    });
    document.addEventListener('pointerdown', event => {
      if (panel.hidden || panel.contains(event.target) || button.contains(event.target)) return;
      close({restoreFocus:false});
    });
    load({renderPanel:false});
  }

  function updateCount() {
    state.unread = state.items.filter(item => !item.read).length;
    const host = document.getElementById('notificationCount');
    if (!host) return;
    host.textContent = String(state.unread);
    host.hidden = state.unread === 0;
  }

  function notificationButton(label, attributes) {
    return UI.actionButton({label, className:'household-notification-action', attributes});
  }

  function render() {
    const panel = document.getElementById('notificationPanel');
    if (!panel || panel.hidden) return;
    panel.innerHTML = `<div class="household-notification-head"><strong id="notificationPanelTitle">Notifications · ${state.unread} unread</strong>${notificationButton('Close', 'data-notification-close')}</div>
      <div class="household-notification-toolbar">${notificationButton('Mark all read', 'data-notification-read-all')}${notificationButton('Clear all', 'data-notification-clear-all')}</div>
      <div class="household-notification-list">${state.items.length ? state.items.map(item => `<article class="household-notification-item household-notification-${safe(item.type)} ${item.read ? 'household-notification-read' : 'household-notification-unread'}">
        <div class="household-notification-copy"><strong>${safe(item.title)}</strong><p>${safe(item.message)}</p><small>${safe(item.source)} · ${safe(relative(item.created_ts))}</small></div>
        <div class="household-notification-actions">${item.read ? '' : notificationButton('Mark read', `data-notification-read="${safe(item.id)}"`)}${notificationButton('Delete', `data-notification-clear="${safe(item.id)}"`)}</div>
      </article>`).join('') : '<div class="household-notification-empty">No notifications.</div>'}</div>`;
    panel.querySelector('[data-notification-close]').onclick = () => close();
    panel.querySelector('[data-notification-read-all]').onclick = async () => {
      try {
        await request('/api/notifications/mark-all-read', 'POST');
        state.items.forEach(item => { item.read = true; });
        updateCount();
        render();
      } catch (error) { UI.toast(error.message, 'error'); }
    };
    panel.querySelector('[data-notification-clear-all]').onclick = async () => {
      if (!confirm('Clear all notifications?')) return;
      try {
        await request('/api/notifications/clear-all', 'DELETE');
        state.items = [];
        updateCount();
        render();
      } catch (error) { UI.toast(error.message, 'error'); }
    };
    panel.querySelectorAll('[data-notification-read]').forEach(button => button.onclick = async () => {
      try {
        await request(`/api/notifications/${encodeURIComponent(button.dataset.notificationRead)}/read`, 'POST');
        const item = state.items.find(value => value.id === button.dataset.notificationRead);
        if (item) item.read = true;
        updateCount();
        render();
      } catch (error) { UI.toast(error.message, 'error'); }
    });
    panel.querySelectorAll('[data-notification-clear]').forEach(button => button.onclick = async () => {
      try {
        await request(`/api/notifications/${encodeURIComponent(button.dataset.notificationClear)}`, 'DELETE');
        state.items = state.items.filter(value => value.id !== button.dataset.notificationClear);
        updateCount();
        render();
      } catch (error) { UI.toast(error.message, 'error'); }
    });
  }

  function load({renderPanel=true}={}) {
    if (state.loadPromise) return state.loadPromise;
    state.loading = true;
    state.loadPromise = (async () => {
      try {
        const payload = await request('/api/notifications');
        state.items = payload.notifications || [];
        state.loaded = true;
        updateCount();
        if (renderPanel || !document.getElementById('notificationPanel')?.hidden) render();
      } catch (error) {
        UI.toast(error.message, 'error');
      } finally {
        state.loading = false;
        state.loadPromise = null;
      }
    })();
    return state.loadPromise;
  }

  install();
})();
