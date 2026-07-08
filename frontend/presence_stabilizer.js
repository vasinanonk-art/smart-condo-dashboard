(function () {
  function presenceLabel(item) {
    if (item && item.home && item.online) return { text: '🟢 Home', cls: 'home' };
    if (item && item.home && !item.online) return { text: '🟡 Recently Seen', cls: 'stale' };
    return { text: '⚪ Away', cls: 'away' };
  }

  function fmtLastSeen(ts) {
    const value = Number(ts || 0);
    if (!value) return '--';
    return new Date(value * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  window.renderPresence = function (presence) {
    const box = document.getElementById('presenceCards');
    if (!box) return;
    const source = presence && typeof presence === 'object' ? presence : {};
    const entries = Object.entries(source).filter(function (entry) {
      return entry[1] && typeof entry[1] === 'object';
    });
    if (!entries.length) {
      box.innerHTML = '<div class="metric"><div class="muted">Presence</div><div class="num">--</div></div>';
      return;
    }
    box.innerHTML = '';
    entries.forEach(function ([key, item]) {
      const label = presenceLabel(item);
      const el = document.createElement('div');
      el.className = 'metric';
      el.innerHTML = `<div class="presence-name">${item.name || key}</div><div class="presence-state ${label.cls}">${label.text}</div><div class="presence-ip">Source: ${item.source || '-'}</div><div class="presence-small">Last Seen: ${fmtLastSeen(item.last_seen)}</div>`;
      box.appendChild(el);
    });
  };

  window.loadCondoStatus = async function () {
    try {
      const j = await getJson('/api/condo/status');
      deviceState.condo.sensor = j.sensor || deviceState.condo.sensor || {};
      deviceState.condo.presence = j.presence || deviceState.condo.presence || {};
      renderSensor(deviceState.condo.sensor);
      renderPresence(deviceState.condo.presence);
    } catch (e) {
      if (deviceState.condo && deviceState.condo.sensor) renderSensor(deviceState.condo.sensor);
      if (deviceState.condo && deviceState.condo.presence) renderPresence(deviceState.condo.presence);
      show('Presence refresh failed, keeping previous state');
    }
  };

  if (!window._presenceStableRefresh) {
    window._presenceStableRefresh = setInterval(window.loadCondoStatus, 10000);
  }
})();
