(() => {
  'use strict';
  if (window.__notificationCenterInstalled) return;
  window.__notificationCenterInstalled = true;

  const state = {items:[], unread:0, loaded:false, loading:false};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
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
  function install() {
    const row = document.querySelector('.topbar .status-row');
    if (!row) return;
    document.getElementById('notificationButton')?.remove();
    document.getElementById('notificationPanel')?.remove();
    const button = document.createElement('button');
    button.id = 'notificationButton';
    button.type = 'button';
    button.className = 'btn ghost notification-button';
    button.innerHTML = 'Notifications <span id="notificationCount" class="notification-count" hidden>0</span>';
    row.prepend(button);
    const panel = document.createElement('aside');
    panel.id = 'notificationPanel';
    panel.className = 'notification-panel';
    panel.setAttribute('aria-label', 'Notification center');
    panel.hidden = true;
    document.body.appendChild(panel);
    button.addEventListener('click', async () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) await load();
    });
  }
  function updateCount() {
    state.unread = state.items.filter(item => !item.read).length;
    const host = document.getElementById('notificationCount');
    if (!host) return;
    host.textContent = String(state.unread);
    host.hidden = state.unread === 0;
  }
  function render() {
    const panel = document.getElementById('notificationPanel');
    if (!panel) return;
    panel.innerHTML = `<div class="notification-head"><strong>Notifications · ${state.unread} unread</strong><button class="btn ghost" type="button" data-notification-close>Close</button></div>
      <div class="notification-toolbar"><button class="btn ghost" type="button" data-notification-read-all>Mark all read</button><button class="btn ghost" type="button" data-notification-clear-all>Clear all</button></div>
      <div class="notification-list">${state.items.length ? state.items.map(item => `<article class="notification-item ${safe(item.type)} ${item.read ? 'read' : 'unread'}">
        <div><strong>${safe(item.title)}</strong><p>${safe(item.message)}</p><small class="notification-source">${safe(item.source)} · ${safe(relative(item.created_ts))}</small></div>
        <div class="notification-actions">${item.read ? '' : `<button class="btn ghost" type="button" data-notification-read="${safe(item.id)}">Read</button>`}<button class="btn ghost" type="button" data-notification-clear="${safe(item.id)}">Clear</button></div>
      </article>`).join('') : '<div class="notification-empty">No notifications.</div>'}</div>`;
    panel.querySelector('[data-notification-close]').onclick = () => { panel.hidden = true; };
    panel.querySelector('[data-notification-read-all]').onclick = async () => {
      await request('/api/notifications/mark-all-read', 'POST');
      state.items.forEach(item => { item.read = true; }); updateCount(); render();
    };
    panel.querySelector('[data-notification-clear-all]').onclick = async () => {
      if (!confirm('Clear all notifications?')) return;
      await request('/api/notifications/clear-all', 'DELETE');
      state.items = []; updateCount(); render();
    };
    panel.querySelectorAll('[data-notification-read]').forEach(button => button.onclick = async () => {
      await request(`/api/notifications/${encodeURIComponent(button.dataset.notificationRead)}/read`, 'POST');
      const item = state.items.find(value => value.id === button.dataset.notificationRead);
      if (item) item.read = true;
      updateCount(); render();
    });
    panel.querySelectorAll('[data-notification-clear]').forEach(button => button.onclick = async () => {
      await request(`/api/notifications/${encodeURIComponent(button.dataset.notificationClear)}`, 'DELETE');
      state.items = state.items.filter(value => value.id !== button.dataset.notificationClear);
      updateCount(); render();
    });
  }
  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const payload = await request('/api/notifications');
      state.items = payload.notifications || [];
      state.loaded = true;
      updateCount();
      render();
    } finally {
      state.loading = false;
    }
  }
  install();
})();
