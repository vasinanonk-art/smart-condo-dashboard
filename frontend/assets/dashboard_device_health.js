(() => {
  'use strict';
  if (window.DeviceHealthDashboard) return;

  const POLL_INTERVAL_MS = 30000;
  const state = {loading:false, timer:null, payload:null};
  const safe = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');

  function relativeTime(value, now = Date.now()) {
    if (!value) return 'Not seen';
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) return 'Not available';
    const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
    if (seconds < 5) return 'Just now';
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return new Date(timestamp).toLocaleString([], {
      year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit',
    });
  }

  function statusLabel(device) {
    if (device.online === true) return 'Online';
    if (device.online === false) return 'Offline';
    return 'Unknown';
  }

  function healthLabel(device) {
    if (device.health === 'healthy') return 'Healthy';
    if (device.health === 'degraded') return 'Attention';
    if (device.health === 'offline') return 'Offline';
    return 'Unknown';
  }

  function card(device) {
    const latency = Number.isFinite(Number(device.response_time_ms))
      ? `${Number(device.response_time_ms).toFixed(1)} ms`
      : 'Not available';
    return `<article class="device-health-card" data-device-health-id="${safe(device.id)}">
      <div class="device-health-header">
        <h3>${safe(device.display_name)}</h3>
        <span class="device-health-indicator" data-health="${safe(device.health_indicator)}" aria-label="${safe(healthLabel(device))}" title="${safe(healthLabel(device))}"></span>
      </div>
      <div class="device-health-badges">
        <span class="sc-status-chip" data-status="${device.online === true ? 'success' : device.online === false ? 'critical' : 'warning'}">${safe(statusLabel(device))}</span>
        <span class="sc-status-chip" data-status="${device.health_indicator === 'green' ? 'success' : device.health_indicator === 'red' ? 'critical' : 'warning'}">${safe(healthLabel(device))}</span>
      </div>
      <div class="device-health-detail"><span>Last Seen</span><strong>${safe(relativeTime(device.last_seen))}</strong></div>
      <div class="device-health-detail"><span>Response Time</span><strong>${safe(latency)}</strong></div>
    </article>`;
  }

  function render(payload) {
    const host = document.getElementById('deviceHealthDashboard');
    if (!host) return;
    const devices = Array.isArray(payload?.devices) ? payload.devices : [];
    const summary = payload?.summary || {};
    host.innerHTML = `<div class="device-health-summary" aria-label="Device health summary">
      <div class="device-health-summary-item"><span>Online</span><strong>${safe(summary.online ?? 0)}</strong></div>
      <div class="device-health-summary-item"><span>Offline</span><strong>${safe(summary.offline ?? 0)}</strong></div>
      <div class="device-health-summary-item"><span>Unknown</span><strong>${safe(summary.unknown ?? 0)}</strong></div>
    </div>
    <div class="device-health-grid">${devices.length
      ? devices.map(card).join('')
      : '<p class="device-health-empty">No device health observations are available.</p>'}</div>`;
  }

  async function load() {
    if (state.loading || document.hidden) return;
    const host = document.getElementById('deviceHealthDashboard');
    if (!host) return;
    state.loading = true;
    host.setAttribute('aria-busy', 'true');
    try {
      const response = await fetch('/api/device-health', {
        headers:{Accept:'application/json'},
        credentials:'same-origin',
      });
      if (!response.ok) throw new Error(`device health request failed (${response.status})`);
      state.payload = await response.json();
      render(state.payload);
    } catch (error) {
      if (!state.payload) {
        host.innerHTML = '<p class="device-health-error">Device health is temporarily unavailable.</p>';
      }
      console.warn('Device health refresh failed:', error.message);
    } finally {
      state.loading = false;
      host.setAttribute('aria-busy', 'false');
    }
  }

  function start() {
    if (state.timer) return;
    load();
    state.timer = window.setInterval(load, POLL_INTERVAL_MS);
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) load();
  });
  document.addEventListener('DOMContentLoaded', start);

  window.DeviceHealthDashboard = {
    load,
    render,
    relativeTime,
    statusLabel,
    state,
  };
})();
