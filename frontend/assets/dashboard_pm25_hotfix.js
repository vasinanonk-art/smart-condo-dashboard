(() => {
  'use strict';
  if (window.__dashboardChartInteractionInstalled) return;
  window.__dashboardChartInteractionInstalled = true;

  const originalDrawChart = window.drawChart;
  const originalChartReset = window.chartReset;
  const CHART_IDS = new Set(['overviewChart', 'overviewPmChart', 'airChart']);
  const state = new Map();
  const DEBUG = window.DASHBOARD_CHART_DEBUG === true;
  const numeric = value => { const number = Number(value); return Number.isFinite(number) ? number : null; };
  const visibleRowsFor = (id, rows) => typeof window.visibleRows === 'function' ? window.visibleRows(id, rows) : (rows || []);
  const samplePositions = (count, left, right) => count === 1 ? [(left + right) / 2] : count > 1 ? Array.from({length: count}, (_, index) => left + index / (count - 1) * (right - left)) : [];

  function selectSampleIndex(pointerX, positions) {
    if (!positions.length) return -1;
    if (positions.length === 1) return 0;
    const last = positions.length - 1;
    const firstMid = (positions[0] + positions[1]) / 2;
    const lastMid = (positions[last - 1] + positions[last]) / 2;
    if (pointerX <= firstMid) return 0;
    if (pointerX >= lastMid) return last;
    let low = 0, high = last;
    while (low + 1 < high) {
      const mid = Math.floor((low + high) / 2);
      if (positions[mid] <= pointerX) low = mid;
      else high = mid;
    }
    return pointerX - positions[low] <= positions[high] - pointerX ? low : high;
  }

  function clientToSvg(svg, clientX, clientY) {
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    return point.matrixTransform(matrix.inverse());
  }

  function svgToClient(svg, x, y) {
    const matrix = svg.getScreenCTM();
    if (!matrix) return null;
    const point = svg.createSVGPoint();
    point.x = x;
    point.y = y;
    return point.matrixTransform(matrix);
  }

  function hide(id) {
    const svg = document.getElementById(id);
    const layer = svg?.querySelector('.hover-layer');
    const tooltip = svg?.parentElement?.querySelector('.tooltip');
    const debug = svg?.parentElement?.querySelector('.chart-debug-geometry');
    if (layer) layer.style.display = 'none';
    if (tooltip) tooltip.style.display = 'none';
    if (debug) debug.style.display = 'none';
    state.delete(id);
  }

  function attach(config) {
    const {
      id, rows = [], positions = [], plot, hit, layer, crosshair, points, tooltip,
      renderPoints, renderTooltip, onSelected, debugLabel = 'chart'
    } = config;
    const svg = document.getElementById(id);
    const wrap = svg?.parentElement;
    if (!svg || !wrap || !hit || !layer || !crosshair || !points || !tooltip || !rows.length || !positions.length) return null;

    crosshair.setAttribute('y1', plot.top);
    crosshair.setAttribute('y2', plot.bottom);
    hit.setAttribute('x', plot.left);
    hit.setAttribute('y', plot.top);
    hit.setAttribute('width', Math.max(0, plot.right - plot.left));
    hit.setAttribute('height', Math.max(0, plot.bottom - plot.top));
    hit.style.pointerEvents = 'all';
    hit.style.fill = 'transparent';
    hit.style.cursor = 'crosshair';
    svg.appendChild(hit);

    const hideCurrent = () => {
      layer.style.display = 'none';
      tooltip.style.display = 'none';
      state.delete(id);
      const debug = wrap.querySelector('.chart-debug-geometry');
      if (debug) debug.style.display = 'none';
    };

    const move = event => {
      const pointer = event.touches?.[0] || event.changedTouches?.[0] || event;
      const svgPoint = clientToSvg(svg, pointer.clientX, pointer.clientY);
      if (!svgPoint) return;
      const pointerX = Math.max(plot.left, Math.min(plot.right, svgPoint.x));
      const pointerY = Math.max(plot.top, Math.min(plot.bottom, svgPoint.y));
      const index = selectSampleIndex(pointerX, positions);
      if (index < 0) return;
      const sampleX = positions[index];
      const row = rows[index];
      crosshair.setAttribute('x1', sampleX);
      crosshair.setAttribute('x2', sampleX);
      points.innerHTML = '';
      if (typeof renderPoints === 'function') renderPoints({row, index, sampleX, points});
      if (typeof renderTooltip === 'function') tooltip.innerHTML = renderTooltip({row, index, sampleX});
      layer.style.display = 'block';
      tooltip.style.display = 'block';

      const wrapRect = wrap.getBoundingClientRect();
      const anchor = svgToClient(svg, sampleX, pointerY);
      if (anchor) {
        const localX = anchor.x - wrapRect.left;
        const localY = anchor.y - wrapRect.top;
        const tipW = tooltip.offsetWidth || 190;
        const tipH = tooltip.offsetHeight || 72;
        const gap = 12;
        const pad = 8;
        let left = localX + gap;
        if (left + tipW > wrapRect.width - pad) left = localX - tipW - gap;
        left = Math.max(pad, Math.min(Math.max(pad, wrapRect.width - tipW - pad), left));
        let top = localY - tipH / 2;
        top = Math.max(pad, Math.min(Math.max(pad, wrapRect.height - tipH - pad), top));
        tooltip.style.left = `${left}px`;
        tooltip.style.top = `${top}px`;
      }

      state.set(id, {index, pointerX, sampleX, count: rows.length});
      if (typeof onSelected === 'function') onSelected({row, index, sampleX, pointerX});
      if (DEBUG) {
        let debug = wrap.querySelector('.chart-debug-geometry');
        if (!debug) {
          debug = document.createElement('div');
          debug.className = 'chart-debug-geometry';
          wrap.appendChild(debug);
        }
        debug.style.display = 'block';
        debug.textContent = `${debugLabel} plot ${plot.left.toFixed(1)},${plot.top.toFixed(1)} → ${plot.right.toFixed(1)},${plot.bottom.toFixed(1)} · pointerX ${pointerX.toFixed(1)} · index ${index} · sampleX ${sampleX.toFixed(1)} · visible ${rows.length}`;
      }
    };

    hit.onmousemove = move;
    hit.onpointermove = move;
    hit.onpointerenter = move;
    hit.onpointerdown = move;
    hit.ontouchstart = event => { event.preventDefault(); move(event); };
    hit.ontouchmove = event => { event.preventDefault(); move(event); };
    hit.onmouseleave = hideCurrent;
    hit.onpointerleave = hideCurrent;
    hit.ontouchcancel = hideCurrent;
    return {hide: hideCurrent, move};
  }

  function install(id, rows, series) {
    if (!CHART_IDS.has(id)) return;
    const svg = document.getElementById(id);
    const wrap = svg?.parentElement;
    if (!svg || !wrap) return;
    const visible = visibleRowsFor(id, rows);
    const valid = (visible || []).filter(row => series.some(item => numeric(row[item.key]) !== null));
    const hit = svg.querySelector('.hit');
    const layer = svg.querySelector('.hover-layer');
    const line = layer?.querySelector('.crosshair');
    const points = layer?.querySelector('.points');
    const tooltip = wrap.querySelector('.tooltip');
    if (!valid.length || !hit || !layer || !line || !points || !tooltip) return;
    const viewBox = svg.viewBox.baseVal;
    const width = viewBox.width || 900;
    const height = viewBox.height || 310;
    const plot = {left: 48, right: width - 18, top: 18, bottom: height - 35};
    const positions = samplePositions(valid.length, plot.left, plot.right);
    const values = [];
    series.forEach(item => valid.forEach(row => {
      const value = numeric(row[item.key]);
      if (value !== null) values.push(value);
    }));
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const extra = (max - min) * .12;
    min -= extra;
    max += extra;
    const yFor = value => plot.top + (max - value) / (max - min) * (plot.bottom - plot.top);

    attach({
      id, rows: valid, positions, plot, hit, layer, crosshair: line, points, tooltip,
      renderPoints: ({row, sampleX, points: host}) => {
        series.forEach(item => {
          const value = numeric(row[item.key]);
          if (value !== null) host.insertAdjacentHTML('beforeend', `<circle class="point" cx="${sampleX}" cy="${yFor(value)}" r="6" fill="${item.color}"/>`);
        });
      },
      renderTooltip: ({row}) => `<strong>${new Date(Number(row.ts) * 1000).toLocaleString()}</strong><br>${series.map(item => {
        const value = numeric(row[item.key]);
        return `${item.label}: ${value === null ? 'Not available' : value.toFixed(1)}${item.unit}`;
      }).join('<br>')}`,
      debugLabel: id
    });
  }

  if (typeof originalDrawChart === 'function') {
    window.drawChart = function sharedChartDraw(id, rows, series) {
      originalDrawChart(id, rows, series);
      try { install(id, rows, series); }
      catch (error) { console.error('Chart interaction diagnostics', {name: error?.name || 'Error', message: error?.message || 'interaction setup failed'}); }
    };
  }
  window.chartReset = function sharedChartReset(id) {
    if (typeof originalChartReset === 'function') originalChartReset(id);
    hide(id);
  };
  window.DashboardChartInteraction = Object.freeze({selectSampleIndex, samplePositions, visibleRowsFor, clientToSvg, svgToClient, attach});
})();
