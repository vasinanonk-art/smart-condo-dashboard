(() => {
  'use strict';
  if (window.__dashboardPolish10Installed) return;
  window.__dashboardPolish10Installed = true;

  const state = {status:null,maintenance:null,notifications:[],billing:null};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const timeLabel = ts => ts ? new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Bangkok',year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(Number(ts)*1000)) : 'Not available';
  const relative = ts => { if (!ts) return ''; const seconds=Math.max(0,Math.floor(Date.now()/1000-Number(ts))); if(seconds<60)return 'just now'; if(seconds<3600)return `${Math.floor(seconds/60)} min ago`; if(seconds<86400)return `${Math.floor(seconds/3600)} hr ago`; return `${Math.floor(seconds/86400)} d ago`; };
  const percent = value => value == null ? 'Not available' : `${Number(value).toFixed(2)}%`;
  const bytes = value => { const n=Number(value||0); if(n<1024)return `${n} B`; if(n<1048576)return `${(n/1024).toFixed(1)} KB`; return `${(n/1048576).toFixed(1)} MB`; };
  const card = (label,value,sub='') => `<div class="polish-card"><span>${safe(label)}</span><strong>${safe(value)}</strong>${sub?`<small>${safe(sub)}</small>`:''}</div>`;
  const category = item => { const kind=String(item.kind||''); if(/billing|tariff|history|import|retention/.test(kind))return 'Electricity'; if(/maintenance|analysis|prune/.test(kind))return 'Maintenance'; if(/presence/.test(kind))return 'Presence'; if(/camera/.test(kind))return 'Camera'; return 'System'; };

  async function load() {
    const results = await Promise.allSettled([
      window.get('/api/settings/electricity/status'),
      window.get('/api/maintenance/status'),
      window.get('/api/notifications'),
      window.get('/api/electricity/billing-cycle?range=current_billing_cycle')
    ]);
    if(results[0].status==='fulfilled')state.status=results[0].value;
    if(results[1].status==='fulfilled')state.maintenance=results[1].value;
    if(results[2].status==='fulfilled')state.notifications=results[2].value.notifications||[];
    if(results[3].status==='fulfilled')state.billing=results[3].value;
    renderAll();
  }

  function ensureHistoryPage(){
    document.querySelectorAll('.nav,.mobile-nav').forEach(host=>{if(host.querySelector('[data-nav="history"]'))return;const b=document.createElement('button');b.dataset.nav='history';b.dataset.short='HS';b.textContent='History';const settings=host.querySelector('[data-nav="settings"]');settings?host.insertBefore(b,settings):host.appendChild(b);});
    if(!document.querySelector('[data-page="history"]')){const section=document.createElement('section');section.className='page';section.dataset.page='history';section.innerHTML='<div id="historyPage" class="polish-page"></div>';document.querySelector('.main')?.appendChild(section);}
    document.querySelectorAll('[data-nav="history"]').forEach(button=>button.onclick=()=>window.nav('history'));
  }

  function renderHeader(){
    const row=document.querySelector('.topbar .status-row');if(!row)return;
    let host=document.getElementById('dashboardCompactBadges');if(!host){host=document.createElement('div');host.id='dashboardCompactBadges';host.className='compact-status-badges';row.insertBefore(host,row.firstChild);}
    const s=state.status||{},m=state.maintenance||{};
    host.innerHTML=`<span>PJ-1103 <b>${safe(m.history_last_ts?'Healthy':'Unknown')}</b></span><span>History <b>${safe(percent(s.coverage_percent))}</b></span><span>Tariff <b>${safe(s.tariff_source||'Manual')}</b></span><span>Maintenance <b>${safe(m.last_failed_run&&(!m.last_successful_run||m.last_failed_run>m.last_successful_run)?'Warning':'OK')}</b></span><span>Notifications <b>${safe(s.current_notification_count??state.notifications.length)}</b></span>`;
  }

  function renderNotifications(){
    const panel=document.getElementById('notificationPanel');if(!panel)return;
    const groups={};[...state.notifications].sort((a,b)=>Number(b.created_ts||0)-Number(a.created_ts||0)).forEach(item=>(groups[category(item)]??=[]).push(item));
    panel.innerHTML=`<div class="notification-head"><strong>Notifications</strong><div><button class="btn ghost" data-dismiss-all>Dismiss All</button><button class="btn ghost" data-close-notifications>Close</button></div></div>${Object.entries(groups).map(([name,items])=>`<section class="notification-group"><h4>${safe(name)}</h4>${items.map(item=>`<article class="notification-item ${safe(item.severity||'warning')}"><div><strong>${safe(item.title||'Notification')}</strong><p>${safe(item.detail||'')}</p><time>${safe(relative(item.created_ts))}</time></div><button class="btn ghost" data-dismiss-notification="${safe(item.id)}">Dismiss</button></article>`).join('')}</section>`).join('')||'<div class="notification-empty">No active notifications.</div>'}`;
    panel.querySelector('[data-close-notifications]')?.addEventListener('click',()=>panel.hidden=true);
    panel.querySelector('[data-dismiss-all]')?.addEventListener('click',async()=>{await fetch('/api/notifications/dismiss-all',{method:'POST'});state.notifications=[];renderNotifications();renderHeader();});
    panel.querySelectorAll('[data-dismiss-notification]').forEach(button=>button.onclick=async()=>{await fetch(`/api/notifications/${encodeURIComponent(button.dataset.dismissNotification)}/dismiss`,{method:'POST'});state.notifications=state.notifications.filter(item=>item.id!==button.dataset.dismissNotification);renderNotifications();renderHeader();});
  }

  function renderSettings(){
    if(window.currentPage?.()!=='settings')return;const host=document.getElementById('settingsPage');if(!host||!state.status)return;
    let summary=host.querySelector('.settings-live-summary');if(!summary){summary=document.createElement('section');summary.className='settings-live-summary polish-grid';host.prepend(summary);}
    const s=state.status;
    summary.innerHTML=card('Current Billing Cycle',s.billing_cycle?.label||'Not available')+card('Next Billing Reset',timeLabel(s.next_billing_reset_ts))+card('Current Coverage',percent(s.coverage_percent))+card('History Started',timeLabel(s.history_starts))+card('History Samples',s.history_sample_count??'Not available')+card('Projection',s.projection_status==='available'?'Available':'Waiting');
    const form=document.getElementById('electricitySettingsForm');if(form){form.classList.add('polish-settings-form');form.querySelectorAll('label').forEach(label=>{const name=label.querySelector('input')?.name||'';label.dataset.group=/billing_cycle|timezone/.test(name)?'billing':'tariff';});}
    renderMaintenanceCards();
  }

  function renderMaintenanceCards(){
    const grid=document.querySelector('.maintenance-status-grid');if(!grid||!state.maintenance)return;const m=state.maintenance;
    grid.classList.add('polish-grid');grid.innerHTML=card('Last Run',timeLabel(m.last_run_ts))+card('Last Successful Run',timeLabel(m.last_successful_run))+card('Tariff Check',`${m.tariff_check_duration_ms??'N/A'} ms`,timeLabel(m.last_tariff_check_ts))+card('History Prune',`${m.history_prune_duration_ms??'N/A'} ms`,timeLabel(m.last_history_prune_ts))+card('History Analysis',m.history_import?.ok===false?'Failed':'Completed',`${m.history_import_duration_ms??'N/A'} ms`)+card('Projection Status',m.projection_status||'Not available')+card('History Size',bytes(m.history_size_bytes))+card('History Samples',m.history_sample_count??'Not available')+card('Coverage',percent(m.billing_coverage_percent));
    if(m.history_import?.diagnostics){let details=document.querySelector('.maintenance-diagnostics10');if(!details){details=document.createElement('details');details.className='maintenance-diagnostics10';grid.after(details);}details.innerHTML=`<summary>Diagnostics</summary><pre>${safe(JSON.stringify(m.history_import.diagnostics,null,2))}</pre>`;}
  }

  function renderElectricity(){
    if(window.currentPage?.()!=='electricity')return;const section=document.querySelector('.electricity-cost-card');if(!section||!state.status||!state.billing)return;const s=state.status,b=state.billing,coverage=b.coverage||{};
    const warning=!coverage.complete?`<div class="billing-warning"><strong>Current billing cycle is incomplete.</strong><span>Calculations currently cover ${safe(timeLabel(coverage.actual_from_ts))} to ${safe(timeLabel(coverage.actual_to_ts))}.</span></div>`:'';
    section.innerHTML=`<div class="card-head"><div><h2>Electricity Cost</h2><small>Estimated from configured tariff. This is not an official utility invoice.</small></div></div>${warning}<div class="polish-grid">${card('Current Billing Cycle',b.billing_period_label||s.billing_cycle?.label||'Not available')}${card('Coverage',percent(coverage.coverage_percent??s.coverage_percent))}${card('Current Usage',b.actual_partial_usage_kwh==null?'Not available':`${Number(b.actual_partial_usage_kwh).toFixed(2)} kWh`)}${card('Current Cost',b.actual_partial_cost==null?'Not available':`${Number(b.actual_partial_cost).toFixed(2)} THB`)}${card('Projected Cycle Usage',b.projected_cycle_usage_kwh==null?'Waiting':`${Number(b.projected_cycle_usage_kwh).toFixed(2)} kWh`)}${card('Projected Cycle Cost',b.projected_cycle_bill==null?'Waiting':`${Number(b.projected_cycle_bill).toFixed(2)} THB`)}${card('Tariff',s.tariff_version||s.tariff_source||'Manual')}${card('Last Tariff Check',timeLabel(s.last_tariff_check))}${card('History Started',timeLabel(s.history_starts))}${card('History Ends',timeLabel(s.history_ends))}</div>`;
  }

  function renderHistory(){
    if(window.currentPage?.()!=='history')return;const host=document.getElementById('historyPage');if(!host||!state.status)return;const s=state.status,imp=s.history_import||state.maintenance?.history_import||{};
    host.innerHTML=`<section class="card"><div class="card-head"><div><h2>Electricity History</h2><small>${safe(s.timezone_display||'Bangkok (UTC+7)')}</small></div></div><div class="polish-grid">${card('History Starts',timeLabel(s.history_starts))}${card('History Ends',timeLabel(s.history_ends))}${card('Samples',s.history_sample_count??'Not available')}${card('Retention',`${s.history_retention_days??'Not available'} days`)}${card('Import Source',imp.source||'Not available')}${card('Coverage',percent(s.coverage_percent))}${card('Backfill Status',imp.ok===false?'Failed':imp.records_would_import>0?'Available':'Up to date')}</div><div class="history-actions"><button class="btn ghost" data-history-action="analyze">Analyze History</button><button class="btn primary" data-history-action="import">Import History</button><button class="btn ghost" data-history-action="maintenance">Run Maintenance</button></div><div id="historyActionResult"></div></section>`;
    host.querySelectorAll('[data-history-action]').forEach(button=>button.onclick=async()=>{const action=button.dataset.historyAction;let url='/api/electricity/history/analyze',options={method:'POST'};if(action==='import'){if(!confirm('Import analyzed electricity history? A backup will be created.'))return;url='/api/electricity/history/import';options={method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})};}if(action==='maintenance')url='/api/maintenance/run';const response=await fetch(url,options);const payload=await response.json();document.getElementById('historyActionResult').textContent=payload.ok===false?(payload.error||'Action failed'):'Completed';await load();});
  }

  function renderAll(){ensureHistoryPage();renderHeader();renderNotifications();renderSettings();renderElectricity();renderHistory();document.querySelectorAll('[data-nav]').forEach(button=>button.onclick=()=>window.nav(button.dataset.nav));}

  const originalRenderPage=window.renderPage;
  window.renderPage=function polishedRender(page=window.currentPage()){originalRenderPage(page);setTimeout(renderAll,0);};
  document.addEventListener('click',event=>{if(event.target.closest('#notificationButton'))setTimeout(renderNotifications,0);});
  ensureHistoryPage();load();
})();
