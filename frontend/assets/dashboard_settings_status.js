(() => {
  'use strict';
  if (window.__dashboardSettingsStatusInstalled) return;
  window.__dashboardSettingsStatusInstalled = true;
  let cache = null;
  let cacheTs = 0;

  const text = value => value === null || value === undefined || value === '' ? 'Not available' : String(value);
  const dateTime = value => value ? new Date(Number(value) * 1000).toLocaleString() : 'Not available';

  async function load(force = false) {
    if (!force && cache && Date.now() - cacheTs < 15000) return cache;
    try {
      cache = await window.get('/api/settings/electricity/status');
      cacheTs = Date.now();
    } catch (_) {
      cache = null;
    }
    return cache;
  }

  async function enhance(force = false) {
    if (window.currentPage?.() !== 'settings') return;
    const maintenance = document.querySelector('[data-settings-section="maintenance"].active');
    if (!maintenance) return;
    const grid = document.querySelector('.maintenance-status-grid');
    if (!grid || grid.querySelector('[data-electricity-settings-status]')) return;
    const status = await load(force);
    if (!status || !document.body.contains(grid)) return;
    const cards = [
      ['Current billing cycle', status.billing_cycle?.label || 'Not available'],
      ['History starts', dateTime(status.history_starts)],
      ['History ends', dateTime(status.history_ends)],
      ['Tariff version', text(status.tariff_version)],
      ['Last tariff check', dateTime(status.last_tariff_check)],
      ['Last history prune', dateTime(status.last_history_prune)],
      ['Projection status', text(status.projection_status)]
    ];
    cards.forEach(([label, value], index) => {
      const card = document.createElement('div');
      if (index === 0) card.dataset.electricitySettingsStatus = '1';
      card.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
      grid.appendChild(card);
    });
  }

  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithSettingsStatus(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'settings') setTimeout(() => enhance(false), 0);
  };
  document.addEventListener('click', event => {
    if (event.target.closest('[data-settings-section="maintenance"]')) setTimeout(() => enhance(true), 0);
  });
})();
