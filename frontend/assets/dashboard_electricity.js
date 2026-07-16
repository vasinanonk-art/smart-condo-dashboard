(() => {
  'use strict';
  if (window.__dashboardElectricityInstalled) return;
  window.__dashboardElectricityInstalled = true;

  const MAX_GAP_SEC = 900;
  const state = {
    status: null,
    summary: null,
    billing: null,
    tariff: null,
    tariffSync: null,
    billingCycle: null,
    history: null,
    range: 'live',
    series: new Set(['power']),
    live: [],
    lastKey: null,
    zoom: 1,
    pan: 0,
  };

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const number = value => {
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
    try { state.summary = await window.get('/api/electricity/summary'); }
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

  function historyEndpoint(range) {
    if (range === 'current_billing_cycle' || range === 'previous_billing_cycle' || range === 'calendar_month') {
      const period = state.billingCycle?.[range === 'current_billing_cycle' ? 'current_period' : range === 'previous_billing_cycle' ? 'previous_period' : 'current_period'];
      if (range === 'calendar_month') return '/api/electricity/history?range=month';
      if (period?.from_ts && period?.to_ts) return `/api/electricity/history?range=24h&from=${period.from_ts}&to=${period.to_ts}`;
    }
    const apiRange = range === 'this_month' ? 'month' : range;
    return `/api/electricity/history?range=${encodeURIComponent(apiRange)}`;
  }

  async function loadHistory(range = state.range) {
    state.zoom = 1;
    state.pan = 0;
    if (range === 'live') {
      const first = state.live[0]?.ts || null;
      const last = state.live[state.live.length - 1]?.ts || null;
      state.history = {
        range: 'live',
        from: first,
        to: last,
        samples: state.live,
        summary: summarize(state.live),
        coverage: {first_sample_ts: first, last_sample_ts: last, complete: true, coverage_percent: 100},
        max_gap_sec: MAX_GAP_SEC,
      };
      return;
    }
    try {
      state.history = await window.get(historyEndpoint(range));
    } catch (error) {
      state.history = {range, samples: [], summary: {sample_count: 0}, coverage: {complete: false, coverage_percent: 0}};
      console.error('Electricity history failed', {name: error?.name || 'Error'});
    }
  }

  function chartData() {
    return state.range === 'live' ? state.live : (state.history?.samples || []);
  }

  function seriesDefinitions() {
    return [
      {key: 'power', label: 'Power', unit: 'W', cls: 'power'},
      {key: 'voltage', label: 'Voltage', unit: 'V', cls: 'voltage'},
      {key: 'current', label: 'Current', unit: 'A', cls: 'current'},
    ].filter(item => state.series.has(item.key));
  }

  function requestedWindow(rows) {
    if (state.range === 'live') {
      const first = epoch(rows[0]?.ts) || Math.floor(Date.now() / 1000) - 3600;
      const last = epoch(rows[rows.length - 1]?.ts) || Math.floor(Date.now() / 1000);
      return {start: first, end: Math.max(first + 1, last)};
    }
    return {
      start: Number(state.history?.from) || 0,
      end: Number(state.history?.to) || Math.floor(Date.now() / 1000),
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
      const ts = epoch(row.ts);
      return ts !== null && ts >= windowRange.start && ts <= windowRange.end;
    });
  }

  function splitSegments(rows, key, maxGap) {
    const segments = [];
    let current = [];
    rows.forEach(row => {
      const value = number(row[key]);
      const ts = epoch(row.ts);
      if (value === null || ts === null) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      if (current.length && ts - epoch(current[current.length - 1].ts) > maxGap) {
        segments.push(current);
        current = [];
      }
      current.push(row);
    });
    if (current.length) segments.push(current);
    return segments;
  }

  function renderChart() {
    const allRows = chartData().filter(row => epoch(row.ts)).sort((a, b) => epoch(a.ts) - epoch(b.ts));
    const series = seriesDefinitions();
    if (!allRows.length || !series.length) return '<div class="electricity-empty">No history samples for this range.</div>';
    const width = 900, height = 260, left = 58, right = 20, top = 18, bottom = 38;
    const plotRight = width - right, plotBottom = height - bottom, plotW = plotRight - left, plotH = plotBottom - top;
    const base = requestedWindow(allRows), windowRange = visibleWindow(base), rows = visibleRows(allRows, windowRange);
    if (!rows.length) return '<div class="electricity-empty">No samples in the current zoom window.</div>';
    const values = [];
    series.forEach(item => rows.forEach(row => {
      const value = number(row[item.key]);
      if (value !== null) values.push(value);
    }));
    if (!values.length) return '<div class="electricity-empty">No selected metrics are available.</div>';
    let min = Math.min(...values), max = Math.max(...values);
    if (min === max) { min -= 1; max += 1; }
    const xTs = ts => left + ((ts - windowRange.start) / Math.max(1, windowRange.end - windowRange.start)) * plotW;
    const y = value => top + (max - value) / (max - min) * plotH;
    const maxGap = Number(state.history?.max_gap_sec) || MAX_GAP_SEC;
    const paths = series.map(item => splitSegments(rows, item.key, maxGap).map(segment => {
      const path = segment.map((row, index) => `${index ? 'L' : 'M'}${xTs(epoch(row.ts)).toFixed(2)},${y(number(row[item.key])).toFixed(2)}`).join(' ');
      return `<path class="history-line ${item.cls}" d="${path}"/>`;
    }).join('')).join('');
    const positions = rows.map(row => xTs(epoch(row.ts)));
    state.renderedChart = {rows, series, positions, plot: {left, right: plotRight, top, bottom: plotBottom}, y, min, max, windowRange};
    return `<div class="electricity-history-chart-wrap"><svg id="electricityHistoryChart" class="electricity-history-chart" viewBox="0 0 ${width} ${height}"><line class="axis" x1="${left}" y1="${plotBottom}" x2="${plotRight}" y2="${plotBottom}"/>${paths}<g class="history-hover" style="display:none"><line class="history-crosshair" y1="${top}" y2="${plotBottom}"/><g class="history-points"></g></g><rect class="history-hit" x="${left}" y="${top}" width="${plotW}" height="${plotH}" fill="transparent"/></svg><div class="electricity-history-tooltip" style="display:none"></div></div>`;
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
      renderTooltip: ({row}) => `<strong>${safe(localTime(row.ts))}</strong>${model.series.map(item => `<span>${safe(item.label)}: ${number(row[item.key]) === null ? 'Not available' : safe(number(row[item.key]).toFixed(2))} ${item.unit}</span>`).join('')}`,
    });
  }

  function coverageMessage() {
    if (state.range === 'live') return '';
    const coverage = state.history?.coverage || {};
    const first = coverage.first_sample_ts || coverage.actual_from_ts;
    const last = coverage.last_sample_ts || coverage.actual_to_ts;
    if (!first) return '<div class="electricity-history-coverage partial">No persistent history has been collected for this range yet.</div>';
    if (coverage.complete || coverage.coverage_complete) {
      return `<div class="electricity-history-coverage complete">History available: ${safe(localTime(first))} – ${safe(localTime(last))} · Complete requested coverage</div>`;
    }
    return `<div class="electricity-history-coverage partial"><strong>Partial history</strong><small>Available data: ${safe(localTime(first))} – ${safe(localTime(last))}</small><small>${safe(Number(coverage.coverage_percent || 0).toFixed(1))}% of the requested period is covered. Empty time is not interpolated.</small></div>`;
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
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function csvExport() {
    const rows = chartData();
    const lines = ['timestamp,voltage,current,power,total_energy,source,health', ...rows.map(row => [epoch(row.ts) || '', row.voltage ?? '', row.current ?? '', row.power ?? '', row.total_energy ?? '', row.source ?? '', row.health ?? ''].map(value => `"${String(value).replaceAll('"', '""')}"`).join(','))];
    download(new Blob([lines.join('\n')], {type: 'text/csv'}), `electricity-${state.range}.csv`);
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
    const historySummary = (state.range === 'live' ? summarize(state.live) : state.history?.summary) || {};
    const billing = state.billing || {};
    const tariff = state.tariff || {};
    const sync = state.tariffSync || {};
    const mapping = source === 'tuya_local' ? badge('Mapping', diagnostics.mapping_verified === true ? 'Verified' : 'Provisional', diagnostics.mapping_verified === true ? 'ok' : 'warn') : '';
    const safeDiag = ['mapping_verified', 'stale', 'last_success', 'last_attempt_ts', 'last_error', 'consecutive_failures', 'configured_ip', 'runtime_ip', 'auto_discovery', 'last_scan_ts', 'last_scan_result', 'scan_count', 'poller_started', 'poller_alive'];
    const rangeButtons = [
      ['live', 'Live'],
      ['24h', '24H'],
      ['7d', '7D'],
      ['30d', '30D'],
      ['current_billing_cycle', 'Current Billing Cycle'],
      ['previous_billing_cycle', 'Previous Billing Cycle'],
      ['calendar_month', 'Calendar Month'],
    ];
    const partial = !(billing.coverage?.coverage_complete || billing.coverage?.complete);
    const actualUsageLabel = partial ? 'Actual usage in available data' : 'Usage';
    const actualCostLabel = partial ? 'Estimated cost for available data' : 'Estimated bill';

    host.innerHTML = `
      <div class="electricity-badges">${badge('Meter', healthName(payload.health, payload), payload.health === 'offline' ? 'bad' : payload.health === 'healthy' ? 'ok' : 'warn')}${badge('Source', sourceName(source))}${mapping}</div>
      <div class="electricity-primary-grid">${metric('Voltage', payload.voltage, 'V')}${metric('Current', payload.current, 'A')}${metric('Active Power', payload.power, 'W')}${metric('Total Energy', payload.total_energy, 'kWh')}</div>
      <div class="electricity-secondary-grid">${metric('Status / Health', healthName(payload.health, payload), '', true)}${metric('Last Update', localTime(payload.last_update || diagnostics.last_success), '', true)}${metric('Runtime IP', runtimeIp, '', true)}${metric('Poll Latency', pollLatency, pollLatency == null ? '' : 'ms', true)}${metric('Data Source', sourceName(source), '', true)}</div>
      <section class="electricity-history-card">
        <div class="card-head"><div><h2>Electricity History</h2><small>Persistent backend history with real timestamp coverage</small></div><div><button class="btn ghost" data-electricity-export="csv">CSV</button><button class="btn ghost" data-electricity-export="png">PNG</button></div></div>
        <div class="electricity-range-buttons">${rangeButtons.map(([key, label]) => `<button class="btn ghost ${state.range === key ? 'active' : ''}" data-electricity-range="${key}">${label}</button>`).join('')}</div>
        <div class="electricity-chart-tools"><div class="electricity-series-toggles">${[['power', 'Power'], ['voltage', 'Voltage'], ['current', 'Current']].map(([key, label]) => `<label><input type="checkbox" data-electricity-series="${key}" ${state.series.has(key) ? 'checked' : ''}> ${label}</label>`).join('')}</div><div class="electricity-zoom-tools"><button class="btn ghost" data-electricity-view="zoom-in">Zoom +</button><button class="btn ghost" data-electricity-view="zoom-out">Zoom −</button><button class="btn ghost" data-electricity-view="pan-left">Pan ←</button><button class="btn ghost" data-electricity-view="pan-right">Pan →</button><button class="btn ghost" data-electricity-view="reset">Reset</button></div></div>
        ${coverageMessage()}${renderChart()}
        <div class="electricity-history-summary">${metric('Minimum Power', historySummary.min_power, 'W', true)}${metric('Maximum Power', historySummary.max_power, 'W', true)}${metric('Average Power', historySummary.avg_power, 'W', true)}${metric('Energy Used', historySummary.energy_used_kwh, 'kWh', true)}</div>
      </section>
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
      state.range = button.dataset.electricityRange;
      await loadHistory();
      render();
    });
    document.querySelectorAll('[data-electricity-series]').forEach(input => input.onchange = () => {
      input.checked ? state.series.add(input.dataset.electricitySeries) : state.series.delete(input.dataset.electricitySeries);
      render();
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
    await Promise.allSettled([originalRefresh(), loadStatus(), loadSummary(), loadBillingCycleStatus(), loadBilling(), loadTariff(), loadTariffSync()]);
    await loadHistory();
    window.renderPage(window.currentPage());
  };
  window.renderPage = function renderPageWithElectricity(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'electricity') render();
  };
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  Promise.allSettled([loadStatus(), loadSummary(), loadBillingCycleStatus(), loadBilling(), loadTariff(), loadTariffSync()])
    .then(() => loadHistory())
    .then(() => { if (window.currentPage() === 'electricity') render(); });
})();