(() => {
  'use strict';
  if (window.__dashboardPresenceUiInstalled) return;
  window.__dashboardPresenceUiInstalled = true;

  const FIELDS = ['last_seen','last_seen_ts','latest_ts','updated_ts','last_update','timestamp','ts'];
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');

  function toEpoch(value) {
    if (value === null || value === undefined || value === '' || value === 0 || value === '0') return null;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) {
      const seconds = numeric > 1e12 ? Math.floor(numeric / 1000) : Math.floor(numeric);
      return seconds > 0 ? seconds : null;
    }
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed / 1000) : null;
  }

  function observationTs(item) {
    for (const field of FIELDS) {
      const value = toEpoch(item?.[field]);
      if (value) return value;
    }
    return null;
  }

  function relative(ts) {
    if (!ts) return 'Not available';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (seconds < 45) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return `${Math.floor(seconds / 86400)} day${seconds < 172800 ? '' : 's'} ago`;
  }

  function absolute(ts) {
    if (!ts) return 'Not available';
    return `${new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Bangkok', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false
    }).format(new Date(ts * 1000))} ICT`;
  }

  window.renderPresence = function renderPresenceWithLastSeen() {
    const host = document.getElementById('presenceList');
    if (!host) return;
    const people = window.S?.presence || {};
    host.innerHTML = ['beer','seem'].map(key => {
      const item = people[key] || {};
      const name = key === 'beer' ? 'Beer' : 'Seem';
      const status = item.status || item.state || 'Unknown';
      const automation = window.S?.system?.automation?.people?.[key] || {};
      const ts = observationTs(item);
      const stateClass = String(status).toLowerCase() === 'home' ? 'ok' : String(status).toLowerCase().includes('recent') ? 'warn' : 'bad';
      const lastSeen = ts ? `<strong>${safe(relative(ts))}<small>${safe(absolute(ts))}</small></strong>` : '<strong>Not available</strong>';
      return `<div class="card presence-card"><div class="label presence-name">${safe(name)}</div><div class="state ${stateClass}">${safe(status)}</div><div class="kv"><span>Source</span><strong>${safe(item.source || 'Not available')}</strong></div><div class="kv presence-last-seen"><span>Last Seen</span>${lastSeen}</div><div class="kv"><span>Automation home</span><strong>${automation.automation_home == null ? 'Unknown' : automation.automation_home ? 'Home' : 'Away'}</strong></div><div class="kv"><span>Cooldown</span><strong>${Number(automation.cooldown_remaining_sec || 0)}s</strong></div></div>`;
    }).join('');
    if (window.DashboardModules?.renderAutomations) window.DashboardModules.renderAutomations(document.getElementById('automationEvents'), window.automationAction);
  };

  window.DashboardPresenceTime = {toEpoch, observationTs, fields: FIELDS.slice()};
})();