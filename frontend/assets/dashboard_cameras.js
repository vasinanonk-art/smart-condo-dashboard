(() => {
  'use strict';
  if (window.__dashboardCamerasInstalled) return;
  window.__dashboardCamerasInstalled = true;

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const status = camera => {
    if (camera?.online === false) return {label:'Offline', cls:'critical'};
    if (camera?.online === true) {
      const capabilities = camera.capabilities || {};
      return camera.unavailable_reason || (!capabilities.snapshot && !capabilities.live_stream)
        ? {label:'Warning', cls:'warning'} : {label:'Online', cls:'success'};
    }
    return camera?.unavailable_reason ? {label:'Unavailable', cls:'warning'} : {label:'Unknown', cls:'neutral'};
  };

  function install() {
    document.querySelectorAll('.nav,.mobile-nav').forEach(host => {
      if (host.querySelector('[data-nav="camera"]')) return;
      const button = document.createElement('button');
      button.dataset.nav = 'camera';
      button.dataset.short = 'CM';
      button.textContent = 'Cameras';
      const system = host.querySelector('[data-nav="system"]');
      system ? host.insertBefore(button, system) : host.appendChild(button);
    });
    if (!document.querySelector('[data-page="camera"]')) {
      const section = document.createElement('section');
      section.className = 'page';
      section.dataset.page = 'camera';
      section.innerHTML = '<div id="cameraPage" class="camera-page"><div class="card empty">Camera data is loading.</div></div>';
      document.querySelector('.main')?.appendChild(section);
    }
  }

  function cameraCard(camera) {
    const capabilities = camera.capabilities || {};
    const current = status(camera);
    const id = encodeURIComponent(camera.id || '');
    const unavailable = camera.online !== true || current.label === 'Unavailable';
    const snapshot = capabilities.snapshot && camera.online === true
      ? `<img class="camera-latest-snapshot" loading="lazy" src="/api/camera-control/${id}/snapshot" alt="Latest snapshot from ${safe(camera.name || camera.display_name || 'camera')}">`
      : `<div class="camera-snapshot-unavailable"><strong>${unavailable ? current.label : 'Snapshot unavailable'}</strong>${camera.unavailable_reason ? `<span>${safe(camera.unavailable_reason)}</span>` : ''}</div>`;
    const actions = [
      capabilities.snapshot && camera.online === true ? `<button class="btn primary" data-camera-snapshot="${id}">Snapshot</button>` : '',
      capabilities.live_stream && camera.online === true ? `<button class="btn ghost" data-camera-live="${id}">Live View</button>` : '',
    ].filter(Boolean).join('');
    const technical = [
      ['Provider', camera.provider],
      ['Model', camera.model],
      ['Reason', camera.unavailable_reason],
      ['Capabilities', Object.keys(capabilities).filter(key => capabilities[key]).join(', ') || 'Not available'],
    ].filter(([, value]) => value !== null && value !== undefined && value !== '');
    return `<article class="camera-card${unavailable ? ' is-unavailable' : ''}"><div class="camera-card-head"><div><h2>${safe(camera.name || camera.display_name || camera.id || 'Camera')}</h2><span class="camera-status ${current.cls}">${current.label}</span></div></div>${snapshot}<div class="camera-card-actions">${actions || '<span class="muted">No camera actions available.</span>'}</div><details class="camera-advanced"><summary>Details</summary><dl>${technical.map(([label, value]) => `<div><dt>${safe(label)}</dt><dd>${safe(value)}</dd></div>`).join('')}</dl></details></article>`;
  }

  async function render() {
    const host = document.getElementById('cameraPage');
    if (!host) return;
    try {
      const payload = await window.get('/api/camera-control/devices');
      const cameras = Array.isArray(payload?.cameras) ? payload.cameras : [];
      host.innerHTML = `<section class="camera-page-head"><p>View current camera availability and open a live view.</p><span class="muted">${cameras.length} camera${cameras.length === 1 ? '' : 's'}</span></section>${cameras.length ? `<div class="camera-grid">${cameras.map(cameraCard).join('')}</div>` : '<div class="card camera-empty">No camera configuration is available.</div>'}`;
      host.querySelectorAll('[data-camera-snapshot]').forEach(button => button.onclick = () => window.open(`/api/camera-control/${button.dataset.cameraSnapshot}/snapshot`, '_blank', 'noopener'));
      host.querySelectorAll('[data-camera-live]').forEach(button => button.onclick = () => window.open(`/api/camera-control/${button.dataset.cameraLive}/live`, '_blank', 'noopener'));
    } catch (error) {
      host.innerHTML = '<div class="card camera-empty">Camera status is temporarily unavailable.</div>';
    }
  }

  install();
  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithCameras(page = window.currentPage()) {
    originalRenderPage(page);
    if (page === 'camera') render();
  };
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  window.DashboardCameras = Object.freeze({status, render});
})();
