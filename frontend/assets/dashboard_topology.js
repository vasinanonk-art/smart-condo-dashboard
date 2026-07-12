(() => {
  'use strict';

  function installTopologyUi() {
    if (!document.querySelector('link[href="/assets/dashboard_topology.css"]')) {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = '/assets/dashboard_topology.css';
      document.head.appendChild(link);
    }
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
      section.innerHTML = `<div class="topology-summary"><div id="topologyHealth" class="card health-score"></div><div id="topologyRoots" class="root-list"></div></div><div class="topology-shell"><div><div class="card"><div class="card-head"><h2>Live Dependency Graph</h2></div><div id="topologyGraph" class="topology-graph"></div></div><div class="card" style="margin-top:16px"><div class="card-head"><h2>Recent Events</h2></div><div id="topologyEvents" class="event-list"></div></div></div><aside class="card topology-detail"><div class="card-head"><h2>Node Details</h2></div><div id="topologyDetail"><div class="empty">Select a node to inspect status and diagnostics.</div></div></aside></div>`;
      document.querySelector('.main')?.appendChild(section);
    }
  }

  installTopologyUi();

  const topologyState = {data:null, selected:null};
  const originalRefresh = refresh;
  const originalRenderPage = renderPage;
  const originalNav = nav;
  const originalRenderOverview = renderOverview;
  const healthClass = value => ['healthy','warning','offline','unknown'].includes(String(value)) ? String(value) : 'unknown';
  const tvPhysicalOnline = state => state?.tv_online === true || (state?.tv_online === undefined && state?.online === true);
  const tvValue = (value, fallback='Not available') => value === null || value === undefined || value === '' ? fallback : value;
  const tvStatusText = state => {
    if (tvPhysicalOnline(state)) return 'Online';
    if (state?.bridge_online && (state?.tv_online === null || state?.tv_online === undefined)) return 'Bridge Online / TV State Unknown';
    if (state?.bridge_online) return 'TV Offline / Bridge Online';
    if (state?.tv_online === false) return 'Offline';
    return 'Unknown';
  };

  async function loadTopology() {
    try {
      const payload = await get('/api/topology');
      topologyState.data = payload;
      if (payload?.tv) {
        const previous = S.tv.lastValid || {};
        S.tv.lastValid = {...previous, ...payload.tv, ts:payload.tv.last_update_ts||previous.ts, last_update_ts:payload.tv.last_update_ts||previous.last_update_ts};
      }
    } catch (error) {
      console.warn('Topology refresh failed:', error.message);
    }
  }

  refresh = async function refreshWithTopology() {
    await Promise.allSettled([originalRefresh(), loadTopology()]);
    renderPage(currentPage());
  };

  nav = function topologyNav(page) {
    originalNav(page);
    if (page === 'topology' && $('pageTitle')) $('pageTitle').textContent = 'Topology';
  };

  renderPage = function topologyRenderPage(page=currentPage()) {
    originalRenderPage(page);
    if (page === 'topology') renderTopology();
  };

  renderOverview = function overviewWithTv() {
    originalRenderOverview();
    const host = $('overviewMetrics');
    if (!host || host.querySelector('[data-overview-tv]')) return;
    const tvState = S.tv.lastValid;
    const online = tvPhysicalOnline(tvState);
    const cls = online ? 'ok' : tvState?.bridge_online ? 'warn' : 'bad';
    host.insertAdjacentHTML('beforeend', `<div class="card metric" data-overview-tv><div class="label">LG TV Status</div><div class="value ${cls}">${safeText(tvStatusText(tvState))}</div><div class="sub">${safeText(tvValue(tvState?.app || tvState?.input))} · Updated ${safeText(when(tvState?.last_update_ts || tvState?.ts))}</div></div>`);
  };

  renderEntertainment = function renderSynchronizedEntertainment() {
    const host = $('tvButtons'); if (!host) return;
    const tvState = S.tv.lastValid || {};
    const online = tvPhysicalOnline(tvState);
    const statusItems = [
      ['Status', tvStatusText(tvState), online ? 'ok' : tvState.bridge_online ? 'warn' : 'bad'],
      ['Bridge', tvState.bridge_online === true ? 'Online' : tvState.bridge_online === false ? 'Offline' : 'Unknown', tvState.bridge_online ? 'ok' : ''],
      ['Current App', tvValue(tvState.app), ''],
      ['Input', tvValue(tvState.input), ''],
      ['Volume', tvValue(tvState.volume), ''],
      ['Mute', tvState.mute === true ? 'Muted' : tvState.mute === false ? 'Sound on' : tvValue(tvState.mute), ''],
      ['Last Update', when(tvState.last_update_ts || tvState.ts), ''],
    ];
    host.innerHTML = `<div class="tv-dashboard"><section class="tv-section"><h3 class="tv-section-title">TV Status</h3><div class="tv-status-grid">${statusItems.map(([label,value,cls])=>`<div class="tv-status-item"><span>${safeText(label)}</span><strong class="${cls}">${safeText(value)}</strong></div>`).join('')}</div></section><section class="tv-section"><h3 class="tv-section-title">Controls</h3><div class="tv-section-grid">${TV_COMMANDS.map(([label,command])=>`<button class="btn ${command==='power_off'?'danger':command==='power_on'?'primary':'ghost'}" data-tv-command="${command}">${safeText(label)}</button>`).join('')}</div></section></div>`;
  };

  function nodeById(id) { return topologyState.data?.nodes?.find(node => node.id === id) || null; }

  function renderDetail(node) {
    const host = $('topologyDetail'); if (!host) return;
    if (!node) { host.innerHTML = '<div class="empty">Select a node to inspect status and diagnostics.</div>'; return; }
    const diagnostics = node.diagnostics && typeof node.diagnostics === 'object' ? Object.entries(node.diagnostics) : [];
    host.innerHTML = `<div class="kv"><span>Status</span><strong class="${healthClass(node.health)}">${safeText(node.health||'unknown')}</strong></div><div class="kv"><span>Online</span><strong>${node.online===null||node.online===undefined?'Unknown':node.online?'Yes':'No'}</strong></div><div class="kv"><span>Latency</span><strong>${node.latency_ms===null||node.latency_ms===undefined?'Unknown':`${safeText(node.latency_ms)} ms`}</strong></div><div class="kv"><span>Last Update</span><strong>${safeText(when(node.last_update_ts))}</strong></div><div class="kv"><span>Dependencies</span><strong>${safeText((node.dependencies||[]).join(', ')||'None')}</strong></div><div class="kv"><span>Dependents</span><strong>${safeText((node.dependents||[]).join(', ')||'None')}</strong></div><div class="kv"><span>Capabilities</span><strong>${safeText((node.capabilities||[]).join(', ')||'None')}</strong></div>${diagnostics.map(([key,value])=>`<div class="kv"><span>${safeText(key)}</span><strong>${safeText(typeof value==='object'?JSON.stringify(value):value)}</strong></div>`).join('')}`;
  }

  function renderTopology() {
    const data = topologyState.data;
    const graph=$('topologyGraph'), roots=$('topologyRoots'), events=$('topologyEvents'), score=$('topologyHealth');
    if (!graph||!roots||!events||!score) return;
    if (!data) { graph.innerHTML='<div class="empty">Topology data is not available.</div>'; return; }
    score.innerHTML=`<strong>${safeText(data.overall_health??0)}%</strong><span>Overall Health · ${safeText(data.measured_node_count??0)} measured / ${safeText(data.unknown_node_count??0)} unknown</span>`;
    roots.innerHTML=data.root_causes?.length?data.root_causes.map(item=>`<div class="root-cause bad"><strong>Root Cause · ${safeText(item.label)}</strong><small>${safeText(item.message)} · Affected: ${safeText((item.affected||[]).join(', ')||'None')}</small></div>`).join(''):'<div class="root-cause"><strong>No confirmed root cause</strong><small>Unknown nodes are excluded from health scoring.</small></div>';
    graph.innerHTML=(data.nodes||[]).map(node=>`<button class="topology-node ${healthClass(node.health)}" data-topology-node="${safeText(node.id)}"><span class="pulse"></span><span><span class="node-name">${safeText(node.name)}</span><span class="node-meta">${node.latency_ms===null||node.latency_ms===undefined?'Latency unknown':`${safeText(node.latency_ms)} ms`} · ${safeText(when(node.last_update_ts))}</span></span><span class="node-health">${safeText(node.health||'unknown')}</span><span class="packet-lane"></span></button>`).join('');
    graph.querySelectorAll('[data-topology-node]').forEach(button=>button.onclick=()=>{topologyState.selected=button.dataset.topologyNode;renderDetail(nodeById(topologyState.selected));});
    events.innerHTML=data.events?.length?data.events.map(item=>`<div class="event-row"><time>${safeText(shortTime(item.ts))}</time><strong>${safeText(item.message)}</strong></div>`).join(''):'<div class="empty">No recent topology events.</div>';
    renderDetail(nodeById(topologyState.selected));
  }

  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => nav(button.dataset.nav));
  loadTopology().then(() => renderPage(currentPage()));
})();
