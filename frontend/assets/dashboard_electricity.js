(() => {
  'use strict';
  if (window.__dashboardElectricityInstalled) return;
  window.__dashboardElectricityInstalled = true;

  const MAX_CUSTOM_DAYS = 400;
  const state = {
    status: null,
    summary: null,
    billing: null,
    tariff: null,
    tariffSync: null,
    billingCycle: null,
    history: null,
    range: '24h',
    live: [],
    lastKey: null,
    zoom: 1,
    pan: 0,
    historyLoading: false,
    historyError: null,
    historyRequestId: 0,
    bucketMode: 'auto',
    exportLoading: false,
    customStart: '',
    customEnd: '',
    comparison: null,
    comparisonRange: 'today',
    comparisonLoading: false,
    comparisonError: null,
    comparisonRequestId: 0,
    customVisible: false,
    todayPeak: null,
    billingOwnerActive: false,
    billingInFlight: null,
  };

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const number = value => {
    if (value === null || value === undefined || value === '') return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };
  const sourceName = value => ({
    tuya_local: 'Tuya Local',
    home_assistant: 'Home Assistant',
    mqtt: 'MQTT',
    unknown: 'Unknown',
  })[String(value || 'unknown')] || String(value || 'Unknown');
  const healthName = (health, payload) => health === 'healthy'
    ? 'Healthy'
    : health === 'warning' && [payload?.voltage, payload?.current, payload?.power, payload?.total_energy].some(value => number(value) !== null)
      ? 'Partial Data'
      : health === 'offline' ? 'Offline' : 'Unknown';

  function installUi() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="electricity"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'electricity';
      button.dataset.short = 'EL';
      button.textContent = 'Electricity';
      const topology = host.querySelector('[data-nav="topology"]');
      topology ? host.insertBefore(button, topology) : host.appendChild(button);
    });
    if (document.querySelector('[data-page="electricity"]')) return;
    const section = document.createElement('section');
    section.className = 'page';
    section.dataset.page = 'electricity';
    section.innerHTML = '<div id="electricityPage" class="electricity-page"><div class="card"><div class="empty">Electricity data is loading.</div></div></div>';
    document.querySelector('.main')?.appendChild(section);
  }

  function epoch(value) {
    if (value === null || value === undefined || value === '' || value === 0) return null;
    const parsedNumber = Number(value);
    if (Number.isFinite(parsedNumber)) return parsedNumber > 1e12 ? Math.floor(parsedNumber / 1000) : Math.floor(parsedNumber);
    const parsedDate = Date.parse(value);
    return Number.isFinite(parsedDate) ? Math.floor(parsedDate / 1000) : null;
  }

  function localTime(value) {
    const ts = epoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Bangkok',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(ts * 1000)) + ' ICT';
  }

  function localDate(value) {
    const ts = epoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Bangkok',
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    }).format(new Date(ts * 1000));
  }

  function localClock(value) {
    const ts = epoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Bangkok',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).format(new Date(ts * 1000));
  }

  const bucketLabels = {
    '15m': '15 minutes',
    '30m': '30 minutes',
    hour: '1 hour',
    '3h': '3 hours',
    day: '1 day',
  };

  function bucketLabel(value) {
    return bucketLabels[String(value || '')] || 'Not available';
  }

  function money(value) {
    const parsed = number(value);
    return parsed === null ? 'Not available' : `฿${parsed.toFixed(2)}`;
  }

  function metric(label, value, unit = '', secondary = false) {
    const display = value === null || value === undefined || value === '' ? 'Not available' : value;
    return `<div class="electricity-metric${secondary ? ' secondary' : ''}"><span>${safe(label)}</span><strong>${safe(display)}${display !== 'Not available' && unit ? `<small>${safe(unit)}</small>` : ''}</strong></div>`;
  }

  function badge(label, value, cls = '') {
    return `<span class="electricity-badge ${cls}">${safe(label)} · ${safe(value)}</span>`;
  }

  function addLive(payload) {
    const diagnostics = payload?.diagnostics || {};
    const ts = epoch(payload?.last_update || diagnostics.last_success) || Math.floor(Date.now() / 1000);
    const key = `${ts}:${payload?.power}:${payload?.voltage}:${payload?.current}`;
    if (key === state.lastKey) return;
    state.lastKey = key;
    state.live.push({
      ts,
      power: number(payload?.power),
      voltage: number(payload?.voltage),
      current: number(payload?.current),
      total_energy: number(payload?.total_energy),
      source: diagnostics.source,
      health: payload.health,
    });
    state.live = state.live.slice(-120);
  }

  async function loadStatus() {
    try {
      state.status = await window.get('/api/electricity/status');
      addLive(state.status);
    } catch (error) {
      console.error('Electricity status failed', {name: error?.name || 'Error'});
    }
  }

  async function loadSummary() {
    try {
      state.summary = await window.DashboardElectricitySummaryStore.get();
    }
    catch (error) { console.error('Electricity summary failed', {name: error?.name || 'Error'}); }
  }

  async function loadBillingCycleStatus() {
    try { state.billingCycle = await window.get('/api/electricity/billing-cycle/status'); }
    catch (error) { state.billingCycle = null; }
  }

  async function loadBilling() {
    try { state.billing = await window.get('/api/electricity/billing-cycle?range=current_billing_cycle'); }
    catch (error) { console.error('Electricity billing failed', {name: error?.name || 'Error'}); }
  }

  function refreshBilling() {
    if (!state.billingOwnerActive) return Promise.resolve();
    if (state.billingInFlight) return state.billingInFlight;
    state.billingInFlight = Promise.allSettled([loadBillingCycleStatus(), loadBilling()])
      .then(results => {
        if (state.billingOwnerActive) window.renderPage(window.currentPage());
        return results;
      })
      .finally(() => { state.billingInFlight = null; });
    return state.billingInFlight;
  }

  function activateBilling() {
    if (state.billingOwnerActive) return state.billingInFlight || Promise.resolve();
    state.billingOwnerActive = true;
    return refreshBilling();
  }

  function deactivateBilling() {
    state.billingOwnerActive = false;
  }

  async function loadTariff() {
    try { state.tariff = await window.get('/api/electricity/tariff/status'); }
    catch (error) { state.tariff = {configured: false, valid: false, diagnostics: {reason: 'status_unavailable'}}; }
  }

  async function loadTariffSync() {
    try { state.tariffSync = await window.get('/api/electricity/tariff/sync-status'); }
    catch (error) { state.tariffSync = null; }
  }

  function summarize(rows) {
    const powers = rows.map(row => number(row.power)).filter(value => value !== null);
    let energy = null;
    if (rows.length > 1) {
      const first = number(rows[0].total_energy);
      const last = number(rows[rows.length - 1].total_energy);
      if (first !== null && last !== null && last >= first) energy = last - first;
    }
    return {
      sample_count: rows.length,
      min_power: powers.length ? Math.min(...powers) : null,
      max_power: powers.length ? Math.max(...powers) : null,
      avg_power: powers.length ? powers.reduce((a, b) => a + b, 0) / powers.length : null,
      energy_used_kwh: energy,
    };
  }

  function thailandDate(value = new Date()) {
    return new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Bangkok', year: 'numeric', month: '2-digit', day: '2-digit',
    }).format(value);
  }

  function historyRequest(range, customStart = '', customEnd = '', bucketMode = state.bucketMode) {
    const now = new Date();
    let start;
    let end = now;
    if (range === 'custom') {
      if (!customStart || !customEnd) throw new Error('Select both start and end dates.');
      if (customStart > customEnd) throw new Error('Start date must not be after end date.');
      if (customEnd > thailandDate(now)) throw new Error('Future dates are not supported.');
      start = new Date(`${customStart}T00:00:00+07:00`);
      const nextDay = new Date(`${customEnd}T00:00:00+07:00`);
      nextDay.setUTCDate(nextDay.getUTCDate() + 1);
      end = customEnd === thailandDate(now) ? now : new Date(nextDay.getTime() - 1);
      const days = (end.getTime() - start.getTime()) / 86400000;
      if (days > MAX_CUSTOM_DAYS) throw new Error(`Date range must be ${MAX_CUSTOM_DAYS} days or less.`);
    } else {
      const hours = range === '7d' ? 7 * 24 : range === '30d' ? 30 * 24 : 24;
      start = new Date(now.getTime() - hours * 3600000);
    }
    const query = new URLSearchParams({
      start: start.toISOString(),
      end: end.toISOString(),
      bucket: bucketMode,
    });
    return `/api/electricity/history?${query.toString()}`;
  }

  async function loadHistory(range = state.range, customStart = state.customStart, customEnd = state.customEnd) {
    const requestId = ++state.historyRequestId;
    state.zoom = 1;
    state.pan = 0;
    state.range = range;
    state.historyLoading = true;
    state.historyError = null;
    render();
    try {
      const payload = await window.get(historyRequest(range, customStart, customEnd));
      if (requestId !== state.historyRequestId) return;
      state.history = payload;
      const todayStart = epoch(`${thailandDate()}T00:00:00+07:00`);
      if (epoch(payload.start) <= todayStart && epoch(payload.end) >= todayStart) {
        state.todayPeak = (payload.points || []).reduce((best, row) => (
          thailandDate(new Date(epoch(row.timestamp) * 1000)) === thailandDate()
          && number(row.energy_kwh) !== null
          && (!best || number(row.energy_kwh) > number(best.energy_kwh))
            ? row : best
        ), null);
      }
    } catch (error) {
      if (requestId !== state.historyRequestId) return;
      state.historyError = error?.message || 'Electricity history is unavailable.';
      console.error('Electricity history failed', {name: error?.name || 'Error'});
    } finally {
      if (requestId === state.historyRequestId) {
        state.historyLoading = false;
        render();
      }
    }
  }

  async function loadComparison(range = state.comparisonRange) {
    const requestId = ++state.comparisonRequestId;
    state.comparisonRange = range;
    state.comparisonLoading = true;
    state.comparisonError = null;
    render();
    try {
      const query = new URLSearchParams({comparison: range});
      const payload = await window.get(`/api/electricity/history?${query.toString()}`);
      if (requestId !== state.comparisonRequestId) return;
      state.comparison = payload;
    } catch (error) {
      if (requestId !== state.comparisonRequestId) return;
      state.comparisonError = error?.message || 'Comparison is unavailable.';
    } finally {
      if (requestId === state.comparisonRequestId) {
        state.comparisonLoading = false;
        render();
      }
    }
  }

  function chartData() {
    return state.history?.points || [];
  }

  function seriesDefinitions() {
    return [{key: 'energy_kwh', label: 'Energy', unit: 'kWh', cls: 'energy'}];
  }

  function requestedWindow(rows) {
    return {
      start: epoch(state.history?.start) || 0,
      end: epoch(state.history?.end) || Math.floor(Date.now() / 1000),
    };
  }

  function visibleWindow(base) {
    const span = Math.max(1, base.end - base.start);
    const visibleSpan = span / Math.max(1, state.zoom);
    const maxPan = Math.max(0, span - visibleSpan);
    const offset = Math.max(0, Math.min(maxPan, state.pan * maxPan));
    return {start: base.start + offset, end: base.start + offset + visibleSpan};
  }

  function visibleRows(rows, windowRange) {
    return rows.filter(row => {
      const ts = epoch(row.timestamp);
      return ts !== null && ts >= windowRange.start && ts <= windowRange.end;
    });
  }

  function splitSegments(rows, key, maxGap) {
    const segments = [];
    let current = [];
    rows.forEach(row => {
      const value = number(row[key]);
      const ts = epoch(row.timestamp);
      if (value === null || ts === null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      if (current.length && ts - epoch(current[current.length - 1].timestamp) > maxGap) {
        segments.push(current);
        current = [];
      }
      current.push(row);
    });
    if (current.length) segments.push(current);
    return segments;
  }

  function movingAverage(rows, windowSize = 3, maxGap = 7200) {
    const result = [];
    let window = [];
    let previousTimestamp = null;
    rows.forEach(row => {
      const value = number(row.energy_kwh);
      const timestamp = epoch(row.timestamp);
      if (value === null || timestamp === null || (previousTimestamp !== null && timestamp - previousTimestamp > maxGap)) {
        window = [];
      }
      if (value === null || timestamp === null) {
        result.push({...row, moving_average_kwh: null});
        previousTimestamp = timestamp;
        return;
      }
      window.push(value);
      window = window.slice(-windowSize);
      result.push({
        ...row,
        moving_average_kwh: window.length === windowSize
          ? window.reduce((sum, item) => sum + item, 0) / window.length
          : null,
      });
      previousTimestamp = timestamp;
    });
    return result;
  }

  function analyticsStatistics(rows = chartData()) {
    const valid = rows.filter(row => number(row.energy_kwh) !== null && epoch(row.timestamp) !== null);
    const daily = new Map();
    let durationHours = 0;
    valid.forEach(row => {
      const day = thailandDate(new Date(epoch(row.timestamp) * 1000));
      const entry = daily.get(day) || {day, energy: 0, cost: 0, costAvailable: true};
      entry.energy += number(row.energy_kwh);
      const cost = number(row.cost_thb);
      if (cost === null) entry.costAvailable = false;
      else entry.cost += cost;
      daily.set(day, entry);
      const start = epoch(row.interval_start);
      const end = epoch(row.interval_end);
      if (start !== null && end !== null && end > start) durationHours += (end - start) / 3600;
    });
    const days = [...daily.values()].map(item => ({
      ...item,
      cost: item.costAvailable ? item.cost : null,
    }));
    const highest = days.length ? days.reduce((best, item) => item.energy > best.energy ? item : best) : null;
    const lowest = days.length ? days.reduce((best, item) => item.energy < best.energy ? item : best) : null;
    const totalEnergy = valid.reduce((sum, row) => sum + number(row.energy_kwh), 0);
    const costs = valid.map(row => number(row.cost_thb));
    const totalCost = costs.length && costs.every(value => value !== null)
      ? costs.reduce((sum, value) => sum + value, 0)
      : null;
    const maximum = valid.length ? valid.reduce((best, row) => number(row.energy_kwh) > number(best.energy_kwh) ? row : best) : null;
    const minimum = valid.length ? valid.reduce((best, row) => number(row.energy_kwh) < number(best.energy_kwh) ? row : best) : null;
    return {
      highest,
      lowest,
      averageDaily: days.length ? {energy: totalEnergy / days.length, cost: totalCost === null ? null : totalCost / days.length} : null,
      averageHourly: durationHours > 0 ? {energy: totalEnergy / durationHours, cost: totalCost === null ? null : totalCost / durationHours} : null,
      maximum,
      minimum,
    };
  }

  function axisLabelStride(bucket, pointCount) {
    if (bucket === '15m') return 8;
    if (bucket === '30m') return 4;
    if (bucket === 'hour') return 2;
    if (bucket === '3h') return 4;
    return Math.max(1, Math.ceil(pointCount / 10));
  }

  function renderChart() {
    if (state.historyLoading && !state.history) return '<div class="electricity-empty electricity-loading" role="status">Loading electricity history…</div>';
    if (state.historyError && !state.history) return `<div class="electricity-empty electricity-error">${safe(state.historyError)}</div>`;
    const allRows = chartData().filter(row => epoch(row.timestamp)).sort((a, b) => epoch(a.timestamp) - epoch(b.timestamp));
    const series = seriesDefinitions();
    if (!allRows.length || !series.length) return '<div class="electricity-empty">No history samples for this range.</div>';
    const width = 900, height = 260, left = 58, right = 20, top = 18, bottom = 38;
    const plotRight = width - right, plotBottom = height - bottom, plotW = plotRight - left, plotH = plotBottom - top;
    const base = requestedWindow(allRows), windowRange = visibleWindow(base);
    const rawRows = visibleRows(allRows, windowRange);
    const maxGap = Number(state.history?.max_gap_sec) || 7200;
    const rows = movingAverage(rawRows, 3, maxGap);
    if (!rows.length) return '<div class="electricity-empty">No samples in the current zoom window.</div>';
    const values = [];
    rows.forEach(row => {
      const energy = number(row.energy_kwh);
      const average = number(row.moving_average_kwh);
      if (energy !== null) values.push(energy);
      if (average !== null) values.push(average);
    });
    if (!values.length) return '<div class="electricity-empty">No selected metrics are available.</div>';
    const min = 0;
    let max = Math.max(...values);
    if (max <= 0) max = 1;
    const xTs = ts => left + ((ts - windowRange.start) / Math.max(1, windowRange.end - windowRange.start)) * plotW;
    const y = value => top + (max - value) / (max - min) * plotH;
    const spacing = rows.length > 1
      ? Math.min(...rows.slice(1).map((row, index) => Math.max(1, xTs(epoch(row.timestamp)) - xTs(epoch(rows[index].timestamp)))))
      : plotW;
    const barWidth = Math.max(2, Math.min(24, spacing * 0.68));
    const bars = rows.map(row => {
      const value = number(row.energy_kwh);
      if (value === null) return '';
      const barY = y(value);
      const barX = Math.max(left, Math.min(plotRight - barWidth, xTs(epoch(row.timestamp)) - barWidth / 2));
      return `<rect class="history-bar energy" x="${barX.toFixed(2)}" y="${barY.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${Math.max(1, plotBottom - barY).toFixed(2)}" rx="2"/>`;
    }).join('');
    const averagePaths = splitSegments(rows, 'moving_average_kwh', maxGap).map(segment => {
      const path = segment.map((row, index) => `${index ? 'L' : 'M'}${xTs(epoch(row.timestamp)).toFixed(2)},${y(number(row.moving_average_kwh)).toFixed(2)}`).join(' ');
      return `<path class="history-average-line" d="${path}"/>`;
    }).join('');
    const positions = rows.map(row => xTs(epoch(row.timestamp)));
    const bucket = String(state.history?.bucket || 'hour');
    const labelStride = axisLabelStride(bucket, rows.length);
    const labelFormatter = new Intl.DateTimeFormat('en-GB', bucket === 'day'
      ? {timeZone:'Asia/Bangkok', day:'2-digit', month:'short'}
      : {timeZone:'Asia/Bangkok', day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit', hour12:false});
    const axisLabels = rows
      .filter((_row, index) => index % labelStride === 0)
      .map(row => `<text class="history-axis-label" x="${xTs(epoch(row.timestamp)).toFixed(2)}" y="${plotBottom + 22}" text-anchor="middle">${safe(labelFormatter.format(new Date(epoch(row.timestamp) * 1000)))}</text>`)
      .join('');
    state.renderedChart = {rows, series, positions, plot: {left, right: plotRight, top, bottom: plotBottom}, y, min, max, windowRange};
    return `<div class="electricity-history-chart-wrap${state.historyLoading ? ' is-loading' : ''}"><svg id="electricityHistoryChart" class="electricity-history-chart" viewBox="0 0 ${width} ${height}"><line class="axis" x1="${left}" y1="${plotBottom}" x2="${plotRight}" y2="${plotBottom}"/><g class="history-axis-labels">${axisLabels}</g><g class="history-bars">${bars}</g>${averagePaths}<g class="history-hover" style="display:none"><line class="history-crosshair" y1="${top}" y2="${plotBottom}"/><g class="history-points"></g></g><rect class="history-hit" x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent"/></svg><div class="electricity-chart-legend"><span><i class="energy"></i>Energy</span><span><i class="average"></i>3-interval moving average</span></div><div class="electricity-history-tooltip" style="display:none"></div>${state.historyLoading ? '<div class="electricity-chart-loading" role="status">Loading selected range…</div>' : ''}</div>`;
  }

  function tooltipContent(row) {
    const energy = number(row.energy_kwh);
    const cost = number(row.cost_thb);
    const quality = row.data_quality === 'valid' ? 'Good' : row.data_quality === 'partial' ? 'Partial' : 'Unknown';
    return `<strong>${safe(localDate(row.timestamp))}</strong><span>${safe(localClock(row.interval_start || row.timestamp))}–${safe(localClock(row.interval_end))}</span><dl><div><dt>Energy</dt><dd>${energy === null ? 'Not available' : `${safe(energy.toFixed(4))} kWh`}</dd></div><div><dt>Cost</dt><dd>${cost === null ? 'Not available' : safe(money(cost))}</dd></div><div><dt>Bucket</dt><dd>${safe(bucketLabel(state.history?.bucket))}</dd></div><div><dt>Quality</dt><dd>${safe(quality)}</dd></div></dl>`;
  }

  function installChartInteraction() {
    const model = state.renderedChart;
    const engine = window.DashboardChartInteraction;
    const svg = document.getElementById('electricityHistoryChart');
    const wrap = svg?.parentElement;
    if (!model || !engine?.attach || !svg || !wrap) return;
    engine.attach({
      id: 'electricityHistoryChart',
      rows: model.rows,
      positions: model.positions,
      plot: model.plot,
      hit: svg.querySelector('.history-hit'),
      layer: svg.querySelector('.history-hover'),
      crosshair: svg.querySelector('.history-crosshair'),
      points: svg.querySelector('.history-points'),
      tooltip: wrap.querySelector('.electricity-history-tooltip'),
      debugLabel: 'electricity',
      renderPoints: ({row, sampleX, points: host}) => {
        model.series.forEach(item => {
          const value = number(row[item.key]);
          if (value !== null) host.insertAdjacentHTML('beforeend', `<circle class="history-point ${item.cls}" cx="${sampleX}" cy="${model.y(value)}" r="5"/>`);
        });
      },
      renderTooltip: ({row}) => tooltipContent(row),
    });
  }

  function historyRequestState() {
    if (state.historyError) return `<div class="electricity-history-message error" role="alert">${safe(state.historyError)}</div>`;
    if (state.historyLoading) return '<div class="electricity-history-message loading" role="status">Loading electricity history…</div>';
    return '';
  }

  function summaryCards() {
    const comparison = state.comparison || {};
    const current = comparison.current || {};
    const percentage = number(comparison.percentage_difference);
    const hasCurrent = Number(current.point_count || 0) > 0;
    const peak = state.todayPeak;
    const trendClass = percentage === null ? 'neutral' : percentage > 0 ? 'up' : percentage < 0 ? 'down' : 'neutral';
    const trend = percentage === null ? 'Not available' : `${percentage > 0 ? '▲ +' : percentage < 0 ? '▼ ' : ''}${percentage.toFixed(1)}%`;
    return `<section class="electricity-analytics-summary${state.comparisonLoading ? ' is-loading' : ''}" aria-label="Today electricity summary">
      <article class="electricity-summary-card"><span>Today</span><strong>${hasCurrent ? safe(Number(current.total_energy_kwh || 0).toFixed(2)) : 'Not available'}${hasCurrent ? '<small>kWh</small>' : ''}</strong></article>
      <article class="electricity-summary-card"><span>Estimated Cost</span><strong>${hasCurrent ? safe(money(current.total_cost_thb)) : 'Not available'}</strong></article>
      <article class="electricity-summary-card"><span>Today’s Peak</span><strong>${peak ? `${safe(number(peak.energy_kwh).toFixed(2))}<small>kWh</small>` : 'Not available'}</strong><small>${peak ? safe(localClock(peak.timestamp)) : 'No valid interval'}</small></article>
      <article class="electricity-summary-card comparison ${trendClass}"><span>Comparison</span><strong>${safe(trend)}</strong><small>Compared with yesterday</small></article>
    </section>`;
  }

  function statisticCard(label, item, detail = '') {
    const energy = item && number(item.energy ?? item.energy_kwh);
    const cost = item && number(item.cost ?? item.cost_thb);
    return `<article class="electricity-stat-card"><span>${safe(label)}</span><strong>${energy === null ? 'Not available' : `${safe(energy.toFixed(3))} kWh`}</strong><small>${cost === null ? 'Cost unavailable' : safe(money(cost))}${detail ? ` · ${safe(detail)}` : ''}</small></article>`;
  }

  function statisticsPanel() {
    const stats = analyticsStatistics();
    return `<section class="electricity-statistics" aria-label="Electricity statistics">
      <div class="electricity-section-head"><div><h2>Statistics</h2><small>Calculated from the selected history already loaded</small></div></div>
      <div class="electricity-stat-grid">
        ${statisticCard('Highest Day', stats.highest, stats.highest?.day || '')}
        ${statisticCard('Lowest Day', stats.lowest, stats.lowest?.day || '')}
        ${statisticCard('Average Daily', stats.averageDaily)}
        ${statisticCard('Average Hourly', stats.averageHourly)}
        ${statisticCard('Maximum Interval', stats.maximum, stats.maximum ? localTime(stats.maximum.timestamp) : '')}
        ${statisticCard('Minimum Interval', stats.minimum, stats.minimum ? localTime(stats.minimum.timestamp) : '')}
      </div>
    </section>`;
  }

  function billingCoverageWarning(billing) {
    const coverage = billing?.coverage || {};
    if (!billing?.billing_period_label) return '';
    if (coverage.coverage_complete || coverage.complete) {
      return `<div class="electricity-history-coverage complete"><strong>Billing period:</strong> ${safe(billing.billing_period_label)}</div>`;
    }
    const actualFrom = coverage.calculation_from_ts || coverage.actual_from_ts;
    const actualTo = coverage.calculation_to_ts || coverage.actual_to_ts;
    return `<div class="electricity-history-coverage partial"><strong>Incomplete billing data</strong><small>Requested billing period: ${safe(billing.billing_period_label)}</small><small>Calculated from available data: ${safe(localTime(actualFrom))} – ${safe(localTime(actualTo))}</small><small>Coverage: ${safe(Number(coverage.coverage_percent || 0).toFixed(1))}%</small></div>`;
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = name;
    anchor.hidden = true;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function csvExport() {
    if (state.exportLoading || !state.history?.start || !state.history?.end) return;
    state.exportLoading = true;
    state.historyError = null;
    render();
    const query = new URLSearchParams({
      start: state.history.start,
      end: state.history.end,
      bucket: state.history.bucket,
      format: 'csv',
    });
    try {
      const response = await fetch(`/api/electricity/history?${query.toString()}`, {
        credentials: 'same-origin',
        headers: {'Accept': 'text/csv'},
      });
      if (!response.ok) {
        const contentType = response.headers.get('Content-Type') || '';
        const errorPayload = contentType.includes('application/json')
          ? await response.json().catch(() => ({}))
          : {detail: await response.text().catch(() => '')};
        throw new Error(errorPayload.detail || `CSV export failed (${response.status})`);
      }
      const disposition = response.headers.get('Content-Disposition') || '';
      const matched = disposition.match(/filename="?([^";]+)"?/i);
      const fallback = `electricity-history-${thailandDate(new Date(state.history.start))}-to-${thailandDate(new Date(state.history.end))}.csv`;
      download(await response.blob(), matched?.[1] || fallback);
    } catch (error) {
      state.historyError = error?.message || 'CSV export is unavailable.';
    } finally {
      state.exportLoading = false;
      render();
    }
  }

  function pngExport() {
    const svg = document.getElementById('electricityHistoryChart');
    if (!svg) return;
    const image = new Image();
    const canvas = document.createElement('canvas');
    canvas.width = 1200;
    canvas.height = 420;
    image.onload = () => {
      const context = canvas.getContext('2d');
      context.fillStyle = '#0d1520';
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(blob => blob && download(blob, `electricity-${state.range}.png`));
    };
    image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(svg));
  }

  function tariffEmpty() {
    return `<div class="electricity-tariff-empty"><strong>Tariff not configured</strong><p>Run the tariff configuration helper on TinkerBoard and restart the service.</p><details><summary>Advanced Setup</summary><pre>/opt/smart-condo-dashboard-run/venv/bin/python \\\n/opt/smart-condo-dashboard/scripts/generate_electricity_tariff_config.py</pre></details></div>`;
  }

  function render() {
    const host = document.getElementById('electricityPage');
    const payload = state.status;
    if (!host) return;
    if (!payload) {
      host.innerHTML = '<div class="card"><div class="empty">Electricity data is not available.</div></div>';
      return;
    }
    const diagnostics = payload.diagnostics || {};
    const runtimeIp = diagnostics.runtime_ip || diagnostics.configured_ip || null;
    const pollLatency = diagnostics.poll_latency_ms ?? diagnostics.latency_ms ?? null;
    const source = String(diagnostics.source || 'unknown');
    const historySummary = state.history?.summary || {};
    const billing = state.billing || {};
    const tariff = state.tariff || {};
    const sync = state.tariffSync || {};
    const mapping = source === 'tuya_local' ? badge('Mapping', diagnostics.mapping_verified === true ? 'Verified' : 'Provisional', diagnostics.mapping_verified === true ? 'ok' : 'warn') : '';
    const safeDiag = ['mapping_verified', 'stale', 'last_success', 'last_attempt_ts', 'last_error', 'consecutive_failures', 'configured_ip', 'runtime_ip', 'auto_discovery', 'last_scan_ts', 'last_scan_result', 'scan_count', 'poller_started', 'poller_alive'];
    const rangeButtons = [
      ['24h', '24h'],
      ['7d', '7d'],
      ['30d', '30d'],
      ['custom', 'Custom'],
    ];
    const bucketChoices = [
      ['auto', 'Auto'], ['15m', '15 minutes'], ['30m', '30 minutes'],
      ['hour', '1 hour'], ['3h', '3 hours'], ['day', '1 day'],
    ];
    const partial = !(billing.coverage?.coverage_complete || billing.coverage?.complete);
    const actualUsageLabel = partial ? 'Actual usage in available data' : 'Usage';
    const actualCostLabel = partial ? 'Estimated cost for available data' : 'Estimated bill';

    host.innerHTML = `
      ${summaryCards()}
      ${state.comparisonError ? `<div class="electricity-history-message error">${safe(state.comparisonError)}</div>` : ''}
      ${statisticsPanel()}
      <section class="electricity-history-card">
        <div class="electricity-section-head"><div><h2>Consumption History</h2><small>Energy bars with a gap-aware three-interval moving average</small></div></div>
        <div class="electricity-history-toolbar" aria-label="Electricity history controls">
          <div class="electricity-toolbar-group"><span>Range</span><div class="electricity-range-buttons">${rangeButtons.map(([key, label]) => `<button class="btn ghost ${state.range === key ? 'active' : ''}" data-electricity-range="${key}">${label}</button>`).join('')}</div></div>
          <label class="electricity-toolbar-group electricity-bucket-control"><span>Resolution</span><select data-electricity-bucket>${bucketChoices.map(([value,label])=>`<option value="${value}" ${state.bucketMode===value?'selected':''}>${label}</option>`).join('')}</select></label>
          <div class="electricity-toolbar-group electricity-history-available"><span>History</span><strong>${state.history?.available_range?.start ? safe(localDate(state.history.available_range.start)) : 'Unavailable'} <i>→</i> ${state.history?.available_range?.end ? (thailandDate(new Date(epoch(state.history.available_range.end) * 1000)) === thailandDate() ? 'Today' : safe(localDate(state.history.available_range.end))) : 'Unavailable'}</strong></div>
          <div class="electricity-range-buttons electricity-export-actions"><button class="btn ghost electricity-export-button" data-electricity-export="csv" ${state.historyLoading || state.exportLoading || !state.history ? 'disabled' : ''}>${state.exportLoading ? 'Exporting…' : 'Export CSV'}</button><button class="btn ghost electricity-export-button" data-electricity-export="png" ${state.historyLoading || !state.history ? 'disabled' : ''}>PNG</button></div>
        </div>
        <form class="electricity-custom-range${state.customVisible || state.range === 'custom' ? ' is-visible' : ''}" data-electricity-custom>
          <label>Start date<input type="date" name="start" value="${safe(state.customStart)}" max="${safe(thailandDate())}"></label>
          <label>End date<input type="date" name="end" value="${safe(state.customEnd)}" max="${safe(thailandDate())}"></label>
          <button class="btn ghost" type="submit">Apply</button>
          <button class="btn ghost" type="button" data-electricity-custom-reset>Reset</button>
        </form>
        ${historyRequestState()}
        <div class="electricity-chart-meta"><div><strong>${safe(Number(historySummary.total_energy_kwh || 0).toFixed(3))} kWh</strong><span>${number(historySummary.total_cost_thb) === null ? 'Cost unavailable' : safe(money(historySummary.total_cost_thb))}</span></div><div><span>Selected range</span><strong>${state.history ? `${safe(localDate(state.history.start))} – ${safe(localDate(state.history.end))}` : 'Not available'}</strong></div><div><span>Actual resolution</span><strong>${safe(bucketLabel(state.history?.bucket))}</strong></div></div>
        <div class="electricity-chart-tools"><div><strong>Interval consumption</strong><small>Missing intervals remain gaps.</small></div><div class="electricity-zoom-tools"><button class="btn ghost" data-electricity-view="zoom-in">Zoom +</button><button class="btn ghost" data-electricity-view="zoom-out">Zoom −</button><button class="btn ghost" data-electricity-view="pan-left">Pan ←</button><button class="btn ghost" data-electricity-view="pan-right">Pan →</button><button class="btn ghost" data-electricity-view="reset">Reset</button></div></div>
        ${renderChart()}
      </section>
      <details class="electricity-meter-details"><summary>Live Meter Details</summary>
        <div class="electricity-badges">${badge('Meter', healthName(payload.health, payload), payload.health === 'offline' ? 'bad' : payload.health === 'healthy' ? 'ok' : 'warn')}${badge('Source', sourceName(source))}${mapping}</div>
        <div class="electricity-primary-grid">${metric('Voltage', payload.voltage, 'V')}${metric('Current', payload.current, 'A')}${metric('Active Power', payload.power, 'W')}${metric('Total Energy', payload.total_energy, 'kWh')}</div>
        <div class="electricity-secondary-grid">${metric('Status / Health', healthName(payload.health, payload), '', true)}${metric('Last Update', localTime(payload.last_update || diagnostics.last_success), '', true)}${metric('Runtime IP', runtimeIp, '', true)}${metric('Poll Latency', pollLatency, pollLatency == null ? '' : 'ms', true)}${metric('Data Source', sourceName(source), '', true)}</div>
      </details>
      <section class="electricity-cost-card">
        <div class="card-head"><div><h2>Electricity Cost</h2><small>Billing cycle cuts on day ${safe(billing.billing_cycle_day || 2)} of each month</small></div></div>
        ${billingCoverageWarning(billing)}
        ${!tariff.valid || billing.configured === false ? tariffEmpty() : `
          <div class="electricity-cost-grid">${metric(actualUsageLabel, billing.actual_partial_usage_kwh ?? billing.usage_kwh, 'kWh', true)}${metric(actualCostLabel, billing.actual_partial_cost ?? billing.total, 'THB', true)}${metric('Projected cycle usage', billing.projected_cycle_usage_kwh, 'kWh', true)}${metric('Projected cycle bill', billing.projected_cycle_bill, 'THB', true)}</div>
          <div class="electricity-billing-breakdown">${[['Energy charge', billing.base_energy_charge], ['Ft', billing.ft_charge], ['Service charge', billing.service_charge], ['VAT', billing.vat], ['Total for available data', billing.actual_partial_cost ?? billing.total]].map(([label, value]) => `<div><span>${label}</span><strong>${value == null ? 'Not available' : `${Number(value).toFixed(2)} THB`}</strong></div>`).join('')}</div>
          <div class="electricity-note">${safe(tariff.tariff_name || billing.tariff_name || 'Configured tariff')} · ${safe(tariff.effective_date || billing.effective_date || 'No effective date')} · Source: ${safe(sync.source || 'manual')} · ${safe(sync.status || 'manual_update_required')} · Estimated from configured tariff. This is not an official utility invoice.</div>
        `}
      </section>
      <details class="electricity-diagnostics"><summary>Advanced Diagnostics</summary><div class="electricity-diagnostics-grid">${safeDiag.map(key => `<div class="electricity-diagnostic"><span>${safe(key)}</span><strong>${safe(key.includes('_ts') || key === 'last_success' ? localTime(diagnostics[key]) : diagnostics[key] ?? 'Not available')}</strong></div>`).join('')}</div></details>`;
    bind();
    installChartInteraction();
  }

  function bind() {
    document.querySelectorAll('[data-electricity-range]').forEach(button => button.onclick = async () => {
      const range = button.dataset.electricityRange;
      if (range === 'custom') {
        state.customVisible = true;
        state.range = 'custom';
        render();
        return;
      }
      state.customVisible = false;
      await loadHistory(range);
    });
    const bucket = document.querySelector('[data-electricity-bucket]');
    if (bucket) bucket.onchange = async () => {
      state.bucketMode = bucket.value;
      if (state.range === 'custom' && (!state.customStart || !state.customEnd)) {
        render();
        return;
      }
      await loadHistory(state.range, state.customStart, state.customEnd);
    };
    document.querySelector('[data-electricity-custom]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      state.customStart = String(form.get('start') || '');
      state.customEnd = String(form.get('end') || '');
      state.customVisible = true;
      await loadHistory('custom', state.customStart, state.customEnd);
    });
    document.querySelector('[data-electricity-custom-reset]')?.addEventListener('click', () => {
      state.customStart = '';
      state.customEnd = '';
      state.customVisible = false;
      loadHistory('24h');
    });
    document.querySelectorAll('[data-electricity-view]').forEach(button => button.onclick = () => {
      const action = button.dataset.electricityView;
      if (action === 'zoom-in') state.zoom = Math.min(16, state.zoom * 2);
      if (action === 'zoom-out') state.zoom = Math.max(1, state.zoom / 2);
      if (action === 'pan-left') state.pan = Math.max(0, state.pan - 0.2);
      if (action === 'pan-right') state.pan = Math.min(1, state.pan + 0.2);
      if (action === 'reset') { state.zoom = 1; state.pan = 0; }
      render();
    });
    const csv = document.querySelector('[data-electricity-export="csv"]');
    const png = document.querySelector('[data-electricity-export="png"]');
    if (csv) csv.onclick = csvExport;
    if (png) png.onclick = pngExport;
  }

  installUi();
  const originalRefresh = window.refresh;
  const originalRenderPage = window.renderPage;
  window.refresh = async function refreshWithElectricity() {
    await Promise.allSettled([originalRefresh(), loadStatus(), loadSummary(), loadTariff(), loadTariffSync()]);
    window.renderPage(window.currentPage());
  };
  window.renderPage = function renderPageWithElectricity(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'electricity') render();
  };
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  const initialData = document.readyState === 'loading'
    ? Promise.resolve()
    : Promise.allSettled([loadStatus(), loadSummary(), loadTariff(), loadTariffSync()]);
  initialData
    .then(() => Promise.allSettled([loadHistory(), loadComparison()]))
    .then(() => { if (window.currentPage() === 'electricity') render(); });
  window.DashboardElectricityHistory = {
    state, historyRequest, csvExport, axisLabelStride, splitSegments,
    movingAverage, analyticsStatistics, tooltipContent, bucketLabel,
  };
  window.DashboardElectricityBilling = {
    activate: activateBilling,
    deactivate: deactivateBilling,
    refresh: refreshBilling,
    diagnostics: () => ({
      active: state.billingOwnerActive,
      timer_active: false,
      request_active: Boolean(state.billingInFlight),
    }),
  };
})();
