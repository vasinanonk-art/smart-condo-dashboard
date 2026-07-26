(() => {
  'use strict';
  if (window.__lgTvStatusInstalled) return;
  window.__lgTvStatusInstalled = true;

  const state = {status:null, capabilities:null, timer:null, busy:false};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
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

  function mount() {
    const page = document.querySelector('[data-page="entertainment"] .grid');
    const original = document.getElementById('tvButtons')?.closest('.card');
    if (!page || !original) return false;
    if (original.classList.contains('lg-tv-compact-card') && document.getElementById('lgCommandToast')) return true;
    original.className = 'card span-12 lg-tv-compact-card';
    original.innerHTML = `
      <div class="lg-compact-summary">
        <div><h2>LG TV</h2><strong id="lgTvStatus">Checking…</strong></div>
        <div><span>Input / App</span><strong id="lgTvSource">—</strong></div>
        <div><span>Volume</span><strong id="lgTvVolume">—</strong></div>
        <div><span>Last update</span><strong id="lgTvUpdated">—</strong></div>
      </div>
      <div id="tvButtons" class="lg-remote-panel"></div>
      <details class="lg-tv-details"><summary>TV Details / Pairing</summary>
        <p id="lgPairingSummary">Pairing details are available to authenticated dashboard users.</p>
        <div class="lg-detail-actions">
          <button type="button" class="btn ghost" data-lg-refresh>Refresh status</button>
          <button type="button" class="btn ghost" data-lg-pair-test>Test pairing</button>
          <button type="button" class="btn ghost" data-lg-pair-request>Repair</button>
          <button type="button" class="btn ghost" data-lg-pair-save>Save key</button>
          <button type="button" class="btn ghost" data-lg-pair-cancel>Cancel</button>
          <button type="button" class="btn ghost" data-lg-pair-forget>Forget key</button>
        </div>
      </details>
      <div id="lgCommandToast" class="lg-command-toast" role="status" aria-live="polite" hidden></div>`;
    return true;
  }

  function render() {
    const value = state.status || {};
    const online = value.online === true || value.connection_state === 'connected';
    document.getElementById('lgTvStatus').textContent = online ? 'Online' : 'Offline';
    document.getElementById('lgTvSource').textContent = value.input || value.current_input || value.app_name || value.current_app || 'Unavailable';
    const audio = value.audio || {};
    const volumeValue = audio.volume ?? value.volume;
    const muted = audio.muted ?? audio.mute ?? value.muted ?? value.mute;
    const volume = volumeValue == null ? 'Unavailable' : `${volumeValue}${muted ? ' · Muted' : ''}`;
    document.getElementById('lgTvVolume').textContent = volume;
    const updated = value.updated_at || value.last_update_ts || value.last_seen_ts;
    document.getElementById('lgTvUpdated').textContent = updated ? new Date(Number(updated) * 1000).toLocaleString() : 'Unavailable';
    window.renderLgCompactRemote?.(state.capabilities || {});
  }

  function toast(message, error=false) {
    const host = document.getElementById('lgCommandToast');
    if (!host) return;
    host.textContent = message;
    host.className = `lg-command-toast${error ? ' error' : ''}`;
    host.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => { host.hidden = true; }, 3500);
  }

  async function refresh() {
    if (state.busy) return;
    state.busy = true;
    try {
      const [status, capabilities] = await Promise.all([request('/api/lg-tv/status'), request('/api/lg-tv/capabilities')]);
      state.status = status;
      state.capabilities = capabilities;
      render();
    } catch (error) {
      toast(error.message || 'LG TV status unavailable', true);
    } finally {
      state.busy = false;
      clearTimeout(state.timer);
      state.timer = setTimeout(refresh, 15000);
    }
  }

  window.tv = async function compactLgCommand(command, value) {
    try {
      const output = await request('/api/lg-tv/command', 'POST', {command, value});
      if (output.state) {
        state.status = output.state;
        render();
      }
      toast(output.state_refreshed === false ? 'Command sent; state confirmation is pending.' : 'Command completed.');
      return output;
    } catch (error) {
      toast(error.message || 'Command failed', true);
      throw error;
    }
  };
  window.renderLgTvCompact = function renderLgTvCompact() {
    if (mount()) render();
  };

  if (mount()) {
    document.querySelector('[data-lg-refresh]')?.addEventListener('click', refresh);
    const pairingAction = async (selector, path, message) => {
      document.querySelector(selector)?.addEventListener('click', async () => {
        try { await request(path, 'POST', {}); toast(message); }
        catch (error) { toast(error.message || 'Pairing action failed', true); }
      });
    };
    pairingAction('[data-lg-pair-test]', '/api/lg-tv/pairing/test', 'Pairing connection verified.');
    pairingAction('[data-lg-pair-request]', '/api/lg-tv/pairing/request', 'Approve the pairing request on the TV.');
    pairingAction('[data-lg-pair-save]', '/api/lg-tv/pairing/save', 'Pairing key saved.');
    pairingAction('[data-lg-pair-cancel]', '/api/lg-tv/pairing/cancel', 'Pairing cancelled.');
    document.querySelector('[data-lg-pair-forget]')?.addEventListener('click', async () => {
      if (!confirm('Forget the saved LG TV pairing key?')) return;
      try { await request('/api/lg-tv/pairing/forget', 'POST', {}); toast('Pairing key forgotten.'); }
      catch (error) { toast(error.message || 'Pairing action failed', true); }
    });
    refresh();
  }
})();
