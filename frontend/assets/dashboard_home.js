(() => {
  'use strict';
  if (window.SmartCondoHome) return;

  const element = id => document.getElementById(id);
  const number = value => {
    const parsed = Number(value);
    return value === null || value === undefined || value === ''
      || !Number.isFinite(parsed) ? null : parsed;
  };

  function greeting(date) {
    const hour = date.getHours();
    if (hour < 12) return 'Good morning';
    if (hour < 18) return 'Good afternoon';
    return 'Good evening';
  }

  function systemStatus(state) {
    const issues = [];
    if (!state.health?.mqtt_connected) issues.push('MQTT');
    if (!state.air?.configured) issues.push('Air quality');
    if (!state.sonoffAvailable) issues.push('Sonoff');
    return {
      label: issues.length ? `${issues.length} service${issues.length === 1 ? '' : 's'} need attention` : 'Home is operating normally',
      status: issues.length ? 'warning' : 'success',
    };
  }

  function metricCards(state) {
    const ui = window.SmartCondoUI;
    const electricity = window.DashboardElectricityHistory?.state || {};
    const power = number(electricity.status?.power);
    const temperature = number(state.sensor?.temperature);
    const onlineCameras = (state.cameras || []).filter(
      camera => camera.online === true
    ).length;
    const cameraCount = (state.cameras || []).length;
    const cameraStatusKnown = (state.cameras || []).some(
      camera => typeof camera.online === 'boolean'
    );
    const camerasDisconnected = cameraCount > 0
      && (state.cameras || []).every(camera => camera.online === false);
    const cards = [
      {
        label:'Power',
        iconName:'zap',
        value:power === null ? '--' : `${power.toFixed(0)} W`,
        footnote:power === null ? 'No Data' : 'Current demand',
        status:power === null ? 'neutral' : 'success',
      },
      {
        label:'Climate',
        iconName:'thermometer',
        value:temperature === null ? '--' : `${temperature.toFixed(1)}°`,
        footnote:temperature === null ? 'No Data' : 'Indoor temperature',
        status:temperature === null ? 'neutral' : 'success',
      },
      {
        label:'Network',
        iconName:'wifi',
        value:state.health?.mqtt_connected ? 'Online' : 'Offline',
        footnote:'MQTT connection',
        status:state.health?.mqtt_connected ? 'success' : 'warning',
      },
      {
        label:'Security',
        iconName:'shield-check',
        value:camerasDisconnected ? 'Offline' : cameraStatusKnown ? `${onlineCameras} / ${cameraCount}` : '--',
        footnote:camerasDisconnected ? 'Camera connection' : cameraStatusKnown ? 'Cameras available' : 'No Data',
        status:camerasDisconnected ? 'warning' : cameraStatusKnown ? onlineCameras ? 'success' : 'warning' : 'neutral',
      },
    ];
    return cards.map(card => ui.metricCard(card)).join('');
  }

  function todaySummary(state) {
    const electricity = window.DashboardElectricityHistory?.state || {};
    const power = number(electricity.status?.power);
    const temperature = number(state.sensor?.temperature);
    const parts = [];
    if (temperature !== null) parts.push(`${temperature.toFixed(1)}° indoors`);
    if (power !== null) parts.push(`${power.toFixed(0)} W now`);
    if (state.health?.mqtt_connected) parts.push('network online');
    return parts.length ? parts.join(' · ') : 'Waiting for live home data';
  }

  function energyPoints() {
    const points = window.DashboardElectricityHistory?.state?.history?.points;
    return Array.isArray(points) ? points : [];
  }

  function energySummary() {
    return window.DashboardElectricityHistory?.state?.history?.summary || {};
  }

  function drawEnergyChart() {
    const svg = element('homeEnergyChart');
    if (!svg) return;
    const rows = energyPoints();
    const values = rows.map(row => number(row.energy_kwh));
    const valid = values.filter(Number.isFinite);
    const widget = element('homeEnergyWidget');
    svg.setAttribute('viewBox', '0 0 960 250');
    if (!valid.length) {
      widget?.classList?.add('home-energy-empty-mode');
      svg.innerHTML = '';
      svg.hidden = true;
      const empty = element('homeEnergyEmpty');
      if (empty) empty.hidden = false;
      return;
    }
    widget?.classList?.remove('home-energy-empty-mode');
    svg.hidden = false;
    const empty = element('homeEnergyEmpty');
    if (empty) empty.hidden = true;
    const width = 960;
    const height = 250;
    const padding = {left:20, right:20, top:20, bottom:28};
    const maximum = Math.max(...valid, 0.001);
    const x = index => padding.left + index
      / Math.max(1, rows.length - 1)
      * (width - padding.left - padding.right);
    const y = value => padding.top + (maximum - value)
      / maximum
      * (height - padding.top - padding.bottom);
    const paths = [];
    let current = [];
    values.forEach((value, index) => {
      if (Number.isFinite(value)) {
        current.push(`${current.length ? 'L' : 'M'}${x(index).toFixed(1)},${y(value).toFixed(1)}`);
      } else if (current.length) {
        paths.push(current.join(' '));
        current = [];
      }
    });
    if (current.length) paths.push(current.join(' '));
    svg.innerHTML = `
      <line class="home-energy-baseline" x1="${padding.left}" y1="${height-padding.bottom}" x2="${width-padding.right}" y2="${height-padding.bottom}"></line>
      ${paths.map(path => `<path class="home-energy-line" d="${path}"></path>`).join('')}
    `;
  }

  function renderDevices(state) {
    const ui = window.SmartCondoUI;
    const host = element('homeDeviceCards');
    if (!host) return;
    const sonoff = state.sonoff?.devices || [];
    const lights = state.lights || [];
    const cameras = state.cameras || [];
    const cards = [
      {
        title:'Lighting',
        subtitle:`${lights.filter(device => device.online).length} of ${lights.length} online`,
        online:lights.length && lights.some(device => device.online),
        icon:'lightbulb',
      },
      {
        title:'Sonoff',
        subtitle:sonoff.length ? `${sonoff.filter(device => device.online).length} of ${sonoff.length} online` : 'No devices available',
        online:state.sonoffAvailable,
        icon:'toggle-right',
      },
      {
        title:'Cameras',
        subtitle:cameras.length ? `${cameras.filter(camera => camera.online).length} of ${cameras.length} online` : 'Configuration unavailable',
        online:cameras.length ? cameras.some(camera => camera.online) : null,
        icon:'camera',
      },
    ];
    host.innerHTML = cards.map(card => ui.deviceCard({
      title:card.title,
      subtitle:card.subtitle,
      status:ui.statusChip({
        label:card.online === true ? 'Online' : card.online === false ? 'Offline' : 'Unknown',
        status:card.online === true ? 'success' : card.online === false ? 'critical' : 'neutral',
      }),
      content:`<div class="home-device-icon">${ui.icon(card.icon)}</div>`,
    })).join('');
  }

  function renderSecondaryWidgets(state) {
    const ui = window.SmartCondoUI;
    const host = element('homeSecondaryWidgets');
    if (!host) return;
    const sonoff = state.sonoff?.devices || [];
    const lights = state.lights || [];
    const cameras = state.cameras || [];
    const deviceTotal = sonoff.length + lights.length + cameras.length;
    const onlineTotal = sonoff.filter(device => device.online).length
      + lights.filter(device => device.online).length
      + cameras.filter(device => device.online).length;
    const temperature = number(state.sensor?.temperature);
    const humidity = number(state.sensor?.humidity);
    const pm25 = number(state.air?.living_room?.value);
    const widgets = [
      {
        icon:'house-plug',
        title:'Devices',
        value:deviceTotal ? `${onlineTotal} online` : '--',
        summary:deviceTotal ? `${deviceTotal} household devices` : 'No Data',
        status:deviceTotal ? 'success' : 'neutral',
      },
      {
        icon:'thermometer-sun',
        title:'Environment',
        value:temperature === null ? '--' : `${temperature.toFixed(1)}°`,
        summary:humidity === null ? 'No Data' : `${humidity.toFixed(0)}% humidity`,
        status:temperature === null ? 'neutral' : 'success',
      },
      {
        icon:'wind',
        title:'Air Quality',
        value:pm25 === null ? '--' : `${pm25.toFixed(1)} µg/m³`,
        summary:pm25 === null ? 'No Data' : state.air?.living_room?.stale ? 'Last reading is stale' : 'Current living room reading',
        status:pm25 === null ? 'neutral' : state.air?.living_room?.stale ? 'warning' : 'success',
      },
    ];
    host.innerHTML = widgets.map(widget => (
      `<article class="home-secondary-card"><div class="home-secondary-icon">${ui.icon(widget.icon)}</div><div class="home-secondary-copy"><div class="home-secondary-title"><h2>${widget.title}</h2>${ui.statusChip({label:widget.status === 'success' ? 'Current' : widget.status === 'warning' ? 'Attention' : 'No Data', status:widget.status})}</div><strong>${widget.value}</strong><span>${widget.summary}</span></div></article>`
    )).join('');
  }

  function bedroomCamera(state) {
    return (state.cameras || []).find(camera => (
      String(camera.name || camera.display_name || '').toLowerCase().includes('bedroom')
      || String(camera.id || '').toLowerCase().includes('bedroom')
    ));
  }

  function renderQuickActions(state) {
    const ui = window.SmartCondoUI;
    const host = element('homeQuickActions');
    if (!ui || !host) return;
    const household = window.DashboardHouseholdDevices;
    const ac = household?.quickActionState?.() || {
      powerOn:false, powerOff:false, temperature26:false,
      reason:'Bedroom AC controls are unavailable.',
    };
    const camera = bedroomCamera(state);
    const snapshotAvailable = Boolean(camera?.online === true && camera.capabilities?.snapshot);
    const actions = [
      {label:'AC On', iconName:'power', enabled:ac.powerOn, kind:'ir', command:'power_on', confirm:true, reason:ac.reason},
      {label:'AC Off', iconName:'power-off', enabled:ac.powerOff, kind:'ir', command:'power_off', confirm:true, reason:ac.reason},
      {label:'AC 26°', iconName:'thermometer', enabled:ac.temperature26, kind:'temperature', reason:ac.reason},
      {label:'Bedroom Camera', iconName:'camera', enabled:snapshotAvailable, kind:'camera', cameraId:camera?.id ? encodeURIComponent(camera.id) : '', reason:camera?.online === false ? 'Bedroom Camera is offline.' : camera ? 'Snapshot is unavailable.' : 'Bedroom Camera is unavailable.'},
      {label:'Electricity', iconName:'zap', enabled:true, kind:'nav', page:'electricity'},
      {label:'Home Status', iconName:'network', enabled:true, kind:'nav', page:'topology'},
    ];
    host.innerHTML = actions.map(action => (
      `<div class="home-quick-action-item">${ui.quickAction({
        label:action.label,
        iconName:action.iconName,
        disabled:!action.enabled,
        attributes:`data-quick-action="${action.kind}"${action.command ? ` data-command="${action.command}"` : ''}${action.page ? ` data-page="${action.page}"` : ''}${action.cameraId ? ` data-camera-id="${action.cameraId}"` : ''}${action.confirm ? ' data-confirm="true"' : ''} aria-label="${action.enabled ? action.label : `${action.label}. ${action.reason}`}"${action.enabled ? '' : ` title="${action.reason}"`}`,
      })}${action.enabled ? '' : `<small>${action.reason}</small>`}</div>`
    )).join('');
    host.querySelectorAll('[data-quick-action]').forEach(button => button.addEventListener('click', async () => {
      if (button.dataset.quickAction === 'nav') {
        window.nav?.(button.dataset.page);
        return;
      }
      if (button.dataset.quickAction === 'camera') {
        window.open(`/api/camera-control/${button.dataset.cameraId}/snapshot`, '_blank', 'noopener');
        return;
      }
      const body = button.dataset.quickAction === 'temperature'
        ? {capability:'temperature', value:26}
        : {command:button.dataset.command};
      button.dataset.householdIrDevice = 'bed-room-air-conditioner';
      button.dataset.householdIrConfirm = button.dataset.confirm || 'false';
      await household?.sendIrCommand?.(button, body, host);
    }));
  }

  function organizeUtilityBar() {
    const row = document.querySelector('.home-utility-controls');
    const details = element('homeUtilityStatusDetails');
    const technical = element('dashboardCompactBadges');
    if (technical && details && technical.parentElement !== details) {
      details.appendChild(technical);
    }
    if (row && !row.dataset.utilityObserverInstalled && window.MutationObserver) {
      row.dataset.utilityObserverInstalled = 'true';
      const observer = new MutationObserver(mutations => {
        const badgeAdded = mutations.some(mutation => (
          [...mutation.addedNodes].some(node => node.id === 'dashboardCompactBadges')
        ));
        if (badgeAdded) organizeUtilityBar();
      });
      observer.observe(row, {childList:true});
    }
  }

  function renderStatistics(history, stat, fmt, safeText) {
    const host = element('overviewStats');
    if (!host) return;
    const groups = [
      ['Temperature', stat(history, 'temperature'), '°C'],
      ['Humidity', stat(history, 'humidity'), '%'],
      ['Living Room PM2.5', stat(history, 'pm25_living_room'), 'µg/m³'],
      ['Bedroom PM2.5', stat(history, 'pm25_bedroom'), 'µg/m³'],
    ];
    host.innerHTML = groups.map(([label, values, unit]) => (
      `<article class="home-stat-card"><h3 class="sc-widget-title">${safeText(label)}</h3><div class="home-stat-grid">${[
        ['Current', values.current],
        ['Average', values.avg],
      ].map(([name, value]) => `<div class="home-stat-value"><span>${name}</span><strong>${fmt(value)}${value === null ? '' : ` ${safeText(unit)}`}</strong></div>`).join('')}</div><details class="home-stat-details"><summary>Range details</summary><div><span>Minimum</span><strong>${fmt(values.min)}${values.min === null ? '' : ` ${safeText(unit)}`}</strong><span>Maximum</span><strong>${fmt(values.max)}${values.max === null ? '' : ` ${safeText(unit)}`}</strong></div></details></article>`
    )).join('');
  }

  function render({
    state,
    history,
    series,
    fmt,
    stat,
    safeText,
    bindRangeButtons,
    drawChart,
    ensureChartToolbar,
    renderCameraControls,
    renderOverviewSummary,
  }) {
    const ui = window.SmartCondoUI;
    if (!ui) return;
    const now = new Date();
    const overall = systemStatus(state);
    const hero = element('homeHero');
    if (hero) {
      hero.innerHTML = `<div class="home-hero-copy"><span class="sc-metric-label">${safeText(greeting(now))}</span><h1 id="homeHeroTitle" class="home-hero-title">Smart Condo Dashboard</h1><div class="home-hero-clock" aria-label="Current time">${safeText(now.toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}))}</div></div><div class="home-hero-health"><span>Overall System Health</span>${ui.statusChip({label:overall.label, status:overall.status, iconName:overall.status === 'success' ? 'circle-check' : 'triangle-alert'})}<p>${safeText(todaySummary(state))}</p></div>`;
    }
    const metrics = element('overviewMetrics');
    if (metrics) metrics.innerHTML = metricCards(state);
    const summary = energySummary();
    const total = number(summary.total_energy_kwh);
    const cost = number(summary.total_cost_thb);
    const energyHeader = element('homeEnergyHeader');
    if (energyHeader) {
      energyHeader.innerHTML = ui.widgetHeader({
        title:'Energy',
        subtitle:'Recent interval consumption',
        trailing:ui.statusChip({
          label:window.DashboardElectricityHistory?.state?.history?.bucket || 'No resolution',
          status:'info',
        }),
      });
    }
    const energySummaryHost = element('homeEnergySummary');
    if (energySummaryHost) {
      energySummaryHost.innerHTML = `<div><span>Total consumption</span><strong>${total === null ? '--' : `${total.toFixed(2)} kWh`}</strong><small>${total === null ? 'No Data' : ''}</small></div><div><span>Estimated cost</span><strong>${cost === null ? '--' : `฿${cost.toFixed(2)}`}</strong><small>${cost === null ? 'No Data' : ''}</small></div>`;
    }
    const airGauge = element('homeAirGauge');
    const pm25 = number(state.air?.living_room?.value);
    if (airGauge) {
      airGauge.innerHTML = `<strong class="home-gauge-value">${pm25 === null ? '—' : pm25.toFixed(1)}</strong><span>${pm25 === null ? 'Unavailable' : 'µg/m³'}</span>${ui.statusChip({label:state.air?.living_room?.stale ? 'Stale' : pm25 === null ? 'Unknown' : 'Current', status:state.air?.living_room?.stale ? 'warning' : pm25 === null ? 'neutral' : 'success'})}`;
    }
    const ranges = element('overviewRanges');
    if (ranges) {
      ranges.innerHTML = ['24h', '3d', '7d'].map(range => (
        `<button class="sc-button sc-button-secondary${state.range === range ? ' home-range-active' : ''}" data-range="${range}">${range.toUpperCase()}</button>`
      )).join('');
      bindRangeButtons(ranges);
    }
    renderStatistics(history, stat, fmt, safeText);
    drawChart('overviewChart', history, series.overview);
    drawChart('overviewPmChart', history, series.air);
    ensureChartToolbar('overviewChart');
    ensureChartToolbar('overviewPmChart');
    drawEnergyChart();
    renderSecondaryWidgets(state);
    renderQuickActions(state);
    renderDevices(state);
    renderOverviewSummary();
    renderCameraControls();
    organizeUtilityBar();
    ui.refreshIcons(document);
  }

  window.SmartCondoHome = Object.freeze({
    greeting,
    systemStatus,
    todaySummary,
    organizeUtilityBar,
    drawEnergyChart,
    renderQuickActions,
    render,
  });
  window.addEventListener('smart-condo:household-devices-updated', () => {
    if (window.currentPage?.() === 'overview' && window.S) renderQuickActions(window.S);
  });
})();
