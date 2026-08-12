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
  const NAV_ICONS = Object.freeze({
    overview: '<svg class="primary-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 19.5z"/><path d="M9 21v-7h6v7"/></svg>',
    devices: '<svg class="primary-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 9h8M8 15h8"/><circle cx="6" cy="9" r=".5"/><circle cx="18" cy="15" r=".5"/></svg>',
    electricity: '<svg class="primary-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m13 2-8 12h7l-1 8 8-12h-7z"/></svg>',
    camera: '<svg class="primary-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h3l1.5-2h7L17 7h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z"/><circle cx="12" cy="13" r="4"/></svg>',
    more: '<svg class="primary-nav-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg>'
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

  function collectionReachability(items) {
    const states = items.map(reachable);
    if (states.includes(true)) return true;
    if (states.length && states.every(value => value === false)) return false;
    return undefined;
  }

  function installPrimaryNavigation() {
    const desktop = '<button data-nav="overview" data-short="HM">Home</button><button data-nav="devices" data-short="DV">Devices</button><button data-nav="electricity" data-short="EL">Electricity</button><button data-nav="camera" data-short="CM">Cameras</button><button data-nav="more" data-short="MR">More</button>';
    const labels = {overview:'Home', devices:'Devices', electricity:'Electricity', camera:'Cameras', more:'More'};
    const mobile = PRIMARY_ROUTES.map(route => `<button data-nav="${route}" aria-label="${labels[route]}">${NAV_ICONS[route]}<span>${labels[route]}</span></button>`).join('');
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
      : normalizeStatus({online: collectionReachability(lights)});
    const climateStatus = normalizeStatus({online: collectionReachability(climate), available: state.air?.configured === false ? false : undefined});
    const tvPower = String(tv?.power ?? '').toLowerCase();
    const tvOnline = reachable(tv) ?? (['on', 'true', '1', 'online'].includes(tvPower) ? true : undefined);
    const tvStatus = normalizeStatus({online: tvOnline});
    const presenceStatus = normalizeStatus({online: people.length ? true : undefined, stale: people.some(item => String(item?.status || '').toLowerCase().includes('stale'))});
    host.innerHTML = `<header class="devices-landing-head"><p>See what is available, its current state, and where to control it.</p></header><div class="device-category-grid">${deviceCategory({title:'Lights', route:'lighting', description:'Switches and lighting zones', count:`${lights.length} device${lights.length === 1 ? '' : 's'}`, status:lightsStatus})}${deviceCategory({title:'Climate', route:'climate', description:'Air conditioning and air quality', count:`${climate.length} control${climate.length === 1 ? '' : 's'}`, status:climateStatus})}${deviceCategory({title:'TV / Entertainment', route:'entertainment', description:'LG TV status and existing remote controls', count:'1 control destination', status:tvStatus})}${deviceCategory({title:'Presence', route:'presence', description:'Household presence and automation status', count:`${people.length} person${people.length === 1 ? '' : 's'}`, status:presenceStatus})}</div>`;
    host.querySelectorAll('[data-device-route]').forEach(button => { button.onclick = () => window.nav(button.dataset.deviceRoute); });
  }

  function applyPrimaryState(page) {
    const active = ROUTE_GROUP[page] || 'more';
    document.querySelectorAll('.nav [data-nav],.mobile-nav [data-nav]').forEach(button => {
      const current = button.dataset.nav === active;
      button.classList.toggle('active', current);
      if (current) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
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
