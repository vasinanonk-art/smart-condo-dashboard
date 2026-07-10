(() => {
  'use strict';

  const RANGES = { '24h': '24H', '3d': '3D', '7d': '7D' };
  const SERIES = [
    { key: 'temperature', label: 'Temperature', unit: '°C', cls: 'sg-temp' },
    { key: 'humidity', label: 'Humidity', unit: '%', cls: 'sg-hum' },
    { key: 'pm25', label: 'PM2.5', unit: 'µg/m³', cls: 'sg-pm' },
  ];
  let selectedRange = '24h';
  let historyRows = [];
  let sensorCurrent = {};

  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function pm25(row) {
    return num(row && (row.pm25 ?? row.pm2_5 ?? row['pm2.5'] ?? row.PM25 ?? row.pm_25));
  }

  function normalize(row) {
    return {
      ts: Number(row && row.ts) || 0,
      temperature: num(row && (row.temperature ?? row.temp)),
      humidity: num(row && (row.humidity ?? row.hum)),
      pm25: pm25(row),
    };
  }

  function fmt(v, digits = 1) {
    return v === null || v === undefined || !Number.isFinite(v) ? 'Not available' : Number(v).toFixed(digits).replace(/\.0$/, '');
  }

  function stats(key) {
    const values = historyRows.map(r => r[key]).filter(v => v !== null && Number.isFinite(v));
    const current = num(sensorCurrent[key] ?? (key === 'pm25' ? pm25(sensorCurrent) : null));
    if (!values.length) return { current, min: null, max: null, avg: null };
    return {
      current: current ?? values[values.length - 1],
      min: Math.min(...values),
      max: Math.max(...values),
      avg: values.reduce((a, b) => a + b, 0) / values.length,
    };
  }

  function ensureUI() {
    document.querySelectorAll('.statusbox').forEach(el => {
      const card = el.closest('.card');
      if (card) card.remove(); else el.remove();
    });

    const metrics = document.getElementById('sensorMetrics');
    const section = metrics && metrics.closest('section');
    if (!section || section.dataset.sensorUx === '1') return;
    section.dataset.sensorUx = '1';
    section.innerHTML = `
      <div class="sg-head">
        <div><h2>Environment</h2><div class="sg-sub">Temperature · Humidity · PM2.5</div></div>
        <div class="sg-ranges">${Object.entries(RANGES).map(([k,v]) => `<button data-range="${k}" class="${k === selectedRange ? 'active' : ''}">${v}</button>`).join('')}</div>
      </div>
      <div id="sgStats" class="sg-stats"></div>
      <div class="sg-chart-card">
        <div class="sg-legend">${SERIES.map(s => `<span><i class="${s.cls}"></i>${s.label}</span>`).join('')}</div>
        <div id="sgEmpty" class="sg-empty" hidden>No sensor history is available for this range.</div>
        <svg id="sgChart" viewBox="0 0 1000 360" preserveAspectRatio="none" role="img" aria-label="Sensor history chart"></svg>
        <div id="sgTooltip" class="sg-tooltip" hidden></div>
      </div>
      <div id="sgMeta" class="sg-meta">Loading sensor history…</div>`;

    section.querySelectorAll('[data-range]').forEach(btn => btn.addEventListener('click', () => setRange(btn.dataset.range)));
    window.addEventListener('resize', draw);
  }

  function injectStyle() {
    if (document.getElementById('sensorDashboardStyle')) return;
    const style = document.createElement('style');
    style.id = 'sensorDashboardStyle';
    style.textContent = `
      .sg-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:16px}.sg-head h2{margin:0!important;font-size:22px!important}.sg-sub,.sg-meta{color:var(--muted);font-size:13px}.sg-ranges{display:flex;gap:6px;background:#0d1116;border:1px solid var(--line);padding:4px;border-radius:14px}.sg-ranges button{min-width:58px;padding:8px 12px;border-radius:10px;background:transparent;border:0}.sg-ranges button.active{background:var(--blue2);color:white}.sg-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:14px}.sg-stat{background:#0d1116;border:1px solid var(--line);border-radius:16px;padding:14px}.sg-stat h3{font-size:14px;margin:0 0 12px}.sg-stat-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.sg-stat-cell small{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}.sg-stat-cell strong{display:block;margin-top:4px;font-size:17px;font-variant-numeric:tabular-nums}.sg-chart-card{position:relative;background:#0d1116;border:1px solid var(--line);border-radius:18px;padding:12px;overflow:hidden}.sg-legend{display:flex;gap:18px;flex-wrap:wrap;padding:2px 6px 10px;color:var(--muted);font-size:13px}.sg-legend span{display:flex;align-items:center;gap:7px}.sg-legend i{width:10px;height:10px;border-radius:50%}.sg-temp{background:#ffb300}.sg-hum{background:#03a9f4}.sg-pm{background:#ab47bc}.sg-chart-card svg{width:100%;height:340px;display:block;touch-action:none}.sg-grid{stroke:#26313b;stroke-width:1}.sg-axis{fill:#8d99a6;font-size:11px}.sg-line{fill:none;stroke-width:2.5;vector-effect:non-scaling-stroke}.sg-line.sg-temp{stroke:#ffb300}.sg-line.sg-hum{stroke:#03a9f4}.sg-line.sg-pm{stroke:#ab47bc}.sg-cross{stroke:#cbd5e1;stroke-width:1;stroke-dasharray:4 4;vector-effect:non-scaling-stroke}.sg-point{stroke:#0d1116;stroke-width:3;vector-effect:non-scaling-stroke}.sg-point.sg-temp{fill:#ffb300}.sg-point.sg-hum{fill:#03a9f4}.sg-point.sg-pm{fill:#ab47bc}.sg-tooltip{position:absolute;z-index:3;pointer-events:none;min-width:190px;background:rgba(10,14,18,.96);border:1px solid #415064;border-radius:12px;padding:10px 12px;box-shadow:0 12px 28px rgba(0,0,0,.35);font-size:12px;line-height:1.55}.sg-tooltip strong{display:block;margin-bottom:4px}.sg-empty{position:absolute;inset:70px 12px 12px;display:grid;place-items:center;color:var(--muted);text-align:center}.sg-meta{margin-top:10px}.card{scroll-margin-top:12px}
      @media(max-width:900px){.sg-stats{grid-template-columns:1fr}.sg-stat-grid{grid-template-columns:repeat(4,1fr)}}
      @media(max-width:620px){.sg-head{flex-direction:column}.sg-ranges{width:100%}.sg-ranges button{flex:1}.sg-stat-grid{grid-template-columns:repeat(2,1fr);row-gap:12px}.sg-chart-card svg{height:290px}.sg-tooltip{min-width:165px}}
    `;
    document.head.appendChild(style);
  }

  function renderStats() {
    const box = document.getElementById('sgStats');
    if (!box) return;
    box.innerHTML = SERIES.map(s => {
      const st = stats(s.key);
      return `<article class="sg-stat"><h3>${s.label}</h3><div class="sg-stat-grid">
        ${[['Current',st.current],['Min',st.min],['Max',st.max],['AVG',st.avg]].map(([label,value]) => `<div class="sg-stat-cell"><small>${label}</small><strong>${fmt(value)}${value === null || value === undefined ? '' : ' '+s.unit}</strong></div>`).join('')}
      </div></article>`;
    }).join('');
  }

  function pathFor(rows, key, x, y) {
    let path = '';
    let pen = false;
    rows.forEach((r, i) => {
      const v = r[key];
      if (v === null) { pen = false; return; }
      path += `${pen ? 'L' : 'M'}${x(i).toFixed(2)},${y(v).toFixed(2)} `;
      pen = true;
    });
    return path.trim();
  }

  function draw() {
    const svg = document.getElementById('sgChart');
    const empty = document.getElementById('sgEmpty');
    if (!svg) return;
    const rows = historyRows.filter(r => r.ts > 0);
    svg.innerHTML = '';
    if (!rows.length) { empty.hidden = false; return; }
    empty.hidden = true;

    const W=1000,H=360,L=62,R=24,T=20,B=44,PW=W-L-R,PH=H-T-B;
    const all = rows.flatMap(r => SERIES.map(s => r[s.key])).filter(v => v !== null && Number.isFinite(v));
    if (!all.length) { empty.hidden = false; return; }
    let min=Math.min(...all), max=Math.max(...all); if (min===max){min-=1;max+=1} const pad=(max-min)*.1; min-=pad;max+=pad;
    const x=i=>L+(rows.length===1?PW/2:(i/(rows.length-1))*PW); const y=v=>T+PH-((v-min)/(max-min))*PH;
    const ns='http://www.w3.org/2000/svg';
    for(let i=0;i<=5;i++){const yy=T+(PH*i/5);const line=document.createElementNS(ns,'line');line.setAttribute('x1',L);line.setAttribute('x2',W-R);line.setAttribute('y1',yy);line.setAttribute('y2',yy);line.setAttribute('class','sg-grid');svg.appendChild(line);const text=document.createElementNS(ns,'text');text.setAttribute('x',L-8);text.setAttribute('y',yy+4);text.setAttribute('text-anchor','end');text.setAttribute('class','sg-axis');text.textContent=fmt(max-(max-min)*i/5);svg.appendChild(text)}
    const ticks=Math.min(6,rows.length);for(let i=0;i<ticks;i++){const idx=Math.round(i*(rows.length-1)/Math.max(1,ticks-1));const text=document.createElementNS(ns,'text');text.setAttribute('x',x(idx));text.setAttribute('y',H-14);text.setAttribute('text-anchor',i===0?'start':i===ticks-1?'end':'middle');text.setAttribute('class','sg-axis');text.textContent=new Date(rows[idx].ts*1000).toLocaleString([],selectedRange==='24h'?{hour:'2-digit',minute:'2-digit'}:{month:'short',day:'numeric',hour:'2-digit'});svg.appendChild(text)}
    SERIES.forEach(s=>{const d=pathFor(rows,s.key,x,y);if(!d)return;const p=document.createElementNS(ns,'path');p.setAttribute('d',d);p.setAttribute('class','sg-line '+s.cls);svg.appendChild(p)});
    const overlay=document.createElementNS(ns,'rect');overlay.setAttribute('x',L);overlay.setAttribute('y',T);overlay.setAttribute('width',PW);overlay.setAttribute('height',PH);overlay.setAttribute('fill','transparent');overlay.style.cursor='crosshair';svg.appendChild(overlay);
    const cross=document.createElementNS(ns,'line');cross.setAttribute('y1',T);cross.setAttribute('y2',T+PH);cross.setAttribute('class','sg-cross');cross.style.display='none';svg.appendChild(cross);
    const points=SERIES.map(s=>{const c=document.createElementNS(ns,'circle');c.setAttribute('r',6);c.setAttribute('class','sg-point '+s.cls);c.style.display='none';svg.appendChild(c);return c});
    const tip=document.getElementById('sgTooltip');
    const move=e=>{const rect=svg.getBoundingClientRect();const px=(e.clientX-rect.left)/rect.width*W;const idx=Math.max(0,Math.min(rows.length-1,Math.round((px-L)/PW*(rows.length-1))));const row=rows[idx],xx=x(idx);cross.setAttribute('x1',xx);cross.setAttribute('x2',xx);cross.style.display='block';SERIES.forEach((s,j)=>{const v=row[s.key];if(v===null){points[j].style.display='none';return}points[j].setAttribute('cx',xx);points[j].setAttribute('cy',y(v));points[j].style.display='block'});tip.hidden=false;tip.innerHTML=`<strong>${new Date(row.ts*1000).toLocaleString()}</strong>${SERIES.map(s=>`${s.label}: ${fmt(row[s.key])}${row[s.key]===null?'':' '+s.unit}`).join('<br>')}`;let left=(xx/W)*rect.width+12;if(left+210>rect.width)left-=220;tip.style.left=Math.max(8,left)+'px';tip.style.top='54px'};
    overlay.addEventListener('pointermove',move);overlay.addEventListener('pointerdown',move);overlay.addEventListener('pointerleave',()=>{cross.style.display='none';points.forEach(p=>p.style.display='none');tip.hidden=true});
  }

  async function loadHistory() {
    const meta=document.getElementById('sgMeta');
    try {
      const j=await getJson('/api/condo/history?range='+selectedRange);
      historyRows=(j.history||j.points||[]).map(normalize).filter(r=>r.ts);
      if (j.current) sensorCurrent=normalize(j.current);
      renderStats();draw();
      meta.textContent=`${RANGES[selectedRange]} · ${historyRows.length} plotted points${j.raw_count!==undefined?' · '+j.raw_count+' raw':''}`;
    } catch(e) { meta.textContent='History unavailable: '+e.message; historyRows=[];renderStats();draw(); }
  }

  async function loadStatus() {
    try {
      const j=await getJson('/api/condo/status');
      sensorCurrent=normalize(j.sensor||{});
      if (typeof renderPresence==='function') renderPresence(j.presence||{});
      renderStats();
    } catch(_) {}
  }

  function setRange(range) {
    if (!RANGES[range] || range===selectedRange) return;
    selectedRange=range;
    document.querySelectorAll('[data-range]').forEach(b=>b.classList.toggle('active',b.dataset.range===range));
    loadHistory();
  }

  function init() {
    injectStyle();ensureUI();loadStatus();loadHistory();
    window.loadCondoHistory=loadHistory;
    const originalStatus=window.loadCondoStatus;
    window.loadCondoStatus=async()=>{if(typeof originalStatus==='function')await originalStatus();await loadStatus()};
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();