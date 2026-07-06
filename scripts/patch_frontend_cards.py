from pathlib import Path

p = Path('/opt/smart-condo-dashboard-run/frontend/index.html')
s = p.read_text(encoding='utf-8')

s = s.replace(
    '.dev{background:#020617;border:1px solid #334155;border-radius:14px;padding:10px}',
    '.dev{background:#020617;border:1px solid #334155;border-radius:14px;padding:10px;cursor:pointer}.dev:hover{filter:brightness(1.15)}.dev.selected{border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.35)}'
)

old = "function renderLightStatus(){const box=document.getElementById('lightStatus');box.innerHTML='';lastLightDevices.forEach(dev=>{const d=dpsOf(dev);const st=statusText(dev);const el=document.createElement('div');el.className='dev';el.innerHTML=`<div class=\"devtop\"><span><span class=\"dot ${st[2]}\"></span>${dev.name}</span><span class=\"${st[1]}\">${st[0]}</span></div><div class=\"devmeta\">${dev.ip||'-'} · ${dev.source||'-'} · B:${d['22']??'-'} · T:${d['23']??'-'} · Mode:${d['21']??'-'}</div>`;box.appendChild(el)})}"
new = "function renderLightStatus(){const box=document.getElementById('lightStatus');box.innerHTML='';const current=targetValue();lastLightDevices.forEach(dev=>{const d=dpsOf(dev);const st=statusText(dev);const el=document.createElement('div');el.className='dev'+(dev.target===current?' selected':'');el.onclick=()=>selectDeviceCard(dev.target);el.innerHTML=`<div class=\"devtop\"><span><span class=\"dot ${st[2]}\"></span>${dev.name}</span><span class=\"${st[1]}\">${st[0]}</span></div><div class=\"devmeta\">${dev.ip||'-'} · ${dev.source||'-'} · B:${d['22']??'-'} · T:${d['23']??'-'} · Mode:${d['21']??'-'}</div>`;box.appendChild(el)})}"
if old in s:
    s = s.replace(old, new)

marker = "async function refreshStatusCacheOnly(){"
if "function selectDeviceCard(target){" not in s and marker in s:
    s = s.replace(marker, "function selectDeviceCard(target){document.getElementById('lightTarget').value=target;syncSelectedFromStatus();renderLightStatus()}\n" + marker)

s = s.replace('setInterval(()=>loadLightStatus(false),20000);', 'setInterval(()=>loadLightStatus(false),3000);')
s = s.replace('เปลี่ยน Target จะ sync เฉพาะหลอดที่เลือก ไม่โหลดสถานะทุกหลอด', 'กดการ์ดอุปกรณ์เพื่อเลือกหลอดได้ทันที · Refresh ทุก 3 วินาที')
s = s.replace('เลื่อนหรือพิมพ์เลข Brightness/Temperature ยังไม่ส่งคำสั่ง ต้องกด Apply ก่อนเท่านั้น', 'กดการ์ดอุปกรณ์เพื่อเลือกหลอด · เลื่อนหรือพิมพ์เลขแล้วยังกด Apply ก่อนเท่านั้น')

p.write_text(s, encoding='utf-8')
