(() => {
  'use strict';
  if (window.__electricitySettingsHotfix17Installed) return;
  window.__electricitySettingsHotfix17Installed = true;

  // Own the three legacy modules before they load. One stable view and one shared data source.
  window.__dashboardSettingsInstalled = true;
  window.__dashboardTariffSyncInstalled = true;
  window.__dashboardMeaTariffInstalled = true;

  const POLL_MS = 60000;
  const state = {
    settings:null, tariffStatus:null, candidate:null, legacyTariffStatus:null, syncStatus:null,
    maintenance:null, notifications:[], csrf:null, initialLoading:true, refreshing:false,
    settingsDirty:false, saving:false, tariffCheckInProgress:false, activeSection:'electricity',
    pollTimer:null, requestSequence:0, appliedSequence:0, ignoredStaleResponses:0,
    lastRefreshStarted:null, lastRefreshCompleted:null, lastError:null, mounted:false,
  };
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const num = (value, fallback=0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const when = ts => ts ? new Date(Number(ts)*1000).toLocaleString() : 'Not available';
  const diagnosticsEnabled = Boolean(window.DASHBOARD_CHART_DEBUG || /(?:localhost|127\.0\.0\.1)/.test(location.hostname));

  function diagnostics() {
    return {
      active_pollers: state.pollTimer ? 1 : 0,
      last_refresh_started: state.lastRefreshStarted,
      last_refresh_completed: state.lastRefreshCompleted,
      ignored_stale_responses: state.ignoredStaleResponses,
      settings_dirty: state.settingsDirty,
      tariff_check_in_progress: state.tariffCheckInProgress,
    };
  }
  window.dashboardElectricitySettingsDiagnostics = diagnostics;

  async function csrf() {
    if (state.csrf) return state.csrf;
    const response = await fetch('/api/auth/status',{credentials:'same-origin'});
    const body = await response.json();
    state.csrf = body.csrf_token || null;
    return state.csrf;
  }

  async function request(url, method='GET', body, signal) {
    const headers={'Content-Type':'application/json'};
    if (!['GET','HEAD'].includes(method)) headers['X-CSRF-Token']=await csrf();
    const response=await fetch(url,{method,credentials:'same-origin',headers,signal,body:body===undefined?undefined:JSON.stringify(body)});
    const payload=await response.json().catch(()=>({}));
    if(!response.ok) throw new Error(payload.detail||payload.error||`${method} ${url} failed`);
    return payload;
  }

  async function refreshData({initial=false, force=false}={}) {
    if (state.refreshing && !force) return;
    state.refreshing=true;
    state.lastRefreshStarted=Date.now();
    updateRefreshIndicator();
    const sequence=++state.requestSequence;
    const controller=new AbortController();
    try {
      const endpoints=[
        '/api/tariff/status','/api/tariff/candidate','/api/settings',
        '/api/electricity/tariff/status','/api/electricity/tariff/sync-status',
        '/api/maintenance/status','/api/notifications'
      ];
      const results=await Promise.allSettled(endpoints.map(url=>request(url,'GET',undefined,controller.signal)));
      if(sequence < state.appliedSequence){ state.ignoredStaleResponses += 1; return; }
      state.appliedSequence=sequence;
      const previousSuccess={
        tariffStatus:state.tariffStatus,candidate:state.candidate,settings:state.settings,
        legacyTariffStatus:state.legacyTariffStatus,syncStatus:state.syncStatus,
        maintenance:state.maintenance,notifications:state.notifications,
      };
      const keys=['tariffStatus','candidate','settings','legacyTariffStatus','syncStatus','maintenance','notifications'];
      results.forEach((result,index)=>{
        if(result.status==='fulfilled'){
          if(keys[index]==='notifications') state.notifications=result.value.notifications||[];
          else if(keys[index]==='settings'){
            state.settings=result.value.settings||result.value;
          } else state[keys[index]]=result.value;
        }
      });
      const failures=results.filter(result=>result.status==='rejected');
      state.lastError=failures.length ? String(failures[0].reason?.message||'Background refresh failed') : null;
      // Never replace a dirty draft. Only patch read-only status nodes in the stable DOM.
      if(!state.mounted) mount();
      else patchReadOnly();
      if(initial && !state.settingsDirty) hydrateFormsFromSettings();
      if(failures.length && !Object.values(previousSuccess).some(Boolean)) showMessage(state.lastError,true);
    } finally {
      if(sequence===state.requestSequence){
        state.refreshing=false;
        state.initialLoading=false;
        state.lastRefreshCompleted=Date.now();
        updateRefreshIndicator();
      }
    }
  }

  function installNavigation() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host=>{
      if(host.querySelector('[data-nav="settings"]')) return;
      const button=document.createElement('button'); button.dataset.nav='settings'; button.dataset.short='ST'; button.textContent='Settings';
      host.appendChild(button);
    });
    if(!document.querySelector('[data-page="settings"]')){
      const section=document.createElement('section'); section.className='page'; section.dataset.page='settings';
      section.innerHTML='<div id="settingsPage" class="settings-page"><div class="card settings-loading-shell"><div class="settings-loading">Loading settings…</div></div></div>';
      document.querySelector('.main')?.appendChild(section);
    }
  }

  function tierRow(item={},index=0){
    const unlimited=item.up_to_kwh===null||item.up_to_kwh===''||item.up_to_kwh===undefined;
    return `<div class="settings-tier-row" data-tier-row><label>Up to kWh<input type="number" min="0" step="0.001" data-tier-limit value="${unlimited?'':safe(item.up_to_kwh)}" placeholder="Unlimited"></label><label>Rate<input type="number" min="0" step="0.0001" data-tier-rate value="${safe(item.rate??0)}"></label><button class="btn ghost" type="button" data-remove-tier="${index}">Remove</button></div>`;
  }

  function mount(){
    const host=document.getElementById('settingsPage'); if(!host||state.mounted)return;
    state.mounted=true;
    host.innerHTML=`<div class="settings-tabs">
      <button class="btn ghost active" type="button" data-settings-section="electricity">Electricity</button>
      <button class="btn ghost" type="button" data-settings-section="dashboard">Dashboard</button>
      <button class="btn ghost" type="button" data-settings-section="maintenance">Maintenance</button>
      <span id="settingsRefreshIndicator" class="settings-refresh-indicator" aria-live="polite"></span>
    </div>
    <section class="card settings-card" id="settingsStableCard">
      <div class="card-head"><div><h2 id="settingsSectionTitle">Electricity</h2><small>Saved to ~/.smart-condo-dashboard/settings.json</small></div></div>
      <div id="settingsStableError" class="settings-stable-error" aria-live="polite"></div>
      <section data-settings-panel="electricity">
        <form id="electricitySettingsForm" class="settings-form">
          <div class="settings-grid">
            <label>Billing cycle day<input name="billing_cycle_day" type="number" min="1" max="31" required></label>
            <label>Timezone<input name="timezone" type="text" required></label>
            <label>Tariff Name<input name="tariff_name" type="text"></label>
            <label>Effective Date<input name="effective_date" type="date"></label>
            <label>Tariff Source<input name="source" type="text"></label>
            <label>Version<input name="version" type="text"></label>
            <label>Ft per kWh<input name="ft_rate" type="number" min="0" step="0.0001"></label>
            <label>Service Charge<input name="service_charge" type="number" min="0" step="0.01"></label>
            <label>VAT %<input name="vat_percent" type="number" min="0" max="100" step="0.01"></label>
            <label>Minimum Charge<input name="minimum_charge" type="number" min="0" step="0.01"></label>
          </div>
          <div class="settings-subhead"><div><h3>Progressive tiers</h3><p>Leave the final upper limit blank for the unlimited tier.</p></div><button class="btn ghost" id="addTariffTier" type="button">Add Tier</button></div>
          <div id="tariffTierList" class="settings-tier-list"></div>
          <div id="settingsMessage" class="settings-message stable-message" aria-live="polite"></div>
          <div class="settings-actions"><button class="btn primary" id="saveElectricitySettings" type="submit">Save Electricity Settings</button><button class="btn ghost" id="discardElectricityDraft" type="button">Reload / Discard</button></div>
        </form>
        <section class="card tariff-sync-panel stable-tariff-card" id="tariffSyncPanel">
          <div class="card-head"><div><h2>Automatic Tariff Sync</h2><small>Official MEA sources only · review before apply</small></div><span id="tariffProviderBadge" class="badge">Checking</span></div>
          <div id="tariffWarnings" class="stable-warning-area"></div>
          <div class="tariff-sync-summary" id="tariffStatusSummary"></div>
          <div class="tariff-sync-actions">
            <button class="btn primary" id="checkTariffNow" type="button">Check Now</button>
            <button class="btn ghost" id="reviewTariffCandidate" type="button">Review Candidate</button>
            <button class="btn primary" id="applyTariffCandidate" type="button">Apply Now</button>
            <button class="btn primary" id="approveFutureTariff" type="button">Approve for Effective Date</button>
            <button class="btn danger" id="rejectTariffCandidate" type="button">Reject</button>
          </div>
          <div id="tariffComparison" class="stable-comparison" hidden></div>
          <div id="tariffActionMessage" class="tariff-sync-message stable-message" aria-live="polite"></div>
        </section>
      </section>
      <section data-settings-panel="dashboard" hidden><form id="dashboardSettingsForm" class="settings-form"><div class="settings-grid"><label>Dashboard Timezone<input name="timezone" type="text" required></label></div><div class="settings-actions"><button class="btn primary" type="submit">Save Dashboard Settings</button></div></form></section>
      <section data-settings-panel="maintenance" hidden><div class="settings-form"><div class="settings-grid"><label>Daily maintenance hour<input id="maintenanceHour" type="number" min="0" max="23"></label><label>History retention days<input id="retentionDays" type="number" min="1" max="3650"></label><label class="settings-check"><input id="tariffSyncEnabled" type="checkbox"> Enable daily tariff check</label><label>Sync interval days<input id="tariffSyncInterval" type="number" min="1" max="365"></label></div><div class="settings-actions"><button id="saveMaintenance" class="btn primary" type="button">Save Maintenance Settings</button></div><div id="maintenanceStatus" class="maintenance-status-grid"></div></div></section>
      ${diagnosticsEnabled?'<pre id="settingsFrontendDiagnostics" class="settings-frontend-diagnostics"></pre>':''}
    </section>`;
    bind(); hydrateFormsFromSettings(); patchReadOnly();
  }

  function hydrateFormsFromSettings(){
    if(!state.settings)return;
    const e=state.settings.electricity||{}, t=e.tariff||{}, form=document.getElementById('electricitySettingsForm');
    if(form && !state.settingsDirty){
      const values={billing_cycle_day:e.billing_cycle_day??2,timezone:e.timezone||'Asia/Bangkok',tariff_name:t.tariff_name||'',effective_date:t.effective_date||'',source:t.source||'manual',version:t.version||'',ft_rate:t.ft_rate??0,service_charge:t.service_charge??0,vat_percent:t.vat_percent??7,minimum_charge:t.minimum_charge??0};
      Object.entries(values).forEach(([name,value])=>{if(form.elements[name])form.elements[name].value=value;});
      document.getElementById('tariffTierList').innerHTML=(Array.isArray(t.tiers)&&t.tiers.length?t.tiers:[{up_to_kwh:null,rate:0}]).map(tierRow).join('');
      bindTierRemovers(); state.settingsDirty=false;
    }
    const dashboard=document.getElementById('dashboardSettingsForm'); if(dashboard) dashboard.elements.timezone.value=state.settings.dashboard?.timezone||'Asia/Bangkok';
    const m=state.settings.maintenance||{};
    if(document.getElementById('maintenanceHour')) document.getElementById('maintenanceHour').value=m.daily_hour??3;
    if(document.getElementById('retentionDays')) document.getElementById('retentionDays').value=m.history_retention_days??400;
    if(document.getElementById('tariffSyncEnabled')) document.getElementById('tariffSyncEnabled').checked=Boolean(m.tariff_sync_enabled);
    if(document.getElementById('tariffSyncInterval')) document.getElementById('tariffSyncInterval').value=m.tariff_sync_interval_days??1;
  }

  function comparisonHtml(){
    const rows=Object.entries(state.candidate?.comparison||{});
    return rows.length?`<div class="tariff-comparison">${rows.map(([field,item])=>`<div class="tariff-compare-row ${item.changed?'changed':''}"><strong>${safe(field.replaceAll('_',' '))}</strong><div><span>Current</span><code>${safe(typeof item.current==='object'?JSON.stringify(item.current):item.current??'—')}</code></div><div><span>Candidate</span><code>${safe(typeof item.candidate==='object'?JSON.stringify(item.candidate):item.candidate??'—')}</code></div></div>`).join('')}</div>`:'<div class="settings-empty">No candidate changes are available.</div>';
  }

  function patchReadOnly(){
    if(!state.mounted)return;
    const s=state.tariffStatus||{}, active=s.active_tariff||s.current_tariff||{}, ft=s.active_ft||{};
    const badge=document.getElementById('tariffProviderBadge');
    if(badge){badge.textContent=s.provider_available===false?'Degraded':'Available';badge.className=`badge ${s.provider_available===false?'bad':'ok'}`;}
    const summary=document.getElementById('tariffStatusSummary');
    if(summary) summary.innerHTML=[
      ['Provider',s.provider||s.current_provider||'mea'],['Active tariff',active.tariff_name||'Not configured'],['Active Ft',ft.rate??active.ft_rate??'Not available'],
      ['Effective date',active.effective_date||'Not available'],['Last successful check',when(s.last_success||s.last_check_ts)],['Next check',when(s.next_check||s.next_scheduled_check_ts)],
      ['Parser confidence',s.parser_confidence||'Not available'],['Candidate status',s.candidate_status||s.status||'None']
    ].map(([label,value])=>`<div><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`).join('');
    const warnings=[]; if(s.last_error)warnings.push(`Last error: ${s.last_error}. Previous successful data remains visible.`); if(s.candidate_status==='future')warnings.push('Candidate effective date is in the future.');
    const warningHost=document.getElementById('tariffWarnings'); if(warningHost)warningHost.innerHTML=warnings.map(x=>`<div class="mea-warning">${safe(x)}</div>`).join('')||'<div class="stable-warning-placeholder" aria-hidden="true"></div>';
    const available=Boolean(state.candidate?.available), canApply=Boolean(state.candidate?.apply_allowed), future=s.candidate_status==='future';
    for(const [id,disabled] of [['reviewTariffCandidate',!available],['applyTariffCandidate',!canApply],['approveFutureTariff',!future],['rejectTariffCandidate',!available]]){const button=document.getElementById(id);if(button)button.disabled=disabled;}
    const check=document.getElementById('checkTariffNow'); if(check){check.disabled=state.tariffCheckInProgress;check.textContent=state.tariffCheckInProgress?'Checking…':'Check Now';}
    const compare=document.getElementById('tariffComparison'); if(compare)compare.innerHTML=comparisonHtml();
    const maintenance=document.getElementById('maintenanceStatus'), m=state.maintenance||{};
    if(maintenance) maintenance.innerHTML=[['Last run',when(m.last_run_ts)],['Last tariff check',when(m.last_tariff_check_ts)],['Projection',m.projection_status||'Not available']].map(([a,b])=>`<div><span>${safe(a)}</span><strong>${safe(b)}</strong></div>`).join('');
    const error=document.getElementById('settingsStableError'); if(error){error.textContent=state.lastError||'';error.classList.toggle('visible',Boolean(state.lastError));}
    const debug=document.getElementById('settingsFrontendDiagnostics'); if(debug)debug.textContent=JSON.stringify(diagnostics(),null,2);
    updateRefreshIndicator();
  }

  function updateRefreshIndicator(){const host=document.getElementById('settingsRefreshIndicator');if(host)host.textContent=state.refreshing&&!state.initialLoading?'Refreshing…':'';}
  function showMessage(text,error=false,id='settingsMessage'){const box=document.getElementById(id);if(!box)return;box.textContent=text||'';box.className=`${id==='settingsMessage'?'settings-message':'tariff-sync-message'} stable-message ${error?'error':'success'}`;}
  function collectTiers(){return [...document.querySelectorAll('#tariffTierList [data-tier-row]')].map(row=>({up_to_kwh:row.querySelector('[data-tier-limit]').value===''?null:num(row.querySelector('[data-tier-limit]').value),rate:num(row.querySelector('[data-tier-rate]').value)}));}
  function markDirty(){state.settingsDirty=true;patchReadOnly();}
  function bindTierRemovers(){document.querySelectorAll('#tariffTierList [data-remove-tier]').forEach(button=>button.onclick=()=>{if(document.querySelectorAll('#tariffTierList [data-tier-row]').length<=1)return;button.closest('[data-tier-row]')?.remove();markDirty();});}

  function bind(){
    document.querySelectorAll('[data-settings-section]').forEach(button=>button.onclick=()=>{
      state.activeSection=button.dataset.settingsSection;
      document.querySelectorAll('[data-settings-section]').forEach(item=>item.classList.toggle('active',item===button));
      document.querySelectorAll('[data-settings-panel]').forEach(panel=>panel.hidden=panel.dataset.settingsPanel!==state.activeSection);
      document.getElementById('settingsSectionTitle').textContent=button.textContent;
    });
    const form=document.getElementById('electricitySettingsForm'); form.addEventListener('input',markDirty); form.addEventListener('change',markDirty);
    document.getElementById('addTariffTier').onclick=()=>{document.getElementById('tariffTierList').insertAdjacentHTML('beforeend',tierRow({up_to_kwh:null,rate:0},document.querySelectorAll('#tariffTierList [data-tier-row]').length));bindTierRemovers();markDirty();};
    document.getElementById('discardElectricityDraft').onclick=()=>{state.settingsDirty=false;hydrateFormsFromSettings();showMessage('Draft reloaded.');patchReadOnly();};
    form.onsubmit=async event=>{
      event.preventDefault(); if(state.saving)return; state.saving=true; const button=document.getElementById('saveElectricitySettings');button.disabled=true;const scrollY=window.scrollY;
      const data=new FormData(form), payload={billing_cycle_day:num(data.get('billing_cycle_day'),2),timezone:String(data.get('timezone')||'Asia/Bangkok'),tariff:{tariff_name:String(data.get('tariff_name')||''),effective_date:String(data.get('effective_date')||''),source:String(data.get('source')||'manual'),version:String(data.get('version')||''),tiers:collectTiers(),ft_rate:num(data.get('ft_rate')),service_charge:num(data.get('service_charge')),vat_percent:num(data.get('vat_percent'),7),minimum_charge:num(data.get('minimum_charge'))}};
      try{const result=await request('/api/settings/electricity','PUT',payload);state.settings={...(state.settings||{}),electricity:result.settings?.electricity||payload};state.settingsDirty=false;hydrateFormsFromSettings();showMessage('Electricity settings saved. No restart required.');await refreshData({force:true});}
      catch(error){showMessage(error.message||'Save failed.',true);}finally{state.saving=false;button.disabled=false;window.scrollTo(0,scrollY);patchReadOnly();}
    };
    document.getElementById('reviewTariffCandidate').onclick=()=>{const box=document.getElementById('tariffComparison');box.hidden=!box.hidden;};
    document.getElementById('checkTariffNow').onclick=async()=>{if(state.tariffCheckInProgress)return;state.tariffCheckInProgress=true;patchReadOnly();try{await request('/api/tariff/check','POST',{});showMessage('Official MEA check completed.',false,'tariffActionMessage');await refreshData({force:true});}catch(error){showMessage(error.message||'Check failed.',true,'tariffActionMessage');}finally{state.tariffCheckInProgress=false;patchReadOnly();}};
    document.getElementById('applyTariffCandidate').onclick=()=>tariffAction('/api/tariff/apply',{confirm_medium_confidence:true},'Tariff applied.');
    document.getElementById('approveFutureTariff').onclick=()=>tariffAction('/api/tariff/approve-future',{},'Future tariff approved.');
    document.getElementById('rejectTariffCandidate').onclick=()=>tariffAction('/api/tariff/reject',{},'Candidate rejected.');
    document.getElementById('saveMaintenance').onclick=async()=>{const payload={...(state.settings||{}),maintenance:{...(state.settings?.maintenance||{}),daily_hour:num(document.getElementById('maintenanceHour').value,3),history_retention_days:num(document.getElementById('retentionDays').value,400),tariff_sync_enabled:document.getElementById('tariffSyncEnabled').checked,tariff_sync_interval_days:num(document.getElementById('tariffSyncInterval').value,1)}};const result=await request('/api/settings','PUT',payload);state.settings=result.settings||payload;hydrateFormsFromSettings();};
    bindTierRemovers();
  }

  async function tariffAction(url,body,success){try{await request(url,'POST',body);showMessage(success,false,'tariffActionMessage');await refreshData({force:true});}catch(error){showMessage(error.message||'Operation failed.',true,'tariffActionMessage');}}

  function startPolling(){if(state.pollTimer)return;state.pollTimer=setInterval(()=>{if(document.visibilityState==='visible')refreshData();},POLL_MS);patchReadOnly();}
  function stopPolling(){if(state.pollTimer){clearInterval(state.pollTimer);state.pollTimer=null;}patchReadOnly();}

  installNavigation();
  const previousRenderPage=window.renderPage;
  window.renderPage=function stableRenderPage(page=window.currentPage()){
    previousRenderPage(page);
    if(page==='settings'){
      if(!state.mounted)mount();
      startPolling();
    } else stopPolling();
  };
  const previousRefresh=window.refresh;
  window.refresh=async function stableRefresh(){await previousRefresh();if(window.currentPage()==='settings')await refreshData();};
  document.querySelectorAll('[data-nav]').forEach(button=>button.onclick=()=>window.nav(button.dataset.nav));
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible'&&window.currentPage()==='settings'){startPolling();refreshData();}else if(document.visibilityState==='hidden')stopPolling();});
  window.addEventListener('beforeunload',stopPolling);
  refreshData({initial:true}).then(()=>{if(window.currentPage()==='settings'){mount();startPolling();}});
})();