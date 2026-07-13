(() => {
  'use strict';

  function installTopologyUi() {
    document.querySelectorAll('.nav, .mobile-nav').forEach(navHost => {
      if (navHost.querySelector('[data-nav="topology"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'topology';
      button.dataset.short = 'NC';
      button.textContent = 'Topology';
      navHost.appendChild(button);
    });
    if (!document.querySelector('[data-page="topology"]')) {
      const section = document.createElement('section');
      section.className = 'page';
      section.dataset.page = 'topology';
      section.innerHTML = `<div class="topology-summary"><div id="topologyHealth" class="card health-score"></div><div id="topologyRoots" class="root-list"></div></div><div class="topology-shell"><div><div class="card topology-map-card"><div class="card-head"><h2>Live Dependency Graph</h2></div><div id="topologyGraph" class="topology-map"></div></div><div class="card" style="margin-top:16px"><div class="card-head"><h2>Recent Events</h2></div><div id="topologyEvents" class="event-list"></div></div></div><aside class="card topology-detail"><div class="card-head"><h2>Node Details</h2></div><div id="topologyDetail"><div class="empty">Select a node to inspect status and diagnostics.</div></div></aside></div>`;
      document.querySelector('.main')?.appendChild(section);
    }
  }

  installTopologyUi();

  const topologyState = {data:null, selected:null, mobile:false};
  const originalRefresh = window.refresh;
  const originalRenderPage = window.renderPage;
  const originalNav = window.nav;
  const originalRenderOverview = window.renderOverview;
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const healthClass = value => ['healthy','warning','offline','unknown'].includes(String(value)) ? String(value) : 'unknown';
  const tvValue = (value, fallback='Not available') => value === null || value === undefined || value === '' ? fallback : value;

  function toEpoch(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'number') return value > 1e12 ? Math.floor(value / 1000) : Math.floor(value);
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric > 1e12 ? Math.floor(numeric / 1000) : Math.floor(numeric);
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
  }

  function relativeTime(value) {
    const ts = toEpoch(value);
    if (!ts) return 'Not available';
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (seconds < 45) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} hr ago`;
    return `${Math.floor(seconds / 86400)} day${seconds < 172800 ? '' : 's'} ago`;
  }

  function thailandTime(value) {
    const ts = toEpoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone:'Asia/Bangkok', year:'numeric', month:'short', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false
    }).format(new Date(ts * 1000));
  }

  async function loadTopology() {
    try {
      const payload = await window.get('/api/topology');
      topologyState.data = payload;
      if (payload?.tv) {
        const previous = window.S?.tv?.lastValid || {};
        window.S.tv.lastValid = {...previous, ...payload.tv, ts:payload.tv.last_update_ts || previous.ts};
      }
    } catch (error) {
      console.warn('Topology refresh failed:', error.message);
    }
  }

  window.refresh = async function refreshWithTopology() {
    await Promise.allSettled([originalRefresh(), loadTopology()]);
    window.renderPage(window.currentPage());
  };

  window.nav = function topologyNav(page) {
    originalNav(page);
    if (page === 'topology' && document.getElementById('pageTitle')) document.getElementById('pageTitle').textContent = 'Topology';
  };

  window.renderPage = function unifiedRenderPage(page=window.currentPage()) {
    originalRenderPage(page);
    if (page === 'topology') renderTopology();
  };

  window.renderOverview = function overviewWithTv() {
    originalRenderOverview();
    const host = document.getElementById('overviewMetrics');
    if (!host) return;
    host.querySelector('[data-overview-tv]')?.remove();
    const tvState = window.S?.tv?.lastValid || {};
    const online = tvState.tv_online === true;
    const bridgeOnly = tvState.bridge_online === true && tvState.tv_online == null;
    const label = online ? 'Online' : bridgeOnly ? 'State Unknown' : tvState.tv_online === false ? 'Offline' : 'Unknown';
    const cls = online ? 'ok' : bridgeOnly ? 'warn' : 'bad';
    host.insertAdjacentHTML('beforeend', `<div class="card metric" data-overview-tv><div class="label">LG TV Status</div><div class="value ${cls}">${safe(label)}</div><div class="sub">${safe(tvValue(tvState.app || tvState.input, bridgeOnly ? 'Bridge Online' : 'Not available'))} · Updated ${safe(window.when(tvState.last_update_ts || tvState.last_heartbeat_ts))}</div></div>`);
  };

  function remoteMarkup() {
    return `<div class="remote tv-remote-pad"><span></span><button class="btn" onclick="tv('up')">▲</button><span></span><button class="btn" onclick="tv('left')">◀</button><button class="btn primary" onclick="tv('ok')">OK</button><button class="btn" onclick="tv('right')">▶</button><span></span><button class="btn" onclick="tv('down')">▼</button><span></span></div><div class="tv-remote-actions"><button class="btn ghost" onclick="tv('back')">Back</button><button class="btn ghost" onclick="tv('home_key')">Home</button></div>`;
  }

  function commandSection(title, commands) {
    const byCommand = new Map(window.TV_COMMANDS.map(([label,command]) => [command, label]));
    return `<section class="tv-control-section"><h3>${safe(title)}</h3><div class="tv-control-grid">${commands.map(command => `<button class="btn ${command==='power_on'?'primary':command==='power_off'?'danger':'ghost'}" data-tv-command="${safe(command)}">${safe(byCommand.get(command) || command)}</button>`).join('')}</div></section>`;
  }

  window.renderEntertainment = function renderEntertainmentUnified() {
    const host = document.getElementById('tvButtons');
    if (!host) return;
    const tvState = window.S?.tv?.lastValid || {};
    const online = tvState.tv_online === true;
    const bridgeOnly = tvState.bridge_online === true && tvState.tv_online == null;
    const status = online ? 'Online' : bridgeOnly ? 'Bridge Online / TV State Unknown' : tvState.tv_online === false ? 'Offline' : 'Unknown';
    const items = [
      ['Status', status, online ? 'ok' : bridgeOnly ? 'warn' : 'bad'],
      ['Current App', tvValue(tvState.app), ''],
      ['Input', tvValue(tvState.input), ''],
      ['Volume', tvValue(tvState.volume), ''],
      ['Mute', tvState.mute === true ? 'Muted' : tvState.mute === false ? 'Sound on' : tvValue(tvState.mute), ''],
      ['Last Update', thailandTime(tvState.last_update_ts || tvState.last_heartbeat_ts), ''],
    ];
    host.className = 'tv-page-layout';
    host.innerHTML = `<div class="tv-top-grid"><section class="tv-panel"><h3 class="tv-panel-title">TV Status</h3><div class="tv-status-grid">${items.map(([label,value,cls]) => `<div class="tv-status-item"><span>${safe(label)}</span><strong class="${cls}">${safe(value)}</strong></div>`).join('')}</div></section><section class="tv-panel tv-navigation-panel"><h3 class="tv-panel-title">Navigation / Remote Keys</h3>${remoteMarkup()}</section></div><div class="tv-controls-layout">${commandSection('Power',['power_on','power_off'])}${commandSection('Apps',['netflix','youtube','disney','prime','appletv','livetv','browser','viu','hbo'])}${commandSection('Inputs',['hdmi1','hdmi2','hdmi3','hdmi4'])}${commandSection('Volume',['volume_up','volume_down','mute','unmute'])}</div>`;
    host.querySelectorAll('[data-tv-command]').forEach(button => button.onclick = () => window.tv(button.dataset.tvCommand));
  };

  window.renderPresence = function renderPresenceUnified() {
    const people = window.S?.presence || {};
    const host = document.getElementById('presenceList');
    if (!host) return;
    host.innerHTML = ['beer','seem'].map(key => {
      const item = people[key] || {};
      const name = key === 'beer' ? 'Beer' : 'Seem';
      const status = item.status || item.state || 'Unknown';
      const automation = window.S?.system?.automation?.people?.[key] || {};
      const lastSeen = item.last_seen || item.last_seen_ts || item.latest_ts || item.ts || item.updated_ts;
      const relative = relativeTime(lastSeen);
      const local = thailandTime(lastSeen);
      const stateClass = String(status).toLowerCase() === 'home' ? 'ok' : String(status).toLowerCase().includes('recent') ? 'warn' : 'bad';
      return `<div class="card presence-card"><div class="label presence-name">${name}</div><div class="state ${stateClass}">${safe(status)}</div><div class="kv"><span>Source</span><strong>${safe(item.source || 'Not available')}</strong></div><div class="kv presence-last-seen" title="${safe(local)}"><span>Last seen</span><strong>${safe(relative)}${lastSeen ? `<small>${safe(local)} ICT</small>` : ''}</strong></div><div class="kv"><span>Automation home</span><strong>${automation.automation_home===null||automation.automation_home===undefined?'Unknown':automation.automation_home?'Home':'Away'}</strong></div><div class="kv"><span>Cooldown</span><strong>${Number(automation.cooldown_remaining_sec || 0)}s</strong></div></div>`;
    }).join('');
    window.DashboardModules.renderAutomations(document.getElementById('automationEvents'), window.automationAction);
  };

  function nodeById(id) {
    return topologyState.data?.nodes?.find(node => node.id === id) || null;
  }

  function renderDetail(node) {
    const host = document.getElementById('topologyDetail');
    if (!host) return;
    if (!node) { host.innerHTML = '<div class="empty">Select a node to inspect status and diagnostics.</div>'; return; }
    const diagnostics = node.diagnostics && typeof node.diagnostics === 'object' ? Object.entries(node.diagnostics) : [];
    host.innerHTML = `<div class="kv"><span>Status</span><strong>${safe(node.online===null||node.online===undefined?'Unknown':node.online?'Online':'Offline')}</strong></div><div class="kv"><span>Health</span><strong class="${healthClass(node.health)}">${safe(node.health || 'unknown')}</strong></div><div class="kv"><span>Latency</span><strong>${node.latency_ms===null||node.latency_ms===undefined?'Unknown':`${safe(node.latency_ms)} ms`}</strong></div><div class="kv"><span>Last Update</span><strong>${safe(thailandTime(node.last_update_ts))}</strong></div><div class="kv"><span>Dependencies</span><strong>${safe((node.dependencies||[]).join(', ')||'None')}</strong></div><div class="kv"><span>Dependents</span><strong>${safe((node.dependents||[]).join(', ')||'None')}</strong></div>${diagnostics.map(([key,value]) => `<div class="kv"><span>${safe(key)}</span><strong>${safe(typeof value==='object'?JSON.stringify(value):value)}</strong></div>`).join('')}`;
  }

  const desktopPositions = {
    internet:[430,28], cloudflare_wan:[430,100], condo_router:[430,172], tinkerboard:[430,244],
    dashboard:[180,338], mqtt:[430,338], zerotier_condo:[680,338], sonoff:[75,448], camera:[285,448],
    presence:[390,448], lg_tv:[500,548], zerotier_tunnel:[680,448], zerotier_home:[680,548],
    truenas:[680,648], home_assistant:[680,748], tuya:[500,848], electricity:[680,848], pm25:[860,848]
  };
  const mobileOrder = ['internet','cloudflare_wan','condo_router','tinkerboard','dashboard','sonoff','camera','mqtt','presence','lg_tv','zerotier_condo','zerotier_tunnel','zerotier_home','truenas','home_assistant','tuya','electricity','pm25'];

  function positionsFor(nodes) {
    const mobile = window.innerWidth <= 640;
    topologyState.mobile = mobile;
    if (!mobile) return {width:1040,height:930,positions:desktopPositions,nodeW:150,nodeH:52};
    const positions = {};
    mobileOrder.forEach((id,index) => positions[id] = [35,28 + index * 60]);
    return {width:360,height:1120,positions,nodeW:290,nodeH:46};
  }

  function edgeHealth(parent, child) {
    if (!parent || !child) return 'unknown';
    if (parent.health === 'offline' || child.health === 'offline') return 'offline';
    if (parent.health === 'warning' || child.health === 'warning') return 'warning';
    if (parent.health === 'healthy' && child.health === 'healthy') return 'healthy';
    return 'unknown';
  }

  function renderGraph(nodes) {
    const host = document.getElementById('topologyGraph');
    if (!host) return;
    const layout = positionsFor(nodes);
    const map = new Map(nodes.map(node => [node.id,node]));
    const edges = [];
    nodes.forEach(node => (node.dependencies || []).forEach(dep => { if (map.has(dep)) edges.push([dep,node.id]); }));
    const groups = topologyState.mobile ? '' : `<rect class="topology-group" x="35" y="305" width="565" height="320" rx="20"/><text class="topology-group-label" x="55" y="330">CONDO</text><rect class="topology-group" x="625" y="305" width="205" height="430" rx="20"/><text class="topology-group-label" x="645" y="330">ZEROTIER</text><rect class="topology-group" x="445" y="720" width="555" height="185" rx="20"/><text class="topology-group-label" x="465" y="745">HOME</text>`;
    const edgeSvg = edges.map(([from,to]) => {
      const a = layout.positions[from], b = layout.positions[to];
      if (!a || !b) return '';
      const x1=a[0]+layout.nodeW/2, y1=a[1]+layout.nodeH, x2=b[0]+layout.nodeW/2, y2=b[1];
      const mid=(y1+y2)/2;
      const path=`M${x1},${y1} C${x1},${mid} ${x2},${mid} ${x2},${y2}`;
      const cls=edgeHealth(map.get(from),map.get(to));
      const packet=cls==='healthy'?`<path class="topology-edge-packet" d="${path}"/>`:'';
      const failed=cls==='offline'?`<text class="topology-edge-x" x="${(x1+x2)/2-5}" y="${mid+6}">×</text>`:'';
      return `<path class="topology-edge ${cls}" d="${path}"/>${packet}${failed}`;
    }).join('');
    const nodeSvg = nodes.map(node => {
      const pos=layout.positions[node.id]; if(!pos) return '';
      const status=node.online===true?'Online':node.online===false?'Offline':node.health==='warning'?'Warning':'Unknown';
      return `<g class="topology-node-svg ${healthClass(node.health)}" data-topology-node="${safe(node.id)}" tabindex="0" transform="translate(${pos[0]} ${pos[1]})"><rect class="topology-node-bg" width="${layout.nodeW}" height="${layout.nodeH}"/><circle class="topology-node-dot" cx="17" cy="17" r="6"/><text class="topology-node-name" x="30" y="20">${safe(node.name)}</text><text class="topology-node-status" x="17" y="39">${safe(status)}${node.latency_ms!=null?` · ${safe(node.latency_ms)} ms`:''}</text></g>`;
    }).join('');
    host.innerHTML=`<svg class="topology-svg" viewBox="0 0 ${layout.width} ${layout.height}" role="img" aria-label="Smart condo dependency topology">${groups}${edgeSvg}${nodeSvg}</svg>`;
    host.querySelectorAll('[data-topology-node]').forEach(element => {
      const select=()=>{topologyState.selected=element.dataset.topologyNode;renderDetail(nodeById(topologyState.selected));};
      element.onclick=select;
      element.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}};
    });
  }

  function renderTopology() {
    const data=topologyState.data;
    const roots=document.getElementById('topologyRoots'), events=document.getElementById('topologyEvents'), score=document.getElementById('topologyHealth');
    if (!roots || !events || !score) return;
    if (!data) { document.getElementById('topologyGraph').innerHTML='<div class="empty">Topology data is not available.</div>'; return; }
    score.innerHTML=`<strong>${safe(data.overall_health ?? 0)}%</strong><span>Overall Health · ${safe(data.measured_node_count ?? 0)} measured</span>`;
    roots.innerHTML=data.root_causes?.length?data.root_causes.map(item=>`<div class="root-cause bad"><strong>Root Cause · ${safe(item.label)}</strong><small>${safe(item.message)} · Affected: ${safe((item.affected||[]).join(', ')||'None')}</small></div>`).join(''):'<div class="root-cause"><strong>No confirmed root cause</strong><small>Unknown and unconfigured nodes are excluded from health scoring.</small></div>';
    renderGraph(data.nodes || []);
    events.innerHTML=data.events?.length?data.events.map(item=>`<div class="event-row"><time>${safe(window.shortTime(item.ts))}</time><strong>${safe(item.message)}</strong></div>`).join(''):'<div class="empty">No recent topology events.</div>';
    renderDetail(nodeById(topologyState.selected));
  }

  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer=setTimeout(()=>{if(window.currentPage()==='topology') renderTopology();},120);
  }, {passive:true});
  loadTopology().then(() => window.renderPage(window.currentPage()));
})();