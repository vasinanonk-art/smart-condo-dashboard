(() => {
  'use strict';

  const originalDrawChart = window.drawChart;
  const originalChartReset = window.chartReset;
  const active = new Map();

  function numeric(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function hideInteraction(id) {
    const svg = document.getElementById(id);
    const layer = svg?.querySelector('.hover-layer');
    const tooltip = svg?.parentElement?.querySelector('.tooltip');
    if (layer) layer.style.display = 'none';
    if (tooltip) tooltip.style.display = 'none';
    active.delete(id);
  }

  function installStableInteraction(id, rows, series) {
    const svg = document.getElementById(id);
    if (!svg || !['overviewPmChart', 'airChart'].includes(id)) return;

    const visible = typeof window.visibleRows === 'function' ? window.visibleRows(id, rows) : rows;
    const valid = (visible || []).filter(row => series.some(item => numeric(row[item.key]) !== null));
    const hit = svg.querySelector('.hit');
    const layer = svg.querySelector('.hover-layer');
    const line = layer?.querySelector('.crosshair');
    const points = layer?.querySelector('.points');
    const tooltip = svg.parentElement?.querySelector('.tooltip');
    if (!valid.length || !hit || !layer || !line || !points || !tooltip) return;

    const width = 900;
    const height = 310;
    const pad = {l:48, r:18, t:18, b:35};
    const plotWidth = width - pad.l - pad.r;
    const plotHeight = height - pad.t - pad.b;
    const values = [];
    series.forEach(item => valid.forEach(row => {
      const value = numeric(row[item.key]);
      if (value !== null) values.push(value);
    }));
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const extra = (max - min) * 0.12;
    min -= extra; max += extra;
    const xFor = index => pad.l + index / Math.max(1, valid.length - 1) * plotWidth;
    const yFor = value => pad.t + (max - value) / (max - min) * plotHeight;

    line.setAttribute('y1', String(pad.t));
    line.setAttribute('y2', String(pad.t + plotHeight));

    const selectNearest = event => {
      const rect = svg.getBoundingClientRect();
      const pointer = event.touches?.[0] || event;
      const rawX = pointer.clientX - rect.left;
      const clampedPx = Math.max(pad.l / width * rect.width, Math.min((width - pad.r) / width * rect.width, rawX));
      const graphX = clampedPx / Math.max(1, rect.width) * width;
      const ratio = Math.max(0, Math.min(1, (graphX - pad.l) / plotWidth));
      const index = valid.length === 1 ? 0 : Math.max(0, Math.min(valid.length - 1, Math.round(ratio * (valid.length - 1))));
      const row = valid[index];
      const xx = xFor(index);

      line.setAttribute('x1', String(xx));
      line.setAttribute('x2', String(xx));
      points.innerHTML = '';
      series.forEach(item => {
        const value = numeric(row[item.key]);
        if (value !== null) {
          points.insertAdjacentHTML('beforeend', `<circle class="point" cx="${xx}" cy="${yFor(value)}" r="6" fill="${item.color}"/>`);
        }
      });
      layer.style.display = 'block';
      tooltip.innerHTML = `<strong>${new Date(Number(row.ts) * 1000).toLocaleString()}</strong><br>${series.map(item => {
        const value = numeric(row[item.key]);
        return `${item.label}: ${value === null ? 'Not available' : value.toFixed(1)}${item.unit}`;
      }).join('<br>')}`;
      tooltip.style.display = 'block';

      // Measure after content is visible, then clamp to all chart edges.
      const tipWidth = tooltip.offsetWidth || 180;
      const tipHeight = tooltip.offsetHeight || 70;
      const gap = 12;
      let left = clampedPx + gap;
      if (left + tipWidth > rect.width - 8) left = clampedPx - tipWidth - gap;
      left = Math.max(8, Math.min(rect.width - tipWidth - 8, left));
      let top = pointer.clientY - rect.top - tipHeight / 2;
      top = Math.max(8, Math.min(rect.height - tipHeight - 8, top));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
      active.set(id, {index, ts: row.ts});
    };

    hit.onmousemove = selectNearest;
    hit.onpointermove = selectNearest;
    hit.ontouchmove = event => { event.preventDefault(); selectNearest(event); };
    hit.onmouseleave = () => hideInteraction(id);
    hit.onpointerleave = () => hideInteraction(id);
    hit.setAttribute('x', String(pad.l));
    hit.setAttribute('y', String(pad.t));
    hit.setAttribute('width', String(plotWidth));
    hit.setAttribute('height', String(plotHeight));
  }

  if (typeof originalDrawChart === 'function') {
    window.drawChart = function stablePm25DrawChart(id, rows, series) {
      originalDrawChart(id, rows, series);
      try {
        installStableInteraction(id, rows, series);
      } catch (error) {
        console.error('PM2.5 chart interaction diagnostics', {name:error?.name || 'Error', message:error?.message || 'interaction setup failed'});
      }
    };
  }

  window.chartReset = function stableChartReset(id) {
    hideInteraction(id);
    if (typeof originalChartReset === 'function') originalChartReset(id);
    const svg = document.getElementById(id);
    const tooltip = svg?.parentElement?.querySelector('.tooltip');
    const layer = svg?.querySelector('.hover-layer');
    if (tooltip) tooltip.style.display = 'none';
    if (layer) layer.style.display = 'none';
  };
})();
