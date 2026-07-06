from pathlib import Path

p = Path('/opt/smart-condo-dashboard-run/frontend/index.html')
s = p.read_text(encoding='utf-8')

# Add Cancel button after Temperature apply button if not present.
s = s.replace(
    '<button class="apply light-actions" onclick="applyTemperature()">Apply</button></div>',
    '<button class="apply light-actions" onclick="applyTemperature()">Apply</button><button class="apply light-actions" onclick="cancelDraft()">Cancel</button></div>'
)

# Draft state.
s = s.replace(
    'let lastLightDevices=[];',
    'let lastLightDevices=[];\nlet lightDraft=false;\nlet lightApplying=false;'
)

# Lock draft when user touches sliders / number boxes.
s = s.replace(
    "function syncNumberFromSlider(sliderId,numId,label){const v=document.getElementById(sliderId).value;document.getElementById(numId).value=v;updateSelected(label+' '+v+' (not applied)')}",
    "function syncNumberFromSlider(sliderId,numId,label){lightDraft=true;const v=document.getElementById(sliderId).value;document.getElementById(numId).value=v;updateSelected('Editing · '+label+' '+v+' (not applied)')}"
)
s = s.replace(
    "function syncSliderFromNumber(sliderId,numId,label){const s=document.getElementById(sliderId);const n=document.getElementById(numId);const v=clampInput(n.value,+s.min,+s.max);n.value=v;s.value=v;updateSelected(label+' '+v+' (not applied)')}",
    "function syncSliderFromNumber(sliderId,numId,label){lightDraft=true;const s=document.getElementById(sliderId);const n=document.getElementById(numId);const v=clampInput(n.value,+s.min,+s.max);n.value=v;s.value=v;updateSelected('Editing · '+label+' '+v+' (not applied)')}"
)

# Do not overwrite draft values during polling.
s = s.replace(
    "function syncBoxFromDevice(dev){const d=dpsOf(dev);setSlider('brightness','brightnessVal',d['22']);setSlider('temp','tempVal',d['23']);updateSelected(`B:${d['22']??'-'} · T:${d['23']??'-'} · Mode:${d['21']??'-'}`)}",
    "function syncBoxFromDevice(dev){const d=dpsOf(dev);if(!lightDraft&&!lightApplying){setSlider('brightness','brightnessVal',d['22']);setSlider('temp','tempVal',d['23'])}updateSelected(`${lightDraft?'Editing':'Synced'} · B:${d['22']??'-'} · T:${d['23']??'-'} · Mode:${d['21']??'-'}`)}"
)

# Do not autosync selected sliders while editing.
s = s.replace(
    "async function loadLightStatus(skipSync){try{await refreshStatusCacheOnly();renderLightStatus();if(!skipSync)syncSelectedFromCachedStatus()}catch(e){document.getElementById('lightStatus').innerHTML='<div class=\"dev\">Status failed</div>'}}",
    "async function loadLightStatus(skipSync){try{await refreshStatusCacheOnly();renderLightStatus();if(!skipSync&&!lightDraft&&!lightApplying)syncSelectedFromCachedStatus()}catch(e){document.getElementById('lightStatus').innerHTML='<div class=\"dev\">Status failed</div>'}}"
)

# Apply clears draft after successful send.
s = s.replace(
    "async function light(action,data,label){if(lightBusy)return;updateSelected(label||action);setLightBusy(true);const target=targetValue();try{const r=await post('/api/light',Object.assign({target,action},data));show(r.ok?'Light OK':'Light failed');setTimeout(syncSelectedFromStatus,250)}catch(e){show('ERR: '+e.message)}finally{setTimeout(()=>setLightBusy(false),120)}}",
    "async function light(action,data,label){if(lightBusy)return;lightApplying=true;updateSelected('Applying · '+(label||action));setLightBusy(true);const target=targetValue();try{const r=await post('/api/light',Object.assign({target,action},data));show(r.ok?'Light OK':'Light failed');lightDraft=false;setTimeout(()=>{lightApplying=false;syncSelectedFromStatus()},250)}catch(e){show('ERR: '+e.message);lightApplying=false}finally{setTimeout(()=>setLightBusy(false),120)}}"
)

# Cancel draft function.
marker = "async function syncSelectedFromStatus(){"
if "function cancelDraft(){" not in s and marker in s:
    s = s.replace(marker, "function cancelDraft(){lightDraft=false;lightApplying=false;syncSelectedFromStatus()}\n" + marker)

p.write_text(s, encoding='utf-8')
