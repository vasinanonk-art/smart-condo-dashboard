(() => {
  'use strict';
  if (window.__dashboardTopologyVerificationInstalled) return;
  window.__dashboardTopologyVerificationInstalled = true;

  function verify() {
    if (window.currentPage?.() !== 'topology') return;
    const svg = document.querySelector('#topologyGraph .topology-svg');
    if (!svg) return;
    const nodes = [...svg.querySelectorAll('.topology-node-svg')].map(node => ({node, box: node.getBBox()}));
    const errors = [];
    [...svg.querySelectorAll('.topology-edge')].forEach((edge, edgeIndex) => {
      const length = edge.getTotalLength?.() || 0;
      if (!length) return;
      for (let distance = 12; distance < length - 12; distance += 8) {
        const point = edge.getPointAtLength(distance);
        const hit = nodes.find(({box}) => point.x > box.x + 4 && point.x < box.x + box.width - 4 && point.y > box.y + 4 && point.y < box.y + box.height - 4);
        if (hit) {
          errors.push({type:'edge_node_intersection', edge_index:edgeIndex});
          break;
        }
      }
    });
    if (errors.length) console.warn('Topology verification diagnostics', errors);
  }

  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithTopologyVerification(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'topology') requestAnimationFrame(verify);
  };
  window.addEventListener('resize', () => {
    if (window.currentPage?.() === 'topology') requestAnimationFrame(verify);
  });
})();
