from pathlib import Path

app = Path('/opt/smart-condo-dashboard-run/backend/app.py')
s = app.read_text(encoding='utf-8')

s = s.replace('    "sensor": {},\n    "presence": {},\n', '    "sensor": {},\n    "sensor_history": [],\n    "presence": {},\n')

old = '        state["sensor"] = parsed | {"ts": int(time.time())}\n'
new = '        now = int(time.time())\n        state["sensor"] = parsed | {"ts": now}\n        hist = state.setdefault("sensor_history", [])\n        hist.append({"ts": now, "temperature": parsed.get("temperature"), "humidity": parsed.get("humidity")})\n        cutoff = now - 86400\n        state["sensor_history"] = [x for x in hist if int(x.get("ts", 0)) >= cutoff][-2000:]\n'
if old in s:
    s = s.replace(old, new)

if '@app.get("/api/condo/history")' not in s:
    marker = '@app.get("/api/condo/status")\ndef condo_status():\n    return {"ok": True, "sensor": state.get("sensor", {}), "presence": state.get("presence", {})}\n\n\n'
    add = '@app.get("/api/condo/history")\ndef condo_history():\n    return {"ok": True, "history": state.get("sensor_history", [])}\n\n\n'
    s = s.replace(marker, marker + add)

app.write_text(s, encoding='utf-8')

p = Path('/opt/smart-condo-dashboard-run/frontend/index.html')
h = p.read_text(encoding='utf-8')

h = h.replace('Smart Condo Dashboard</h1><p>LG TV + Lamptan Light Control</p>', 'Smart Condo Dashboard</h1><p>HA-style Condo Control Center</p>')

h = h.replace(
    ':root{--bg:#0f172a;--card:#111827;--card2:#1f2937;--text:#e5e7eb;--muted:#9ca3af;--ok:#22c55e;--bad:#ef4444;--btn:#334155;--btn2:#2563eb;--warn:#f59e0b}',
    ':root{--bg:#111;--card:#1c1c1c;--card2:#242424;--text:#e6e6e6;--muted:#a1a1a1;--ok:#43a047;--bad:#ef5350;--btn:#2b2b2b;--btn2:#03a9f4;--warn:#ffb300}'
)
h = h.replace('background:linear-gradient(135deg,#020617,#0f172a);', 'background:#111;')
h = h.replace('border:1px solid rgba(255,255,255,.08);border-radius:18px;', 'border:1px solid #333;border-radius:18px;')

css = '.metric{font-size:44px;font-weight:500;text-align:center;margin:16px 0}.metric small{font-size:20px;color:var(--muted)}.chart{width:100%;height:210px;background:#1c1c1c;border:1px solid #333;border-radius:14px}.chart path{fill:none;stroke:#4f7df3;stroke-width:2}.chart .humid{stroke:#4f7df3}.chart .temp{stroke:#ffb300}.chart text{fill:#aaa;font-size:11px}.chartline{stroke:#333;stroke-width:1}.ha-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:760px){.ha-grid{grid-template-columns:1fr}.metric{font-size:36px}}'
if '.metric{' not in h:
    h = h.replace('</style>', css + '</style>')

ha_card = '''
      <section class="card" style="grid-column:1/-1">
        <h2>Environment Monitoring</h2>
        <div class="ha-grid">
          <div class="dev"><div class="devtop"><span>Temperature</span><span id="haTempState" class="ok">-- C</span></div><div class="metric" id="haTempBig">-- <small>C</small></div><svg id="tempChart" class="chart" viewBox="0 0 600 210"></svg></div>
          <div class="dev"><div class="devtop"><span>Humidity</span><span id="haHumState" class="warntext">-- %</span></div><div class="metric" id="haHumBig">-- <small>%</small></div><svg id="humChart" class="chart" viewBox="0 0 600 210"></svg></div>
        </div>
      </section>
'''
if 'id="haTempBig"' not in h:
    h = h.replace('      <section class="card" style="grid-column:1/-1">\n        <h2>Condo Status</h2>', ha_card + '      <section class="card" style="grid-column:1/-1">\n        <h2>Condo Status</h2>')

