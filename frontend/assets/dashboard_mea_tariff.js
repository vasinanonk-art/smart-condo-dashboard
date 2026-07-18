(() => {
  'use strict';
  if (window.__dashboardMeaTariffInstalled) return;
  window.__dashboardMeaTariffInstalled = true;

  const state = {status:null,candidate:null,csrf:null};
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
    const response = await fetch(url,{method,credentials:'same-origin',headers,body:body===undefined?undefined:JSON.stringify(body)});
    const payload = await response.json().catch(()=>({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || 'Request failed');
    return payload;
  }

  function sourceLink(url,title) {
    try {
      const parsed = new URL(url);
      if (parsed.protocol !== 'https:' || !['mea.or.th','www.mea.or.th','opendata.mea.or.th'].includes(parsed.hostname)) return '';
      return `<a class="btn ghost" href="${safe(parsed.href)}" target="_blank" rel="noopener noreferrer">${safe(title || 'View Official Source')}</a>`;
    } catch (_) { return ''; }
  }

  function comparison() {
    const rows = Object.entries(state.candidate?.comparison || {});
    if (!rows.length) return '<div class="settings-empty">No candidate changes are available.</div>';
    return `<div class="mea-comparison">${rows.map(([name,item]) => `<div class="mea-compare ${item.changed?'changed':''}"><strong>${safe(name.replaceAll('_',' '))}</strong><div><span>Active</span><code>${safe(typeof item.current==='object'?JSON.stringify(item.current):item.current)}</code></div><div><span>Candidate</span><code>${safe(typeof item.candidate==='object'?JSON.stringify(item.candidate):item.candidate)}</code></div></div>`).join('')}</div>`;
  }

  function warnings(status,candidate) {
    const rows=[];
    if (!status.provider_available) rows.push('Official MEA source is unavailable. The active tariff remains unchanged.');
    if (candidate?.parser_confidence && candidate.parser_confidence !== 'high') rows.push('Parser confidence is not high. Explicit confirmation is required.');
    if (status.last_error === 'ft_period_expired') rows.push('The published Ft period is expired.');
    if (status.last_error === 'tariff_category_mismatch') rows.push('The official document does not match MEA Residential Type 1.2.');
    if (status.candidate_status === 'future') rows.push('The candidate becomes effective in the future. Approve it for its effective date or wait.');
    return rows.map(text=>`<div class="mea-warning">${safe(text)}</div>`).join('');
  }

  function html() {
    const s=state.status||{}, c=state.candidate?.candidate||{}, active=s.active_tariff||{}, ft=s.active_ft||{};
    const canApply=state.candidate?.apply_allowed;
    const future=s.candidate_status==='future';
    return `<section class="card mea-sync-card" id="meaOfficialSync">
      <div class="card-head"><div><h2>Official MEA Tariff Sync</h2><small>Official MEA sources only · MEA Residential Type 1.2</small></div><span class="badge ${s.provider_available?'ok':'bad'}">${s.provider_available?'Available':'Degraded'}</span></div>
      ${warnings(s,c)}
      <div class="mea-status-grid">
        <div><span>Provider</span><strong>${safe(s.provider||'mea')}</strong></div>
        <div><span>Active tariff</span><strong>${safe(active.tariff_name||'Not configured')}</strong></div>
        <div><span>Active Ft</span><strong>${safe(ft.rate ?? 'Not available')}</strong></div>
        <div><span>Effective date</span><strong>${safe(active.effective_date||'Not available')}</strong></div>
        <div><span>Effective end</span><strong>${safe(s.effective_period?.to||'Open-ended')}</strong></div>
        <div><span>Last successful check</span><strong>${safe(when(s.last_success))}</strong></div>
        <div><span>Next check</span><strong>${safe(when(s.next_check))}</strong></div>
        <div><span>Parser confidence</span><strong>${safe(s.parser_confidence||'Not available')}</strong></div>
        <div><span>Candidate status</span><strong>${safe(s.candidate_status||'None')}</strong></div>
        <div><span>Auto apply</span><strong>${safe(s.auto_apply_mode||'never')}</strong></div>
      </div>
      <div class="mea-actions">
        <button class="btn primary" id="meaCheckNow" type="button">Check Now</button>
        <button class="btn ghost" id="meaReview" type="button" ${state.candidate?.available?'':'disabled'}>Review Candidate</button>
        <button class="btn primary" id="meaApply" type="button" ${canApply?'':'disabled'}>Apply Now</button>
        <button class="btn primary" id="meaApproveFuture" type="button" ${future?'':'disabled'}>Approve for Effective Date</button>
        <button class="btn danger" id="meaReject" type="button" ${state.candidate?.available?'':'disabled'}>Reject</button>
        ${sourceLink(s.source_url,'View Official Source')}
      </div>
      <div id="meaComparison" hidden>${comparison()}</div>
      <div id="meaMessage" class="tariff-sync-message" hidden></div>
    </section>`;
  }

  function message(text,error=false) {
    const host=document.getElementById('meaMessage'); if(!host)return;
    host.hidden=false; host.className=`tariff-sync-message ${error?'error':''}`; host.textContent=text;
  }

  async function load() {
    const [status,candidate]=await Promise.allSettled([request('/api/tariff/status'),request('/api/tariff/candidate')]);
    if(status.status==='fulfilled') state.status=status.value;
    if(candidate.status==='fulfilled') state.candidate=candidate.value;
    render();
  }

  function render() {
    const existing=document.getElementById('meaOfficialSync');
    if(existing) existing.remove();
    const old=document.getElementById('tariffSyncPanel');
    if(old) old.hidden=true;
    const form=document.getElementById('electricitySettingsForm');
    if(!form)return;
    form.insertAdjacentHTML('afterend',html());
    bind();
  }

  async function run(action,success) {
    try { await action(); await load(); message(success); }
    catch(error){ message(error.message||'Operation failed',true); }
  }

  function bind() {
    document.getElementById('meaReview')?.addEventListener('click',()=>{const box=document.getElementById('meaComparison');box.hidden=!box.hidden;});
    document.getElementById('meaCheckNow')?.addEventListener('click',()=>run(()=>request('/api/tariff/check','POST',{}),'Official MEA check completed.'));
    document.getElementById('meaApply')?.addEventListener('click',()=>{
      const medium=state.candidate?.candidate?.parser_confidence==='medium';
      if(!confirm(medium?'Parser confidence is medium. Apply after explicit review?':'Apply this currently effective official candidate?'))return;
      run(()=>request('/api/tariff/apply','POST',{confirm_medium_confidence:medium}),'Tariff applied without restart.');
    });
    document.getElementById('meaApproveFuture')?.addEventListener('click',()=>{
      if(!confirm('Approve this official future tariff for automatic application on its effective date?'))return;
      run(()=>request('/api/tariff/approve-future','POST',{}),'Future tariff approved.');
    });
    document.getElementById('meaReject')?.addEventListener('click',()=>{
      if(!confirm('Reject this candidate?'))return;
      run(()=>request('/api/tariff/reject','POST',{}),'Candidate rejected.');
    });
  }

  const observer=new MutationObserver(()=>{if(document.getElementById('electricitySettingsForm')&&!document.getElementById('meaOfficialSync'))load();});
  observer.observe(document.documentElement,{childList:true,subtree:true});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',load);else load();
})();