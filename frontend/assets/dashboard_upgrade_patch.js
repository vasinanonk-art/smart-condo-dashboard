(() => {
  const baseOverview = renderOverview;
  renderOverview = function(){
    baseOverview();
    const host = document.getElementById('overviewMetrics');
    if(host){
      const people=['beer','seem'].map(k=>{const p=S.presence?.[k]||{};return `${k}: ${p.status||p.state||'Unknown'}`}).join(' · ');
      const systemOk=Boolean(S.health?.mqtt_connected)&&Boolean(S.sonoffAvailable)&&Boolean(S.air?.configured);
      if(!document.getElementById('overviewPresenceMetric')){
        host.insertAdjacentHTML('beforeend',`<div id="overviewPresenceMetric" class="card metric"><div class="label">Presence</div><div class="value" style="font-size:20px">${people}</div><div class="sub">Beer and Seem</div></div><div id="overviewSystemMetric" class="card metric"><div class="label">System Health</div><div class="value ${systemOk?'ok':'warn'}" style="font-size:24px">${systemOk?'Healthy':'Attention'}</div><div class="sub">MQTT · Sonoff · Home Assistant</div></div>`);
      }else{
        document.querySelector('#overviewPresenceMetric .value').textContent=people;
        const value=document.querySelector('#overviewSystemMetric .value');value.textContent=systemOk?'Healthy':'Attention';value.className=`value ${systemOk?'ok':'warn'}`;
      }
    }
    drawChart('overviewPmChart',S.history,SERIES.air);
  };

  renderSystem = function(){
    const d=S.system||{},h=d.history||{},cloud=sonoffCloudStatus(),safeError=S.sonoff?.last_error||S.sonoffError;
    const cameraCount=Number(d.camera?.count||0),cameraState=d.camera?.config_loaded?'Configured':'Not configured';
    document.getElementById('systemDetails').innerHTML=`
      <div class="kv"><span>Service</span><strong class="ok">${safeText(d.service||'online')}</strong></div>
      <div class="kv"><span>Application version</span><strong>${safeText(d.version||'-')}</strong></div>
      <div class="kv"><span>MQTT</span><strong class="${d.mqtt?.connected?'ok':'bad'}">${d.mqtt?.connected?'Connected':'Disconnected'}</strong></div>
      <div class="kv"><span>Sonoff Cloud</span><strong class="${cloud.cls}">${cloud.label}</strong></div>
      <div class="kv"><span>Sonoff devices</span><strong>${(S.sonoff?.devices||[]).length}</strong></div>
      <div class="kv"><span>Last successful Sonoff sync</span><strong>${safeText(when(S.sonoffLastSyncTs))}</strong></div>
      ${safeError?`<div class="kv"><span>Sonoff error</span><strong class="bad">${safeText(safeError)}</strong></div>`:''}
      <div class="kv"><span>Home Assistant</span><strong class="${d.home_assistant?.configured?'ok':'warn'}">${d.home_assistant?.configured?'Connected':'Not configured'}</strong></div>
      <div class="kv"><span>Camera</span><strong>${cameraState} · ${cameraCount}</strong></div>
      <div class="kv"><span>History</span><strong>${h.loaded_count||0} loaded · ${h.appended_count||0} appended · ${h.pruned_count||0} pruned</strong></div>`;
  };
})();
