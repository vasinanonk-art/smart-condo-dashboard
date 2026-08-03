(() => {
  'use strict';
  if (window.__householdDevicesInstalled) return;
  window.__householdDevicesInstalled = true;

  const UI = window.HouseholdUI;
  const state = {devices:[], loading:false, inFlight:new Set()};
  const safe = UI.safe;
  const status = device => device.online === true ? 'online' : device.online === false ? 'offline' : 'unknown';
  const disabledButton = (label, reason) => UI.actionButton({label, disabled:true, reason});
  const cameraReasons = {
    camera_disabled:'Camera is disabled in persistent configuration.',
    camera_credentials_missing:'Camera credentials are not configured.',
    camera_timeout:'Camera discovery timed out.',
    invalid_credentials:'Camera credentials were rejected.',
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
    if (device.unavailable_reason === 'Configured Tapo IR remote discovered; transmit interface is not verified.') {
      return device.unavailable_reason;
    }
    return 'Controls are not configured yet.';
  };

  function irCommandButton(device, capability, command) {
    return UI.actionButton({
      label:command.label,
      attributes:`data-household-ir-device="${safe(device.id)}" data-household-ir-command="${safe(command.id)}" data-household-ir-confirm="${capability.confirm ? 'true' : 'false'}" data-household-ir-icon="${safe(command.icon || capability.icon || '')}" aria-label="${safe(`${device.display_name}: ${command.label}`)}"`,
    });
  }

  function irCapabilityControl(device, capability) {
    const commands = Array.isArray(capability.commands) ? capability.commands : [];
    if (!commands.length) return '';
    if (capability.type === 'select') {
      const available = (capability.values || []).filter(value => commands.some(command => command.value === value));
      if (!available.length) return '';
      return `<label class="household-ir-field"><span>${safe(capability.label)}</span><select data-household-ir-device="${safe(device.id)}" data-household-ir-capability="${safe(capability.id)}" data-household-ir-confirm="${capability.confirm ? 'true' : 'false'}"><option value="">Select</option>${available.map(value => `<option value="${safe(value)}">${safe(value)}</option>`).join('')}</select></label>`;
    }
    if (capability.type === 'range') {
      const expected = Math.floor((Number(capability.max) - Number(capability.min)) / Number(capability.step)) + 1;
      const available = commands.filter(command => Number.isFinite(Number(command.value)));
      if (available.length !== expected) return '';
      const commanded = Number(device.state?.ir_diagnostics?.last_commanded?.target_temperature);
      const selected = Number.isInteger(commanded) && commanded >= capability.min && commanded <= capability.max
        ? commanded : capability.min;
      return `<label class="household-ir-field"><span>${safe(capability.label)} <output>${safe(selected)}${safe(capability.unit || '')}</output></span><input type="range" min="${safe(capability.min)}" max="${safe(capability.max)}" step="${safe(capability.step)}" value="${safe(selected)}" data-household-ir-device="${safe(device.id)}" data-household-ir-capability="${safe(capability.id)}" data-household-ir-unit="${safe(capability.unit || '')}" data-household-ir-confirm="${capability.confirm ? 'true' : 'false'}"></label>`;
    }
    return commands.map(command => irCommandButton(device, capability, command)).join('');
  }

  function irActions(device) {
    const capabilities = Array.isArray(device.capabilities?.ir) ? device.capabilities.ir : [];
    const groups = new Map();
    capabilities.forEach(capability => {
      const controls = irCapabilityControl(device, capability);
      if (!controls) return;
      const group = capability.group || 'main';
      groups.set(group, `${groups.get(group) || ''}<div class="household-ir-capability" data-ir-capability-type="${safe(capability.type)}">${controls}</div>`);
    });
    return [...groups.entries()].map(([group, controls]) => `<section class="household-ir-group" data-ir-group="${safe(group)}">${controls}</section>`).join('');
  }

  function bindIrCommands(host) {
    const send = async (control, body) => {
      if (control.dataset.householdIrConfirm === 'true' && !window.confirm('Send this IR command?')) return;
      const target = control.dataset.householdIrDevice;
      if (!target || state.inFlight.has(target)) return;
      state.inFlight.add(target);
      const controls = [...host.querySelectorAll('[data-household-ir-device]')]
        .filter(item => item.dataset.householdIrDevice === target);
      controls.forEach(item => { item.disabled = true; });
      try {
        const response = await fetch(`/api/ir/${encodeURIComponent(target)}/command`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify(body),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(payload.detail || 'IR command failed');
        const device = state.devices.find(item => item.id === target);
        if (device && payload.last_commanded) {
          device.state = device.state || {};
          device.state.ir_diagnostics = device.state.ir_diagnostics || {};
          device.state.ir_diagnostics.last_commanded = payload.last_commanded;
          device.state.ir_diagnostics.physical_state_confirmed = false;
        }
        UI.toast('Command sent; IR state is not physically confirmed.', 'success');
        render();
      } catch (error) {
        UI.toast(error.message, 'error');
      } finally {
        state.inFlight.delete(target);
        controls.forEach(item => { item.disabled = false; });
      }
    };
    host?.querySelectorAll('[data-household-ir-command]').forEach(button => button.addEventListener('click', () => {
      send(button, {command:button.dataset.householdIrCommand});
    }));
    host?.querySelectorAll('select[data-household-ir-capability]').forEach(select => select.addEventListener('change', () => {
      if (select.value !== '') send(select, {capability:select.dataset.householdIrCapability, value:select.value});
    }));
    host?.querySelectorAll('input[type="range"][data-household-ir-capability]').forEach(input => {
      input.addEventListener('input', () => {
        const output = input.closest('label')?.querySelector('output');
        if (output) output.textContent = `${input.value}${input.dataset.householdIrUnit || ''}`;
      });
      input.addEventListener('change', () => send(input, {
        capability:input.dataset.householdIrCapability,
        value:Number(input.value),
      }));
    });
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const response = await fetch('/api/devices');
      if (!response.ok) throw new Error('device_registry_unavailable');
      const payload = await response.json();
      state.devices = payload.devices || [];
      const bedroomAc = state.devices.find(device => (
        device.id === 'bed-room-air-conditioner'
        && Array.isArray(device.capabilities?.ir)
      ));
      if (bedroomAc) {
        try {
          const statusResponse = await fetch('/api/ir/bed-room-air-conditioner/status');
          if (statusResponse.ok) {
            const statusPayload = await statusResponse.json();
            bedroomAc.state = bedroomAc.state || {};
            bedroomAc.state.ir_diagnostics = bedroomAc.state.ir_diagnostics || {};
            bedroomAc.state.ir_diagnostics.last_commanded = statusPayload.last_commanded || {};
            bedroomAc.state.ir_diagnostics.physical_state_confirmed = false;
          }
        } catch (_error) {
          // The registry remains usable when the explicit cloud status read fails.
        }
      }
      render();
    } catch (error) {
      console.warn('Household device registry unavailable:', error.message);
    } finally {
      state.loading = false;
    }
  }

  function card(device, controls) {
    const stateText = device.state_quality === 'assumed' ? 'IR has no device feedback.' : '';
    const ir = device.state?.ir_diagnostics;
    const irDetails = ir && typeof ir === 'object' ? `
        <div class="household-detail-item"><span>Bridge Status</span><strong>${safe(ir.online === true ? 'Online' : ir.online === false ? 'Offline' : 'Unknown')}</strong></div>
        <div class="household-detail-item"><span>Driver Status</span><strong>${safe(ir.healthy === true ? 'Ready' : ir.authenticated === true ? 'Sender unavailable' : 'Unavailable')}</strong></div>
        <div class="household-detail-item"><span>Queue</span><strong>${safe(ir.pending_queue ?? 0)}</strong></div>
        <div class="household-detail-item"><span>Last Command</span><strong>${safe(ir.last_command || 'None')}</strong></div>
        <div class="household-detail-item"><span>Last Result</span><strong>${safe(ir.last_response || ir.last_error || 'None')}</strong></div>
        <div class="household-detail-item"><span>Latency</span><strong>${safe(Number.isFinite(ir.latency_ms) ? `${ir.latency_ms} ms` : 'Not available')}</strong></div>
        ${ir.remote_discovered ? `<div class="household-detail-item"><span>Configured Remote</span><strong>${safe(ir.configured_remote_name || 'Discovered')}</strong></div>` : ''}
        ${ir.remote_discovered ? `<div class="household-detail-item"><span>Stored Commands</span><strong>${safe(ir.stored_commands_present ? 'Present but unavailable' : 'Not reported')}</strong></div>` : ''}
        ${ir.remote_discovered ? '<div class="household-detail-item"><span>Control Available</span><strong>No</strong></div>' : ''}
      ` : '';
    const details = UI.deviceDetails({
      content: `<div class="household-detail-grid">
        <div class="household-detail-item"><span>Category</span><strong>${safe(device.category)}</strong></div>
        <div class="household-detail-item"><span>Health</span><strong>${safe(device.health)}</strong></div>
        ${device.unavailable_reason ? `<div class="household-detail-item"><span>Technical status</span><strong>${safe(device.unavailable_reason)}</strong></div>` : ''}
        ${irDetails}
      </div>`,
    });
    const sensorSummary = device.id === 'bed-room-t3-hub'
      ? [
          Number.isFinite(device.state?.temperature_c) ? `${device.state.temperature_c.toFixed(1)} °C` : '',
          Number.isFinite(device.state?.humidity_percent) ? `${device.state.humidity_percent.toFixed(0)}% humidity` : '',
        ].filter(Boolean).join(' · ')
      : '';
    const bedroomAcSummary = device.id === 'bed-room-air-conditioner'
      ? [
          Number.isFinite(device.state?.temperature_c) ? `Room ${device.state.temperature_c.toFixed(1)} °C` : '',
          Number.isFinite(device.state?.humidity_percent) ? `${device.state.humidity_percent.toFixed(0)}% humidity` : '',
          device.state?.ir_diagnostics?.last_commanded?.power === 1 ? 'Last commanded: On' : '',
          device.state?.ir_diagnostics?.last_commanded?.power === 0 ? 'Last commanded: Off' : '',
          Number.isInteger(device.state?.ir_diagnostics?.last_commanded?.target_temperature)
            ? `Target ${device.state.ir_diagnostics.last_commanded.target_temperature} °C` : '',
          'IR state not physically confirmed',
        ].filter(Boolean).join(' · ')
      : '';
    const virtualSummary = device.state?.ir_diagnostics?.provider === 'smartlife_cloud'
      && device.category === 'climate'
      && device.state?.ir_diagnostics?.controllable !== true
      ? 'Virtual Device · Controls unavailable' : '';
    return UI.deviceCard({
      id:device.id, title:device.display_name,
      room:device.room === 'bed_room' ? 'Bed Room' : 'Living Room',
      status:status(device), quality:device.state_quality,
      state:sensorSummary || bedroomAcSummary || virtualSummary || stateText, warning:userReason(device),
      actions:controls, details,
    });
  }

  function renderEntertainment() {
    const grid = document.querySelector('[data-page="entertainment"] .grid');
    if (!grid) return;
    let host = document.getElementById('soundbarHouseholdCard');
    if (!host) {
      host = document.createElement('div');
      host.id = 'soundbarHouseholdCard';
      host.className = 'household-grid household-entertainment-grid';
      grid.appendChild(host);
    }
    const devices = state.devices.filter(item => (
      item.category === 'soundbar' || item.id === 'living-room-configured-tv-ir'
    ));
    host.innerHTML = devices.map(device => card(device, irActions(device))).join('');
    bindIrCommands(host);
  }

  function renderClimate() {
    const host = document.getElementById('climateControls');
    if (!host) return;
    host.className = 'household-grid';
    const devices = state.devices.filter(item => ['climate', 'fan'].includes(item.category) || item.id === 'bed-room-t3-hub');
    host.innerHTML = devices.map(device => card(device, irActions(device))).join('');
    bindIrCommands(host);
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
  window.DashboardHouseholdDevices = Object.freeze({bindIrCommands, irActions});
  load();
})();
