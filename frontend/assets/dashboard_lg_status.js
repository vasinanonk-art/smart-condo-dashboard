(() => {
  'use strict';
  if (window.__dashboardLgStatusInstalled) return;
  window.__dashboardLgStatusInstalled = true;
  window.__dashboardLgPairingInstalled = true;

  const S = {
    status:null,pairing:null,job:null,csrf:null,timer:null,pairTimer:null,seq:0,applied:0,
    ignored:0,lastStarted:null,lastCompleted:null,pollMs:30000,lastError:null,command:null,
    mounted:false,countdownTimer:null
  };
  const safe = v => window.safeText ? window.safeText(v) : String(v ?? '');
  const text = (v, fallback='Unavailable') => v === null || v === undefined || v === '' ? fallback : String(v);
  const rel = ts => {
    if (!ts) return 'Unavailable';
    const s=Math.max(0,Math.floor(Date.now()/1000-Number(ts)));
    if(s<5)return 'Just now'; if(s<60)return `${s} sec ago`; if(s<3600)return `${Math.floor(s/60)} min ago`;
    return new Date(Number(ts)*1000).toLocaleString();
  };
  async function csrf(){if(S.csrf)return S.csrf;const r=await fetch('/api/auth/status',{credentials:'same-origin'});const p=await r.json();S.csrf=p.csrf_token||null;return S.csrf;}
  async function req(url,method='GET',body){const h={'Content-Type':'application/json'};if(!['GET','HEAD'].includes(method))h['X-CSRF-Token']=await csrf();const r=await fetch(url,{method,credentials:'same-origin',headers:h,body:body===undefined?undefined:JSON.stringify(body)});const p=await r.json().catch(()=>({}));if(!r.ok)throw new Error(p.detail||p.error||'request_failed');return p;}

  function mount(){
    if(S.mounted)return;
    const card=document.querySelector('[data-page="entertainment"] .card');
    const remote=document.getElementById('tvButtons');
    if(!card||!remote)return;
    const shell=document.createElement('div');
    shell.id='lgLiveShell'; shell.className='lg-live-shell';
    shell.innerHTML=`<div id="lgLiveBanner" class="lg-live-banner neutral" aria-live="polite"><div><h3 id="lgBannerTitle">LG TV status loading…</h3><p id="lgBannerText">Current values will remain visible during refresh.</p></div><button id="lgManualRefresh" class="btn ghost" type="button">Refresh</button></div>
      <div class="lg-live-grid">
        <div class="lg-live-item"><span>Status</span><strong id="lgStatusValue">Loading…</strong></div>
        <div class="lg-live-item"><span>Current App</span><strong id="lgAppValue">—</strong></div>
        <div class="lg-live-item"><span>Input</span><strong id="lgInputValue">—</strong></div>
        <div class="lg-live-item"><span>Volume</span><strong id="lgVolumeValue">—</strong></div>
        <div class="lg-live-item"><span>Mute</span><strong id="lgMuteValue">—</strong></div>
        <div class="lg-live-item"><span>Last Update</span><strong id="lgUpdateValue">—</strong></div>
      </div>
      <div class="lg-live-meta" id="lgLiveMeta"></div><div id="lgLiveMessage" class="lg-live-message" aria-live="polite"></div>`;
    card.insertBefore(shell,remote);
    const pair=document.createElement('section'); pair.id='lgPairingCardV9'; pair.className='card lg-pair-card-v9';
    pair.innerHTML=`<div class="card-head"><div><h2>LG TV Pairing</h2><small>Secure webOS pairing repair</small></div><span id="lgPairBadge" class="badge">Checking</span></div>
      <div id="lgPairReady" class="lg-pair-ready" aria-live="polite"><h3 id="lgPairTitle">Checking pairing…</h3><p id="lgPairHelp"></p></div>
      <div class="lg-pair-fields">
        <div><span>TV IP</span><strong id="lgPairIp">192.168.1.33</strong></div><div><span>Service</span><strong id="lgPairService">—</strong></div>
        <div><span>Connection</span><strong id="lgPairConnection">—</strong></div><div><span>Key source</span><strong id="lgPairSource">—</strong></div>
        <div><span>Last connection</span><strong id="lgPairLastConnection">—</strong></div><div><span>Last pairing</span><strong id="lgPairLastPairing">—</strong></div>
        <div><span>Last error</span><strong id="lgPairError">None</strong></div>
      </div>
      <div id="lgPairCountdown" class="lg-countdown"></div><div class="lg-pair-actions">
        <button id="lgPairTest" class="btn primary" type="button">Test Connection</button>
        <button id="lgPairRepair" class="btn ghost" type="button">Repair Pairing</button>
        <button id="lgPairSaveV9" class="btn primary" type="button" disabled aria-describedby="lgPairSaveHelp">Save & Reconnect</button>
        <button id="lgPairCancelV9" class="btn danger lg-hidden" type="button">Cancel Pairing</button>
        <button id="lgPairForget" class="btn danger" type="button">Forget Pairing</button>
      </div><small id="lgPairSaveHelp">Available only after a new key is registered.</small><div id="lgPairMessageV9" class="lg-pair-message" aria-live="polite"></div>`;
    card.parentElement?.appendChild(pair);
    document.getElementById('lgPairingCard')?.remove();
    bind(); S.mounted=true;
  }

  function set(id,value,title){const el=document.getElementById(id);if(!el)return;if(el.textContent!==String(value))el.textContent=String(value);if(title!==undefined)el.title=title||'';}
  function renderStatus(){const s=S.status||{};mount();
    let label='Offline',cls='bad',banner='LG TV is offline',detail='Waiting for the TV to become reachable.';
    if(s.connection_state==='pairing_required'||s.connection_state==='key_missing'){label='Pairing required';banner='LG TV requires pairing repair';detail='Use Repair Pairing, then approve the request on the TV.';}
    else if(s.connection_state==='connecting'||s.power_state==='starting'){label='Connecting';cls='warn';banner='LG TV is starting';detail='Wake command sent. Connecting to secure webOS…';}
    else if(s.online){label='Online';cls='ok';banner='LG TV online and ready';detail='Remote control and live telemetry are available.';}
    else if(s.power_state==='standby'||s.connection_state==='standby'){label='Standby';cls='neutral';banner='LG TV is in standby';detail='Status polling continues at a reduced rate.';}
    const b=document.getElementById('lgLiveBanner');if(b)b.className=`lg-live-banner ${cls}`;
    set('lgBannerTitle',banner);set('lgBannerText',detail);set('lgStatusValue',label);
    set('lgAppValue',text(s.current_app?.name,s.current_app?.id||'Unavailable'),s.current_app?.id||'');
    set('lgInputValue',text(s.current_input?.name));set('lgVolumeValue',s.audio?.volume===null||s.audio?.volume===undefined?'Unavailable':String(s.audio.volume));
    set('lgMuteValue',s.audio?.muted===true?'Muted':s.audio?.muted===false?'Unmuted':'Unavailable');
    set('lgUpdateValue',rel(s.last_update_ts||s.last_success_ts),s.last_update_ts?new Date(Number(s.last_update_ts)*1000).toLocaleString():'');
    const meta=document.getElementById('lgLiveMeta');if(meta){const cmd=s.last_command?`${safe(s.last_command)}: ${s.last_command_success===true?'OK':s.last_command_success===false?'Failed':'Pending'}`:'No command result';meta.innerHTML=`<span>${s.paired?'Paired':'Not paired'}</span><span>Secure WebSocket</span><span>Service ${s.service_active?'active':'inactive'}</span><span>Data age ${s.data_age_sec??'—'} sec</span><span>${cmd}</span>`;}
  }
  function renderPairing(){const p=S.pairing||{},j=S.job||{state:'idle'},s=S.status||{};mount();const active=['connecting','prompted'].includes(j.state),registered=j.state==='registered',ready=p.paired&&p.connection_status==='connected';
    set('lgPairBadge',ready?'Paired & Connected':p.pairing_required?'Pairing required':'Checking');
    set('lgPairTitle',ready?'✓ LG TV is paired and ready':active?(j.state==='prompted'?'Approve the connection request on the LG TV':'Connecting to TV…'):registered?'Pairing registered — ready to save':'Pairing required');
    set('lgPairHelp',ready?'Remote control is available. No action required.':active?'Waiting for approval…':'Click Repair Pairing, then approve the request on the LG TV.');
    set('lgPairIp',p.tv_ip||'192.168.1.33');set('lgPairService',p.service_active?'Active':'Inactive');set('lgPairConnection',p.connection_status||s.connection_state||'Unavailable');set('lgPairSource',p.key_source||s.key_source||'none');
    set('lgPairLastConnection',rel(p.last_connection_success||s.last_success_ts));set('lgPairLastPairing',rel(p.last_pair_success||p.last_pair_attempt));set('lgPairError',p.last_error||s.last_error||'None');
    const save=document.getElementById('lgPairSaveV9');if(save)save.disabled=!registered;document.getElementById('lgPairCancelV9')?.classList.toggle('lg-hidden',!active);
    const countdown=document.getElementById('lgPairCountdown');if(countdown)countdown.textContent=active&&j.expires_ts?`Time remaining: ${Math.max(0,Number(j.expires_ts)-Math.floor(Date.now()/1000))} sec`:'';
    managePairPoll(active);
  }
  function msg(id,value,error=false){const el=document.getElementById(id);if(!el)return;el.textContent=value;el.classList.toggle('error',error);}

  async function fetchAll(background=true){mount();const seq=++S.seq;S.lastStarted=Date.now();try{const [status,pairing,job]=await Promise.all([req('/api/lg-tv/status'),req('/api/lg-tv/pairing/status'),req('/api/lg-tv/pairing/job')]);if(seq<S.applied){S.ignored++;return;}S.applied=seq;S.status=status;S.pairing=pairing;S.job=job;S.lastError=null;renderStatus();renderPairing();schedule();}catch(e){S.lastError=e.message||'status_failed';if(!background)msg('lgLiveMessage',S.lastError,true);}finally{S.lastCompleted=Date.now();}}
  function schedule(){const ms=S.status?.online?5000:30000;S.pollMs=ms;if(S.timer){clearInterval(S.timer);S.timer=null;}if(document.hidden||window.currentPage?.()!=='entertainment')return;S.timer=setInterval(()=>fetchAll(true),ms);}
  function managePairPoll(active){if(active&&!S.pairTimer)S.pairTimer=setInterval(()=>fetchAll(true),2000);if(!active&&S.pairTimer){clearInterval(S.pairTimer);S.pairTimer=null;}}
  async function action(button,fn,success){const y=scrollY;button.disabled=true;try{const result=await fn();msg('lgPairMessageV9',success||result.result||'Done');await fetchAll(false);return result;}catch(e){msg('lgPairMessageV9',e.message||'Operation failed',true);}finally{button.disabled=false;scrollTo(0,y);}}
  function bind(){
    document.getElementById('lgManualRefresh')?.addEventListener('click',e=>action(e.currentTarget,()=>req('/api/lg-tv/status/refresh','POST',{}),'Refreshing…'));
    document.getElementById('lgPairTest')?.addEventListener('click',e=>action(e.currentTarget,()=>req('/api/lg-tv/pairing/test','POST',{}),'Connection successful. LG TV is ready.'));
    document.getElementById('lgPairRepair')?.addEventListener('click',e=>action(e.currentTarget,()=>req('/api/lg-tv/pairing/request','POST',{}),'Approve the connection request on the LG TV'));
    document.getElementById('lgPairSaveV9')?.addEventListener('click',e=>action(e.currentTarget,()=>req('/api/lg-tv/pairing/save','POST',{}),'Client key saved and service reconnected.'));
    document.getElementById('lgPairCancelV9')?.addEventListener('click',e=>action(e.currentTarget,()=>req('/api/lg-tv/pairing/cancel','POST',{}),'Pairing cancelled.'));
    document.getElementById('lgPairForget')?.addEventListener('click',e=>{if(!confirm('Forget the saved LG TV pairing key? A timestamped backup will be kept.'))return;action(e.currentTarget,()=>req('/api/lg-tv/pairing/forget','POST',{}),'Pairing forgotten.');});
  }

  const oldTv=window.tv;
  if(typeof oldTv==='function')window.tv=function epic09Tv(command){const started=performance.now();S.command=command;const button=document.querySelector(`[data-lg-command="${CSS.escape(command)}"]`);if(button)button.disabled=true;let out;try{out=oldTv(command);Promise.resolve(out).then(()=>{msg('lgLiveMessage',`${command} sent successfully.`);setTimeout(()=>req('/api/lg-tv/status/refresh','POST',{}).catch(()=>{}),command==='power_on'?3000:900);}).catch(()=>msg('lgLiveMessage',`${command} failed.`,true)).finally(()=>{if(button)button.disabled=false;});}catch(e){if(button)button.disabled=false;msg('lgLiveMessage',`${command} failed.`,true);throw e;}return out;};

  document.addEventListener('visibilitychange',()=>{if(document.hidden){if(S.timer)clearInterval(S.timer);S.timer=null;}else if(window.currentPage?.()==='entertainment'){fetchAll(true);}});
  window.addEventListener('beforeunload',()=>{if(S.timer)clearInterval(S.timer);if(S.pairTimer)clearInterval(S.pairTimer);});
  const oldRender=window.renderEntertainment;window.renderEntertainment=function(){if(typeof oldRender==='function')oldRender();mount();fetchAll(true);};
  window.dashboardLgTvDiagnostics=()=>({active_pollers:(S.timer?1:0)+(S.pairTimer?1:0),last_status_fetch_started:S.lastStarted,last_status_fetch_completed:S.lastCompleted,ignored_stale_responses:S.ignored,current_poll_interval_ms:S.pollMs,status_age_sec:S.status?.data_age_sec??null,pairing_job_poll_active:!!S.pairTimer,command_in_progress:S.command,last_ui_error:S.lastError});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>fetchAll(false));else fetchAll(false);
})();