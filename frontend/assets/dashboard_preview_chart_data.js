(() => {
  'use strict';

  const parameters = new URLSearchParams(window.location.search);
  if (parameters.get('previewChartData') !== '1') return;

  const start = Date.parse('2026-07-28T17:00:00Z');
  const stepMs = 30 * 60 * 1000;
  const timestamps = Array.from({length:24}, (_, index) => (
    Math.floor((start + index * stepMs) / 1000)
  ));
  const sensorRows = timestamps.map((ts, index) => ({
    ts,
    temperature:index === 8 ? null : 20 + index * 0.5,
    humidity:index === 12 ? null : 40 + index,
    pm25_living_room:index === 15 ? null : 5 + index,
    pm25_bedroom:index === 6 ? null : 8 + index * 0.75,
  }));
  const electricityPoints = timestamps.map((ts, index) => {
    const energy = index === 11 ? null : Number((0.1 + index * 0.05).toFixed(2));
    return {
      timestamp:new Date(ts * 1000).toISOString(),
      interval_start:new Date(ts * 1000).toISOString(),
      interval_end:new Date((ts * 1000) + stepMs).toISOString(),
      energy_kwh:energy,
      cost_thb:energy === null ? null : energy * 5,
      data_quality:energy === null ? 'missing' : 'valid',
    };
  });
  const first = sensorRows[0];
  const last = sensorRows[sensorRows.length - 1];
  const firstElectricity = electricityPoints[0];
  const lastElectricity = electricityPoints[electricityPoints.length - 1];
  const startIso = firstElectricity.timestamp;
  const endIso = new Date((timestamps[timestamps.length - 1] * 1000) + stepMs).toISOString();
  const totalEnergy = electricityPoints.reduce(
    (total, point) => total + (Number(point.energy_kwh) || 0),
    0
  );
  const totalCost = electricityPoints.reduce(
    (total, point) => total + (Number(point.cost_thb) || 0),
    0
  );

  const historyPayload = {
    start:startIso,
    end:endIso,
    timezone:'Asia/Bangkok',
    bucket:'30m',
    points:electricityPoints,
    summary:{
      total_energy_kwh:totalEnergy,
      total_cost_thb:totalCost,
      point_count:electricityPoints.length,
    },
    available_range:{start:startIso, end:endIso},
    max_gap_sec:3600,
  };
  const responses = new Map([
    ['/api/condo/history', {history:sensorRows, current:last}],
    ['/api/condo/status', {sensor:last, presence:[]}],
    ['/api/air-quality', {
      configured:true,
      living_room:{value:last.pm25_living_room, stale:false},
      bedroom:{value:last.pm25_bedroom, stale:false},
    }],
    ['/api/electricity/status', {
      health:'healthy',
      power:625,
      voltage:230,
      current:2.7,
      total_energy:1250,
      diagnostics:{source:'preview_data'},
    }],
    ['/api/electricity/summary', {
      health:'healthy',
      power:625,
      total_energy:1250,
    }],
    ['/api/electricity/history', historyPayload],
    ['/api/electricity/billing-cycle/status', {}],
    ['/api/electricity/billing-cycle', {}],
    ['/api/electricity/tariff/status', {configured:false, valid:false}],
    ['/api/electricity/tariff/sync-status', {}],
  ]);

  const originalFetch = window.fetch.bind(window);
  window.fetch = function previewChartFetch(input, options) {
    const url = new URL(
      typeof input === 'string' ? input : input.url,
      window.location.href
    );
    let payload = responses.get(url.pathname);
    if (url.pathname === '/api/electricity/history' && url.searchParams.has('comparison')) {
      payload = {
        comparison:url.searchParams.get('comparison'),
        current:{point_count:24, total_energy_kwh:totalEnergy, total_cost_thb:totalCost},
        previous:{point_count:24, total_energy_kwh:totalEnergy * 0.9, total_cost_thb:totalCost * 0.9},
        percentage_difference:11.1,
      };
    }
    if (payload !== undefined) {
      return Promise.resolve(new Response(JSON.stringify(payload), {
        status:200,
        headers:{'Content-Type':'application/json'},
      }));
    }
    return originalFetch(input, options);
  };

  function localTime(ts) {
    return new Intl.DateTimeFormat('en-GB', {
      timeZone:'Asia/Bangkok',
      day:'2-digit',
      month:'short',
      hour:'2-digit',
      minute:'2-digit',
      hour12:false,
    }).format(new Date(ts * 1000));
  }

  const labels = {
    overviewChart:`Temperature ${first.temperature.toFixed(1)}→${last.temperature.toFixed(1)} °C · Humidity ${first.humidity.toFixed(0)}→${last.humidity.toFixed(0)}%`,
    overviewPmChart:`PM2.5 ${first.pm25_living_room.toFixed(1)}→${last.pm25_living_room.toFixed(1)} µg/m³`,
    airChart:`PM2.5 ${first.pm25_living_room.toFixed(1)}→${last.pm25_living_room.toFixed(1)} µg/m³`,
    electricityHistoryChart:`Energy ${firstElectricity.energy_kwh.toFixed(2)}→${lastElectricity.energy_kwh.toFixed(2)} kWh`,
  };
  const timeRange = `${localTime(first.ts)} → ${localTime(last.ts)}`;

  function installLabels() {
    Object.entries(labels).forEach(([id, values]) => {
      const svg = document.getElementById(id);
      const host = svg?.parentElement;
      if (!host || host.querySelector(`[data-preview-chart-label="${id}"]`)) return;
      const badge = document.createElement('div');
      badge.className = 'preview-chart-data-label';
      badge.dataset.previewChartLabel = id;
      badge.innerHTML = `<strong>Preview data</strong><span>${values}</span><small>${timeRange}</small>`;
      host.prepend(badge);
    });
  }

  const style = document.createElement('style');
  style.textContent = `
    .preview-chart-data-label {
      display:grid;
      gap:3px;
      margin:0 0 8px;
      padding:8px 11px;
      border:1px solid rgba(91,167,255,.45);
      border-radius:10px;
      background:rgba(91,167,255,.09);
      color:#dcecff;
      font-size:12px;
    }
    .preview-chart-data-label strong { color:#78b8ff; }
    .preview-chart-data-label small { color:#9eb1c6; }
  `;
  document.head.appendChild(style);
  document.addEventListener('DOMContentLoaded', installLabels);
  const observer = new MutationObserver(installLabels);
  observer.observe(document.documentElement, {childList:true, subtree:true});

  window.DashboardPreviewChartData = Object.freeze({
    sensorRows,
    electricityPoints,
    historyPayload,
    labels,
    installLabels,
  });
})();
