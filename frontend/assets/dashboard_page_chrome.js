(() => {
  'use strict';
  if (window.__dashboardPageChromeInstalled) return;
  window.__dashboardPageChromeInstalled = true;

  const PAGE_CHROME = Object.freeze({
    overview: {title: 'Overview', subtitle: 'Live condo controls and status'},
    devices: {title: 'Devices', subtitle: 'Device availability and control destinations'},
    lighting: {title: 'Lighting', subtitle: 'Lighting control'},
    climate: {title: 'PM2.5 & Air Quality', subtitle: 'Indoor air quality'},
    entertainment: {title: 'Entertainment', subtitle: 'TV and remote control'},
    presence: {title: 'Presence & Automation', subtitle: 'Presence and last-seen status'},
    system: {title: 'System', subtitle: 'System health and services'},
    topology: {title: 'Topology', subtitle: 'Live dependency graph'},
    electricity: {title: 'Electricity Monitoring', subtitle: 'Real-time electricity monitoring'},
    camera: {title: 'Cameras', subtitle: 'Camera availability and viewing'},
    history: {title: 'Electricity History', subtitle: 'History coverage and maintenance'},
    automation: {title: 'Automation', subtitle: 'Rule validation and safe simulation'},
    settings: {title: 'Settings', subtitle: 'Dashboard configuration and maintenance'},
    backup: {title: 'Backup', subtitle: 'NAS backup health and verification'},
    more: {title: 'More', subtitle: 'System information and dashboard tools'}
  });
  const PAGE_PARENT = Object.freeze({
    lighting: {page:'devices', label:'Devices'},
    climate: {page:'devices', label:'Devices'},
    entertainment: {page:'devices', label:'Devices'},
    presence: {page:'devices', label:'Devices'},
    system: {page:'more', label:'More'},
    topology: {page:'more', label:'More'},
    history: {page:'more', label:'More'},
    automation: {page:'more', label:'More'},
    settings: {page:'more', label:'More'},
    backup: {page:'more', label:'More'},
  });

  function applyBackButton(page) {
    const identity = document.querySelector('.home-page-identity');
    if (!identity) return;
    let button = document.getElementById('pageBackButton');
    if (!button) {
      button = document.createElement('button');
      button.id = 'pageBackButton';
      button.type = 'button';
      button.className = 'sc-button sc-button-secondary page-back-button';
      button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg><span>Back</span>';
      identity.prepend(button);
    }
    const parent = PAGE_PARENT[page];
    button.hidden = !parent;
    identity.classList.toggle('has-page-back', Boolean(parent));
    if (!parent) return;
    button.setAttribute('aria-label', `Back to ${parent.label}`);
    button.onclick = () => window.nav(parent.page);
  }

  function applyPageChrome(page) {
    const chrome = PAGE_CHROME[page] || {title: 'Dashboard', subtitle: 'Smart Condo Dashboard'};
    const title = document.getElementById('pageTitle');
    const subtitle = document.getElementById('pageSubtitle');
    if (title) title.textContent = chrome.title;
    if (subtitle) subtitle.textContent = chrome.subtitle;
    applyBackButton(page);
  }

  const originalNav = window.nav;
  const originalRenderPage = window.renderPage;
  window.applyPageChrome = applyPageChrome;
  window.nav = function isolatedPageNav(page) {
    const previousPage = document.documentElement.dataset.dashboardPage;
    originalNav(page);
    applyPageChrome(page);
    if (page !== previousPage) window.scrollTo({top:0, left:0, behavior:'auto'});
  };
  window.renderPage = function isolatedPageRender(page = window.currentPage()) {
    originalRenderPage(page);
    applyPageChrome(page);
  };
  document.querySelectorAll('[data-nav]').forEach(button => { button.onclick = () => window.nav(button.dataset.nav); });
  applyPageChrome(window.currentPage());
  window.DashboardPageChrome = Object.freeze({PAGE_CHROME, PAGE_PARENT, applyPageChrome});
})();
