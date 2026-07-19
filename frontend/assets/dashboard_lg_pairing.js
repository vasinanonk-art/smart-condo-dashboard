(() => {
  'use strict';
  if (window.__dashboardLgPairingInstalled) return;
  window.__dashboardLgPairingInstalled = true;

  const state = { status:null, job:null, timer:null, csrf:null, busy:false };
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const when = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : 'Not available';

  async function csrf() {
    if (state.csrf) return state.csrf;
    const response = await fetch('/api/auth/status', {credentials:'same-origin'});
    const payload = await response.json();
    state.csrf = payload.csrf_token || null;
    return state.csrf;
  }

  async function request(url, method='GET', body) {
    const headers = {'Content-Type':'application/json'};
    if (!['GET','HEAD'].includes(method)) headers['X-CSRF-Token'] = await csrf();
    const response = await fetch(url, {method, credentials:'same-origin', headers, body:body===undefined?undefined:JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || 'Request failed');
    return payload;
  }

  async function refresh() {
    const [status, job] = await Promise.allSettled([
      request('/api/lg-tv/pairing/status'), request('/api/lg-tv/pairing/job')
    ]);
    if (status.status === 'fulfilled') state.status = status.value;
    if (job.status === 'fulfilled') state.job = job.value;
    render();
    managePolling();
  }

  function message(text, error=false) {
    const host = document.getElementById('lgPairingMessage');
    if (!host) return;
    host.hidden = false;
    host.className = `lg-pairing-message ${error ? 'error' : ''}`;
    host.textContent = text;
  }

  function panelHtml() {
    const status = state.status || {};
    const job = state.job || {state:'idle'};
    const active = ['connecting','prompted'].includes(job.state);
    const registered = job.state === 'registered';
    const pairingText = job.state === 'prompted'
      ? 'Approve the connection request on the LG TV'
      : job.state === 'registered' ? 'New client key received and ready to save.'
      : job.error ? `Pairing failed: ${safe(job.error)}` : 'No pairing request is active.';
    return `<section class="card lg-pairing-card" id="lgPairingCard">
      <div class="card-head"><div><h2>LG TV Pairing</h2><small>Secure webOS pairing and client-key repair</small></div><span class="badge">${safe(status.paired ? 'Paired' : status.pairing_required ? 'Pairing required' : 'Unknown')}</span></div>
      <div class="lg-pairing-summary">
        <div><span>TV IP</span><strong>${safe(status.tv_ip || '192.168.1.33')}</strong></div>
        <div><span>Service</span><strong>${safe(status.service_active ? 'Active' : 'Inactive')}</strong></div>
        <div><span>Connection</span><strong>${safe(status.connection_status || 'Unknown')}</strong></div>
        <div><span>Key source</span><strong>${safe(status.key_source || 'none')}</strong></div>
        <div><span>Last success</span><strong>${safe(when(status.last_pair_success))}</strong></div>
        <div><span>Last error</span><strong>${safe(status.last_error || 'None')}</strong></div>
      </div>
      <div class="lg-pairing-job"><span>Job</span><strong>${safe(job.state || 'idle')}</strong><p>${pairingText}</p></div>
      <div class="lg-pairing-actions">
        <button class="btn primary" type="button" id="lgPairRequest" ${active ? 'disabled' : ''}>Request New Key</button>
        <button class="btn ghost" type="button" id="lgPairCheck">Check Pairing Status</button>
        <button class="btn primary" type="button" id="lgPairSave" ${registered ? '' : 'disabled'}>Save & Reconnect</button>
        <button class="btn danger" type="button" id="lgPairCancel" ${active ? '' : 'disabled'}>Cancel Pairing</button>
      </div>
      <div id="lgPairingMessage" class="lg-pairing-message" hidden></div>
    </section>`;
  }

  function render() {
    const page = document.querySelector('[data-page="entertainment"] .grid');
    if (!page) return;
    const existing = document.getElementById('lgPairingCard');
    const html = panelHtml();
    if (existing) existing.outerHTML = html; else page.insertAdjacentHTML('beforeend', html);
    bind();
  }

  async function action(fn, successText) {
    if (state.busy) return;
    state.busy = true;
    try {
      await fn();
      await refresh();
      if (successText) message(successText);
    } catch (error) {
      message(error.message || 'Operation failed.', true);
    } finally {
      state.busy = false;
    }
  }

  function bind() {
    document.getElementById('lgPairRequest')?.addEventListener('click', () => action(async () => {
      await request('/api/lg-tv/pairing/request','POST',{});
    }, 'Approve the connection request on the LG TV'));
    document.getElementById('lgPairCheck')?.addEventListener('click', refresh);
    document.getElementById('lgPairSave')?.addEventListener('click', () => action(async () => {
      await request('/api/lg-tv/pairing/save','POST',{});
    }, 'Client key saved and LG TV service reconnected.'));
    document.getElementById('lgPairCancel')?.addEventListener('click', () => action(async () => {
      await request('/api/lg-tv/pairing/cancel','POST',{});
    }, 'Pairing cancelled.'));
  }

  function managePolling() {
    const active = ['connecting','prompted'].includes(state.job?.state);
    if (active && !state.timer) state.timer = setInterval(refresh, 2000);
    if (!active && state.timer) { clearInterval(state.timer); state.timer = null; }
  }

  const observer = new MutationObserver(() => {
    if (document.querySelector('[data-page="entertainment"]') && !document.getElementById('lgPairingCard')) refresh();
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',refresh); else refresh();
})();
