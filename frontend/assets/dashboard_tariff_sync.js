(() => {
  'use strict';
  if (window.__dashboardTariffSyncInstalled) return;
  window.__dashboardTariffSyncInstalled = true;

  const state = {status:null,candidate:null,settings:null,csrf:null,busy:false,loading:false};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const when = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : 'Not available';
  const valueText = value => typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—');

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
    const response = await fetch(url, {method,credentials:'same-origin',headers,body:body===undefined?undefined:JSON.stringify(body)});
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || 'Request failed');
    return payload;
  }

  async function load() {
    if (state.loading) return;
    state.loading = true;
    try {
      const results = await Promise.allSettled([
        request('/api/tariff/status'),
        request('/api/tariff/candidate'),
        request('/api/settings')
      ]);
      if (results[0].status === 'fulfilled') state.status = results[0].value;
      if (results[1].status === 'fulfilled') state.candidate = results[1].value;
      if (results[2].status === 'fulfilled') state.settings = results[2].value;
      render();
    } finally {
      state.loading = false;
    }
  }

  function comparisonHtml() {
    const comparison = state.candidate?.comparison || {};
    const rows = Object.entries(comparison);
    if (!rows.length) return '<div class="settings-empty">No candidate comparison is available.</div>';
    return `<div class="tariff-comparison">${rows.map(([field,item]) => `<div class="tariff-compare-row ${item.changed?'changed':''}"><strong>${safe(field.replaceAll('_',' '))}</strong><div><span>Current</span><code>${safe(valueText(item.current))}</code></div><div><span>Candidate</span><code>${safe(valueText(item.candidate))}</code></div></div>`).join('')}</div>`;
  }

  function panelHtml() {
    const status = state.status || {};
    const maintenance = state.settings?.maintenance || {};
    const current = status.current_tariff || {};
    return `<section class="card tariff-sync-panel" id="tariffSyncPanel">
      <div class="card-head"><div><h2>Automatic Tariff Check</h2><small>Detection and review only. Tariffs are never applied automatically.</small></div><span class="badge">${safe(status.status || 'not checked')}</span></div>
      <div class="tariff-sync-summary">
        <div><span>Current tariff</span><strong>${safe(current.tariff_name || 'Not configured')}</strong></div>
        <div><span>Current version</span><strong>${safe(status.current_version || 'Not available')}</strong></div>
        <div><span>Effective date</span><strong>${safe(status.current_effective_date || 'Not available')}</strong></div>
        <div><span>Last check</span><strong>${safe(when(status.last_check_ts))}</strong></div>
        <div><span>Next scheduled check</span><strong>${safe(when(status.next_scheduled_check_ts))}</strong></div>
        <div><span>Provider</span><strong>${safe(status.current_provider || 'manual')}</strong></div>
      </div>
      <div class="tariff-sync-controls">
        <label>Auto tariff check<select id="tariffAutoCheck"><option value="true" ${maintenance.tariff_sync_enabled?'selected':''}>On</option><option value="false" ${!maintenance.tariff_sync_enabled?'selected':''}>Off</option></select></label>
        <label>Check interval (days)<input id="tariffCheckInterval" type="number" min="1" max="365" value="${safe(maintenance.tariff_sync_interval_days || 1)}"></label>
        <label>Source<select id="tariffProvider"><option value="manual" ${maintenance.tariff_provider==='manual'?'selected':''}>Manual</option><option value="local_candidate" ${maintenance.tariff_provider==='local_candidate'?'selected':''}>Local candidate</option><option value="mea" ${maintenance.tariff_provider==='mea'?'selected':''}>MEA</option><option value="pea" ${maintenance.tariff_provider==='pea'?'selected':''}>PEA</option></select></label>
      </div>
      <div class="tariff-sync-actions">
        <button class="btn primary" type="button" id="saveTariffSyncSettings">Save tariff check settings</button>
        <button class="btn ghost" type="button" id="checkTariffNow">Check now</button>
        <button class="btn primary" type="button" id="applyTariffCandidate" ${state.candidate?.available?'':'disabled'}>Apply candidate</button>
        <button class="btn danger" type="button" id="rejectTariffCandidate" ${state.candidate?.available?'':'disabled'}>Reject candidate</button>
        <button class="btn ghost" type="button" id="toggleTariffComparison">Show comparison</button>
      </div>
      <div id="tariffComparison" hidden>${comparisonHtml()}</div>
      <div id="tariffSyncMessage" class="tariff-sync-message" hidden></div>
    </section>`;
  }

  function render() {
    const form = document.getElementById('electricitySettingsForm');
    if (!form || document.getElementById('tariffSyncPanel')) return;
    form.insertAdjacentHTML('afterend', panelHtml());
    bind();
  }

  function message(text,error=false) {
    const host = document.getElementById('tariffSyncMessage');
    if (!host) return;
    host.hidden = false;
    host.className = `tariff-sync-message ${error?'error':''}`;
    host.textContent = text;
  }

  async function refreshAfter(action) {
    if (state.busy) return;
    state.busy = true;
    try {
      await action();
      document.getElementById('tariffSyncPanel')?.remove();
      await load();
    } catch (error) {
      message(error.message || 'Tariff operation failed.',true);
    } finally {
      state.busy = false;
    }
  }

  function bind() {
    document.getElementById('toggleTariffComparison')?.addEventListener('click', () => {
      const comparison = document.getElementById('tariffComparison');
      comparison.hidden = !comparison.hidden;
    });
    document.getElementById('saveTariffSyncSettings')?.addEventListener('click', async () => {
      try {
        const config = await request('/api/settings');
        config.maintenance = {...config.maintenance,
          tariff_sync_enabled:document.getElementById('tariffAutoCheck').value === 'true',
          tariff_sync_interval_days:Number(document.getElementById('tariffCheckInterval').value || 1),
          tariff_provider:document.getElementById('tariffProvider').value
        };
        const saved = await request('/api/settings','PUT',config);
        state.settings = saved.settings || config;
        message('Tariff check settings saved.');
      } catch (error) { message(error.message || 'Save failed.',true); }
    });
    document.getElementById('checkTariffNow')?.addEventListener('click', () => refreshAfter(async () => { await request('/api/tariff/check','POST',{}); }));
    document.getElementById('applyTariffCandidate')?.addEventListener('click', () => {
      if (!confirm('Apply this validated tariff candidate? Current settings will be backed up.')) return;
      refreshAfter(async () => { await request('/api/tariff/apply','POST',{}); });
    });
    document.getElementById('rejectTariffCandidate')?.addEventListener('click', () => {
      if (!confirm('Reject this tariff candidate?')) return;
      refreshAfter(async () => { await request('/api/tariff/reject','POST',{}); });
    });
  }

  const observer = new MutationObserver(() => {
    if (!state.loading && document.getElementById('electricitySettingsForm') && !document.getElementById('tariffSyncPanel')) load();
  });
  observer.observe(document.documentElement,{childList:true,subtree:true});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',load); else load();
})();
