(() => {
  'use strict';
  if (window.__dashboardLgUiConsolidated) return;
  window.__dashboardLgUiConsolidated = true;
  window.__dashboardLgStatusInstalled = true;
  window.__dashboardLgPairingInstalled = true;

  const CSS_VERSION = 'lg-ui-10.0';
  const state = {
    mounted: false, mounts: 0, pollTimer: null, pollMs: 30000,
    status: null, pairing: null, job: {state: 'idle'}, csrf: null,
    sequence: 0, appliedSequence: 0, ignoredStale: 0,
    lastRefresh: null, lastError: null, commandInProgress: null,
  };
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const value = (input, fallback = '—') => input === null || input === undefined || input === '' ? fallback : String(input);
  const relative = ts => {
    if (!ts) return '—';
    const age = Math.max(0, Math.floor(Date.now() / 1000 - Number(ts)));
    if (age < 5) return 'Just now';
    if (age < 60) return `${age} sec ago`;
    if (age < 3600) return `${Math.floor(age / 60)} min ago`;
    return new Date(Number(ts) * 1000).toLocaleString();
  };
  const exactTime = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : '';

  async function csrf() {
    if (state.csrf) return state.csrf;
    const response = await fetch('/api/auth/status', {credentials: 'same-origin'});
    const payload = await response.json();
    state.csrf = payload.csrf_token || null;
    return state.csrf;
  }

  async function request(url, method = 'GET', body) {
    const headers = {'Content-Type': 'application/json'};
    if (!['GET', 'HEAD'].includes(method)) headers['X-CSRF-Token'] = await csrf();
    const response = await fetch(url, {
      method, credentials: 'same-origin', headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || 'request_failed');
    return payload;
  }

  function mountLgTvPage() {
    if (state.mounted) return document.getElementById('lgTvPage');
    const entertainment = document.querySelector('[data-page="entertainment"] .grid');
    const remoteCard = document.getElementById('tvButtons')?.closest('.card');
    if (!entertainment || !remoteCard) return null;

    document.getElementById('lgPairingCard')?.remove();
    document.getElementById('lgPairingCardV9')?.remove();
    document.getElementById('lgLiveShell')?.remove();

    const page = document.createElement('section');
    page.id = 'lgTvPage';
    page.className = 'lg-tv-page span-12';
    page.innerHTML = `
      <section class="lg-tv-status-panel" aria-labelledby="lgTvStatusTitle">
        <div id="lgTvBanner" class="lg-tv-banner neutral" aria-live="polite">
          <div><h2 id="lgTvStatusTitle">LG TV</h2><p id="lgTvBannerText">Loading live telemetry…</p></div>
          <button id="lgTvRefresh" class="btn ghost" type="button">Refresh</button>
        </div>
        <div class="lg-tv-metrics">
          <article class="lg-tv-metric" data-lg-card="status"><span>Status</span><strong id="lgTvStatus">Loading…</strong></article>
          <article class="lg-tv-metric" data-lg-card="app"><span>Current App</span><strong id="lgTvApp">—</strong></article>
          <article class="lg-tv-metric" data-lg-card="input"><span>Input</span><strong id="lgTvInput">—</strong></article>
          <article class="lg-tv-metric" data-lg-card="volume"><span>Volume</span><strong id="lgTvVolume">—</strong></article>
          <article class="lg-tv-metric" data-lg-card="mute"><span>Mute</span><strong id="lgTvMute">—</strong></article>
          <article class="lg-tv-metric" data-lg-card="updated"><span>Last Update</span><strong id="lgTvUpdated">—</strong></article>
        </div>
        <div class="lg-tv-detail-grid">
          <div><span>Sound Output</span><strong id="lgTvSoundOutput">—</strong></div>
          <div><span>Device Name</span><strong id="lgTvDeviceName">—</strong></div>
          <div><span>Model</span><strong id="lgTvModel">—</strong></div>
          <div><span>Product</span><strong id="lgTvProduct">—</strong></div>
          <div><span>Software Version</span><strong id="lgTvSoftware">—</strong></div>
          <div><span>Firmware</span><strong id="lgTvFirmware">—</strong></div>
          <div><span>webOS Version</span><strong id="lgTvWebos">—</strong></div>
        </div>
        <div id="lgTvMeta" class="lg-tv-meta"></div>
        <div id="lgTvMessage" class="lg-tv-message" aria-live="polite"></div>
      </section>
      <section class="lg-tv-pairing-panel" aria-labelledby="lgPairTitle">
        <div class="card-head"><div><h2 id="lgPairTitle">LG TV Pairing</h2><small>Secure webOS pairing and recovery</small></div><span id="lgPairBadge" class="badge">Checking</span></div>
        <div class="lg-pair-state" aria-live="polite"><h3 id="lgPairStateTitle">Checking pairing…</h3><p id="lgPairStateHelp"></p></div>
        <div class="lg-pair-grid">
          <div><span>TV IP</span><strong id="lgPairIp">192.168.1.33</strong></div>
          <div><span>Service</span><strong id="lgPairService">—</strong></div>
          <div><span>Connection</span><strong id="lgPairConnection">—</strong></div>
          <div><span>Key Source</span><strong id="lgPairSource">—</strong></div>
          <div><span>Last Connection</span><strong id="lgPairLastConnection">—</strong></div>
          <div><span>Last Pairing</span><strong id="lgPairLastPairing">—</strong></div>
          <div><span>Last Error</span><strong id="lgPairError">None</strong></div>
        </div>
        <div id="lgPairCountdown" class="lg-pair-countdown"></div>
        <div class="lg-pair-actions">
          <button id="lgPairTest" class="btn primary" type="button">Test Connection</button>
          <button id="lgPairRepair" class="btn ghost" type="button">Repair Pairing</button>
          <button id="lgPairSave" class="btn primary" type="button" disabled aria-describedby="lgPairSaveHelp">Save & Reconnect</button>
          <button id="lgPairCancel" class="btn danger" type="button" hidden>Cancel Pairing</button>
          <button id="lgPairForget" class="btn danger" type="button">Forget Pairing</button>
        </div>
        <small id="lgPairSaveHelp">Available after a new key is registered.</small>
        <div id="lgPairMessage" class="lg-tv-message" aria-live="polite"></div>
      </section>`;

    entertainment.insertBefore(page, remoteCard);
    remoteCard.classList.add('lg-tv-remote-card');
    bindOnce();
    state.mounted = true;
    state.mounts += 1;
    return page;
  }

  function setText(id, next, title) {
    const element = document.getElementById(id);
    if (!element) return;
    const text = String(next);
    if (element.textContent !== text) element.textContent = text;
    if (title !== undefined && element.title !== title) element.title = title || '';
  }

  function friendlyApp(app) {
    const id = String(app?.id || '').trim();
    const name = String(app?.name || '').trim();
    if (name) return name;
    if (!id) return '—';
    const low = id.toLowerCase();
    const mappings = [
      ['netflix', 'Netflix'], ['youtube', 'YouTube'], ['amazon', 'Prime Video'], ['prime', 'Prime Video'],
      ['disney', 'Disney+'], ['browser', 'Browser'], ['home', 'Home'], ['hdmi', id.toUpperCase().replace(/[^0-9]/g, '') ? `HDMI ${id.replace(/[^0-9]/g, '')}` : 'HDMI'],
    ];
    return mappings.find(([token]) => low.includes(token))?.[1] || id;
  }

  function renderStatus() {
    const s = state.status || {};
    let label = 'Offline', banner = 'LG TV is offline', detail = 'Waiting for the TV to become reachable.', tone = 'bad';
    if (['pairing_required', 'key_missing'].includes(s.connection_state)) {
      label = 'Pairing required'; banner = 'LG TV requires pairing repair'; detail = 'Use Repair Pairing, then approve the request on the TV.';
    } else if (s.connection_state === 'connecting' || s.power_state === 'starting') {
      label = 'Connecting'; banner = 'LG TV is starting'; detail = 'Connecting to secure webOS…'; tone = 'warn';
    } else if (s.online) {
      label = 'Online'; banner = 'LG TV online and ready'; detail = 'Remote control and live telemetry are available.'; tone = 'ok';
    } else if (s.power_state === 'standby' || s.connection_state === 'standby') {
      label = 'Standby'; banner = 'LG TV is in standby'; detail = 'Status polling continues at a reduced rate.'; tone = 'neutral';
    }
    const bannerElement = document.getElementById('lgTvBanner');
    if (bannerElement) bannerElement.className = `lg-tv-banner ${tone}`;
    setText('lgTvStatusTitle', banner);
    setText('lgTvBannerText', detail);
    setText('lgTvStatus', label);
    setText('lgTvApp', friendlyApp(s.current_app), s.current_app?.id || '');
    setText('lgTvInput', value(s.current_input?.name, value(s.current_input?.id)));
    setText('lgTvVolume', s.audio?.volume === null || s.audio?.volume === undefined ? '—' : Math.round(Number(s.audio.volume)));
    setText('lgTvMute', s.audio?.muted === true ? 'Muted' : s.audio?.muted === false ? 'Unmuted' : '—');
    setText('lgTvUpdated', relative(s.last_update_ts || s.last_success_ts), exactTime(s.last_update_ts || s.last_success_ts));
    setText('lgTvSoundOutput', value(s.audio?.sound_output));
    setText('lgTvDeviceName', value(s.device?.name));
    setText('lgTvModel', value(s.device?.model));
    setText('lgTvProduct', value(s.device?.product_name));
    setText('lgTvSoftware', value(s.device?.software_version));
    setText('lgTvFirmware', value(s.device?.firmware_version));
    setText('lgTvWebos', value(s.device?.webos_version));
    const meta = document.getElementById('lgTvMeta');
    if (meta) {
      const command = s.last_command ? `${safe(s.last_command)}: ${s.last_command_success === true ? 'OK' : s.last_command_success === false ? 'Failed' : 'Pending'}` : 'No command result';
      const values = [s.paired ? 'Paired' : 'Not paired', 'Secure WebSocket', `Service ${s.service_active ? 'active' : 'inactive'}`, `Data age ${s.data_age_sec ?? '—'} sec`, command];
      [...meta.children].forEach((child, index) => { if (values[index] !== undefined) child.textContent = values[index]; });
      while (meta.children.length < values.length) { const chip = document.createElement('span'); chip.textContent = values[meta.children.length]; meta.appendChild(chip); }
    }
  }

  function renderPairing() {
    const p = state.pairing || {}, j = state.job || {state: 'idle'}, s = state.status || {};
    const active = ['connecting', 'prompted'].includes(j.state);
    const registered = j.state === 'registered';
    const ready = Boolean((p.paired || s.paired) && (p.connection_status === 'connected' || s.connection_state === 'connected'));
    setText('lgPairBadge', ready ? 'Paired & Connected' : p.pairing_required || s.pairing_required ? 'Pairing required' : 'Checking');
    setText('lgPairStateTitle', ready ? '✓ LG TV is paired and ready' : active ? (j.state === 'prompted' ? 'Approve the connection request on the LG TV' : 'Connecting to TV…') : registered ? 'Pairing registered — ready to save' : 'Pairing required');
    setText('lgPairStateHelp', ready ? 'Remote control is available. No action required.' : active ? 'Waiting for approval…' : 'Click Repair Pairing, then approve the request on the LG TV.');
    setText('lgPairIp', p.tv_ip || s.tv_ip || '192.168.1.33');
    setText('lgPairService', (p.service_active ?? s.service_active) ? 'Active' : 'Inactive');
    setText('lgPairConnection', p.connection_status || s.connection_state || '—');
    setText('lgPairSource', p.key_source || s.key_source || 'none');
    setText('lgPairLastConnection', relative(p.last_connection_success || s.last_success_ts));
    setText('lgPairLastPairing', relative(p.last_pair_success || p.last_pair_attempt));
    setText('lgPairError', p.last_error || s.last_error || 'None');
    const save = document.getElementById('lgPairSave'); if (save) save.disabled = !registered;
    const cancel = document.getElementById('lgPairCancel'); if (cancel) cancel.hidden = !active;
    setText('lgPairCountdown', active && j.expires_ts ? `Time remaining: ${Math.max(0, Number(j.expires_ts) - Math.floor(Date.now() / 1000))} sec` : '');
  }

  function showMessage(id, message, error = false) {
    const element = document.getElementById(id);
    if (!element) return;
    element.textContent = message;
    element.classList.toggle('error', error);
  }

  function desiredPollMs() {
    if (['connecting', 'prompted'].includes(state.job?.state)) return 2000;
    return state.status?.online ? 5000 : 30000;
  }

  function schedulePoll() {
    if (state.pollTimer) clearTimeout(state.pollTimer);
    state.pollTimer = null;
    state.pollMs = desiredPollMs();
    if (document.hidden || window.currentPage?.() !== 'entertainment') return;
    state.pollTimer = setTimeout(() => refreshAll(true), state.pollMs);
  }

  async function refreshAll(background = true) {
    if (!mountLgTvPage()) return;
    const sequence = ++state.sequence;
    try {
      const [status, pairing, job] = await Promise.all([
        request('/api/lg-tv/status'), request('/api/lg-tv/pairing/status'), request('/api/lg-tv/pairing/job'),
      ]);
      if (sequence < state.appliedSequence) { state.ignoredStale += 1; return; }
      state.appliedSequence = sequence;
      state.status = status; state.pairing = pairing; state.job = job;
      state.lastRefresh = Date.now(); state.lastError = null;
      renderStatus(); renderPairing();
    } catch (error) {
      state.lastError = error.message || 'status_failed';
      if (!background) showMessage('lgTvMessage', state.lastError, true);
    } finally {
      schedulePoll();
    }
  }

  async function runAction(button, action, success, messageId = 'lgPairMessage') {
    const scroll = window.scrollY;
    button.disabled = true;
    try {
      const result = await action();
      showMessage(messageId, success || result.result || 'Done');
      await refreshAll(false);
      return result;
    } catch (error) {
      showMessage(messageId, error.message || 'Operation failed', true);
      return null;
    } finally {
      button.disabled = false;
      window.scrollTo(0, scroll);
    }
  }

  function bindOnce() {
    document.getElementById('lgTvRefresh')?.addEventListener('click', event => runAction(event.currentTarget, () => request('/api/lg-tv/status/refresh', 'POST', {}), 'Refreshing…', 'lgTvMessage'));
    document.getElementById('lgPairTest')?.addEventListener('click', event => runAction(event.currentTarget, () => request('/api/lg-tv/pairing/test', 'POST', {}), 'Connection successful. LG TV is ready.'));
    document.getElementById('lgPairRepair')?.addEventListener('click', event => runAction(event.currentTarget, () => request('/api/lg-tv/pairing/request', 'POST', {}), 'Approve the connection request on the LG TV'));
    document.getElementById('lgPairSave')?.addEventListener('click', event => runAction(event.currentTarget, () => request('/api/lg-tv/pairing/save', 'POST', {}), 'Client key saved and service reconnected.'));
    document.getElementById('lgPairCancel')?.addEventListener('click', event => runAction(event.currentTarget, () => request('/api/lg-tv/pairing/cancel', 'POST', {}), 'Pairing cancelled.'));
    document.getElementById('lgPairForget')?.addEventListener('click', event => {
      if (!confirm('Forget the saved LG TV pairing key? A timestamped backup will be kept.')) return;
      runAction(event.currentTarget, () => request('/api/lg-tv/pairing/forget', 'POST', {}), 'Pairing forgotten.');
    });
  }

  const originalTv = window.tv;
  if (typeof originalTv === 'function') {
    window.tv = function consolidatedLgCommand(command) {
      const button = document.querySelector(`[data-lg-command="${CSS.escape(command)}"]`);
      state.commandInProgress = command;
      if (button) button.disabled = true;
      try {
        const output = originalTv(command);
        Promise.resolve(output)
          .then(() => { showMessage('lgTvMessage', `${command} sent successfully.`); setTimeout(() => request('/api/lg-tv/status/refresh', 'POST', {}).catch(() => {}), command === 'power_on' ? 3000 : 900); })
          .catch(() => showMessage('lgTvMessage', `${command} failed.`, true))
          .finally(() => { if (button) button.disabled = false; state.commandInProgress = null; });
        return output;
      } catch (error) {
        if (button) button.disabled = false;
        state.commandInProgress = null;
        showMessage('lgTvMessage', `${command} failed.`, true);
        throw error;
      }
    };
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; }
    else if (window.currentPage?.() === 'entertainment') refreshAll(true);
  });
  window.addEventListener('beforeunload', () => { if (state.pollTimer) clearTimeout(state.pollTimer); state.pollTimer = null; });
  const previousRenderEntertainment = window.renderEntertainment;
  window.renderEntertainment = function renderConsolidatedLgPage() {
    if (typeof previousRenderEntertainment === 'function') previousRenderEntertainment();
    mountLgTvPage();
    refreshAll(true);
  };

  window.dashboardLgDiagnostics = () => ({
    active_mounts: state.mounts,
    active_pollers: state.pollTimer ? 1 : 0,
    legacy_renderer_detected: Boolean(document.getElementById('lgPairingCard') || document.getElementById('lgPairingCardV9') || document.getElementById('lgLiveShell')),
    duplicate_cards: Math.max(0, document.querySelectorAll('[data-lg-card="status"]').length - 1) + Math.max(0, document.querySelectorAll('.lg-tv-pairing-panel').length - 1),
    css_version: CSS_VERSION,
    last_refresh: state.lastRefresh,
    status_age: state.status?.data_age_sec ?? null,
  });
  window.dashboardLgTvDiagnostics = window.dashboardLgDiagnostics;

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => { mountLgTvPage(); refreshAll(false); }, {once: true});
  else { mountLgTvPage(); refreshAll(false); }
})();