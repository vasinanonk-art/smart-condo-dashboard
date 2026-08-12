(() => {
  'use strict';

  const PRIMARY_ROUTES = Object.freeze(['overview', 'devices', 'electricity', 'camera', 'more']);
  const ROUTE_GROUP = Object.freeze({
    overview: 'overview',
    devices: 'devices', lighting: 'devices', climate: 'devices', entertainment: 'devices', presence: 'devices',
    electricity: 'electricity', camera: 'camera',
    more: 'more', system: 'more', topology: 'more', history: 'more', settings: 'more', automation: 'more'
  });
  const STATUS = Object.freeze({
    healthy: {label: 'Healthy', tone: 'success'}, online: {label: 'Online', tone: 'success'},
    off: {label: 'Off', tone: 'neutral'}, attention: {label: 'Attention', tone: 'warning'},
    offline: {label: 'Offline', tone: 'critical'}, unavailable: {label: 'Unavailable', tone: 'warning'},
    unknown: {label: 'Unknown', tone: 'neutral'}
  });

  function normalizeStatus({healthy, online, powered, available, attention, stale} = {}) {
    if (healthy === true) return STATUS.healthy;
    if (available === false) return STATUS.unavailable;
    if (attention === true || stale === true) return STATUS.attention;
    if (online === true && powered === false) return STATUS.off;
    if (online === true) return STATUS.online;
    if (online === false) return STATUS.offline;
    return STATUS.unknown;
  }

  function reachable(value) {
    if (value?.online === true || value?.reachable === true || value?.connected === true) return true;
    if (value?.online === false || value?.reachable === false || value?.connected === false) return false;
    const state = String(value?.connection_state || '').toLowerCase();
    if (['connected', 'online'].includes(state)) return true;
    if (['disconnected', 'offline', 'unreachable'].includes(state)) return false;
    return undefined;
  }

  function installPrimaryNavigation() {
    const desktop = '<button data-nav="overview" data-short="HM">Home</button><button data-nav="devices" data-short="DV">Devices</button><button data-nav="electricity" data-short="EL">Electricity</button><button data-nav="camera" data-short="CM">Cameras</button><button data-nav="more" data-short="MR">More</button>';
    const mobile = '<button data-nav="overview" aria-label="Home"><i data-lucide="house" aria-hidden="true"></i><span>Home</span></button><button data-nav="devices" aria-label="Devices"><i data-lucide="panels-top-left" aria-hidden="true"></i><span>Devices</span></button><button data-nav="electricity" aria-label="Electricity"><i data-lucide="zap" aria-hidden="true"></i><span>Electricity</span></button><button data-nav="camera" aria-label="Cameras"><i data-lucide="camera" aria-hidden="true"></i><span>Cameras</span></button><button data-nav="more" aria-label="More"><i data-lucide="menu" aria-hidden="true"></i><span>More</span></button>';
    const desktopHost = document.querySelector('.sidebar .nav');
    const mobileHost = document.querySelector('.mobile-nav');
    if (desktopHost) desktopHost.innerHTML = desktop;
    if (mobileHost) mobileHost.innerHTML = mobile;
  }

  function deviceCategory({title, route, description, count, status}) {
    return `<article class="device-category-card"><div><span class="device-category-status ${status.tone}">${status.label}</span><h2>${title}</h2><p>${description}</p><small>${count}</small></div><button class="btn ghost" data-device-route="${route}">Open controls</button></article>`;
  }

  function renderDevices() {
    const host = document.getElementById('devicesLanding');
    if (!host) return;
    const state = window.S || {};
    const lights = [...(state.sonoff?.devices || []), ...(state.lights || [])];
    const climate = state.climateDevices || [];
    const people = Object.values(state.presence || {});
    const tv = state.tv?.lastValid;
    const lightsStatus = state.sonoffAvailable === false ? normalizeStatus({available: false})
      : normalizeStatus({online: lights.length ? lights.some(item => reachable(item) !== false) : undefined});
    const climateStatus = normalizeStatus({online: climate.length ? climate.some(item => reachable(item) !== false) : undefined, available: state.air?.configured === false ? false : undefined});
    const tvPower = String(tv?.power ?? '').toLowerCase();
    const tvOnline = reachable(tv) ?? (['on', 'true', '1', 'online'].includes(tvPower) ? true : undefined);
    const tvStatus = normalizeStatus({online: tvOnline});
    const presenceStatus = normalizeStatus({online: people.length ? true : undefined, stale: people.some(item => String(item?.status || '').toLowerCase().includes('stale'))});
    host.innerHTML = `<header class="devices-landing-head"><h2>Devices</h2><p>See what is available, its current state, and where to control it.</p></header><div class="device-category-grid">${deviceCategory({title:'Lights', route:'lighting', description:'Switches and lighting zones', count:`${lights.length} device${lights.length === 1 ? '' : 's'}`, status:lightsStatus})}${deviceCategory({title:'Climate', route:'climate', description:'Air conditioning and air quality', count:`${climate.length} control${climate.length === 1 ? '' : 's'}`, status:climateStatus})}${deviceCategory({title:'TV / Entertainment', route:'entertainment', description:'LG TV status and existing remote controls', count:'1 control destination', status:tvStatus})}${deviceCategory({title:'Presence', route:'presence', description:'Household presence and automation status', count:`${people.length} person${people.length === 1 ? '' : 's'}`, status:presenceStatus})}</div>`;
    host.querySelectorAll('[data-device-route]').forEach(button => { button.onclick = () => window.nav(button.dataset.deviceRoute); });
  }

  function applyPrimaryState(page) {
    const active = ROUTE_GROUP[page] || 'more';
    document.querySelectorAll('.nav [data-nav],.mobile-nav [data-nav]').forEach(button => button.classList.toggle('active', button.dataset.nav === active));
  }

  function normalizeSystemPresentation() {
    document.querySelectorAll('.system-summary-grid>div').forEach(row => {
      const label = row.querySelector('span')?.textContent?.trim();
      const value = row.querySelector('strong');
      if (label === 'Lighting' && value?.textContent?.trim() === 'Unavailable') {
        value.classList.remove('bad');
        value.classList.add('warn');
      }
    });
  }

  installPrimaryNavigation();
  const originalNav = window.nav;
  const originalRenderPage = window.renderPage;
  window.nav = function milestone2Nav(page) {
    originalNav(page);
    applyPrimaryState(page);
    if (page === 'devices') renderDevices();
    if (page === 'system') normalizeSystemPresentation();
  };
  window.renderPage = function milestone2Render(page = window.currentPage()) {
    originalRenderPage(page);
    applyPrimaryState(page);
    if (page === 'devices') renderDevices();
    if (page === 'system') normalizeSystemPresentation();
  };
  document.querySelectorAll('.nav [data-nav],.mobile-nav [data-nav]').forEach(button => { button.onclick = () => window.nav(button.dataset.nav); });
  document.querySelectorAll('[data-more-route]').forEach(button => { button.onclick = () => window.nav(button.dataset.moreRoute); });
  applyPrimaryState(window.currentPage());
  if (window.currentPage() === 'devices') renderDevices();
  window.lucide?.createIcons?.();
  window.DashboardStatusSemantics = Object.freeze({STATUS, normalizeStatus, PRIMARY_ROUTES, ROUTE_GROUP});
})();
