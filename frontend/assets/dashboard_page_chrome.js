(() => {
  'use strict';
  if (window.__dashboardPageChromeInstalled) return;
  window.__dashboardPageChromeInstalled = true;

  const PAGE_CHROME = Object.freeze({
    overview: {title: 'Overview', subtitle: 'Live condo controls and status'},
    lighting: {title: 'Lighting', subtitle: 'Lighting control'},
    climate: {title: 'PM2.5 & Air Quality', subtitle: 'Indoor air quality'},
    entertainment: {title: 'Entertainment', subtitle: 'TV and remote control'},
    presence: {title: 'Presence & Automation', subtitle: 'Presence and last-seen status'},
    system: {title: 'System', subtitle: 'System health and services'},
    topology: {title: 'Topology', subtitle: 'Live dependency graph'},
    electricity: {title: 'Electricity Monitoring', subtitle: 'Real-time electricity monitoring'},
    camera: {title: 'Camera', subtitle: 'Live camera monitoring'},
    history: {title: 'Electricity History', subtitle: 'History coverage and maintenance'},
    settings: {title: 'Settings', subtitle: 'Dashboard configuration and maintenance'}
  });

  function applyPageChrome(page) {
    const chrome = PAGE_CHROME[page] || {title: 'Dashboard', subtitle: 'Smart Condo Dashboard'};
    const title = document.getElementById('pageTitle');
    const subtitle = document.getElementById('pageSubtitle');
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
  document.querySelectorAll('[data-nav]').forEach(button => { button.onclick = () => window.nav(button.dataset.nav); });
  applyPageChrome(window.currentPage());
})();
