(() => {
  'use strict';
  if (window.__lgTvStatusInstalled) return;
  window.__lgTvStatusInstalled = true;

  const UI = window.HouseholdUI;
  const state = {
    status:null, capabilities:null, timer:null, inventoryTimer:null, busy:false,
    detailsLoaded:false, detailsLoading:false, pairing:null, diagnostics:null,
    pendingCommands:new Set(),
  };
  const safe = UI.safe;
  const request = async (url, method='GET', body) => {
    const options = {method, headers:{}};
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'request_failed');
    return payload;
  };

  function technicalActions() {
    return UI.actionGrid([
      UI.actionButton({label:'Refresh status', attributes:'data-lg-refresh'}),
      UI.actionButton({label:'Test pairing', attributes:'data-lg-pair-test'}),
      UI.actionButton({label:'Repair', attributes:'data-lg-pair-request'}),
      UI.actionButton({label:'Save key', attributes:'data-lg-pair-save'}),
      UI.actionButton({label:'Cancel', attributes:'data-lg-pair-cancel'}),
      UI.actionButton({label:'Forget key', attributes:'data-lg-pair-forget'}),
    ].join(''), 'LG TV pairing actions');
  }

  function mount() {
    const page = document.querySelector('[data-page="entertainment"] .grid');
    const original = document.getElementById('tvButtons')?.closest('article, .card');
    if (!page || !original) return false;
    if (original.classList.contains('household-lg-card') && document.getElementById('lgTvStatusBadge')) return true;
    original.className = 'household-device-card household-lg-card household-entertainment-grid';
    original.setAttribute('aria-labelledby', 'lgTvTitle');
    original.innerHTML = `
      ${UI.deviceHeader({title:'LG TV', room:'Living Room', status:'unknown', titleId:'lgTvTitle', statusId:'lgTvStatusBadge'})}
      <div id="lgTvQualityBadge" class="household-lg-quality-owner" aria-live="polite"></div>
      <div class="household-lg-summary">
        <div class="household-lg-summary-item"><span>Input or app</span><strong id="lgTvSource">Unavailable</strong></div>
        <div class="household-lg-summary-item"><span>Volume</span><strong id="lgTvVolume">Unavailable</strong></div>
        <div class="household-lg-summary-item"><span>Last update</span><strong id="lgTvUpdated">Unavailable</strong></div>
      </div>
      <div id="tvButtons" class="household-lg-remote"></div>
      ${UI.deviceDetails({
        summary:'TV Details',
        attributes:'data-lg-details',
        content:`<div id="lgTvTechnicalDetails" class="household-detail-grid"><div class="household-detail-item"><span>Details</span><strong>Open to load authenticated diagnostics.</strong></div></div>${technicalActions()}`,
      })}`;
    return true;
  }

  function sourceLabel(value) {
    const input = value.current_input;
    const app = value.current_app;
    if (input && typeof input === 'object' && input.name) return input.name;
    if (app && typeof app === 'object' && app.name) return app.name;
    return value.input || value.app_name || 'Unavailable';
  }

  function render() {
    const value = state.status || {};
    const statusValue = value.online === true || value.connection_state === 'connected'
      ? 'online' : value.online === false ? 'offline' : 'unknown';
    const statusHost = document.getElementById('lgTvStatusBadge');
    if (statusHost) {
      statusHost.className = `household-badge household-status-badge household-status-${statusValue}`;
      statusHost.textContent = statusValue === 'online' ? 'Online' : statusValue === 'offline' ? 'Offline' : 'Unknown';
    }
    const qualityHost = document.getElementById('lgTvQualityBadge');
    if (qualityHost) qualityHost.innerHTML = UI.stateQualityBadge(value.last_success_ts ? 'confirmed' : 'unknown');
    const source = document.getElementById('lgTvSource');
    if (source) source.textContent = sourceLabel(value);
    const audio = value.audio || {};
    const volumeValue = audio.volume ?? value.volume;
    const muted = audio.muted ?? audio.mute ?? value.muted ?? value.mute;
    const volume = document.getElementById('lgTvVolume');
    if (volume) volume.textContent = volumeValue == null ? 'Unavailable' : `${volumeValue}${muted ? ' · Muted' : ''}`;
    const updated = value.updated_at || value.last_update_ts || value.last_seen_ts;
    const updatedHost = document.getElementById('lgTvUpdated');
    if (updatedHost) updatedHost.textContent = updated ? new Date(Number(updated) * 1000).toLocaleString() : 'Unavailable';
    window.renderLgCompactRemote?.(state.capabilities || {});
    if (state.detailsLoaded) renderDetails();
  }

  function detail(label, value) {
    return `<div class="household-detail-item"><span>${safe(label)}</span><strong>${safe(value ?? 'Unavailable')}</strong></div>`;
  }

  function renderDetails() {
    const host = document.getElementById('lgTvTechnicalDetails');
    if (!host) return;
    const device = state.status?.device || {};
    const pairing = state.pairing || {};
    const diagnostics = state.diagnostics || {};
    host.innerHTML = [
      detail('Pairing', pairing.paired ? 'Paired' : pairing.pairing_required ? 'Pairing required' : 'Unknown'),
      detail('Key source', pairing.key_source || 'Unavailable'),
      detail('Service', pairing.service_active === true || diagnostics.service_active === true ? 'Active' : 'Unavailable'),
      detail('Connection', diagnostics.connection_state || pairing.connection_status || 'Unknown'),
      detail('Software', device.software_version || 'Unavailable'),
      detail('Firmware', device.firmware_version || 'Unavailable'),
      detail('webOS', device.webos_version || 'Unavailable'),
      detail('Status worker', diagnostics.status_worker_active === true ? 'Active' : 'Unavailable'),
      detail('Wake-on-LAN', diagnostics.wol_configured === true ? 'Configured' : 'Not configured'),
      detail('Last wake request', diagnostics.last_wol_sent_at ? new Date(Number(diagnostics.last_wol_sent_at) * 1000).toLocaleString() : 'Not sent'),
      detail('Wake reconnect attempts', diagnostics.reconnect_attempts ?? 0),
      detail('Last wake result', diagnostics.last_wol_result || 'Not sent'),
    ].join('');
  }

  async function loadDetails() {
    if (state.detailsLoaded || state.detailsLoading) return;
    state.detailsLoading = true;
    try {
      const [pairing, diagnostics] = await Promise.all([
        request('/api/lg-tv/pairing/status'),
        request('/api/lg-tv/status/diagnostics'),
      ]);
      state.pairing = pairing;
      state.diagnostics = diagnostics;
      state.detailsLoaded = true;
      renderDetails();
    } catch (error) {
      UI.toast(error.message || 'TV details are unavailable.', 'error');
    } finally {
      state.detailsLoading = false;
    }
  }

  async function refresh() {
    if (state.busy) return;
    state.busy = true;
    try {
      const [status, capabilities] = await Promise.all([request('/api/lg-tv/status'), request('/api/lg-tv/capabilities')]);
      state.status = status;
      state.capabilities = capabilities;
      render();
      clearTimeout(state.inventoryTimer);
      if (capabilities.inventory_refreshing === true) {
        state.inventoryTimer = setTimeout(refreshInventory, 500);
      }
    } catch (error) {
      UI.toast(error.message || 'LG TV status unavailable', 'error');
    } finally {
      state.busy = false;
      clearTimeout(state.timer);
      state.timer = setTimeout(refresh, 15000);
    }
  }

  async function refreshInventory() {
    try {
      state.capabilities = await request('/api/lg-tv/capabilities');
      render();
      if (state.capabilities.inventory_refreshing === true) {
        clearTimeout(state.inventoryTimer);
        state.inventoryTimer = setTimeout(refreshInventory, 500);
      }
    } catch (_) {
      // Preserve and continue rendering the last successful inventory.
    }
  }

  async function runPairing(path, message) {
    try {
      await request(path, 'POST', {});
      state.detailsLoaded = false;
      UI.toast(message);
    } catch (error) {
      UI.toast(error.message || 'Pairing action failed', 'error');
    }
  }

  function bind() {
    document.querySelector('[data-lg-details]')?.addEventListener('toggle', event => {
      if (event.currentTarget.open) loadDetails();
    });
    document.querySelector('[data-lg-refresh]')?.addEventListener('click', refresh);
    document.querySelector('[data-lg-pair-test]')?.addEventListener('click', () => runPairing('/api/lg-tv/pairing/test', 'Pairing connection verified.'));
    document.querySelector('[data-lg-pair-request]')?.addEventListener('click', () => runPairing('/api/lg-tv/pairing/request', 'Approve the pairing request on the TV.'));
    document.querySelector('[data-lg-pair-save]')?.addEventListener('click', () => runPairing('/api/lg-tv/pairing/save', 'Pairing key saved.'));
    document.querySelector('[data-lg-pair-cancel]')?.addEventListener('click', () => runPairing('/api/lg-tv/pairing/cancel', 'Pairing cancelled.'));
    document.querySelector('[data-lg-pair-forget]')?.addEventListener('click', () => {
      if (confirm('Forget the saved LG TV pairing key?')) runPairing('/api/lg-tv/pairing/forget', 'Pairing key forgotten.');
    });
  }

  window.tv = async function compactLgCommand(command, value) {
    const requestKey = `${command}:${value ?? ''}`;
    if (state.pendingCommands.has(requestKey)) return {ok:false, duplicate_ignored:true};
    state.pendingCommands.add(requestKey);
    try {
      const output = await request('/api/lg-tv/command', 'POST', {command, value});
      if (output.state) {
        state.status = output.state;
        render();
      }
      UI.toast(output.state_refreshed === false ? 'Command sent; state confirmation is pending.' : 'Command completed.');
      return output;
    } catch (error) {
      UI.toast(error.message || 'Command failed', 'error');
      throw error;
    } finally {
      state.pendingCommands.delete(requestKey);
    }
  };

  window.renderLgTvCompact = function renderLgTvCompact() {
    if (mount()) render();
  };

  if (mount()) {
    bind();
    refresh();
  }
})();
