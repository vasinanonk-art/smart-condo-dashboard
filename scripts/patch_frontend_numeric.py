from pathlib import Path

p = Path('/opt/smart-condo-dashboard-run/frontend/index.html')
s = p.read_text(encoding='utf-8')

s = s.replace(
    '<span id="brightnessVal" class="val">700</span>',
    '<input id="brightnessVal" class="val" type="number" min="10" max="1000" value="700" oninput="document.getElementById(\'brightness\').value=this.value;updateSelected(\'Brightness \'+this.value+\' (not applied)\')" onkeydown="if(event.key===\'Enter\')applyBrightness()">'
)

s = s.replace(
    '<span id="tempVal" class="val">500</span>',
    '<input id="tempVal" class="val" type="number" min="0" max="1000" value="500" oninput="document.getElementById(\'temp\').value=this.value;updateSelected(\'Temperature \'+this.value+\' (not applied)\')" onkeydown="if(event.key===\'Enter\')applyTemperature()">'
)

s = s.replace(
    "function showValue(id,v){document.getElementById(id).textContent=v}",
    "function showValue(id,v){document.getElementById(id).value=v}"
)

s = s.replace(
    "function setSlider(id,valId,value){if(value===undefined||value===null)return;document.getElementById(id).value=value;showValue(valId,value)}",
    "function setSlider(id,valId,value){if(value===undefined||value===null)return;document.getElementById(id).value=value;document.getElementById(valId).value=value}"
)

s = s.replace(
    "function applyBrightness(){const v=+document.getElementById('brightness').value;light('brightness',{value:v},'Brightness '+v)}",
    "function applyBrightness(){const v=Math.max(10,Math.min(1000,+document.getElementById('brightnessVal').value));setSlider('brightness','brightnessVal',v);light('brightness',{value:v},'Brightness '+v)}"
)

s = s.replace(
    "function applyTemperature(){const v=+document.getElementById('temp').value;light('temperature',{value:v},'Temperature '+v)}",
    "function applyTemperature(){const v=Math.max(0,Math.min(1000,+document.getElementById('tempVal').value));setSlider('temp','tempVal',v);light('temperature',{value:v},'Temperature '+v)}"
)

p.write_text(s, encoding='utf-8')
