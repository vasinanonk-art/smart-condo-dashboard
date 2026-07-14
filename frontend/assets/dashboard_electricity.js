(() => {
  'use strict';

  const state = { payload: null, samples: [], lastSampleKey: null };
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const valueOrNA = value => value === null || value === undefined || value === '' ? 'Not available' : value;

  function installUi() {
    document.querySelectorAll('.nav, .mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="electricity"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'electricity';
      button.dataset.short = 'EL';
      button.textContent = 'Electricity';
      const topology = host.querySelector('[data-nav="topology"]');
      if (topology) host.insertBefore(button, topology);
      else host.appendChild(button);
    });
    if (document.querySelector('[data-page="electricity"]')) return;
    const section = document.createElement('section');
    section.className = 'page';
    section.dataset.page = 'electricity';
    section.innerHTML = '<div id="electricityPage" class="electricity-page"><div class="card"><div class="empty">Electricity data is loading.</div></div></div>';
    document.querySelector('.main')?.appendChild(section);
  }

  function toEpoch(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric > 1e12 ? Math.floor(numeric / 1000) : Math.floor(numeric);
    const parsed = Date.parse(value);
    return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
  }

  function localTime(value) {
    const ts = toEpoch(value);
    if (!ts) return 'Not available';
    return new Intl.DateTimeFormat('en-GB', {
      timeZone: 'Asia/Bangkok', year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
    }).format(new Date(ts * 1000));
  }

  function addSample(payload) {
    const diagnostics = payload?.diagnostics || {};
    const ts = toEpoch(payload?.last_update || diagnostics.last_success || diagnostics.last_attempt_ts) || Math.floor(Date.now() / 1000);
    const key = `${ts}:${payload?.power ?? ''}:${payload?.voltage ?? ''}:${payload?.current ?? ''}`;
    if (key === state.lastSampleKey) return;
    state.lastSampleKey = key;
    state.samples.push({ ts, voltage: payload?.voltage, current: payload?.current, power: payload?.power });
    state.samples = state.samples.slice(-20);
  }

  async function loadElectricity() {
    try {
      const payload = await window.get('/api/electricity/status');
      state.payload = payload;
      addSample(payload);
    } catch (error) {
      console.warn('Electricity refresh failed:', error.message);
    }
  }

  function metricCard(label, value, unit, secondary = false) {
    const display = valueOrNA(value);
    return `<div class="electricity-metric${secondary ? ' secondary' : ''}"><span>${safe(label)}</span><strong>${safe(display)}${display !== 'Not available' && unit ? `<small>${safe(unit)}</small>` : ''}</strong></div>`;
  }

  function badge(label, value, cls) {
    return `<span class="electricity-badge ${cls || ''}">${safe(label)} · ${safe(value)}</span>`;
  }

  function niceScale(value) {
    const samples = state.samples.map(item => Number(item.power)).filter(Number.isFinite);
    const observed = Math.max(Number(value) || 0, ...samples, 1);
    const padded = observed * 1.25;
    const magnitude = 10 ** Math.floor(Math.log10(padded));
    const normalized = padded / magnitude;
    const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
    return step * magnitude;
  }

  function loadLabel(power, max) {
    if (!Number.isFinite(Number(power))) return 'Load unavailable';
    const ratio = max > 0 ? Number(power) / max : 0;
    if (ratio < 0.3) return 'Low load';
    if (ratio < 0.7) return 'Normal load';
    return 'High load';
  }

  function renderChart() {
    const samples = state.samples.filter(item => Number.isFinite(Number(item.power)));
    if (samples.length < 2) return '<div class="electricity-empty">Waiting for recent power samples.</div>';
    const width = 900, height = 210, left = 44, right = 16, top = 16, bottom = 28;
    const plotW = width - left - right, plotH = height - top - bottom;
    const values = samples.map(item => Number(item.power));
    const min = Math.min(...values), max = Math.max(...values);
    const range = Math.max(1, max - min);
    const points = samples.map((item, index) => {
      const x = left + (index / (samples.length - 1)) * plotW;
      const y = top + (1 - (Number(item.power) - min) / range) * plotH;
      return [x, y];
    });
    const line = points.map(([x, y], index) => `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    const area = `${line} L${points[points.length - 1][0].toFixed(1)},${(top + plotH).toFixed(1)} L${points[0][0].toFixed(1)},${(top + plotH).toFixed(1)} Z`;
    return `<svg class="electricity-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Recent power trend"><line class="axis" x1="${left}" y1="${top + plotH}" x2="${width - right}" y2="${top + plotH}"/><path class="area" d="${area}"/><path class="line" d="${line}"/><text x="4" y="${top + 5}">${safe(max.toFixed(1))} W</text><text x="4" y="${top + plotH}">${safe(min.toFixed(1))} W</text><text x="${left}" y="${height - 7}">${safe(localTime(samples[0].ts).slice(12))}</text><text text-anchor="end" x="${width - right}" y="${height - 7}">${safe(localTime(samples[samples.length - 1].ts).slice(12))}</text></svg>`;
  }

  function render() {
    const host = document.getElementById('electricityPage');
    if (!host) return;
    const payload = state.payload;
    if (!payload) {
      host.innerHTML = '<div class="card"><div class="empty">Electricity data is not available.</div></div>';
      return;
    }
    const d = payload.diagnostics || {};
    const online = payload.health === 'healthy' || payload.health === 'warning';
    const stale = d.stale === true;
    const max = niceScale(payload.power);
    const gaugePct = Number.isFinite(Number(payload.power)) ? Math.max(0, Math.min(100, Number(payload.power) / max * 100)) : 0;
    const diagnosticsKeys = ['source','configured','mapping_verified','stale','poll_latency_ms','last_success','last_attempt_ts','last_error','consecutive_failures','configured_ip','runtime_ip','auto_discovery','last_scan_ts','last_scan_result','scan_count'];
    host.innerHTML = `
      <div class="electricity-page-head">
        <div><h2>Electricity Monitoring</h2><p>Live PJ-1103 meter data from the condo</p></div>
        <div class="electricity-badges">
          ${badge('Meter', online ? 'Online' : payload.health === 'offline' ? 'Offline' : 'Unknown', online ? 'ok' : payload.health === 'offline' ? 'bad' : 'warn')}
          ${badge('Data', stale ? 'Stale' : 'Fresh', stale ? 'warn' : 'ok')}
          ${badge('Source', d.source || 'Unknown', '')}
          ${badge('Mapping', d.mapping_verified === true ? 'Verified' : 'Provisional', d.mapping_verified === true ? 'ok' : 'warn')}
        </div>
      </div>
      <div class="electricity-primary-grid">
        ${metricCard('Voltage', payload.voltage, 'V')}
        ${metricCard('Current', payload.current, 'A')}
        ${metricCard('Active Power', payload.power, 'W')}
        ${metricCard('Total Energy', payload.total_energy, 'kWh')}
      </div>
      <div class="electricity-secondary-grid">
        ${metricCard('Health', payload.health || 'unknown', '', true)}
        ${metricCard('Last Update', localTime(payload.last_update || d.last_success), '', true)}
        ${metricCard('Poll Latency', d.poll_latency_ms, d.poll_latency_ms == null ? '' : 'ms', true)}
        ${metricCard('Runtime IP', d.runtime_ip, '', true)}
        ${metricCard('Data Source', d.source, '', true)}
        ${metricCard('Consecutive Failures', d.consecutive_failures, '', true)}
      </div>
      <div class="electricity-live-grid">
        <section class="electricity-gauge-card"><div class="card-head"><h2>Live Power</h2></div><div class="electricity-gauge-value">${safe(valueOrNA(payload.power))}${payload.power == null ? '' : '<small> W</small>'}</div><div class="electricity-gauge-track"><div class="electricity-gauge-fill" style="width:${gaugePct.toFixed(1)}%"></div></div><div class="electricity-gauge-scale"><span>0 W</span><span>${safe(max.toFixed(0))} W dynamic scale</span></div><div class="electricity-load-label">${safe(loadLabel(payload.power, max))}</div></section>
        <section class="electricity-chart-card"><div class="card-head"><h2>Recent Power Trend</h2><small>Last ${state.samples.length} dashboard refresh samples</small></div>${renderChart()}</section>
      </div>
      <div class="electricity-unsupported-grid">
        ${metricCard('Energy Today', payload.energy_today, 'kWh', true)}
        ${metricCard('Energy Month', payload.energy_month, 'kWh', true)}
        ${metricCard('Frequency', payload.frequency, 'Hz', true)}
        ${metricCard('Power Factor', payload.power_factor, '', true)}
      </div>
      <div class="electricity-note">Daily/monthly usage and billing will be enabled after cumulative history and tariff configuration are completed.</div>
      <details class="electricity-diagnostics"><summary>Safe Diagnostics</summary><div class="electricity-diagnostics-grid">${diagnosticsKeys.map(key => `<div class="electricity-diagnostic"><span>${safe(key)}</span><strong>${safe(key.includes('_ts') || key === 'last_success' ? localTime(d[key]) : valueOrNA(d[key]))}</strong></div>`).join('')}</div></details>`;
  }

  installUi();
  const originalRefresh = window.refresh;
  const originalRenderPage = window.renderPage;
  const originalNav = window.nav;

  window.refresh = async function refreshWithElectricity() {
    await Promise.allSettled([originalRefresh(), loadElectricity()]);
    window.renderPage(window.currentPage());
  };
  window.renderPage = function renderPageWithElectricity(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'electricity') render();
  };
  window.nav = function navWithElectricity(page) {
    originalNav(page);
    if (page === 'electricity') {
      const title = document.getElementById('pageTitle');
      const subtitle = title?.nextElementSibling;
      if (title) title.textContent = 'Electricity Monitoring';
      if (subtitle) subtitle.textContent = 'Live PJ-1103 meter data from the condo';
      render();
    }
  };

  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  loadElectricity().then(() => window.renderPage(window.currentPage()));
})();
