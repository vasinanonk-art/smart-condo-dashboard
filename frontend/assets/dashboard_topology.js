(() => {
  'use strict';
  if (window.__dashboardTopologyInstalled) return;
  window.__dashboardTopologyInstalled = true;

  function installUi(){
    document.querySelectorAll('.nav,.mobile-nav').forEach(host=>{if(host.querySelector('[data-nav="topology"]'))return;const button=document.createElement('button');button.dataset.nav='topology';button.dataset.short='NC';button.textContent='Topology';host.appendChild(button);});
    if(document.querySelector('[data-page="topology"]'))return;
    const section=document.createElement('section');section.className='page';section.dataset.page='topology';section.innerHTML=`<div class="topology-summary"><div id="topologyHealth" class="card health-score"></div><div id="topologyRoots" class="root-list"></div></div><div class="card topology-map-card"><div class="card-head"><h2>Live Dependency Graph</h2><button id="topologyFit" class="btn ghost" type="button" disabled>Fit to View</button></div><div id="topologyGraph" class="topology-map"></div></div><section id="topologyDetailCard" class="card topology-detail collapsed"><div class="card-head"><h2>Node Details</h2><button id="topologyDetailClose" class="btn ghost" type="button">Close</button></div><div id="topologyDetail"><div class="empty">Select a node to inspect status and diagnostics.</div></div></section><div class="card topology-events-card"><div class="card-head"><h2>Recent Events</h2></div><div id="topologyEvents" class="event-list"></div></div>`;document.querySelector('.main')?.appendChild(section);
  }
  installUi();

  const state={data:null,selected:null,viewBox:null,fitted:null,dragging:false,dragStart:null,layoutSignature:null};
  const originalRefresh=window.refresh,originalRenderPage=window.renderPage;
  const safe=value=>window.safeText?window.safeText(value):String(value??'');
  const object=value=>value&&typeof value==='object'&&!Array.isArray(value)?value:{};
  const list=value=>Array.isArray(value)?value.map(String):[];
  const healthClass=value=>['healthy','warning','offline','unknown'].includes(String(value))?String(value):'unknown';
  const SITE={internet:'cloud',cloudflare_wan:'cloud',condo_router:'condo',tinkerboard:'condo',dashboard:'condo',mqtt:'condo',sonoff:'condo',camera:'condo',electricity:'condo',tapo_ir:'condo',presence:'condo',lg_tv:'condo',tuya:'condo',pm25:'condo',zerotier_condo:'zerotier',zerotier_tunnel:'zerotier',zerotier_home:'zerotier',truenas:'home',home_assistant:'home'};
  const ORDER=['internet','cloudflare_wan','condo_router','tinkerboard','dashboard','mqtt','sonoff','camera','electricity','tapo_ir','presence','lg_tv','tuya','pm25','zerotier_condo','zerotier_tunnel','zerotier_home','truenas','home_assistant'];
  const GROUPS={cloud:['internet','cloudflare_wan'],condo:['condo_router','tinkerboard','dashboard','mqtt','sonoff','camera','electricity','tapo_ir','presence','lg_tv','tuya','pm25'],zerotier:['zerotier_condo','zerotier_tunnel','zerotier_home'],home:['truenas','home_assistant']};
  const OPERATIONAL_EDGES=[
    ['internet','cloudflare_wan','primary_dependency'],['cloudflare_wan','condo_router','primary_dependency'],['condo_router','tinkerboard','primary_dependency'],
    ['tinkerboard','dashboard','primary_dependency'],['tinkerboard','mqtt','primary_dependency'],['tinkerboard','sonoff','primary_dependency'],['tinkerboard','camera','primary_dependency'],['tinkerboard','electricity','primary_dependency'],['tinkerboard','tapo_ir','primary_dependency'],
    ['mqtt','presence','primary_dependency'],['mqtt','lg_tv','primary_dependency'],
    ['home_assistant','tuya','data_source'],['home_assistant','pm25','data_source'],
    ['tinkerboard','zerotier_condo','network_tunnel'],['zerotier_condo','zerotier_tunnel','network_tunnel'],['zerotier_tunnel','zerotier_home','network_tunnel'],['zerotier_home','truenas','network_tunnel'],['truenas','home_assistant','network_tunnel']
  ];

  function normalizeNode(raw,index){const node=object(raw),id=String(node.id||`unknown_node_${index}`),metadata=object(node.metadata),diagnostics=object(node.diagnostics);return{...node,id,name:String(node.name||node.label||id),health:healthClass(node.health),online:node.online===true?true:node.online===false?false:null,dependencies:list(node.dependencies),dependents:list(node.dependents),capabilities:list(node.capabilities),metadata,diagnostics,physical_site:node.physical_site||metadata.physical_site||SITE[id]||'condo',data_source:node.data_source||diagnostics.source||metadata.source||'unknown'};}
  function normalizeTopology(raw){const payload=object(raw?.data||raw),seen=new Set(),nodes=[];(Array.isArray(payload.nodes)?payload.nodes:[]).forEach((item,index)=>{const node=normalizeNode(item,index);if(!seen.has(node.id)){seen.add(node.id);nodes.push(node);}});return{...payload,nodes,root_causes:Array.isArray(payload.root_causes)?payload.root_causes:[],events:Array.isArray(payload.events)?payload.events:[]};}
  async function load(){try{state.data=normalizeTopology(await window.get('/api/topology'));}catch(error){console.error('Topology refresh failed',{name:error?.name||'Error',message:error?.message||'Unknown topology error'});}}
  function nodeById(id){return state.data?.nodes?.find(node=>node.id===id)||null;}
  function timeText(value){if(!value)return'Not available';const number=Number(value),date=new Date(Number.isFinite(number)?number*1000:Date.parse(value));return Number.isNaN(date.getTime())?'Not available':date.toLocaleString();}
  function renderDetail(node){const card=document.getElementById('topologyDetailCard'),host=document.getElementById('topologyDetail');if(!card||!host)return;if(!node){card.classList.add('collapsed');host.innerHTML='<div class="empty">Select a node to inspect status and diagnostics.</div>';return;}card.classList.remove('collapsed');const primary=[['Status',node.online==null?'Unknown':node.online?'Online':'Offline'],['Health',node.health],['Physical Site',node.physical_site],['Data Source',node.data_source],['Latency',node.latency_ms==null?'Unknown':`${node.latency_ms} ms`],['Last Update',timeText(node.last_update_ts)],['Dependencies',node.dependencies.join(', ')||'None'],['Dependents',node.dependents.join(', ')||'None'],['Capabilities',node.capabilities.join(', ')||'None']];const diagnostics=Object.entries(node.diagnostics);host.innerHTML=`<div class="topology-detail-grid">${primary.map(([label,value])=>`<div class="topology-detail-item"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`).join('')}</div><div class="topology-diagnostics"><h3>Diagnostics</h3><div class="topology-detail-grid">${diagnostics.map(([key,value])=>`<div class="topology-detail-item"><span>${safe(key)}</span><strong>${safe(typeof value==='object'?JSON.stringify(value):value)}</strong></div>`).join('')||'<div class="empty">No diagnostics.</div>'}</div></div>`;}

  const makeLayout=(nodes,available)=>{
    const mode=available<=640?'mobile':available<=1050?'tablet':'desktop',present=new Set(nodes.map(node=>node.id)),positions={},nodeW=mode==='desktop'?164:mode==='tablet'?150:Math.min(288,available-32),nodeH=58;
    if(mode==='mobile'){
      let y=44;ORDER.filter(id=>present.has(id)).forEach(id=>{positions[id]=[Math.round((available-nodeW)/2),y];y+=98;});
      return{mode,width:available,height:y+30,nodeW,nodeH,positions};
    }
    const designW=1800,scale=Math.min(1,Math.max(.76,available/designW)),width=Math.max(1320,Math.round(designW*scale));
    const sx=value=>Math.round(value*width/designW),place=(id,x,y)=>{if(present.has(id))positions[id]=[sx(x),sx(y)];};
    place('internet',90,70);place('cloudflare_wan',90,170);
    place('condo_router',90,330);place('tinkerboard',90,440);
    [['dashboard',40],['mqtt',220],['sonoff',400],['camera',580],['electricity',760],['tapo_ir',940]].forEach(([id,x])=>place(id,x,600));
    [['presence',220],['lg_tv',400],['tuya',760],['pm25',940]].forEach(([id,x],index)=>place(id,x,index<2?790:index===2?760:850));
    place('zerotier_condo',1280,330);place('zerotier_tunnel',1280,470);place('zerotier_home',1280,610);
    place('truenas',1580,610);place('home_assistant',1580,790);
    nodes.filter(node=>!positions[node.id]).forEach((node,index)=>place(node.id,1060,760+index*90));
    return{mode,width,height:sx(980),nodeW,nodeH,positions};
  };
  const rect=(id,l)=>{const p=l.positions[id];return p?{x:p[0],y:p[1],w:l.nodeW,h:l.nodeH}:null;};
  const port=(r,side)=>side==='top'?{x:r.x+r.w/2,y:r.y}:side==='bottom'?{x:r.x+r.w/2,y:r.y+r.h}:side==='left'?{x:r.x,y:r.y+r.h/2}:{x:r.x+r.w,y:r.y+r.h/2};
  const path=points=>points.filter((p,i)=>i===0||p.x!==points[i-1].x||p.y!==points[i-1].y).map((p,i)=>`${i?'L':'M'}${p.x},${p.y}`).join(' ');

  function buildRoutes(l){
    const result=[],has=id=>Boolean(l.positions[id]),add=(from,to,category,points,kind='edge')=>{if(has(from)&&has(to))result.push({from,to,category,points,d:path(points),kind});};
    if(l.mode==='mobile'){
      const left=18,right=l.width-18;
      OPERATIONAL_EDGES.forEach(([from,to,category],index)=>{if(!has(from)||!has(to))return;const a=rect(from,l),b=rect(to,l),start=port(a,'bottom'),end=port(b,'top');if(start.y<=end.y&&Math.abs(start.x-end.x)<2)add(from,to,category,[start,end]);else{const corridor=category==='network_tunnel'?right:left-(index%3)*8;add(from,to,category,[category==='network_tunnel'?port(a,'right'):port(a,'left'),{x:corridor,y:a.y+a.h/2},{x:corridor,y:b.y+b.h/2},category==='network_tunnel'?port(b,'right'):port(b,'left')]);}});
      return result;
    }
    const vertical=(from,to,category='primary_dependency')=>{const a=rect(from,l),b=rect(to,l);if(a&&b)add(from,to,category,[port(a,'bottom'),port(b,'top')]);};
    vertical('internet','cloudflare_wan');vertical('cloudflare_wan','condo_router');vertical('condo_router','tinkerboard');

    if(has('tinkerboard')){
      const source=rect('tinkerboard',l),start=port(source,'bottom'),children=['dashboard','mqtt','sonoff','camera','electricity','tapo_ir'].filter(has),busY=Math.min(...children.map(id=>rect(id,l).y))-52;
      if(children.length){const centers=children.map(id=>port(rect(id,l),'top').x),minX=Math.min(...centers),maxX=Math.max(...centers);result.push({from:'tinkerboard',to:'service_bus',category:'primary_dependency',kind:'bus',points:[start,{x:start.x,y:busY},{x:minX,y:busY},{x:maxX,y:busY}],d:path([start,{x:start.x,y:busY},{x:minX,y:busY},{x:maxX,y:busY}])});children.forEach(id=>{const end=port(rect(id,l),'top');result.push({from:'tinkerboard',to:id,category:'primary_dependency',kind:'branch',points:[{x:end.x,y:busY},end],d:path([{x:end.x,y:busY},end])});});}
    }
    if(has('mqtt')){
      const source=rect('mqtt',l),start=port(source,'bottom'),children=['presence','lg_tv'].filter(has),busY=Math.min(...children.map(id=>rect(id,l).y))-48;
      if(children.length){const centers=children.map(id=>port(rect(id,l),'top').x),minX=Math.min(start.x,...centers),maxX=Math.max(start.x,...centers);result.push({from:'mqtt',to:'mqtt_bus',category:'primary_dependency',kind:'bus',points:[start,{x:start.x,y:busY},{x:minX,y:busY},{x:maxX,y:busY}],d:path([start,{x:start.x,y:busY},{x:minX,y:busY},{x:maxX,y:busY}])});children.forEach(id=>{const end=port(rect(id,l),'top');result.push({from:'mqtt',to:id,category:'primary_dependency',kind:'branch',points:[{x:end.x,y:busY},end],d:path([{x:end.x,y:busY},end])});});}
    }

    if(has('tinkerboard')&&has('zerotier_condo')){const a=port(rect('tinkerboard',l),'right'),b=port(rect('zerotier_condo',l),'left'),corridorX=rect('zerotier_condo',l).x-72,corridorY=a.y+82;add('tinkerboard','zerotier_condo','network_tunnel',[a,{x:a.x+42,y:a.y},{x:a.x+42,y:corridorY},{x:corridorX,y:corridorY},{x:corridorX,y:b.y},b]);}
    vertical('zerotier_condo','zerotier_tunnel','network_tunnel');vertical('zerotier_tunnel','zerotier_home','network_tunnel');
    if(has('zerotier_home')&&has('truenas')){const a=port(rect('zerotier_home',l),'right'),b=port(rect('truenas',l),'left'),midX=(a.x+b.x)/2;add('zerotier_home','truenas','network_tunnel',[a,{x:midX,y:a.y},{x:midX,y:b.y},b]);}
    vertical('truenas','home_assistant','network_tunnel');

    if(has('home_assistant')){
      const a=rect('home_assistant',l),start=port(a,'left'),corridorBase=rect('zerotier_condo',l).x-90;
      ['tuya','pm25'].filter(has).forEach((id,index)=>{const end=port(rect(id,l),'right'),corridorX=corridorBase-index*14;add('home_assistant',id,'data_source',[start,{x:corridorX,y:start.y},{x:corridorX,y:end.y},end]);});
    }
    return result;
  }

  function groupBox(ids,l,label){if(l.mode==='mobile')return null;const rs=ids.map(id=>rect(id,l)).filter(Boolean);if(!rs.length)return null;const pad=40,x=Math.min(...rs.map(r=>r.x))-pad,y=Math.min(...rs.map(r=>r.y))-pad,right=Math.max(...rs.map(r=>r.x+r.w))+pad,bottom=Math.max(...rs.map(r=>r.y+r.h))+pad;return{x,y,right,bottom,label,svg:`<rect class="topology-group" x="${x}" y="${y}" width="${right-x}" height="${bottom-y}" rx="20"/><text class="topology-group-label" x="${x+18}" y="${y+24}">${safe(label)}</text>`};}
  const segments=points=>points.slice(1).map((point,index)=>({a:points[index],b:point}));
  const intersectsRect=(segment,r,margin=4)=>{const minX=Math.min(segment.a.x,segment.b.x),maxX=Math.max(segment.a.x,segment.b.x),minY=Math.min(segment.a.y,segment.b.y),maxY=Math.max(segment.a.y,segment.b.y);return maxX>r.x-margin&&minX<r.x+r.w+margin&&maxY>r.y-margin&&minY<r.y+r.h+margin;};
  function validateGeometry(nodes,l,routes,boxes){const errors=[],nodeRects=new Map(nodes.map(node=>[node.id,rect(node.id,l)]).filter(([,value])=>value));const seen=new Set();routes.forEach(route=>{const key=`${route.from}>${route.to}:${route.kind}`;if(seen.has(key))errors.push({type:'duplicate_edge',edge:key});seen.add(key);segments(route.points).forEach(segment=>nodeRects.forEach((r,id)=>{if(id!==route.from&&id!==route.to&&intersectsRect(segment,r,2))errors.push({type:'edge_node_intersection',edge:`${route.from}>${route.to}`,node:id});}));});for(let i=0;i<boxes.length;i+=1)for(let j=i+1;j<boxes.length;j+=1){const a=boxes[i],b=boxes[j],overlap=!(a.right+48<=b.x||b.right+48<=a.x||a.bottom+48<=b.y||b.bottom+48<=a.y);if(overlap)errors.push({type:'group_overlap',groups:[a.label,b.label]});}const available=new Set(nodes.map(node=>node.id));OPERATIONAL_EDGES.forEach(([from,to])=>{if(available.has(to)&&!available.has(from))errors.push({type:'orphan_dependency',from,to});});return errors;}
  function edgeHealth(edge,map){const a=map.get(edge.from),b=map.get(edge.to);if(a?.health==='offline'||b?.health==='offline')return'offline';if(a?.health==='warning'||b?.health==='warning')return'warning';if(a?.health==='healthy'&&b?.health==='healthy')return'healthy';return'unknown';}
  function boundsOf(l,routes,boxes){const xs=[0,l.width],ys=[0,l.height];Object.values(l.positions).forEach(([x,y])=>{xs.push(x,x+l.nodeW);ys.push(y,y+l.nodeH);});routes.forEach(route=>route.points.forEach(point=>{xs.push(point.x);ys.push(point.y);}));boxes.forEach(box=>{xs.push(box.x,box.right);ys.push(box.y,box.bottom);});const pad=28,minX=Math.min(...xs)-pad,minY=Math.min(...ys)-pad,maxX=Math.max(...xs)+pad,maxY=Math.max(...ys)+pad;return{x:minX,y:minY,w:maxX-minX,h:maxY-minY};}

  function bindPanZoom(svg){svg.onwheel=event=>{event.preventDefault();const box=state.viewBox||state.fitted;if(!box)return;const factor=event.deltaY>0?1.12:.88,nextW=Math.max(box.w*.55,Math.min(box.w*2.5,box.w*factor)),nextH=nextW*(box.h/box.w),next={x:box.x+(box.w-nextW)/2,y:box.y+(box.h-nextH)/2,w:nextW,h:nextH};state.viewBox=next;svg.setAttribute('viewBox',`${next.x} ${next.y} ${next.w} ${next.h}`);};svg.onpointerdown=event=>{state.dragging=true;state.dragStart={x:event.clientX,y:event.clientY,box:{...(state.viewBox||state.fitted)}};svg.setPointerCapture(event.pointerId);};svg.onpointermove=event=>{if(!state.dragging||!state.dragStart)return;const r=svg.getBoundingClientRect(),s=state.dragStart,dx=(event.clientX-s.x)*s.box.w/r.width,dy=(event.clientY-s.y)*s.box.h/r.height,next={...s.box,x:s.box.x-dx,y:s.box.y-dy};state.viewBox=next;svg.setAttribute('viewBox',`${next.x} ${next.y} ${next.w} ${next.h}`);};svg.onpointerup=()=>{state.dragging=false;state.dragStart=null;};svg.onpointercancel=svg.onpointerup;}
  function fit(showFeedback=false){const svg=document.querySelector('#topologyGraph .topology-svg');if(!svg||!state.fitted)return;state.viewBox={...state.fitted};svg.setAttribute('viewBox',`${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}`);if(showFeedback){const button=document.getElementById('topologyFit');if(button){button.classList.add('active');setTimeout(()=>button.classList.remove('active'),650);}}}

  function renderGraph(nodes,forceFit=false){const host=document.getElementById('topologyGraph');if(!host)return;const l=makeLayout(nodes,Math.max(320,host.clientWidth||1200)),routes=buildRoutes(l),boxes=[groupBox(GROUPS.cloud,l,'CLOUD'),groupBox(GROUPS.condo,l,'CONDO'),groupBox(GROUPS.zerotier,l,'ZEROTIER'),groupBox(GROUPS.home,l,'HOME')].filter(Boolean),map=new Map(nodes.map(node=>[node.id,node]));const errors=validateGeometry(nodes,l,routes,boxes);if(errors.length)console.warn('Topology geometry diagnostics',errors);const signature=JSON.stringify([l.mode,l.positions,routes.map(route=>[route.from,route.to,route.category,route.d])]);if(state.layoutSignature&&forceFit&&state.layoutSignature!==signature)console.info('Topology layout changed after responsive relayout',{mode:l.mode});state.layoutSignature=signature;const groups=boxes.map(box=>box.svg).join('');const edges=routes.map(edge=>`<path class="topology-edge ${edgeHealth(edge,map)} topology-edge-${edge.category}${edge.kind==='bus'?' topology-bus':''}" d="${edge.d}"/>`).join('');const nodeSvg=nodes.map(node=>{const p=l.positions[node.id];if(!p)return'';const status=node.online===true?'Online':node.online===false?'Offline':node.health==='warning'?'Warning':'Unknown',selected=node.id===state.selected?' selected':'';return`<g class="topology-node-svg ${healthClass(node.health)}${selected}" data-topology-node="${safe(node.id)}" tabindex="0" transform="translate(${p[0]} ${p[1]})"><rect class="topology-node-bg" width="${l.nodeW}" height="${l.nodeH}"/><circle class="topology-node-dot" cx="17" cy="17" r="6"/><text class="topology-node-name" x="30" y="20">${safe(node.name)}</text><text class="topology-node-status" x="17" y="41">${safe(status)}${node.latency_ms!=null?` · ${safe(node.latency_ms)} ms`:''}</text></g>`;}).join('');state.fitted=boundsOf(l,routes,boxes);if(forceFit||!state.viewBox)state.viewBox={...state.fitted};host.innerHTML=`<svg class="topology-svg" viewBox="${state.viewBox.x} ${state.viewBox.y} ${state.viewBox.w} ${state.viewBox.h}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Smart condo dependency topology">${groups}${edges}${nodeSvg}</svg>`;const svg=host.querySelector('svg');bindPanZoom(svg);const button=document.getElementById('topologyFit');if(button)button.disabled=false;host.querySelectorAll('[data-topology-node]').forEach(element=>{const select=()=>{state.selected=element.dataset.topologyNode;renderDetail(nodeById(state.selected));renderGraph(state.data?.nodes||[],false);};element.onclick=select;element.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}};});}

  function renderTopology(forceFit=false){const data=state.data,roots=document.getElementById('topologyRoots'),events=document.getElementById('topologyEvents'),score=document.getElementById('topologyHealth');if(!roots||!events||!score)return;if(!data){document.getElementById('topologyGraph').innerHTML='<div class="empty">Topology data is not available.</div>';return;}score.innerHTML=`<strong>${safe(data.overall_health??0)}%</strong><span>Overall Health · ${safe(data.measured_node_count??0)} measured</span>`;roots.innerHTML=data.root_causes?.length?data.root_causes.map(item=>`<div class="root-cause bad"><strong>Root Cause · ${safe(item.label)}</strong><small>${safe(item.message)} · Affected: ${safe((item.affected||[]).join(', ')||'None')}</small></div>`).join(''):'<div class="root-cause"><strong>No confirmed root cause</strong><small>Unknown and unconfigured nodes are excluded from health scoring.</small></div>';renderGraph(data.nodes||[],forceFit);events.innerHTML=data.events?.length?data.events.map(item=>`<div class="event-row"><time>${safe(window.shortTime(item.ts))}</time><strong>${safe(item.message)}</strong></div>`).join(''):'<div class="empty">No recent topology events.</div>';renderDetail(nodeById(state.selected));}

  window.refresh=async function refreshWithTopology(){await Promise.allSettled([originalRefresh(),load()]);if(window.currentPage()==='topology')renderTopology(true);};
  window.renderPage=function renderPageWithTopology(page=window.currentPage()){originalRenderPage(page);if(page==='topology')renderTopology(true);};
  document.querySelectorAll('[data-nav]').forEach(button=>button.onclick=()=>window.nav(button.dataset.nav));
  document.getElementById('topologyFit')?.addEventListener('click',()=>{renderTopology(true);fit(true);});
  document.getElementById('topologyDetailClose')?.addEventListener('click',()=>{state.selected=null;renderDetail(null);renderGraph(state.data?.nodes||[],false);});
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(window.currentPage()==='topology'){state.viewBox=null;renderTopology(true);}},160);},{passive:true});
  load().then(()=>{if(window.currentPage()==='topology')renderTopology(true);});
  window.DashboardTopologyGeometry=Object.freeze({makeLayout,buildRoutes,validateGeometry,OPERATIONAL_EDGES});
})();
