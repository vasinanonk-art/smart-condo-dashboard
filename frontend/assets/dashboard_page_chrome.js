(() => {
  'use strict';

  const PAGE_CHROME = Object.freeze({
    overview: {title: 'Overview', subtitle: 'Live condo controls, climate, air quality and system health'},
    lighting: {title: 'Lighting', subtitle: 'Control Sonoff switches and condo lighting zones'},
    climate: {title: 'PM2.5 & Climate', subtitle: 'Indoor air quality, temperature and humidity monitoring'},
    entertainment: {title: 'Entertainment', subtitle: 'LG TV status and remote controls'},
    presence: {title: 'Presence & Automation', subtitle: 'Presence state and Home Assistant automation activity'},
    system: {title: 'System', subtitle: 'Dashboard services, devices and integration health'},
    topology: {title: 'Topology', subtitle: 'Physical sites and live data dependencies across Condo and Home'},
    electricity: {title: 'Electricity Monitoring', subtitle: 'Live PJ-1103 meter data from the condo'},
    camera: {title: 'Camera', subtitle: 'Condo camera availability and connection status'}
  });

  function applyPageChrome(page) {
    const chrome = PAGE_CHROME[page] || {title: 'Dashboard', subtitle: 'Smart Condo Dashboard'};
    const title = document.getElementById('pageTitle');
    const subtitle = document.getElementById('pageSubtitle') || title?.nextElementSibling;
    if (title) title.textContent = chrome.title;
    if (subtitle) subtitle.textContent = chrome.subtitle;
  }

  const originalNav = window.nav;
  const originalRenderPage = window.renderPage;

  window.applyPageChrome = applyPageChrome;
  window.nav = function isolatedPageNav(page) {
    originalNav(page);
    applyPageChrome(page);
  };
  window.renderPage = function isolatedPageRender(page = window.currentPage()) {
    originalRenderPage(page);
    applyPageChrome(page);
  };

  document.querySelectorAll('[data-nav]').forEach(button => {
    button.onclick = () => window.nav(button.dataset.nav);
  });
  applyPageChrome(window.currentPage());
})();
