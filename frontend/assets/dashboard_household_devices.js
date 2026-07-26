(() => {
  'use strict';
  if (window.__householdDevicesInstalled) return;
  window.__householdDevicesInstalled = true;

  const UI = window.HouseholdUI;
  const state = {devices:[], loading:false};
  const safe = UI.safe;
  const status = device => device.online === true ? 'online' : device.online === false ? 'offline' : 'unknown';
  const disabledButton = (label, reason) => UI.actionButton({label, disabled:true, reason});
  const cameraReasons = {
    camera_disabled:'Camera is disabled in persistent configuration.',
    camera_timeout:'Camera discovery timed out.',
    onvif_unavailable:'ONVIF connectivity is unavailable.',
    onvif_provider_unavailable:'ONVIF support is not installed.',
    rtsp_configuration_incomplete:'Camera stream configuration is incomplete.',
    rtsp_unreachable:'Camera stream connectivity is unavailable.',
    tapo_native_provider_unavailable:'Tapo-native camera support is unavailable.',
    read_only_provider_unavailable:'No verified read-only camera provider is configured.',
  };
  const userReason = device => {
    if (!device.unavailable_reason) return '';
    if (device.category === 'camera') {
      if (device.unavailable_reason === 'Configuration unavailable') return 'Configuration unavailable.';
      return cameraReasons[device.unavailable_reason] || 'Camera capability is unavailable.';
    }
    if (device.id === 'bed-room-air-conditioner') return 'Control path is not verified.';
    return 'Controls are not configured yet.';
  };

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
    const stateText = device.state_quality === 'assumed' ? 'IR has no device feedback.' : '';
    const details = UI.deviceDetails({
      content: `<div class="household-detail-grid">
        <div class="household-detail-item"><span>Category</span><strong>${safe(device.category)}</strong></div>
        <div class="household-detail-item"><span>Health</span><strong>${safe(device.health)}</strong></div>
        ${device.unavailable_reason ? `<div class="household-detail-item"><span>Technical status</span><strong>${safe(device.unavailable_reason)}</strong></div>` : ''}
      </div>`,
    });
    return UI.deviceCard({
      id:device.id, title:device.display_name,
      room:device.room === 'bed_room' ? 'Bed Room' : 'Living Room',
      status:status(device), quality:device.state_quality,
      state:stateText, warning:userReason(device),
      actions:controls, details,
    });
  }

  function renderEntertainment() {
    window.renderLgTvCompact?.();
    const grid = document.querySelector('[data-page="entertainment"] .grid');
    if (!grid) return;
    let host = document.getElementById('soundbarHouseholdCard');
    if (!host) {
      host = document.createElement('div');
      host.id = 'soundbarHouseholdCard';
      host.className = 'household-grid household-entertainment-grid';
      grid.appendChild(host);
    }
    const device = state.devices.find(item => item.id === 'living-room-samsung-soundbar');
    if (!device) return;
    const reason = userReason(device) || 'Controls are unavailable.';
    host.innerHTML = card(device, ['Power','Volume +','Volume -','Mute','Source'].map(label => disabledButton(label, reason)).join(''));
  }

  function renderClimate() {
    const host = document.getElementById('climateControls');
    if (!host) return;
    host.className = 'household-grid';
    const devices = state.devices.filter(item => ['living-room-air-conditioner','living-room-fan','bed-room-air-conditioner'].includes(item.id));
    host.innerHTML = devices.map(device => {
      const reason = userReason(device) || 'Controls are unavailable.';
      const labels = device.category === 'fan'
        ? ['Power','Speed','Oscillation','Timer']
        : ['Power','Mode','Temperature','Fan speed','Swing'];
      return card(device, labels.map(label => disabledButton(label, reason)).join(''));
    }).join('');
  }

  function renderCameras() {
    const host = document.getElementById('cameraControls');
    if (!host) return;
    host.className = 'household-grid';
    const devices = state.devices.filter(item => item.category === 'camera');
    host.innerHTML = devices.map(device => {
      const capabilities = device.capabilities || {};
      const cameraState = device.state || {};
      const discovered = Array.isArray(cameraState.discovered_capabilities) ? cameraState.discovered_capabilities : [];
      const reason = userReason(device) || 'Capability unavailable';
      const readonlyReason = 'Control is unavailable in read-only camera mode.';
      const controls = [];
      if (device.unavailable_reason === 'Configuration unavailable') {
        ['Snapshot','Live View','PTZ','Presets','Home'].forEach(label => controls.push(disabledButton(label, reason)));
      } else {
        if (capabilities.snapshot) controls.push(UI.actionButton({label:'Snapshot', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="snapshot"`}));
        if (capabilities.live_stream) controls.push(UI.actionButton({label:'Live View', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="live"`}));
        if (discovered.includes('ptz_move')) controls.push(disabledButton('PTZ', readonlyReason));
        if (capabilities.presets) controls.push(UI.actionButton({label:'Presets', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="presets"`}));
        if (discovered.includes('home_position')) controls.push(disabledButton('Home', readonlyReason));
      }
      const model = cameraState.provider_verified && cameraState.model ? cameraState.model : 'Not verified';
      const vendor = cameraState.provider_verified && cameraState.vendor ? cameraState.vendor : 'Not verified';
      const lastUpdate = cameraState.last_update
        ? new Date(cameraState.last_update * 1000).toLocaleString()
        : 'Not available';
      const badges = Object.entries(capabilities)
        .filter(([, available]) => available)
        .map(([name]) => `<span class="household-badge">${safe(name.replaceAll('_', ' '))}</span>`)
        .join('');
      const preview = capabilities.snapshot
        ? `<img class="household-camera-preview" src="/api/camera-control/${encodeURIComponent(device.id)}/snapshot" alt="${safe(`${device.display_name} snapshot`)}" loading="lazy">`
        : '';
      return UI.deviceCard({
        id:device.id, title:device.display_name,
        room:device.room === 'bed_room' ? 'Bed Room' : device.room === 'living_room' ? 'Living Room' : 'Location unknown',
        status:status(device), quality:device.state_quality,
        state:cameraState.model && cameraState.provider_verified ? cameraState.model : '',
        warning:userReason(device), actions:`${preview}${controls.join('')}`,
        details:UI.deviceDetails({
          content:`<div class="household-detail-grid">
            <div class="household-detail-item"><span>Vendor</span><strong>${safe(vendor)}</strong></div>
            <div class="household-detail-item"><span>Model</span><strong>${safe(model)}</strong></div>
            <div class="household-detail-item"><span>Last update</span><strong>${safe(lastUpdate)}</strong></div>
            <div class="household-detail-item"><span>Capabilities</span><strong>${badges || 'None verified'}</strong></div>
          </div>`,
        }),
      });
    }).join('');
    host.querySelectorAll('[data-household-camera]').forEach(button => button.addEventListener('click', async () => {
      const identifier = encodeURIComponent(button.dataset.householdCamera);
      if (button.dataset.cameraAction === 'snapshot') {
        window.open(`/api/camera-control/${identifier}/snapshot`, '_blank', 'noopener');
        return;
      }
      button.disabled = true;
      try {
        const resource = button.dataset.cameraAction === 'presets' ? 'presets' : 'stream';
        const response = await fetch(`/api/camera-control/${identifier}/${resource}`);
        if (!response.ok) throw new Error('Camera information is unavailable');
        const payload = await response.json();
        if (resource === 'presets') {
          const names = (payload.presets || []).map(item => item.name).filter(Boolean);
          UI.toast(names.length ? `Presets: ${names.join(', ')}` : 'No presets are available.', 'success');
        } else {
          UI.toast(payload.stream?.available ? 'Live view is available.' : 'Live view is unavailable.', payload.stream?.available ? 'success' : 'error');
        }
      } catch (error) {
        UI.toast(error.message, 'error');
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
