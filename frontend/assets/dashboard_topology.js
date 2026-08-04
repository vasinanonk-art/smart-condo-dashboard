(() => {
  'use strict';
  if (window.__dashboardTopologyInstalled) return;
  window.__dashboardTopologyInstalled = true;

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const array = value => Array.isArray(value) ? value.map(String) : [];
  const HEALTH = new Set(['healthy', 'warning', 'offline']);
  const SITE = {
    internet:'cloud', cloudflare_wan:'cloud', condo_router:'condo', tinkerboard:'condo',
    dashboard:'condo', mqtt:'condo', sonoff:'condo', camera:'condo', electricity:'condo',
    tapo_ir:'condo', presence:'condo', lg_tv:'condo', tuya:'condo', pm25:'condo',
    zerotier_condo:'zerotier', zerotier_tunnel:'zerotier', zerotier_home:'zerotier',
    truenas:'home', home_assistant:'home'
  };
  const ORDER = Object.keys(SITE);
  const GROUPS = {
    cloud:['internet','cloudflare_wan'],
    condo:['condo_router','tinkerboard','dashboard','mqtt','sonoff','camera','electricity','tapo_ir','presence','lg_tv','tuya','pm25'],
    zerotier:['zerotier_condo','zerotier_tunnel','zerotier_home'],
    home:['truenas','home_assistant']
  };
  const LAYERS = [
    ['internet'],
    ['cloudflare_wan'],
    ['condo_router'],
    ['tinkerboard'],
    ['dashboard','mqtt','sonoff','camera','electricity','tapo_ir','zerotier_condo'],
    ['presence','lg_tv','zerotier_tunnel'],
    ['zerotier_home'],
    ['truenas'],
    ['home_assistant'],
    ['tuya','pm25']
  ];
  const LAYER_LABELS = ['Internet','Edge','Condo network','Dashboard host','Services & devices','Connected devices','Secure tunnel','Home storage','Home automation','Sensor sources'];
  const ICONS = {
    internet:'☁', cloudflare_wan:'◆', condo_router:'⌁', tinkerboard:'▣', dashboard:'⌂',
    mqtt:'⇄', sonoff:'◫', camera:'◉', electricity:'ϟ', tapo_ir:'⌁', presence:'●',
    lg_tv:'▤', tuya:'◇', pm25:'≈', zerotier_condo:'Z', zerotier_tunnel:'Z',
    zerotier_home:'Z', truenas:'▥', home_assistant:'⌂'
  };
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

  function install() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host => {
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
    section.innerHTML = `
      <div class="topology-summary">
        <div id="topologyHealth" class="card topology-overview" aria-live="polite"></div>
        <div id="topologyRoots" class="root-list"></div>
      </div>
      <div class="card topology-map-card">
        <div class="card-head"><div><h2>System Connections</h2><p class="muted">Select a device for technical details</p></div><button id="topologyFit" class="btn ghost" type="button" disabled>Fit to View</button></div>
        <div id="topologyGraph" class="topology-map"></div>
      </div>
      <section id="topologyDetailCard" class="card topology-detail collapsed">
        <div class="card-head"><h2>Device Details</h2><button id="topologyDetailClose" class="btn ghost" type="button">Close</button></div>
        <div id="topologyDetail"></div>
      </section>
      <div class="card topology-events-card"><div class="card-head"><h2>Recent Events</h2></div><div id="topologyEvents" class="event-list"></div></div>`;
    document.querySelector('.main')?.appendChild(section);
  }

  install();
  const state = {data:null, selected:null, fitted:null};
  const originalRefresh = window.refresh;
  const originalRender = window.renderPage;

  function normalize(raw) {
    const payload = object(raw?.data || raw);
    const seen = new Set();
    const nodes = [];
    (Array.isArray(payload.nodes) ? payload.nodes : []).forEach((candidate, index) => {
      const record = object(candidate);
      const id = String(record.id || `unknown_${index}`);
      if (seen.has(id)) return;
      seen.add(id);
      const metadata = object(record.metadata);
      const diagnostics = object(record.diagnostics);
      nodes.push({
        ...record,
        id,
        name:String(record.name || record.label || id),
        health:HEALTH.has(record.health) ? record.health : 'unknown',
        online:record.online === true ? true : record.online === false ? false : null,
        dependencies:array(record.dependencies),
        dependents:array(record.dependents),
        capabilities:array(record.capabilities),
        metadata,
        diagnostics,
        physical_site:record.physical_site || metadata.physical_site || SITE[id] || 'condo'
      });
    });
    return {...payload, nodes, root_causes:Array.isArray(payload.root_causes) ? payload.root_causes : [], events:Array.isArray(payload.events) ? payload.events : []};
  }

  async function load() {
    try {
      state.data = normalize(await window.get('/api/topology'));
    } catch (error) {
      console.error('Topology refresh failed', {name:error?.name || 'Error'});
    }
  }

  const rect = (id, layout) => {
    const point = layout.pos[id];
    return point ? {x:point[0], y:point[1], w:layout.w, h:layout.h} : null;
  };
  const port = (box, side) => side === 'top'
    ? {x:box.x + box.w / 2, y:box.y}
    : {x:box.x + box.w / 2, y:box.y + box.h};
  const path = points => points.map((point, index) => `${index ? 'L' : 'M'}${point.x},${point.y}`).join(' ');

  function layout(nodes, availableWidth) {
    const width = Math.max(320, Math.round(availableWidth));
    const mobile = width <= 640;
    const gapX = mobile ? 12 : 18;
    const w = mobile ? Math.min(292, width - 32) : width <= 1050 ? 142 : 158;
    const h = 56;
    const side = mobile ? 16 : 32;
    const labelHeight = 24;
    const rowGap = 14;
    const layerGap = mobile ? 28 : 30;
    const maxColumns = Math.max(1, Math.floor((width - side * 2 + gapX) / (w + gapX)));
    const present = new Set(nodes.map(node => node.id));
    const layers = LAYERS.map(ids => ids.filter(id => present.has(id)));
    const unknown = nodes.filter(node => !LAYERS.some(ids => ids.includes(node.id))).map(node => node.id);
    if (unknown.length) layers.push(unknown);
    const pos = {};
    const layerBounds = [];
    let y = 24;
    layers.forEach((ids, layerIndex) => {
      if (!ids.length) return;
      const columns = Math.min(maxColumns, ids.length);
      const rows = Math.ceil(ids.length / columns);
      const layerTop = y;
      ids.forEach((id, index) => {
        const row = Math.floor(index / columns);
        const rowStart = row * columns;
        const rowCount = Math.min(columns, ids.length - rowStart);
        const rowWidth = rowCount * w + (rowCount - 1) * gapX;
        const rowX = Math.max(side, (width - rowWidth) / 2);
        const column = index - rowStart;
        pos[id] = [rowX + column * (w + gapX), y + labelHeight + row * (h + rowGap)];
      });
      const contentHeight = labelHeight + rows * h + Math.max(0, rows - 1) * rowGap;
      const layerBottom = layerTop + contentHeight + 14;
      layerBounds.push({index:layerIndex, label:LAYER_LABELS[layerIndex] || 'Other devices', x:8, y:layerTop, width:width - 16, height:layerBottom - layerTop, ids});
      y = layerBottom + layerGap;
    });
    return {mobile, width, height:Math.max(y - layerGap + 24, 180), w, h, pos, layers:layerBounds};
  }

  function routes(layout) {
    const output = [];
    const has = id => Boolean(layout.pos[id]);
    EDGES.forEach(([from, to, category], edgeIndex) => {
      if (!has(from) || !has(to)) return;
      const source = rect(from, layout);
      const target = rect(to, layout);
      const start = port(source, 'bottom');
      const end = port(target, 'top');
      const available = Math.max(8, end.y - start.y);
      const laneOffset = Math.min(18, 6 + (edgeIndex % 4) * 4);
      const laneY = start.y + Math.min(available / 2, laneOffset);
      const points = [start, {x:start.x, y:laneY}, {x:end.x, y:laneY}, end];
      output.push({from, to, cat:category, pts:points, d:path(points)});
    });
    return output;
  }

  function group(_ids, _layout, _label) {
    return null;
  }

  function diagnostics(nodes, layout, edges) {
    const ids = new Set(nodes.map(node => node.id));
    const errors = [];
    EDGES.forEach(([from, to]) => {
      if (ids.has(from) && ids.has(to) && !edges.some(edge => edge.from === from && edge.to === to)) {
        errors.push({type:'missing_required_edge', edge:`${from}->${to}`});
      }
    });
    edges.forEach(edge => {
      const first = edge.pts?.[0];
      const last = edge.pts?.[edge.pts.length - 1];
      if (!first || !last || first.x == null || first.y == null || last.x == null || last.y == null) {
        errors.push({type:'disconnected_edge', edge:`${edge.from}->${edge.to}`});
      }
    });
    const boxes = nodes.map(node => ({id:node.id, box:rect(node.id, layout)})).filter(item => item.box);
    boxes.forEach((first, index) => boxes.slice(index + 1).forEach(second => {
      const a = first.box;
      const b = second.box;
      if (!(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)) {
        errors.push({type:'node_overlap', nodes:[first.id, second.id]});
      }
    }));
    return errors;
  }

  function summary(nodes) {
    const counts = {healthy:0, warning:0, critical:0, unknown:0};
    nodes.forEach(node => {
      if (node.health === 'healthy') counts.healthy += 1;
      else if (node.health === 'warning') counts.warning += 1;
      else if (node.health === 'offline') counts.critical += 1;
      else counts.unknown += 1;
    });
    const stateName = counts.critical ? 'Critical' : counts.warning ? 'Attention' : counts.unknown ? 'Unknown' : 'Healthy';
    const stateClass = counts.critical ? 'critical' : counts.warning ? 'warning' : counts.unknown ? 'unknown' : 'healthy';
    return {counts, state:stateName, className:stateClass};
  }

  function linkHealth(from, to, map) {
    const first = map.get(from);
    const second = map.get(to);
    if (first?.health === 'offline' || second?.health === 'offline') return 'broken';
    if (first?.health === 'warning' || second?.health === 'warning') return 'warning';
    if (first?.health === 'healthy' && second?.health === 'healthy') return 'healthy';
    return 'unknown';
  }

  function statusLabel(node) {
    if (node.health === 'healthy') return 'Healthy';
    if (node.health === 'warning') return 'Attention';
    if (node.health === 'offline') return 'Offline';
    return 'Unknown';
  }

  function readable(value, fallback = 'Not available') {
    if (value == null || value === '') return fallback;
    if (typeof value === 'object') return fallback;
    return String(value);
  }

  function detail(node, names = new Map()) {
    const card = document.getElementById('topologyDetailCard');
    const host = document.getElementById('topologyDetail');
    if (!card || !host) return;
    if (!node) {
      card.classList.add('collapsed');
      host.innerHTML = '';
      return;
    }
    const diagnostics = object(node.diagnostics);
    const metadata = object(node.metadata);
    const dependencies = node.dependencies.map(id => names.get(id) || id).join(', ') || 'None';
    const fields = [
      ['Status', statusLabel(node)],
      ['Location', readable(node.physical_site)],
      ['Dependencies', dependencies],
      ['Provider', readable(diagnostics.provider || metadata.provider || diagnostics.source || node.source)],
      ['Protocol', readable(diagnostics.protocol || metadata.protocol || node.protocol)],
      ['IP address', readable(diagnostics.runtime_ip || diagnostics.configured_ip || metadata.ip_address || node.runtime_ip)],
      ['Latency', diagnostics.latency_ms != null || node.latency_ms != null ? `${readable(diagnostics.latency_ms ?? node.latency_ms)} ms` : 'Not available'],
      ['Last error', readable(diagnostics.last_error || diagnostics.error || node.last_error)]
    ];
    card.classList.remove('collapsed');
    host.innerHTML = `<div class="topology-detail-grid">${fields.map(([label, value]) => `<div class="topology-detail-item"><span>${safe(label)}</span><strong>${safe(value)}</strong></div>`).join('')}</div>`;
  }

  function marker(edge) {
    if (edge.health !== 'broken') return '';
    const point = pathMidpoint(edge.pts);
    const {x, y} = point;
    return `<g class="topology-link-break" transform="translate(${x} ${y})" aria-hidden="true"><circle r="9"/><path d="M-4,-4 L4,4 M4,-4 L-4,4"/></g>`;
  }

  function pathMidpoint(points) {
    const segments = points.slice(1).map((point, index) => {
      const start = points[index];
      return {start, point, length:Math.hypot(point.x - start.x, point.y - start.y)};
    });
    const target = segments.reduce((sum, segment) => sum + segment.length, 0) / 2;
    let traversed = 0;
    for (const segment of segments) {
      if (traversed + segment.length >= target && segment.length) {
        const ratio = (target - traversed) / segment.length;
        return {
          x:segment.start.x + (segment.point.x - segment.start.x) * ratio,
          y:segment.start.y + (segment.point.y - segment.start.y) * ratio
        };
      }
      traversed += segment.length;
    }
    return points[0] || {x:0, y:0};
  }

  function render() {
    const host = document.getElementById('topologyGraph');
    if (!host || !state.data) return;
    const nodes = state.data.nodes;
    const layoutResult = layout(nodes, Math.max(320, host.clientWidth || 1100));
    const map = new Map(nodes.map(node => [node.id, node]));
    const names = new Map(nodes.map(node => [node.id, node.name]));
    const edges = routes(layoutResult).map(edge => ({...edge, health:linkHealth(edge.from, edge.to, map)}));
    const padding = 8;
    state.fitted = {x:-padding, y:-padding, w:layoutResult.width + padding * 2, h:layoutResult.height + padding * 2};
    const layerMarkup = layoutResult.layers.map(layer => `<g class="topology-layer"><rect x="${layer.x}" y="${layer.y}" width="${layer.width}" height="${layer.height}" rx="18"/><text x="${layer.x + 16}" y="${layer.y + 20}">${safe(layer.label)}</text></g>`).join('');
    const edgeMarkup = edges.map(edge => `<path class="topology-edge ${edge.health} topology-edge-${edge.cat}" data-edge-from="${safe(edge.from)}" data-edge-to="${safe(edge.to)}" d="${edge.d}"/>${marker(edge)}`).join('');
    const nodeMarkup = nodes.map(node => {
      const point = layoutResult.pos[node.id];
      if (!point) return '';
      return `<g class="topology-node-svg ${node.health}${state.selected === node.id ? ' selected' : ''}" data-topology-node="${safe(node.id)}" tabindex="0" role="button" aria-label="${safe(`${node.name}, ${statusLabel(node)}`)}" transform="translate(${point[0]} ${point[1]})">
        <rect class="topology-node-bg" width="${layoutResult.w}" height="${layoutResult.h}"/>
        <text class="topology-node-icon" x="18" y="25">${safe(ICONS[node.id] || '•')}</text>
        <text class="topology-node-name" x="39" y="22">${safe(node.name)}</text>
        <circle class="topology-node-dot" cx="45" cy="42" r="4"/>
        <text class="topology-node-status" x="55" y="45">${safe(statusLabel(node))}</text>
      </g>`;
    }).join('');
    host.innerHTML = `<svg class="topology-svg" viewBox="${state.fitted.x} ${state.fitted.y} ${state.fitted.w} ${state.fitted.h}" width="${layoutResult.width}" height="${layoutResult.height}" preserveAspectRatio="xMidYMin meet" aria-label="System dependency topology">${layerMarkup}${edgeMarkup}${nodeMarkup}</svg>`;
    host.querySelectorAll('[data-topology-node]').forEach(element => {
      const select = () => {
        state.selected = element.dataset.topologyNode;
        detail(map.get(state.selected), names);
        render();
      };
      element.onclick = select;
      element.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          select();
        }
      };
    });
    const fit = document.getElementById('topologyFit');
    if (fit) {
      fit.disabled = false;
      fit.onclick = () => render();
    }
    const errors = diagnostics(nodes, layoutResult, edges);
    if (errors.length) console.warn('Topology verification diagnostics', errors);
    const overview = summary(nodes);
    document.getElementById('topologyHealth').innerHTML = `
      <div class="topology-overall ${overview.className}"><span>Overall state</span><strong>${safe(overview.state)}</strong></div>
      <div class="topology-counts">
        <div><strong>${overview.counts.healthy}</strong><span>Healthy</span></div>
        <div><strong>${overview.counts.warning}</strong><span>Warning</span></div>
        <div><strong>${overview.counts.critical}</strong><span>Critical</span></div>
        <div><strong>${overview.counts.unknown}</strong><span>Unknown</span></div>
      </div>`;
    document.getElementById('topologyRoots').innerHTML = (state.data.root_causes || []).map(root => `<div class="root-cause"><strong>${safe(root.title || root.node || 'System notice')}</strong><small>${safe(root.detail || root.reason || '')}</small></div>`).join('') || '<div class="root-cause"><strong>Connections operational</strong><small>No dependency issue is currently reported.</small></div>';
    document.getElementById('topologyEvents').innerHTML = (state.data.events || []).slice(0, 10).map(event => `<div class="event-row"><time>${safe(event.time || '')}</time><strong>${safe(event.message || event.event || '')}</strong></div>`).join('');
  }

  window.refresh = async function refreshWithTopology() {
    await Promise.allSettled([originalRefresh(), load()]);
    window.renderPage(window.currentPage());
  };
  window.renderPage = function renderPageWithTopology(page = window.currentPage()) {
    originalRender(page);
    if (page === 'topology') render();
  };
  document.getElementById('topologyDetailClose')?.addEventListener('click', () => {
    state.selected = null;
    detail(null);
  });
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (window.currentPage() === 'topology') render();
    }, 150);
  });
  document.querySelectorAll('[data-nav]').forEach(button => {
    button.onclick = () => window.nav(button.dataset.nav);
  });
  load().then(() => {
    if (window.currentPage() === 'topology') render();
  });
  window.DashboardTopologyModel = {edges:EDGES, groups:GROUPS, order:ORDER, normalize, layout, routes, diagnostics, summary, linkHealth, statusLabel, pathMidpoint};
})();
