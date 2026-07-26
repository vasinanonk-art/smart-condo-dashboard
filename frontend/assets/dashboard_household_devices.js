(() => {
  'use strict';
  if (window.__householdDevicesInstalled) return;
  window.__householdDevicesInstalled = true;

  const state = {devices:[], loading:false};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const status = device => device.online === true ? 'Online' : device.online === false ? 'Offline' : 'Unknown';
  const quality = device => ({confirmed:'Confirmed', assumed:'Assumed', unknown:'Unknown'})[device.state_quality] || 'Unknown';
  const disabledButton = (label, reason) => `<button type="button" class="btn ghost" disabled title="${safe(reason)}">${safe(label)}</button>`;

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const response = await fetch('/api/devices');
      if (!response.ok) throw new Error('device_registry_unavailable');
      const payload = await response.json();
      state.devices = payload.devices || [];
      render();
    } catch (error) {
      console.warn('Household device registry unavailable:', error.message);
    } finally {
      state.loading = false;
    }
  }

  function card(device, controls) {
    return `<article class="card household-device-card">
      <div class="household-device-head"><div><h3>${safe(device.display_name)}</h3><small>${safe(device.room === 'bed_room' ? 'Bed Room' : 'Living Room')}</small></div><span class="status-pill">${safe(status(device))}</span></div>
      <div class="household-device-state">State quality: ${safe(quality(device))}${device.state_quality === 'assumed' ? ' · IR has no feedback' : ''}</div>
      ${device.unavailable_reason ? `<div class="household-device-reason">${safe(device.unavailable_reason)}</div>` : ''}
      <div class="household-controls">${controls}</div>
    </article>`;
  }

  function renderEntertainment() {
    window.renderLgTvCompact?.();
    const grid = document.querySelector('[data-page="entertainment"] .grid');
    if (!grid) return;
    let host = document.getElementById('soundbarHouseholdCard');
    if (!host) {
      host = document.createElement('div');
      host.id = 'soundbarHouseholdCard';
      host.className = 'span-12';
      grid.appendChild(host);
    }
    const device = state.devices.find(item => item.id === 'living-room-samsung-soundbar');
    if (!device) return;
    const reason = device.unavailable_reason || 'Controls are unavailable.';
    host.innerHTML = card(device, ['Power','Volume +','Volume -','Mute','Source'].map(label => disabledButton(label, reason)).join(''));
  }

  function renderClimate() {
    const host = document.getElementById('climateControls');
    if (!host) return;
    const devices = state.devices.filter(item => ['living-room-air-conditioner','living-room-fan','bed-room-air-conditioner'].includes(item.id));
    host.innerHTML = devices.map(device => {
      const reason = device.unavailable_reason || 'Controls are unavailable.';
      const labels = device.category === 'fan'
        ? ['Power','Speed','Oscillation','Timer']
        : ['Power','Mode','Temperature','Fan speed','Swing'];
      return card(device, labels.map(label => disabledButton(label, reason)).join(''));
    }).join('');
  }

  function renderCameras() {
    const host = document.getElementById('cameraControls');
    if (!host) return;
    const devices = state.devices.filter(item => item.category === 'camera');
    host.innerHTML = `<div class="household-camera-grid">${devices.map(device => {
      const capabilities = device.capabilities || {};
      const reason = device.unavailable_reason || 'Capability unavailable';
      const controls = [
        capabilities.snapshot ? `<button type="button" class="btn ghost" data-household-camera="${safe(device.id)}" data-camera-action="snapshot">Snapshot</button>` : disabledButton('Snapshot', reason),
        capabilities.live_stream ? `<button type="button" class="btn ghost" disabled title="Authenticated stream metadata is not available in this view.">Live stream</button>` : disabledButton('Live stream', reason),
        capabilities.ptz_move ? `<button type="button" class="btn ghost" data-household-camera="${safe(device.id)}" data-camera-action="move">PTZ</button>` : disabledButton('PTZ', reason),
        capabilities.zoom ? `<button type="button" class="btn ghost" data-household-camera="${safe(device.id)}" data-camera-action="zoom">Zoom</button>` : disabledButton('Zoom', reason),
      ].join('');
      return `<article class="household-camera-card">
      <h3>${safe(device.display_name)}</h3><div class="household-device-state">Status: ${safe(status(device))}</div>
      ${device.unavailable_reason ? `<div class="household-device-reason">${safe(device.unavailable_reason)}</div>` : ''}
      <div class="household-controls">${controls}</div>
    </article>`;
    }).join('')}</div>`;
    host.querySelectorAll('[data-household-camera]').forEach(button => button.addEventListener('click', async () => {
      const identifier = encodeURIComponent(button.dataset.householdCamera);
      if (button.dataset.cameraAction === 'snapshot') {
        window.open(`/api/camera-control/${identifier}/snapshot`, '_blank', 'noopener');
        return;
      }
      const command = button.dataset.cameraAction === 'zoom'
        ? {command:'zoom', zoom:0.2, duration:0.2}
        : {command:'move', direction:'right', duration:0.2};
      button.disabled = true;
      try {
        const response = await fetch(`/api/camera-control/${identifier}/command`, {
          method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(command),
        });
        if (!response.ok) throw new Error('Camera command failed');
      } catch (error) {
        console.warn(error.message);
      } finally {
        button.disabled = false;
      }
    }));
  }

  function render() {
    renderEntertainment();
    renderClimate();
    renderCameras();
  }

  const originalRenderPage = window.renderPage;
  window.renderPage = function deviceCentricRender(page = window.currentPage?.()) {
    originalRenderPage(page);
    render();
  };
  load();
})();