# Final light draft UX override script placed before closing script.
final_js = r'''
function markDraft(label,v){lightDraft=true;updateSelected('Editing · '+label+' '+v+' (not applied)')}
function syncNumberFromSlider(sliderId,numId,label){const v=document.getElementById(sliderId).value;document.getElementById(numId).value=v;markDraft(label,v)}
function syncSliderFromNumber(sliderId,numId,label){const s=document.getElementById(sliderId);const n=document.getElementById(numId);const v=clampInput(n.value,+s.min,+s.max);n.value=v;s.value=v;markDraft(label,v)}
function syncBoxFromDevice(dev){const d=dpsOf(dev);if(!lightDraft&&!lightApplying){setSlider('brightness','brightnessVal',d['22']);setSlider('temp','tempVal',d['23'])}updateSelected(`${lightDraft?'Editing':'Synced'} · B:${d['22']??'-'} · T:${d['23']??'-'} · Mode:${d['21']??'-'}`)}
function cancelDraft(){lightDraft=false;lightApplying=false;syncSelectedFromStatus()}
async function light(action,data,label){if(lightBusy)return;lightApplying=true;updateSelected('Applying · '+(label||action));setLightBusy(true);const target=targetValue();try{const r=await post('/api/light',Object.assign({target,action},data));show(r.ok?'Light OK':'Light failed');lightDraft=false;setTimeout(()=>{lightApplying=false;syncSelectedFromStatus();loadLightStatus(false)},350)}catch(e){show('ERR: '+e.message);lightApplying=false}finally{setTimeout(()=>setLightBusy(false),150)}}
async function loadLightStatus(skipSync){try{await refreshStatusCacheOnly();renderLightStatus();if(!skipSync&&!lightDraft&&!lightApplying)syncSelectedFromCachedStatus()}catch(e){document.getElementById('lightStatus').innerHTML='<div class="dev">Status failed</div>'}}
async function loadCondoHistory(){try{const r=await fetch('/api/condo/history');const j=await r.json();const hist=j.history||[];drawChart('tempChart',hist,'temperature',20,35,'temp');drawChart('humChart',hist,'humidity',40,80,'humid')}catch(e){}}
function drawChart(id,data,key,min,max,cls){const svg=document.getElementById(id);if(!svg)return;svg.innerHTML='';for(let y of [40,90,140,190]){svg.innerHTML+=`<line class="chartline" x1="35" y1="${y}" x2="580" y2="${y}"/>`}if(!data.length)return;const pts=data.filter(x=>x[key]!=null).slice(-240);if(pts.length<2)return;const x0=35,w=545,h=170,y0=190;const path=pts.map((p,i)=>{const x=x0+(i/(pts.length-1))*w;const y=y0-((Number(p[key])-min)/(max-min))*h;return (i?'L':'M')+x.toFixed(1)+' '+Math.max(20,Math.min(190,y)).toFixed(1)}).join(' ');svg.innerHTML+=`<path class="${cls}" d="${path}"/>`;svg.innerHTML+=`<text x="38" y="18">24H Trend</text>`}
'''
if 'async function loadCondoHistory()' not in h:
    h = h.replace('async function loadState(){', final_js + '\nasync function loadState(){')

h = h.replace('loadState(); loadCondoStatus(); loadLightStatus(false);', 'loadState(); loadCondoStatus(); loadCondoHistory(); loadLightStatus(false);')
h = h.replace('setInterval(loadCondoStatus,3000);', 'setInterval(loadCondoStatus,3000); setInterval(loadCondoHistory,30000);')

# Make condo status update HA cards too.
h = h.replace(
    "document.getElementById('cSensorMeta').textContent=sensor.ip?('IP '+sensor.ip):'Waiting MQTT';",
    "document.getElementById('cSensorMeta').textContent=sensor.ip?('IP '+sensor.ip):'Waiting MQTT';if(document.getElementById('haTempBig')){document.getElementById('haTempBig').innerHTML=(sensor.temperature??'--')+' <small>C</small>';document.getElementById('haHumBig').innerHTML=(sensor.humidity??'--')+' <small>%</small>';document.getElementById('haTempState').textContent=(sensor.temperature??'--')+' C';document.getElementById('haHumState').textContent=(sensor.humidity??'--')+' %';}"
)

p.write_text(h, encoding='utf-8')
