(() => {
  'use strict';
  if (window.__dashboardElectricityProjectionInstalled) return;
  window.__dashboardElectricityProjectionInstalled = true;
  let cache = null;
  let cacheTs = 0;

  async function summary() {
    const now = Date.now();
    if (cache && now - cacheTs < 15000) return cache;
    try {
      cache = await window.get('/api/electricity/summary');
      cacheTs = now;
    } catch (_) {
      cache = null;
    }
    return cache;
  }

  async function enhance() {
    if (window.currentPage?.() !== 'electricity') return;
    const grid = document.querySelector('.electricity-cost-grid');
    if (!grid || grid.querySelector('[data-projected-bill]')) return;
    const payload = await summary();
    if (!payload || !document.body.contains(grid)) return;
    const value = Number(payload.estimated_month_end_bill);
    const card = document.createElement('div');
    card.className = 'electricity-metric secondary';
    card.dataset.projectedBill = '1';
    card.innerHTML = `<span>Projected Month-End Bill</span><strong>${Number.isFinite(value) ? `${value.toFixed(2)}<small>THB</small>` : 'Not available'}</strong>`;
    grid.appendChild(card);
  }

  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithProjection(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'electricity') enhance();
  };
})();
