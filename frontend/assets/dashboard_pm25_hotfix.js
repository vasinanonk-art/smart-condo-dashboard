(() => {
  'use strict';

  const originalDrawChart = window.drawChart;
  const originalChartReset = window.chartReset;
  const active = new Map();
  const SUPPORTED = new Set(['overviewChart', 'overviewPmChart', 'airChart']);
  const WIDTH = 900;
  const HEIGHT = 310;
  const PAD = {l:48, r:18, t:18, b:35};

  function numeric(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function visibleRowsFor(id, rows) {
    return typeof window.visibleRows === 'function' ? window.visibleRows(id, rows) : (rows || []);
  }

  function chartRows(rows, series) {
    return (rows || []).filter(row => series.some(item => numeric(row?.[item.key]) !== null));
  }

  function samplePositions(count, left = PAD.l, right = WIDTH - PAD.r) {
    if (count <= 0) return [];
    if (count === 1) return [(left + right) / 2];
    const span = right - left;
    return Array.from({length: count}, (_, index) => left + index / (count - 1) * span);
  }

  function selectSampleIndex(pointerX, positions) {
    if (!positions.length) return -1;
    if (positions.length === 1) return 0;
    const clamped = Math.max(PAD.l, Math.min(WIDTH - PAD.r, pointerX));
    const firstMidpoint = (positions[0] + positions[1]) / 2;
    const lastIndex = positions.length - 1;
    const lastMidpoint = (positions[lastIndex - 1] + positions[lastIndex]) / 2;
    if (clamped <= firstMidpoint) return 0;
    if (clamped >= lastMidpoint) return lastIndex;
    let low = 0;
    let high = lastIndex;
    while (low + 1 < high) {
      const mid = Math.floor((low + high) / 2);
      if (positions[mid] <= clamped) low = mid;
      else high = mid;
    }
    return clamped - positions[low] <= positions[high] - clamped ? low : high;
  }

  function hideInteraction(id) {
    const svg = document.getElementById(id);
    const layer = svg?.querySelector('.hover-layer');
    const tooltip = svg?.parentElement?.querySelector('.tooltip');
    if (layer) layer.style.display = 'none';
    if (tooltip) tooltip.style.display = 'none';
    active.delete(id);
  }

  function installInteraction(id, rows, series) {
    const svg = document.getElementById(id);
    if (!svg || !SUPPORTED.has(id)) return;

    const visible = visibleRowsFor(id, rows);
    const valid = chartRows(visible, series);
    const hit = svg.querySelector('.hit');
    const layer = svg.querySelector('.hover-layer');
    const line = layer?.querySelector('.crosshair');
    const points = layer?.querySelector('.points');
    const tooltip = svg.parentElement?.querySelector('.tooltip');
    if (!valid.length || !hit || !layer || !line || !points || !tooltip) return;

    const plotWidth = WIDTH - PAD.l - PAD.r;
    const plotHeight = HEIGHT - PAD.t - PAD.b;
    const positions = samplePositions(valid.length);
    const values = [];
    series.forEach(item => valid.forEach(row => {
      const value = numeric(row?.[item.key]);
      if (value !== null) values.push(value);
    }));
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const extra = (max - min) * 0.12;
    min -= extra;
    max += extra;
    const yFor = value => PAD.t + (max - value) / (max - min) * plotHeight;

    line.setAttribute('y1', String(PAD.t));
    line.setAttribute('y2', String(PAD.t + plotHeight));
    hit.setAttribute('x', String(PAD.l));
    hit.setAttribute('y', String(PAD.t));
    hit.setAttribute('width', String(plotWidth));
    hit.setAttribute('height', String(plotHeight));
    hit.setAttribute('pointer-events', 'all');

    const select = event => {
      const rect = svg.getBoundingClientRect();
      const pointer = event.touches?.[0] || event.changedTouches?.[0] || event;
      if (!pointer || rect.width <= 0 || rect.height <= 0) return;
      const pointerPx = Math.max(PAD.l / WIDTH * rect.width, Math.min((WIDTH - PAD.r) / WIDTH * rect.width, pointer.clientX - rect.left));
      const pointerGraphX = pointerPx / rect.width * WIDTH;
      const index = selectSampleIndex(pointerGraphX, positions);
      if (index < 0) return;
      const row = valid[index];
      const selectedX = positions[index];
      const selectedPx = selectedX / WIDTH * rect.width;

      line.setAttribute('x1', String(selectedX));
      line.setAttribute('x2', String(selectedX));
      points.innerHTML = '';
      series.forEach(item => {
        const value = numeric(row?.[item.key]);
        if (value !== null) points.insertAdjacentHTML('beforeend', `<circle class="point" cx="${selectedX}" cy="${yFor(value)}" r="6" fill="${item.color}"/>`);
      });
      layer.style.display = 'block';
      tooltip.innerHTML = `<strong>${new Date(Number(row.ts) * 1000).toLocaleString()}</strong><br>${series.map(item => {
        const value = numeric(row?.[item.key]);
        return `${item.label}: ${value === null ? 'Not available' : value.toFixed(1)}${item.unit}`;
      }).join('<br>')}`;
      tooltip.style.display = 'block';

      const tipWidth = tooltip.offsetWidth || 180;
      const tipHeight = tooltip.offsetHeight || 70;
      const gap = 12;
      let left = selectedPx + gap;
      if (left + tipWidth > rect.width - 8) left = selectedPx - tipWidth - gap;
      left = Math.max(8, Math.min(Math.max(8, rect.width - tipWidth - 8), left));
      let top = pointer.clientY - rect.top - tipHeight / 2;
      top = Math.max(8, Math.min(Math.max(8, rect.height - tipHeight - 8), top));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
      active.set(id, {index, ts: row.ts, selectedX});
    };

    hit.onmousemove = select;
    hit.onpointermove = select;
    hit.ontouchstart = event => { event.preventDefault(); select(event); };
    hit.ontouchmove = event => { event.preventDefault(); select(event); };
    hit.onmouseleave = () => hideInteraction(id);
    hit.onpointerleave = () => hideInteraction(id);
  }

  if (typeof originalDrawChart === 'function') {
    window.drawChart = function sharedInteractiveDrawChart(id, rows, series) {
      originalDrawChart(id, rows, series);
      try {
        installInteraction(id, rows, series);
      } catch (error) {
        console.error('Shared chart interaction diagnostics', {name:error?.name || 'Error', message:error?.message || 'interaction setup failed', chart:id});
      }
    };
  }

  window.chartReset = function sharedChartReset(id) {
    hideInteraction(id);
    if (typeof originalChartReset === 'function') originalChartReset(id);
    hideInteraction(id);
  };

  window.DashboardChartInteraction = Object.freeze({selectSampleIndex, samplePositions});
})();
