(() => {
  'use strict';

  function installTopologyUi() {
    document.querySelectorAll('.nav, .mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="topology"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'topology';
      button.dataset.short = 'NC';
      button.textContent = 'Topology';
      host.appendChild(button);
    });
    if (document.querySelector('[data-page="topology"]')) return;
    const section = document.createElement('section');
    section.className = 'page';
    section.dataset.page = 'topology';
    section.innerHTML = `<div class="topology-summary"><div id="topologyHealth" class="card health-score"></div><div id="topologyRoots" class="root-list"></div></div><div class="card topology-map-card"><div class="card-head"><h2>Live Dependency Graph</h2><button id="topologyFit" class="btn ghost" type="button" disabled>Fit to View</button></div><div id="topologyGraph" class="topology-map"></div></div><section id="topologyDetailCard" class="card topology-detail collapsed"><div class="card-head"><h2>Node Details</h2><button id="topologyDetailClose" class="btn ghost" type="button">Close</button></div><div id="topologyDetail"><div class="empty">Select a node to inspect status and diagnostics.</div></div></section><div class="card topology-events-card"><div class="card-head"><h2>Recent Events</h2></div><div id="topologyEvents" class="event-list"></div></div>`;
    document.querySelector('.main')?.appendChild(section);
  }

  installTopologyUi();

  const state = {data:null, selected:null, viewBox:null, fitted:null, dragging:false, dragStart:null};
  const originalRefresh = window.refresh;
  const originalRenderPage = window.renderPage;
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const list = value => Array.isArray(value) ? value.map(String) : [];
  const healthClass = value => ['healthy','warning','offline','unknown'].includes(String(value)) ? String(value) : 'unknown';
  const SITE = {internet:'cloud',cloudflare_wan:'cloud',condo_router:'condo',tinkerboard:'condo',dashboard:'condo',mqtt:'condo',presence:'condo',lg_tv:'condo',sonoff:'condo',camera:'condo',tuya:'condo',electricity:'condo',pm25:'condo',tapo_ir:'condo',zerotier_condo:'zerotier',zerotier_tunnel:'zerotier',zerotier_home:'zerotier',truenas:'home',home_assistant:'home'};
  const SOURCE = {mqtt:'mqtt',presence:'mqtt',lg_tv:'mqtt',sonoff:'sonoff_cloud',camera:'local_runtime',tuya:'home_assistant',electricity:'tuya_local',pm25:'home_assistant',tapo_ir:'tapo_local',home_assistant:'home_assistant'};
  const ORDER = ['internet','cloudflare_wan','condo_router','tinkerboard','dashboard','mqtt','sonoff','camera','electricity','tapo_ir','presence','lg_tv','tuya','pm25','zerotier_condo','zerotier_tunnel','zerotier_home','truenas','home_assistant'];
  const GROUPS = {cloud:['internet','cloudflare_wan'],condo:['condo_router','tinkerboard','dashboard','mqtt','sonoff','camera','electricity','tapo_ir','presence','lg_tv','tuya','pm25'],zerotier:['zerotier_condo','zerotier_tunnel','zerotier_home'],home:['truenas','home_assistant']};
  const EDGES = [
    ['internet','cloudflare_wan','primary_dependency'],
    ['cloudflare_wan','condo_router','primary_dependency'],
    ['condo_router','tinkerboard','primary_dependency'],
    ['tinkerboard','dashboard','primary_dependency'],
    ['tinkerboard','mqtt','primary_dependency'],
    ['tinkerboard','sonoff','primary_dependency'],
    ['tinkerboard','camera','primary_dependency'],
    ['tinkerboard','electricity','primary_dependency'],
    ['tinkerboard','tapo_ir','primary_dependency'],
    ['mqtt','presence','primary_dependency'],
    ['mqtt','lg_tv','primary_dependency'],
    ['home_assistant','tuya','data_source'],
    ['home_assistant','pm25','data_source'],
    ['tinkerboard','zerotier_condo','network_tunnel'],
    ['zerotier_condo','zerotier_tunnel','network_tunnel'],
    ['zerotier_tunnel','zerotier_home','network_tunnel'],
    ['zerotier_home','truenas','network_tunnel'],
    ['truenas','home_assistant','network_tunnel']
  ];

  function toEpoch(value) {
    if (value == null || value === '') return null;
    const number = Number(value);
    if (Number.isFinite(number)) return number > 1e12 ? Math.floor(number / 1000) : Math.floor(number);
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
  }

  function timeText(value) {
    const ts = toEpoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Bangkok',year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(ts * 1000));
  }

  function normalizeNode(raw, index) {
    const node = object(raw);
    const id = String(node.id || `unknown_node_${index}`);
    const metadata = object(node.metadata);
    const diagnostics = object(node.diagnostics);
    return {...node,id,name:String(node.name || node.label || id),health:healthClass(node.health),online:node.online===true?true:node.online===false?false:null,dependencies:list(node.dependencies),dependents:list(node.dependents),capabilities:list(node.capabilities),metadata,diagnostics,physical_site:node.physical_site || metadata.physical_site || SITE[id] || 'local',data_source:node.data_source || diagnostics.source || metadata.source || SOURCE[id] || 'unknown'};
  }

  function normalizeTopology(raw) {
    const payload = object(raw?.data || raw);
    const seen = new Set();
    const nodes = [];
    (Array.isArray(payload.nodes) ? payload.nodes : []).forEach((item,index) => {
      const node = normalizeNode(item,index);
      if (seen.has(node.id)) return;
      seen.add(node.id);
      nodes.push(node);
    });
    return {...payload,nodes,root_causes:Array.isArray(payload.root_causes)?payload.root_causes:[],events:Array.isArray(payload.events)?payload.events:[]};
  }

  async function loadTopology() {
    try {
      const payload = await window.get('/api/topology');
      state.data = normalizeTopology(payload);
      if (payload?.tv && window.S?.tv) {
        const previous = window.S.tv.lastValid || {};
        window.S.tv.lastValid = {...previous,...payload.tv,ts:payload.tv.last_update_ts || previous.ts};
      }
    } catch (error) {
      console.error('Topology refresh failed',{name:error?.name || 'Error',message:error?.message || 'Unknown topology error'});
    }
  }

  function nodeById(id) { return state.data?.nodes?.find(node => node.id === id) || null; }

  function renderDetail(node) {
    const card = document.getElementById('topologyDetailCard');
    const host = document.getElementById('topologyDetail');
    if (!card || !host) return;
    if (!node) {
      card.classList.add('collapsed');
      host.innerHTML = '<div class="empty">Select a node to inspect status and diagnostics.</div>';
      return;
    }
    card.classList.remove('collapsed');
    const primary = [
      ['Status',node.online==null?'Unknown':node.online?'Online':'Offline'],['Health',node.health],['Physical Site',node.physical_site],['Data Source',node.data_source],['Latency',node.latency_ms==null?'Unknown':`${node.latency_ms} ms`],['Last Update',timeText(node.last_update_ts)],['Dependencies',node.dependencies.join(', ')||'None'],['Dependents',node.dependents.join(', ')||'None'],['Capabilities',node.capabilities.join(', ')||'None']
    ];
    const diagnostics = Object.entries(node.diagnostics);
    host.innerHTML = `<div class="topology-detail-grid">${primary.map(([label,value])=>`<div class="topology-detail-item"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`).join('')}</div><div class="topology-diagnostics"><h3>Diagnostics</h3><div class="topology-detail-grid">${diagnostics.map(([key,value])=>`<div class="topology-detail-item"><span>${safe(key)}</span><strong>${safe(typeof value==='object'?JSON.stringify(value):value)}</strong></div>`).join('') || '<div class="empty">No diagnostics.</div>'}</div></div>`;
  }

  function viewportMode(width) { return width <= 640 ? 'mobile' : width <= 1050 ? 'tablet' : 'desktop'; }

  function buildLayout(nodes) {
    const host = document.getElementById('topologyGraph');
    const available = Math.max(320, host?.clientWidth || 1200);
    const mode = viewportMode(available);
    const nodeW = mode === 'desktop' ? 156 : mode === 'tablet' ? 144 : Math.min(286, available - 30);
    const nodeH = 56;
    const positions = {};
    const present = new Set(nodes.map(node => node.id));
    if (mode === 'mobile') {
      let y = 36;
      ORDER.filter(id => present.has(id)).forEach(id => { positions[id] = [Math.round((available-nodeW)/2), y]; y += 88; });
      return {mode,width:available,height:y+28,nodeW,nodeH,positions,groupPadding:18};
    }
    const width = Math.max(available, mode === 'desktop' ? 1460 : 1100);
    const x = {
      cloud: width * 0.13,
      condoInfra: width * 0.31,
      condoService: width * 0.47,
      condoDevice: width * 0.61,
      zeroTier: width * 0.76,
      home: width * 0.91
    };
    const place = (id,cx,cy) => { if (present.has(id)) positions[id] = [Math.round(cx-nodeW/2),Math.round(cy-nodeH/2)]; };
    place('internet',x.cloud,90); place('cloudflare_wan',x.cloud,190);
    place('condo_router',x.condoInfra,90); place('tinkerboard',x.condoInfra,210); place('dashboard',x.condoInfra,330);
    place('mqtt',x.condoService,150); place('sonoff',x.condoService,270); place('camera',x.condoService,390); place('electricity',x.condoService,510); place('tapo_ir',x.condoService,630);
    place('presence',x.condoDevice,110); place('lg_tv',x.condoDevice,230); place('tuya',x.condoDevice,430); place('pm25',x.condoDevice,550);
    place('zerotier_condo',x.zeroTier,170); place('zerotier_tunnel',x.zeroTier,310); place('zerotier_home',x.zeroTier,450);
    place('truenas',x.home,310); place('home_assistant',x.home,500);
    const unknown = nodes.filter(node => !positions[node.id]);
    unknown.forEach((node,index)=>{ positions[node.id]=[Math.round(width*0.46-nodeW/2),760+index*84]; });
    const maxBottom = Math.max(...Object.values(positions).map(([,py])=>py+nodeH));
    return {mode,width,height:maxBottom+70,nodeW,nodeH,positions,groupPadding:30};
  }

  function nodeRect(id,layout) {
    const point = layout.positions[id];
    return point ? {x:point[0],y:point[1],w:layout.nodeW,h:layout.nodeH} : null;
  }

  function port(rect, side, offset=0) {
    if (side === 'left') return {x:rect.x,y:rect.y+rect.h/2+offset};
    if (side === 'right') return {x:rect.x+rect.w,y:rect.y+rect.h/2+offset};
    if (side === 'top') return {x:rect.x+rect.w/2+offset,y:rect.y};
    return {x:rect.x+rect.w/2+offset,y:rect.y+rect.h};
  }

  function routeEdge(from,to,category,layout,index) {
    const a = nodeRect(from,layout), b = nodeRect(to,layout);
    if (!a || !b) return null;
    const dx = (b.x+b.w/2) - (a.x+a.w/2);
    const dy = (b.y+b.h/2) - (a.y+a.h/2);
    const horizontal = Math.abs(dx) >= Math.abs(dy);
    let startSide = horizontal ? (dx >= 0 ? 'right':'left') : (dy >= 0 ? 'bottom':'top');
    let endSide = horizontal ? (dx >= 0 ? 'left':'right') : (dy >= 0 ? 'top':'bottom');
    const lane = ((index % 5) - 2) * 8;
    const start = port(a,startSide,lane);
    const end = port(b,endSide,-lane);
    let points;
    if (category === 'network_tunnel') {
      const corridorX = Math.max(start.x,end.x) + 24 + (index%3)*12;
      points = [start,{x:corridorX,y:start.y},{x:corridorX,y:end.y},end];
    } else if (category === 'data_source') {
      const corridorY = Math.max(start.y,end.y) + 34 + (index%2)*14;
      points = [start,{x:start.x,y:corridorY},{x:end.x,y:corridorY},end];
    } else if (horizontal) {
      const corridorX = (start.x + end.x) / 2 + lane;
      points = [start,{x:corridorX,y:start.y},{x:corridorX,y:end.y},end];
    } else {
      const corridorY = (start.y + end.y) / 2 + lane;
      points = [start,{x:start.x,y:corridorY},{x:end.x,y:corridorY},end];
    }
    const compact = points.filter((point,i)=>i===0||point.x!==points[i-1].x||point.y!==points[i-1].y);
    const d = compact.map((point,i)=>`${i?'L':'M'}${point.x},${point.y}`).join(' ');
    return {d,points:compact};
  }

  function groupBounds(ids,layout,label) {
    if (layout.mode === 'mobile') return '';
    const rects = ids.map(id=>nodeRect(id,layout)).filter(Boolean);
    if (!rects.length) return '';
    const pad = layout.groupPadding;
    const x = Math.min(...rects.map(rect=>rect.x))-pad;
    const y = Math.min(...rects.map(rect=>rect.y))-pad;
    const right = Math.max(...rects.map(rect=>rect.x+rect.w))+pad;
    const bottom = Math.max(...rects.map(rect=>rect.y+rect.h))+pad;
    return `<rect class="topology-group" x="${x}" y="${y}" width="${right-x}" height="${bottom-y}" rx="20"/><text class="topology-group-label" x="${x+18}" y="${y+24}">${safe(label)}</text>`;
  }

  function edgeHealth(from,to,map) {
    const a=map.get(from), b=map.get(to);
    if (a?.health==='offline'||b?.health==='offline') return 'offline';
    if (a?.health==='warning'||b?.health==='warning') return 'warning';
    if (a?.health==='healthy'&&b?.health==='healthy') return 'healthy';
    return 'unknown';
  }

  function renderGraph(nodes, forceFit=false) {
    const host = document.getElementById('topologyGraph');
    if (!host) return;
    const layout = buildLayout(nodes);
    const map = new Map(nodes.map(node=>[node.id,node]));
    const edges = [];
    const seen = new Set();
    EDGES.forEach((edge,index)=>{
      const [from,to,category]=edge;
      if (!map.has(from)||!map.has(to)) return;
      const key=`${from}>${to}`;
      if (seen.has(key)) return;
      seen.add(key);
      const route=routeEdge(from,to,category,layout,index);
      if (route) edges.push({...route,from,to,category,index});
    });
    const groups = [groupBounds(GROUPS.cloud,layout,'CLOUD'),groupBounds(GROUPS.condo,layout,'CONDO'),groupBounds(GROUPS.zerotier,layout,'ZEROTIER'),groupBounds(GROUPS.home,layout,'HOME')].join('');
    const edgeSvg = edges.map(edge=>{
      const health=edgeHealth(edge.from,edge.to,map);
      const style=edge.category==='data_source'?'opacity:.52;stroke-dasharray:6 6':edge.category==='network_tunnel'?'stroke-width:2.8':'stroke-width:2.2';
      return `<path class="topology-edge ${health} topology-edge-${edge.category}" style="${style}" d="${edge.d}"/>`;
    }).join('');
    const nodeSvg = nodes.map(node=>{
      const pos=layout.positions[node.id];
      if(!pos)return'';
      const status=node.online===true?'Online':node.online===false?'Offline':node.health==='warning'?'Warning':'Unknown';
      const selected=node.id===state.selected?' selected':'';
      return `<g class="topology-node-svg ${healthClass(node.health)}${selected}" data-topology-node="${safe(node.id)}" tabindex="0" transform="translate(${pos[0]} ${pos[1]})"><rect class="topology-node-bg" width="${layout.nodeW}" height="${layout.nodeH}"/><circle class="topology-node-dot" cx="17" cy="17" r="6"/><text class="topology-node-name" x="30" y="20">${safe(node.name)}</text><text class="topology-node-status" x="17" y="40">${safe(status)}${node.latency_ms!=null?` · ${safe(node.latency_ms)} ms`:''}</text></g>`;
    }).join('');
    const padding=28;
    state.fitted={x:-padding,y:-padding,w:layout.width+padding*2,h:layout.height+padding*2};
    if(forceFit||!state.viewBox)state.viewBox={...state.fitted};
    host.innerHTML=`<svg class="topology-svg" viewBox="${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Smart condo dependency topology">${groups}${edgeSvg}${nodeSvg}</svg>`;
    bindPanZoom(host.querySelector('svg'));
    const fit=document.getElementById('topologyFit'); if(fit)fit.disabled=false;
    host.querySelectorAll('[data-topology-node]').forEach(element=>{
      const select=()=>{state.selected=element.dataset.topologyNode;renderDetail(nodeById(state.selected));renderGraph(state.data?.nodes||[],false);};
      element.onclick=select;
      element.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}};
    });
  }

  function fitToView(showFeedback=false) {
    const svg=document.querySelector('#topologyGraph .topology-svg');
    if(!svg||!state.fitted)return;
    state.viewBox={...state.fitted};
    svg.setAttribute('viewBox',`${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}`);
    if(showFeedback)window.toast?.('Topology fitted to view');
  }

  function bindPanZoom(svg) {
    if(!svg)return;
    svg.onwheel=event=>{event.preventDefault();const box=state.viewBox||state.fitted;if(!box)return;const factor=event.deltaY>0?1.12:.88;const nextW=Math.max(box.w*.55,Math.min(box.w*2.5,box.w*factor));const nextH=nextW*(box.h/box.w);state.viewBox={x:box.x+(box.w-nextW)/2,y:box.y+(box.h-nextH)/2,w:nextW,h:nextH};svg.setAttribute('viewBox',`${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}`);};
    svg.onpointerdown=event=>{state.dragging=true;state.dragStart={x:event.clientX,y:event.clientY,box:{...(state.viewBox||state.fitted)}};svg.setPointerCapture(event.pointerId);};
    svg.onpointermove=event=>{if(!state.dragging||!state.dragStart)return;const rect=svg.getBoundingClientRect(),start=state.dragStart;const dx=(event.clientX-start.x)*start.box.w/rect.width,dy=(event.clientY-start.y)*start.box.h/rect.height;state.viewBox={...start.box,x:start.box.x-dx,y:start.box.y-dy};svg.setAttribute('viewBox',`${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}`);};
    svg.onpointerup=()=>{state.dragging=false;state.dragStart=null;};
    svg.onpointercancel=svg.onpointerup;
  }

  function renderTopology(forceFit=false) {
    const data=state.data,roots=document.getElementById('topologyRoots'),events=document.getElementById('topologyEvents'),score=document.getElementById('topologyHealth');
    if(!roots||!events||!score)return;
    if(!data){const graph=document.getElementById('topologyGraph');if(graph)graph.innerHTML='<div class="empty">Topology data is not available.</div>';return;}
    score.innerHTML=`<strong>${safe(data.overall_health??0)}%</strong><span>Overall Health · ${safe(data.measured_node_count??0)} measured</span>`;
    roots.innerHTML=data.root_causes?.length?data.root_causes.map(item=>`<div class="root-cause bad"><strong>Root Cause · ${safe(item.label)}</strong><small>${safe(item.message)} · Affected: ${safe((item.affected||[]).join(', ')||'None')}</small></div>`).join(''):'<div class="root-cause"><strong>No confirmed root cause</strong><small>Unknown and unconfigured nodes are excluded from health scoring.</small></div>';
    renderGraph(data.nodes||[],forceFit);
    events.innerHTML=data.events?.length?data.events.map(item=>`<div class="event-row"><time>${safe(window.shortTime(item.ts))}</time><strong>${safe(item.message)}</strong></div>`).join(''):'<div class="empty">No recent topology events.</div>';
    renderDetail(nodeById(state.selected));
  }

  window.refresh=async function refreshWithTopology(){await Promise.allSettled([originalRefresh(),loadTopology()]);window.renderPage(window.currentPage());};
  window.renderPage=function renderPageWithTopology(page=window.currentPage()){originalRenderPage(page);if(page==='topology')renderTopology();};
  document.querySelectorAll('[data-nav]').forEach(button=>button.onclick=()=>window.nav(button.dataset.nav));
  document.getElementById('topologyFit')?.addEventListener('click',()=>fitToView(true));
  document.getElementById('topologyDetailClose')?.addEventListener('click',()=>{state.selected=null;renderDetail(null);renderGraph(state.data?.nodes||[],false);});
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(window.currentPage()==='topology'){state.viewBox=null;renderTopology(true);}},140);},{passive:true});
  loadTopology().then(()=>window.renderPage(window.currentPage()));
})();
