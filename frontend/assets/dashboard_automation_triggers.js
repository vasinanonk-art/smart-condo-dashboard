(() => {
  'use strict';
  if (window.__dashboardAutomationTriggersInstalled) return;
  window.__dashboardAutomationTriggersInstalled = true;

  const state = {runtime:null,items:[],editingId:null,csrf:null};
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const originalFetch = window.fetch.bind(window);

  async function csrf(){
    if(state.csrf)return state.csrf;
    const response=await originalFetch('/api/auth/status',{credentials:'same-origin'});
    const payload=await response.json();
    state.csrf=payload.csrf_token||null;
    return state.csrf;
  }

  function triggerFromForm(){
    const type=document.getElementById('automationTriggerType')?.value||'manual';
    if(type==='manual')return {type:'manual'};
    if(type==='interval')return {type:'interval',every_sec:Number(document.getElementById('triggerIntervalSec')?.value||30)};
    if(type==='time'){
      const mode=document.getElementById('triggerTimeMode')?.value||'exact';
      const days=document.getElementById('triggerWeekdays')?.value||'';
      const weekdays=days==='weekday'?'weekday':days==='weekend'?'weekend':undefined;
      if(mode==='cron')return {type:'time',cron:String(document.getElementById('triggerCron')?.value||'* * * * *'),...(weekdays?{weekdays}:{})};
      if(mode==='hourly')return {type:'time',minute:Number(document.getElementById('triggerMinute')?.value||0),...(weekdays?{weekdays}:{})};
      return {type:'time',at:String(document.getElementById('triggerExactTime')?.value||'08:30'),...(weekdays?{weekdays}:{})};
    }
    if(type==='presence')return {type:'presence',event:String(document.getElementById('triggerPresenceEvent')?.value||'anyone_home')};
    const field=String(document.getElementById('triggerField')?.value||'power');
    const event=String(document.getElementById('triggerEventMode')?.value||'threshold');
    if(event==='change')return {type,field,event:'change'};
    const raw=document.getElementById('triggerValue')?.value;
    const value=['power','voltage','current','living_room','bedroom','maximum','cpu'].includes(field)?Number(raw):String(raw||'');
    return {type,field,operator:String(document.getElementById('triggerOperator')?.value||'gt'),value,edge:String(document.getElementById('triggerEdge')?.value||'rising')};
  }

  window.fetch=async function triggerAwareFetch(input,init={}){
    const url=typeof input==='string'?input:input?.url||'';
    if(init?.body && ['/api/automations','/api/automations/simulate'].some(path=>url===path||url.endsWith(path)) || (init?.body && /\/api\/automations\/automation_[^/]+$/.test(url))){
      try{
        const payload=JSON.parse(init.body);
        if(payload?.automation)payload.automation.trigger=triggerFromForm();
        else if(payload && typeof payload==='object')payload.trigger=triggerFromForm();
        init={...init,body:JSON.stringify(payload)};
      }catch(_){ }
    }
    return originalFetch(input,init);
  };

  function options(values,selected){return values.map(([value,label])=>`<option value="${value}" ${value===selected?'selected':''}>${label}</option>`).join('');}
  function fieldsFor(type){
    if(type==='electricity')return [['power','Power'],['voltage','Voltage'],['current','Current'],['health','Health']];
    if(type==='pm25')return [['living_room','Living room'],['bedroom','Bedroom'],['maximum','Maximum']];
    if(type==='temperature')return [['cpu','CPU temperature']];
    if(type==='system')return [['mqtt_connected','MQTT connected'],['dashboard_health','Dashboard health']];
    return [];
  }

  function triggerEditor(trigger={type:'manual'}){
    const type=trigger.type||'manual';
    let detail='';
    if(type==='interval')detail=`<label>Every seconds<input id="triggerIntervalSec" type="number" min="1" step="1" value="${safe(trigger.every_sec||30)}"></label>`;
    else if(type==='time')detail=`<label>Schedule<select id="triggerTimeMode">${options([['exact','Exact time'],['hourly','Every hour'],['cron','cron-lite']],trigger.cron?'cron':trigger.hour==null&&trigger.minute!=null?'hourly':'exact')}</select></label><label>Exact time<input id="triggerExactTime" type="time" value="${safe(trigger.hour!=null?`${String(trigger.hour).padStart(2,'0')}:${String(trigger.minute||0).padStart(2,'0')}`:'08:30')}"></label><label>Minute<input id="triggerMinute" type="number" min="0" max="59" value="${safe(trigger.minute??0)}"></label><label>cron-lite<input id="triggerCron" value="${safe(trigger.cron||'* * * * *')}"></label><label>Days<select id="triggerWeekdays">${options([['','Every day'],['weekday','Weekday only'],['weekend','Weekend only']],typeof trigger.weekdays==='string'?trigger.weekdays:'')}</select></label>`;
    else if(type==='presence')detail=`<label>Presence event<select id="triggerPresenceEvent">${options([['beer_arrives','Beer arrives'],['beer_leaves','Beer leaves'],['seem_arrives','Seem arrives'],['seem_leaves','Seem leaves'],['anyone_home','Anyone home'],['everyone_away','Everyone away']],trigger.event||'anyone_home')}</select></label>`;
    else if(['electricity','pm25','temperature','system'].includes(type))detail=`<label>Field<select id="triggerField">${options(fieldsFor(type),trigger.field||fieldsFor(type)[0][0])}</select></label><label>Event<select id="triggerEventMode">${options([['threshold','Threshold crossing'],['change','Value changes']],trigger.event||'threshold')}</select></label><label>Operator<select id="triggerOperator">${options([['gt','>'],['gte','≥'],['lt','<'],['lte','≤'],['eq','='],['ne','≠']],trigger.operator||'gt')}</select></label><label>Value<input id="triggerValue" value="${safe(trigger.value??'')}"></label><label>Edge<select id="triggerEdge">${options([['rising','Rising'],['falling','Falling'],['both','Both']],trigger.edge||'rising')}</select></label>`;
    return `<section class="automation-trigger-builder"><div><h3>Trigger</h3><small>Detection only. Actions remain pending.</small></div><div class="automation-trigger-grid"><label>Type<select id="automationTriggerType">${options([['manual','Manual'],['time','Time'],['interval','Interval'],['electricity','Electricity'],['presence','Presence'],['pm25','PM2.5'],['temperature','Temperature'],['system','System']],type)}</select></label>${detail}</div></section>`;
  }

  async function loadRuntime(){
    const [runtime,list]=await Promise.allSettled([originalFetch('/api/automations/runtime',{credentials:'same-origin'}).then(r=>r.json()),originalFetch('/api/automations',{credentials:'same-origin'}).then(r=>r.json())]);
    if(runtime.status==='fulfilled')state.runtime=runtime.value;
    if(list.status==='fulfilled')state.items=list.value.automations||[];
    enhance();
  }

  function runtimePanel(){
    const r=state.runtime||{};
    const last=r.last_trigger;
    return `<section class="card automation-runtime-panel"><div class="card-head"><div><h2>Trigger Runtime</h2><small>Single worker · detection only</small></div><span class="automation-execution-disabled">Execution Disabled</span></div><div class="automation-runtime-grid"><div><span>Worker</span><strong>${r.worker_alive?'Running':'Stopped'}</strong></div><div><span>Queue</span><strong>${safe(r.pending_queue_count??0)} / 100</strong></div><div><span>Cooldowns</span><strong>${safe(r.cooldown_count??0)}</strong></div><div><span>Triggers</span><strong>${safe(r.trigger_count??0)}</strong></div><div><span>Last trigger</span><strong>${last?`${safe(last.automation_id)} · ${new Date(last.ts*1000).toLocaleString()}`:'None'}</strong></div><div><span>Pending actions</span><strong>${safe(r.pending_queue_count??0)}</strong></div></div>${(r.pending_queue||[]).length?`<details><summary>Pending queue</summary><div class="automation-pending-list">${r.pending_queue.slice().reverse().map(item=>`<div><strong>${safe(item.automation_id)}</strong><span>${safe(item.reason)}</span><small>${new Date(item.queued_ts*1000).toLocaleString()}</small></div>`).join('')}</div></details>`:''}</section>`;
  }

  function enhance(){
    if(window.currentPage?.()!=='automation')return;
    const host=document.getElementById('automationCorePage');if(!host)return;
    let panel=host.querySelector('.automation-runtime-panel');if(!panel){host.insertAdjacentHTML('afterbegin',runtimePanel());}
    const form=document.getElementById('automationEditorForm');
    if(form&&!form.querySelector('.automation-trigger-builder')){
      const item=state.items.find(entry=>entry.id===state.editingId)||{trigger:{type:'manual'}};
      form.querySelector('.automation-condition-builder')?.insertAdjacentHTML('beforebegin',triggerEditor(item.trigger));
      document.getElementById('automationTriggerType')?.addEventListener('change',event=>{form.querySelector('.automation-trigger-builder').outerHTML=triggerEditor({type:event.target.value});enhance();});
    }
    document.querySelectorAll('[data-auto-edit]').forEach(button=>button.addEventListener('click',()=>{state.editingId=button.dataset.autoEdit;setTimeout(enhance,0);},{once:true}));
    document.getElementById('createAutomation')?.addEventListener('click',()=>{state.editingId=null;setTimeout(enhance,0);},{once:true});
    document.querySelectorAll('.automation-rule-actions').forEach(actions=>{
      const card=actions.closest('.automation-rule-card');const edit=actions.querySelector('[data-auto-edit]');if(!card||!edit||actions.querySelector('[data-auto-manual]'))return;
      const item=state.items.find(entry=>entry.id===edit.dataset.autoEdit);if(item?.trigger?.type!=='manual')return;
      const button=document.createElement('button');button.className='btn ghost';button.dataset.autoManual=item.id;button.textContent='Trigger';actions.insertBefore(button,actions.querySelector('[data-auto-delete]'));
      button.onclick=async()=>{const response=await originalFetch(`/api/automations/${encodeURIComponent(item.id)}/trigger`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':await csrf()},body:'{}'});const payload=await response.json();alert(payload.pending_actions?'Trigger matched. Actions are pending; execution is disabled.':`Trigger result: ${payload.reason||payload.detail||'not matched'}`);await loadRuntime();};
    });
  }

  const observer=new MutationObserver(()=>enhance());
  observer.observe(document.body,{childList:true,subtree:true});
  const originalRenderPage=window.renderPage;
  window.renderPage=function triggerRuntimeRender(page=window.currentPage()){originalRenderPage(page);if(page==='automation'){setTimeout(()=>{loadRuntime();enhance();},0);}};
  loadRuntime();
})();
