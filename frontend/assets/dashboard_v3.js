const S = {
  range: '24h', history: [], sensor: {}, presence: {}, air: {},
  sonoff: {devices: []}, sonoffAvailable: false, sonoffLastSyncTs: null, sonoffError: null,
  lights: [], scenes: {}, health: {}, system: {}, cameras: [], tv: {lastValid: null}
};
const SERIES = {
  overview: [
    {key:'temperature', label:'Temperature', unit:'°C', cls:'line-temp', color:'var(--yellow)'},
    {key:'humidity', label:'Humidity', unit:'%', cls:'line-hum', color:'var(--cyan)'}
  ],
  air: [
    {key:'pm25_living_room', label:'Living Room', unit:' µg/m³', cls:'line-living', color:'var(--accent)'},
    {key:'pm25_bedroom', label:'Bedroom', unit:' µg/m³', cls:'line-bedroom', color:'var(--purple)'}
  ]
};
const chartState = {};
const sonoffCards = new Map();
let refreshTimer = null;

const $ = id => document.getElementById(id);
const num = value => value === null || value === undefined || value === '' ? null : Number(value);
const fmt = (value, digits = 1) => num(value) === null ? 'Not available' : Number(value).toFixed(digits);
const when = ts => ts ? new Date(Number(ts) * 1000).toLocaleString() : 'Not available';
const shortTime = ts => ts ? new Date(Number(ts) * 1000).toLocaleTimeString([], {hour:'numeric', minute:'2-digit'}) : '—';
const safeText = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function get(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} ${response.status}`);
  return response.json();
}
async function post(url, data) {
  const response = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `${url} ${response.status}`);
  return payload;
}
function toast(text) {
  const element = $('toast');
  if (!element) return;
  element.textContent = text;
  element.style.display = 'block';
  clearTimeout(window.__toast);
  window.__toast = setTimeout(() => element.style.display = 'none', 2200);
}

function currentPage() {
  return (location.hash || '#overview').slice(1);
}
function nav(page) {
  document.querySelectorAll('.page').forEach(section => section.classList.toggle('active', section.dataset.page === page));
  document.querySelectorAll('[data-nav]').forEach(button => button.classList.toggle('active', button.dataset.nav === page));
  const names = {overview:'Overview', lighting:'Lighting', climate:'Climate & Air Quality', entertainment:'Entertainment', presence:'Presence & Automation', system:'System'};
  if ($('pageTitle')) $('pageTitle').textContent = names[page] || 'Dashboard';
  if (location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
  window.requestAnimationFrame(() => renderPage(page));
}
function renderPage(page = currentPage()) {
  if (page === 'overview') renderOverview();
  if (page === 'lighting') renderLighting();
  if (page === 'climate') renderClimate();
  if (page === 'entertainment') renderEntertainment();
  if (page === 'presence') renderPresence();
  if (page === 'system') renderSystem();
}

function stat(rows, key) {
  const values = rows.map(row => num(row[key])).filter(Number.isFinite);
  return {
    current: values.length ? values[values.length - 1] : null,
    min: values.length ? Math.min(...values) : null,
    max: values.length ? Math.max(...values) : null,
    avg: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
  };
}
function metricHTML(label, value, unit = '', sub = '') {
  return `<div class="card metric"><div class="label">${safeText(label)}</div><div class="value">${safeText(value)}${value === 'Not available' ? '' : ` <small>${safeText(unit)}</small>`}</div><div class="sub">${safeText(sub)}</div></div>`;
}
function statsHTML(label, values, unit) {
  return `<div class="card"><div class="card-head"><h3>${safeText(label)}</h3></div><div class="metric-grid">${[['Current',values.current],['Min',values.min],['Max',values.max],['AVG',values.avg]].map(([name,value]) => `<div class="mini"><div class="k">${name}</div><div class="v">${fmt(value)}${value === null ? '' : ` ${safeText(unit)}`}</div></div>`).join('')}</div></div>`;
}
function rangeButtons() {
  return ['24h','3d','7d'].map(range => `<button class="btn ghost ${S.range === range ? 'active' : ''}" data-range="${range}">${range.toUpperCase()}</button>`).join('');
}
async function setRange(range) {
  S.range = range;
  await loadHistory();
  renderOverview();
  renderClimate();
}

function visibleRows(id, sourceRows) {
  const state = chartState[id] || {zoom:1, offset:0};
  const rows = sourceRows || [];
  if (state.zoom <= 1 || rows.length < 3) return rows;
  const count = Math.max(10, Math.round(rows.length / state.zoom));
  const maxStart = Math.max(0, rows.length - count);
  const start = Math.max(0, Math.min(maxStart, Math.round(state.offset * maxStart)));
  return rows.slice(start, start + count);
}
function drawChart(id, rows, series) {
  const svg = $(id);
  if (!svg) return;
  const displayRows = visibleRows(id, rows);
  svg.innerHTML = '';
  const valid = displayRows.filter(row => series.some(item => Number.isFinite(num(row[item.key]))));
  if (!valid.length) {
    svg.innerHTML = '<text x="50%" y="50%" text-anchor="middle" class="axis-label">No data available for this range</text>';
    return;
  }
  const width = 900, height = 310, pad = {l:48,r:18,t:18,b:35};
  const values = [];
  series.forEach(item => valid.forEach(row => { const value = num(row[item.key]); if (Number.isFinite(value)) values.push(value); }));
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const extra = (max - min) * 0.12;
  min -= extra; max += extra;
  const x = index => pad.l + index / Math.max(1, valid.length - 1) * (width - pad.l - pad.r);
  const y = value => pad.t + (max - value) / (max - min) * (height - pad.t - pad.b);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  for (let index = 0; index < 5; index += 1) {
    const yy = pad.t + index * (height - pad.t - pad.b) / 4;
    const value = max - index * (max - min) / 4;
    svg.insertAdjacentHTML('beforeend', `<line class="gridline" x1="${pad.l}" y1="${yy}" x2="${width-pad.r}" y2="${yy}"/><text class="axis-label" x="8" y="${yy+4}">${value.toFixed(1)}</text>`);
  }
  [0,.25,.5,.75,1].forEach(ratio => {
    const index = Math.round((valid.length - 1) * ratio), xx = x(index);
    const label = new Date(valid[index].ts * 1000).toLocaleDateString([], S.range === '24h' ? {hour:'2-digit', minute:'2-digit'} : {month:'short', day:'numeric'});
    svg.insertAdjacentHTML('beforeend', `<text class="axis-label" text-anchor="middle" x="${xx}" y="${height-10}">${label}</text>`);
  });
  series.forEach(item => {
    const points = valid.map((row,index) => ({index,value:num(row[item.key])})).filter(point => Number.isFinite(point.value));
    if (!points.length) return;
    const path = points.map((point,index) => `${index ? 'L' : 'M'}${x(point.index).toFixed(1)},${y(point.value).toFixed(1)}`).join(' ');
    svg.insertAdjacentHTML('beforeend', `<path d="${path}" class="${item.cls}"/>`);
  });
  svg.insertAdjacentHTML('beforeend', '<g class="hover-layer" style="display:none"><line class="crosshair" y1="18" y2="275"/><g class="points"></g></g><rect class="hit" x="48" y="18" width="834" height="257" fill="transparent"/>');
  const layer = svg.querySelector('.hover-layer'), line = layer.querySelector('.crosshair'), points = layer.querySelector('.points'), hit = svg.querySelector('.hit'), tooltip = svg.parentElement.querySelector('.tooltip');
  const move = event => {
    const rect = svg.getBoundingClientRect();
    const pointer = event.touches ? event.touches[0] : event;
    const px = pointer.clientX - rect.left, scaled = px / rect.width * width;
    const index = Math.max(0, Math.min(valid.length - 1, Math.round((scaled - pad.l) / (width - pad.l - pad.r) * (valid.length - 1))));
    const row = valid[index], xx = x(index);
    line.setAttribute('x1', xx); line.setAttribute('x2', xx); points.innerHTML = '';
    series.forEach(item => { const value = num(row[item.key]); if (Number.isFinite(value)) points.insertAdjacentHTML('beforeend', `<circle class="point" cx="${xx}" cy="${y(value)}" r="6" fill="${item.color}"/>`); });
    layer.style.display = 'block';
    if (tooltip) {
      tooltip.innerHTML = `<strong>${new Date(row.ts*1000).toLocaleString()}</strong><br>${series.map(item => `${item.label}: ${fmt(row[item.key])}${item.unit}`).join('<br>')}`;
      tooltip.style.display = 'block'; tooltip.style.left = `${Math.min(rect.width - 190, Math.max(8, px + 12))}px`; tooltip.style.top = '16px';
    }
  };
  hit.onmousemove = move;
  hit.ontouchmove = event => { event.preventDefault(); move(event); };
  hit.onmouseleave = () => { layer.style.display = 'none'; if (tooltip) tooltip.style.display = 'none'; };
  if (svg.dataset.panBound !== '1') {
    let startX = 0;
    svg.onpointerdown = event => startX = event.clientX;
    svg.onpointerup = event => { const delta = event.clientX - startX; if (Math.abs(delta) > 30) chartPan(id, delta < 0 ? 1 : -1); };
    svg.dataset.panBound = '1';
  }
}
function chartSeries(id) { return id === 'overviewChart' ? SERIES.overview : SERIES.air; }
function redrawChart(id) { drawChart(id, S.history, chartSeries(id)); }
function chartZoom(id, factor) { const state = chartState[id] || (chartState[id] = {zoom:1,offset:0}); state.zoom = Math.max(1, Math.min(12, state.zoom * factor)); redrawChart(id); }
function chartPan(id, direction) { const state = chartState[id] || (chartState[id] = {zoom:1,offset:0}); state.offset = Math.max(0, Math.min(1, state.offset + direction * 0.15)); redrawChart(id); }
function chartReset(id) { chartState[id] = {zoom:1,offset:0}; redrawChart(id); }
function chartCsv(id) {
  const rows = visibleRows(id, S.history), keys = id === 'overviewChart' ? ['ts','temperature','humidity'] : ['ts','pm25_living_room','pm25_bedroom'];
  const csv = [keys.join(','), ...rows.map(row => keys.map(key => row[key] ?? '').join(','))].join('\n');
  const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], {type:'text/csv'})); link.download = `${id}-${S.range}.csv`; link.click(); URL.revokeObjectURL(link.href);
}
function chartPng(id) {
  const svg = $(id); if (!svg) return;
  const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(svg)], {type:'image/svg+xml'}));
  const image = new Image();
  image.onload = () => { const canvas = document.createElement('canvas'); canvas.width = 1200; canvas.height = 420; const context = canvas.getContext('2d'); context.fillStyle = '#0b1018'; context.fillRect(0,0,canvas.width,canvas.height); context.drawImage(image,0,0,canvas.width,canvas.height); URL.revokeObjectURL(url); const link = document.createElement('a'); link.href = canvas.toDataURL('image/png'); link.download = `${id}-${S.range}.png`; link.click(); };
  image.src = url;
}
function ensureChartToolbar(id) {
  const svg = $(id), card = svg?.closest('.card'), head = card?.querySelector('.card-head');
  if (!head || head.querySelector(`[data-tools-for="${id}"]`)) return;
  const tools = document.createElement('div'); tools.className = 'chart-tools'; tools.dataset.toolsFor = id;
  tools.innerHTML = `<button class="btn ghost" onclick="chartZoom('${id}',1.5)">Zoom +</button><button class="btn ghost" onclick="chartZoom('${id}',0.67)">Zoom -</button><button class="btn ghost" onclick="chartPan('${id}',-1)">◀</button><button class="btn ghost" onclick="chartPan('${id}',1)">▶</button><button class="btn ghost" onclick="chartReset('${id}')">Reset</button><button class="btn ghost" onclick="chartPng('${id}')">PNG</button><button class="btn ghost" onclick="chartCsv('${id}')">CSV</button>`;
  head.appendChild(tools);
}

function bindRangeButtons(container) {
  container?.querySelectorAll('[data-range]').forEach(button => button.onclick = () => setRange(button.dataset.range));
}
function renderOverview() {
  const history = S.history;
  $('overviewMetrics').innerHTML = metricHTML('Temperature',fmt(S.sensor.temperature),'°C','Current indoor reading') + metricHTML('Humidity',fmt(S.sensor.humidity),'%','Current indoor reading') + metricHTML('Living Room PM2.5',fmt(S.air.living_room?.value),'µg/m³',S.air.living_room?.stale?'Stale':'Home Assistant') + metricHTML('Bedroom PM2.5',fmt(S.air.bedroom?.value),'µg/m³',S.air.bedroom?.stale?'Stale':'Home Assistant');
  $('overviewStats').innerHTML = statsHTML('Temperature',stat(history,'temperature'),'°C') + statsHTML('Humidity',stat(history,'humidity'),'%') + statsHTML('Living Room PM2.5',stat(history,'pm25_living_room'),'µg/m³') + statsHTML('Bedroom PM2.5',stat(history,'pm25_bedroom'),'µg/m³');
  $('overviewRanges').innerHTML = rangeButtons(); bindRangeButtons($('overviewRanges'));
  drawChart('overviewChart', history, SERIES.overview); drawChart('overviewPmChart', history, SERIES.air);
  ensureChartToolbar('overviewChart'); ensureChartToolbar('overviewPmChart'); renderOverviewSummary();
}
function renderOverviewSummary() {
  const people = S.presence || {};
  $('overviewPresence').innerHTML = ['beer','seem'].map(name => { const item = people[name] || {}, status = item.status || item.state || 'Unknown'; return `<div class="mini"><div class="k">${name}</div><div class="v">${safeText(status)}</div><div class="sub">${safeText(item.source || 'No source')}</div></div>`; }).join('');
  const devices = S.sonoff.devices || [], online = devices.filter(device => device.online).length;
  $('deviceSummary').innerHTML = `<div class="kv"><span>Sonoff online</span><strong>${online} / ${devices.length}</strong></div><div class="kv"><span>Tuya lights online</span><strong>${S.lights.filter(device => device.online).length} / ${S.lights.length}</strong></div><div class="kv"><span>Cameras online</span><strong>${S.cameras.filter(camera => camera.online).length} / ${S.cameras.length}</strong></div>`;
  const alerts = [];
  if (!S.health.mqtt_connected) alerts.push('MQTT is disconnected');
  if (!S.air.configured) alerts.push('Home Assistant PM2.5 source is not configured');
  if (S.air.living_room?.stale) alerts.push('Living Room PM2.5 is stale');
  if (S.air.bedroom?.stale) alerts.push('Bedroom PM2.5 is stale');
  if (!S.sonoffAvailable) alerts.push('Sonoff Cloud API is offline');
  $('alerts').innerHTML = alerts.length ? alerts.map(text => `<div class="alert warn">${safeText(text)}</div>`).join('') : '<div class="alert ok">All monitored systems look normal.</div>';
}

function sonoffChannels(device) {
  const raw = Array.isArray(device.channels) ? device.channels : [];
  if (raw.length) return raw.map(item => Number(item.channel || item)).filter(Boolean);
  return Array.from({length:Math.max(1,Number(device.gang_count)||1)},(_,index)=>index+1);
}
function sonoffSignature(device) {
  return JSON.stringify([device.online, device.state, device.channel_states, device.last_update_ts, device.updated_ts, device.rssi, S.sonoff.auth_status]);
}
function switchHtml(deviceId, channel, state) {
  const checked = state === 'on';
  return `<label class="relay-switch" title="Toggle relay"><input type="checkbox" ${checked?'checked':''} data-sonoff="${safeText(deviceId)}" data-channel="${channel}" data-next="${checked?'off':'on'}"><span></span></label>`;
}
function sonoffCardHtml(device) {
  const id = String(device.deviceid || ''), channels = sonoffChannels(device);
  return `<div class="sonoff-card-head"><div><h3>${safeText(device.name || id)}</h3><div class="sonoff-meta"><span class="dot ${device.online?'on':''}"></span>${device.online?'Online':'Offline'} · Updated ${shortTime(device.last_update_ts || device.updated_ts || S.sonoffLastSyncTs)}${device.rssi!==undefined&&device.rssi!==null?` · RSSI ${safeText(device.rssi)}`:''}</div></div></div><div class="relay-list">${channels.map(channel => { const state = device.channel_states?.[String(channel)] || 'off'; return `<div class="relay"><div><strong>CH${channel}</strong><div class="relay-state ${state==='on'?'ok':'bad'}">${state.toUpperCase()}</div></div>${switchHtml(id,channel,state)}</div>`; }).join('')}</div>`;
}
function renderSonoffGrid() {
  const host = $('sonoffList'); if (!host) return;
  host.className = 'sonoff-grid';
  const devices = S.sonoff.devices || [], seen = new Set();
  if (!devices.length) { host.innerHTML = '<div class="empty sonoff-empty">No Sonoff devices available</div>'; sonoffCards.clear(); return; }
  host.querySelector('.sonoff-empty')?.remove();
  devices.forEach(device => {
    const key = String(device.deviceid || ''), domId = `sonoff-${key.replace(/[^a-zA-Z0-9_-]/g,'')}`, signature = sonoffSignature(device);
    seen.add(domId);
    let card = sonoffCards.get(domId) || document.getElementById(domId);
    if (!card) { card = document.createElement('article'); card.id = domId; card.className = 'sonoff-card'; host.appendChild(card); sonoffCards.set(domId, card); }
    if (card.dataset.signature !== signature) { card.innerHTML = sonoffCardHtml(device); card.dataset.signature = signature; card.querySelectorAll('[data-sonoff]').forEach(input => input.onchange = () => sonoff(input.dataset.sonoff, Number(input.dataset.channel), input.dataset.next)); }
  });
  [...host.children].forEach(child => { if (child.id && !seen.has(child.id)) { sonoffCards.delete(child.id); child.remove(); } });
}
function renderLighting() {
  renderSonoffGrid();
  DashboardModules.renderZones($('tuyaList'), post, toast);
}
async function sonoff(deviceId, channel, action) { try { await post('/api/sonoff',{deviceid:deviceId,channel,action}); toast(`CH${channel} ${action.toUpperCase()}`); await loadSonoff(); renderSonoffGrid(); } catch (error) { toast(error.message); } }
async function sonoffDevice(deviceId, action) { try { await post('/api/sonoff/device',{deviceid:deviceId,action}); toast(`Device ${action.toUpperCase()}`); await loadSonoff(); renderSonoffGrid(); } catch (error) { toast(error.message); } }
async function sonoffAll(action) { try { await post('/api/sonoff/all',{action}); toast(`All Sonoff ${action.toUpperCase()}`); await loadSonoff(); renderSonoffGrid(); } catch (error) { toast(error.message); } }

function renderClimate() {
  const history = S.history;
  $('climateRanges').innerHTML = rangeButtons(); bindRangeButtons($('climateRanges'));
  $('airCards').innerHTML = metricHTML('Living Room PM2.5',fmt(S.air.living_room?.value),'µg/m³',airSub(S.air.living_room)) + metricHTML('Bedroom PM2.5',fmt(S.air.bedroom?.value),'µg/m³',airSub(S.air.bedroom));
  $('airStats').innerHTML = statsHTML('Living Room',stat(history,'pm25_living_room'),'µg/m³') + statsHTML('Bedroom',stat(history,'pm25_bedroom'),'µg/m³');
  drawChart('airChart', history, SERIES.air); ensureChartToolbar('airChart');
}
function airSub(room) { if (!S.air.configured) return 'Not configured'; if (!room || room.value === null) return 'Unavailable'; const filter = room.filter_life ? ` · Filter ${room.filter_life.value}` : ''; return `${room.stale?'Stale':'Live'} · ${when(room.updated_ts)}${filter}`; }

function renderPresence() {
  const people = S.presence || {};
  $('presenceList').innerHTML = ['beer','seem'].map(name => { const item = people[name] || {}, status = item.status || item.state || 'Unknown', automation = S.system.automation?.people?.[name] || {}; return `<div class="card presence-card"><div class="label">${name}</div><div class="state ${String(status).toLowerCase()==='home'?'ok':String(status).toLowerCase().includes('recent')?'warn':'bad'}">${safeText(status)}</div><div class="kv"><span>Source</span><strong>${safeText(item.source||'Not available')}</strong></div><div class="kv"><span>Last seen</span><strong>${when(item.last_seen_ts||item.ts)}</strong></div><div class="kv"><span>Automation home</span><strong>${automation.automation_home===null||automation.automation_home===undefined?'Unknown':automation.automation_home?'Home':'Away'}</strong></div><div class="kv"><span>Cooldown</span><strong>${automation.cooldown_remaining_sec||0}s</strong></div></div>`; }).join('');
  DashboardModules.renderAutomations($('automationEvents'), automationAction);
}
async function automationAction(entityId, action) { try { await post('/api/ha/automation',{entity_id:entityId,action}); toast(`Automation ${action} complete`); await DashboardModules.loadAutomations(get); renderPresence(); } catch (error) { toast(error.message); } }

const TV_COMMANDS = [['Power ON','power_on'],['Power OFF','power_off'],['Home','home'],['YouTube','youtube'],['Netflix','netflix'],['Disney+','disney'],['Prime Video','prime'],['Apple TV','appletv'],['Browser','browser'],['Live TV','livetv'],['Viu','viu'],['HBO Max','hbo'],['HDMI 1','hdmi1'],['HDMI 2','hdmi2'],['HDMI 3','hdmi3'],['HDMI 4','hdmi4'],['VOL +','volume_up'],['VOL -','volume_down'],['Mute','mute'],['Unmute','unmute']];
function extractTvState(payload) {
  const candidates = [payload?.tv, payload?.last_state, payload?.state, payload];
  for (const item of candidates) {
    if (!item || typeof item !== 'object') continue;
    const power = item.power ?? item.status ?? item.state ?? item.online;
    const app = item.app ?? item.current_app ?? item.input ?? item.source;
    const volume = item.volume ?? item.vol;
    const mute = item.mute ?? item.muted;
    if (power !== undefined || app !== undefined || volume !== undefined || mute !== undefined) return {power,app,volume,mute,ts:item.ts||payload?.last_state_ts||Math.floor(Date.now()/1000)};
  }
  return null;
}
function tvStatusLabel() {
  const tv = S.tv.lastValid;
  if (!tv) return {label:'Offline', online:false};
  const raw = String(tv.power ?? '').toLowerCase();
  const online = !['off','false','0','offline','unknown',''].includes(raw);
  return {label:online?'Online':'Offline',online};
}
function renderEntertainment() {
  const host = $('tvButtons'); if (!host) return;
  const status = tvStatusLabel(), tv = S.tv.lastValid;
  host.innerHTML = `<div class="tv-status-card"><div><strong>LG TV</strong><div class="device-meta">${status.online?'Online':'Offline'}${tv?.app?` · ${safeText(tv.app)}`:''}${tv?.volume!==undefined?` · Volume ${safeText(tv.volume)}`:''}${tv?.mute!==undefined?` · ${tv.mute?'Muted':'Sound on'}`:''}</div></div><span class="status-pill ${status.online?'ok':''}">${status.label}</span></div><div class="tv-command-grid">${TV_COMMANDS.map(([label,command]) => `<button class="btn ${command==='power_off'?'danger':command==='power_on'?'primary':'ghost'}" data-tv-command="${command}">${safeText(label)}</button>`).join('')}</div>`;
  host.querySelectorAll('[data-tv-command]').forEach(button => button.onclick = () => tv(button.dataset.tvCommand));
}
async function tv(command) { try { await post('/api/command',{cmd:command}); toast(`TV: ${command}`); } catch (error) { toast(error.message); } }

function sonoffCloudStatus() { if (!S.sonoffAvailable) return {label:'Offline',cls:'bad'}; if (S.sonoff.config_loaded===false) return {label:'Not configured',cls:'warn'}; if (S.sonoff.config_loaded===true&&S.sonoff.auth_status==='authenticated') return {label:'Connected',cls:'ok'}; if (S.sonoff.config_loaded===true) return {label:'Authentication issue',cls:'bad'}; return {label:'Offline',cls:'bad'}; }
function renderSystem() {
  const data = S.system, history = data.history || {}, cloud = sonoffCloudStatus(), safeError = S.sonoff.last_error || S.sonoffError;
  $('systemDetails').innerHTML = `<div class="kv"><span>Service</span><strong class="ok">${safeText(data.service||'unknown')}</strong></div><div class="kv"><span>Application version</span><strong>${safeText(data.version||'-')}</strong></div><div class="kv"><span>MQTT</span><strong class="${data.mqtt?.connected?'ok':'bad'}">${data.mqtt?.connected?'Connected':'Disconnected'}</strong></div><div class="kv"><span>Sonoff Cloud</span><strong class="${cloud.cls}">${cloud.label}</strong></div><div class="kv"><span>Sonoff devices</span><strong>${(S.sonoff.devices||[]).length}</strong></div><div class="kv"><span>Sonoff last successful sync</span><strong>${when(S.sonoffLastSyncTs)}</strong></div>${safeError?`<div class="kv"><span>Sonoff error</span><strong class="bad">${safeText(safeError)}</strong></div>`:''}<div class="kv"><span>Home Assistant PM2.5</span><strong class="${data.home_assistant?.configured?'ok':'warn'}">${data.home_assistant?.configured?'Configured':'Not configured'}</strong></div><div class="kv"><span>History store</span><strong>${safeText(history.history_store_path||'-')}</strong></div><div class="kv"><span>History loaded/appended/pruned</span><strong>${history.loaded_count||0} / ${history.appended_count||0} / ${history.pruned_count||0}</strong></div><div class="kv"><span>Camera configuration</span><strong>${data.camera?.config_loaded?'Loaded':'Not loaded'} · ${data.camera?.count||0} cameras</strong></div>`;
}

async function loadHistory() { try { const payload = await get(`/api/condo/history?range=${S.range}`); S.history = payload.history || []; S.sensor = payload.current || S.sensor; } catch (error) { console.warn('History refresh failed:', error.message); } }
async function loadStatus() { try { const payload = await get('/api/condo/status'); S.sensor = payload.sensor || S.sensor; S.presence = payload.presence || S.presence; } catch (error) { console.warn('Condo status refresh failed:', error.message); } }
async function loadAir() { try { S.air = await get('/api/air-quality'); } catch (error) { console.warn('Air-quality refresh failed:', error.message); } }
async function loadSonoff() { try { const payload = await get('/api/sonoff'); S.sonoff = payload; S.sonoffAvailable = true; S.sonoffLastSyncTs = Math.floor(Date.now()/1000); S.sonoffError = null; } catch (error) { S.sonoffAvailable = false; S.sonoffError = error.message; console.warn('Sonoff refresh failed:', error.message); } }
async function loadLights() { try { const payload = await get('/api/lights/status-live'); S.lights = payload.devices || S.lights; } catch (error) { console.warn('Lighting refresh failed:', error.message); } }
async function loadScenes() { try { const payload = await get('/api/scenes'); S.scenes = payload.scenes || S.scenes; } catch (error) { console.warn('Scenes refresh failed:', error.message); } }
async function loadHealth() { try { const payload = await get('/api/health'); S.health = payload; const tv = extractTvState(payload); if (tv) S.tv.lastValid = tv; } catch (error) { console.warn('Health refresh failed:', error.message); } }
async function loadSystem() { try { S.system = await get('/api/dashboard/status'); const tv = extractTvState(S.system); if (tv) S.tv.lastValid = tv; } catch (error) { console.warn('System refresh failed:', error.message); } }
async function loadCameras() { try { const payload = await get('/api/cameras'); S.cameras = payload.cameras || S.cameras; } catch (error) { console.warn('Camera refresh failed:', error.message); } }

function renderBadges() {
  if ($('mqttBadge')) { $('mqttBadge').textContent = S.health.mqtt_connected ? 'MQTT Online' : 'MQTT Offline'; $('mqttBadge').className = `badge ${S.health.mqtt_connected?'ok':'bad'}`; }
  if ($('haBadge')) { $('haBadge').textContent = S.air.configured ? 'HA Air Online' : 'HA Air Unavailable'; $('haBadge').className = `badge ${S.air.configured?'ok':'warn'}`; }
}
async function refresh() {
  await Promise.allSettled([
    loadStatus(), loadHistory(), loadAir(), loadSonoff(), loadLights(), loadScenes(), loadHealth(), loadSystem(), loadCameras(),
    DashboardModules.loadZones(get), DashboardModules.loadAutomations(get)
  ]);
  renderBadges();
  renderPage(currentPage());
}

function bindStaticControls() {
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => nav(button.dataset.nav));
  document.querySelectorAll('[onclick*="refresh()"]')?.forEach(button => { if (button.textContent.includes('Refresh')) button.onclick = refresh; });
  const toolbar = document.querySelector('[data-page="lighting"] .card-head .controls');
  if (toolbar && !toolbar.querySelector('[data-refresh-sonoff]')) {
    toolbar.insertAdjacentHTML('beforeend','<button class="btn ghost" data-refresh-sonoff>Refresh</button>');
    toolbar.querySelector('[data-refresh-sonoff]').onclick = async () => { await loadSonoff(); renderSonoffGrid(); };
  }
  const zoneHead = document.querySelector('[data-page="lighting"] .card:nth-of-type(2) .card-head');
  if (zoneHead && !zoneHead.querySelector('[data-refresh-zones]')) {
    zoneHead.insertAdjacentHTML('beforeend','<button class="btn ghost" data-refresh-zones>Refresh All</button>');
    zoneHead.querySelector('[data-refresh-zones]').onclick = async () => { await DashboardModules.loadZones(get); DashboardModules.renderZones($("tuyaList"),post,toast); };
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  bindStaticControls();
  nav(currentPage());
  await refresh();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = window.setInterval(refresh, 15000);
  window.addEventListener('resize', () => {
    if (currentPage() === 'overview') { redrawChart('overviewChart'); redrawChart('overviewPmChart'); }
    if (currentPage() === 'climate') redrawChart('airChart');
  }, {passive:true});
});