from pathlib import Path

p = Path('/opt/smart-condo-dashboard-run/frontend/index.html')
s = p.read_text(encoding='utf-8')

card = '''
      <section class="card" style="grid-column:1/-1">
        <h2>Condo Status</h2>
        <div class="devgrid">
          <div class="dev"><div class="devtop"><span>Temperature</span><span id="cTemp" class="ok">-- C</span></div><div class="devmeta" id="cSensorMeta">Waiting MQTT</div></div>
          <div class="dev"><div class="devtop"><span>Humidity</span><span id="cHum" class="ok">-- %</span></div><div class="devmeta">T3 Sensor</div></div>
          <div class="dev"><div class="devtop"><span>Beer</span><span id="cBeer" class="bad">--</span></div><div class="devmeta" id="cBeerMeta">Waiting MQTT</div></div>
          <div class="dev"><div class="devtop"><span>Seem</span><span id="cSeem" class="bad">--</span></div><div class="devmeta" id="cSeemMeta">Waiting MQTT</div></div>
        </div>
      </section>
'''

if 'id="cTemp"' not in s:
    s = s.replace('      <section class="card"><h2>Power / Apps</h2>', card + '      <section class="card"><h2>Power / Apps</h2>')

js = '''
async function loadCondoStatus(){try{const r=await fetch('/api/condo/status');const j=await r.json();const sensor=j.sensor||{};document.getElementById('cTemp').textContent=(sensor.temperature??'--')+' C';document.getElementById('cHum').textContent=(sensor.humidity??'--')+' %';document.getElementById('cSensorMeta').textContent=sensor.ip?('IP '+sensor.ip):'Waiting MQTT';const pp=j.presence||{};setCondoPresence('cBeer','cBeerMeta',pp.beer);setCondoPresence('cSeem','cSeemMeta',pp.seem)}catch(e){}}
function setCondoPresence(id,mid,data){const el=document.getElementById(id);const meta=document.getElementById(mid);data=data||{};const home=(data.state||'').toLowerCase()==='home';el.textContent=data.state||'--';el.className=home?'ok':'bad';meta.textContent=data.ip?('IP '+data.ip+' · score '+(data.score??'-')):'Waiting MQTT'}
'''
if 'async function loadCondoStatus()' not in s:
    s = s.replace('async function loadState(){', js + 'async function loadState(){')

s = s.replace('loadFavorites(); loadScenes(); loadState(); loadLightStatus(false);', 'loadFavorites(); loadScenes(); loadState(); loadCondoStatus(); loadLightStatus(false);')
s = s.replace('setInterval(loadState,3000);', 'setInterval(loadState,3000); setInterval(loadCondoStatus,3000);')

p.write_text(s, encoding='utf-8')
