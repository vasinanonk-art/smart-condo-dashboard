(() => {
  'use strict';
  if (window.__householdDevicesInstalled) return;
  window.__householdDevicesInstalled = true;

  const UI = window.HouseholdUI;
  const state = {devices:[], loading:false};
  const safe = UI.safe;
  const status = device => device.online === true ? 'online' : device.online === false ? 'offline' : 'unknown';
  const disabledButton = (label, reason) => UI.actionButton({label, disabled:true, reason});
  const userReason = device => {
    if (!device.unavailable_reason) return '';
    if (device.category === 'camera') return 'Configuration unavailable.';
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
      const reason = userReason(device) || 'Capability unavailable';
      const controls = [
        capabilities.snapshot ? UI.actionButton({label:'Snapshot', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="snapshot"`}) : disabledButton('Snapshot', reason),
        disabledButton('Live stream', capabilities.live_stream ? 'Authenticated stream metadata is not available in this view.' : reason),
        capabilities.ptz_move ? UI.actionButton({label:'PTZ', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="move"`}) : disabledButton('PTZ', reason),
        capabilities.zoom ? UI.actionButton({label:'Zoom', attributes:`data-household-camera="${safe(device.id)}" data-camera-action="zoom"`}) : disabledButton('Zoom', reason),
      ].join('');
      return UI.deviceCard({
        id:device.id, title:device.display_name, room:'Camera',
        status:status(device), quality:device.state_quality,
        warning:userReason(device), actions:controls,
        details:UI.deviceDetails({
          content:`<div class="household-detail-item"><span>Capabilities</span><strong>${Object.values(capabilities).some(Boolean) ? 'Capability-driven controls available' : 'Configuration required'}</strong></div>`,
        }),
      });
    }).join('');
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
