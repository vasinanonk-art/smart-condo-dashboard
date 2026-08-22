(() => {
  'use strict';
  if (window.__dashboardCamerasInstalled) return;
  window.__dashboardCamerasInstalled = true;

  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const reasonText = reason => ({
    read_only_provider_unavailable: 'Camera provider is not available.',
    provider_unavailable: 'Camera provider is not available.',
    configuration_unavailable: 'Camera configuration is not available.',
    not_configured: 'Camera is not configured.'
  }[reason] || String(reason || '').replaceAll('_', ' '));
  const status = camera => {
    if (camera?.online === true) {
      const capabilities = camera.capabilities || {};
      return camera.unavailable_reason || (!capabilities.snapshot && !capabilities.live_stream)
        ? {label:'Attention', cls:'warning'} : {label:'Online', cls:'success'};
    }
    if (camera?.unavailable_reason) return {label:'Unavailable', cls:'warning'};
    if (camera?.online === false) return {label:'Offline', cls:'critical'};
    return {label:'Unknown', cls:'neutral'};
  };
  const ptzInFlight = new Set();
  const SNAPSHOT_REFRESH_MS = 10000;
  let snapshotTimer = null;

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
      ? `<div class="camera-snapshot-frame"><img class="camera-latest-snapshot" data-camera-id="${id}" loading="lazy" src="/api/camera-control/${id}/snapshot" alt="Latest snapshot from ${safe(camera.name || camera.display_name || 'camera')}"><div class="camera-snapshot-placeholder" role="status"><strong>Snapshot unavailable</strong><span>Open Live View to check the camera.</span></div></div>`
      : `<div class="camera-snapshot-unavailable"><strong>${unavailable ? current.label : 'Snapshot unavailable'}</strong>${camera.unavailable_reason ? `<span>${safe(reasonText(camera.unavailable_reason))}</span>` : ''}</div>`;
    const actions = [
      capabilities.snapshot && camera.online === true ? `<button class="btn primary" data-camera-snapshot="${id}">Snapshot</button>` : '',
      capabilities.live_stream && camera.online === true ? `<button class="btn ghost" data-camera-live="${id}">Live View</button>` : '',
    ].filter(Boolean).join('');
    const ptz = capabilities.ptz_move && capabilities.ptz_stop && camera.online === true
      ? `<div class="camera-ptz-grid" role="group" aria-label="${safe(camera.name || camera.display_name || 'Camera')} pan and tilt">
          <button class="btn ghost camera-ptz-up" data-camera-ptz="${id}" data-camera-direction="up">Up</button>
          <button class="btn ghost camera-ptz-left" data-camera-ptz="${id}" data-camera-direction="left">Left</button>
          <button class="btn ghost camera-ptz-stop" data-camera-ptz="${id}" data-camera-command="stop_ptz">Stop</button>
          <button class="btn ghost camera-ptz-right" data-camera-ptz="${id}" data-camera-direction="right">Right</button>
          <button class="btn ghost camera-ptz-down" data-camera-ptz="${id}" data-camera-direction="down">Down</button>
        </div>`
      : '';
    const technical = [
      ['Provider', camera.provider],
      ['Model', camera.model],
      ['Reason', camera.unavailable_reason],
      ['Capabilities', Object.keys(capabilities).filter(key => capabilities[key]).join(', ') || 'Not available'],
    ].filter(([, value]) => value !== null && value !== undefined && value !== '');
    return `<article class="camera-card${unavailable ? ' is-unavailable' : ''}"><div class="camera-card-head"><div><h2>${safe(camera.name || camera.display_name || camera.id || 'Camera')}</h2><span class="camera-status ${current.cls}">${current.label}</span></div></div><div class="camera-primary-layout"><div class="camera-visual">${snapshot}</div><div class="camera-control-panel"><div class="camera-card-actions">${actions || '<span class="muted">No camera actions available.</span>'}</div>${ptz}<details class="camera-advanced"><summary>Details</summary><dl>${technical.map(([label, value]) => `<div><dt>${safe(label)}</dt><dd>${safe(value)}</dd></div>`).join('')}</dl></details></div></div></article>`;
  }

  let cameras = [];
  let renderedSignature = '';

  async function refreshSnapshot(image, announce = false) {
    const target = image?.dataset.cameraId;
    if (!target || image.dataset.refreshing === 'true') return false;
    image.dataset.refreshing = 'true';
    try {
      const response = await window.fetch(`/api/camera-control/${target}/snapshot?refresh=${Date.now()}`, {
        cache:'no-store',
      });
      if (!response.ok) throw new Error('Snapshot unavailable');
      const blob = await response.blob();
      if (!blob.type.startsWith('image/')) throw new Error('Invalid snapshot');
      const objectUrl = URL.createObjectURL(blob);
      try {
        await new Promise((resolve, reject) => {
          const probe = new Image();
          probe.onload = resolve;
          probe.onerror = reject;
          probe.src = objectUrl;
        });
      } catch (error) {
        URL.revokeObjectURL(objectUrl);
        throw error;
      }
      const previous = image.dataset.objectUrl;
      image.dataset.objectUrl = objectUrl;
      image.src = objectUrl;
      image.closest('.camera-snapshot-frame')?.classList.remove('is-placeholder');
      if (previous) URL.revokeObjectURL(previous);
      if (announce) window.toast?.('Snapshot updated.');
      return true;
    } catch (error) {
      if (announce) window.toast?.(error.message || 'Snapshot unavailable');
      return false;
    } finally {
      image.dataset.refreshing = 'false';
    }
  }

  function startSnapshotRefresh() {
    if (snapshotTimer) return;
    snapshotTimer = window.setInterval(() => {
      if (document.hidden || window.currentPage?.() !== 'camera') return;
      document.querySelectorAll('#cameraPage .camera-latest-snapshot').forEach(image => refreshSnapshot(image));
    }, SNAPSHOT_REFRESH_MS);
  }

  function stopSnapshotRefresh() {
    if (!snapshotTimer) return;
    window.clearInterval(snapshotTimer);
    snapshotTimer = null;
  }

  function openLiveView(camera, identifier) {
    document.querySelector('.camera-live-dialog button')?.click();
    const name = camera.name || camera.display_name || 'Camera';
    const dialog = document.createElement('dialog');
    dialog.className = 'camera-live-dialog';
    dialog.setAttribute('aria-label', `${name} live view`);
    dialog.innerHTML = `<div class="camera-live-shell"><header><strong>${safe(name)}</strong><button class="btn ghost" type="button">Close</button></header><video controls autoplay muted playsinline aria-label="${safe(`${name} live video`)}"></video></div>`;
    document.body.appendChild(dialog);
    const video = dialog.querySelector('video');
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      video.pause();
      video.removeAttribute('src');
      video.load();
      dialog.remove();
    };
    dialog.querySelector('button').onclick = () => dialog.open ? dialog.close() : close();
    dialog.addEventListener('cancel', event => { event.preventDefault(); dialog.close(); });
    dialog.addEventListener('close', close);
    video.addEventListener('error', () => {
      window.toast?.('Live View could not start.');
      close();
    });
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    video.src = `/api/camera-control/${identifier}/live?refresh=${Date.now()}`;
    video.play().catch(() => window.toast?.('Press play to start Live View.'));
  }

  function stableSignature(available) {
    return JSON.stringify(available.map(camera => ({
      id:camera.id,
      name:camera.name || camera.display_name,
      online:camera.online,
      unavailable_reason:camera.unavailable_reason,
      provider:camera.provider,
      model:camera.model,
      capabilities:camera.capabilities || {},
    })));
  }

  async function sendPtz(button) {
    const target = button.dataset.cameraPtz;
    const stopping = button.dataset.cameraCommand === 'stop_ptz';
    if (!target || (ptzInFlight.has(target) && !stopping)) return;
    const controls = [...button.closest('.camera-card').querySelectorAll('[data-camera-ptz]')];
    if (stopping) button.disabled = true;
    else {
      ptzInFlight.add(target);
      controls.forEach(control => { control.disabled = control.dataset.cameraCommand !== 'stop_ptz'; });
    }
    const body = stopping
      ? {command:'stop_ptz'}
      : {command:'move', direction:button.dataset.cameraDirection, duration:0.5};
    const originalLabel = button.textContent;
    if (!stopping) button.textContent = 'Moving…';
    try {
      const response = await window.fetch(`/api/camera-control/${target}/command`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || 'Camera movement failed');
      if (!stopping) {
        await refreshSnapshot(button.closest('.camera-card')?.querySelector('.camera-latest-snapshot'));
      }
      window.toast?.(stopping ? 'Camera stopped.' : 'Camera moved and stopped.');
    } catch (error) {
      window.toast?.(error.message);
    } finally {
      if (stopping) button.disabled = false;
      else {
        button.textContent = originalLabel;
        ptzInFlight.delete(target);
        controls.forEach(control => { control.disabled = false; });
      }
    }
  }

  function render() {
    const host = document.getElementById('cameraPage');
    if (!host) return;
    const available = cameras.length ? cameras : (window.S?.cameras || []);
    const signature = stableSignature(available);
    if (signature === renderedSignature && host.querySelector('.camera-grid,.camera-empty')) return;
    host.querySelectorAll('.camera-latest-snapshot').forEach(image => {
      if (image.dataset.objectUrl) URL.revokeObjectURL(image.dataset.objectUrl);
    });
    renderedSignature = signature;
    host.innerHTML = `<section class="camera-page-head"><p>View current camera availability and open a live view.</p><span class="muted">${available.length} camera${available.length === 1 ? '' : 's'}</span></section>${available.length ? `<div class="camera-grid">${available.map(cameraCard).join('')}</div>` : '<div class="card camera-empty">No camera configuration is available.</div>'}`;
    host.querySelectorAll('.camera-latest-snapshot').forEach(image => {
      const frame = image.closest('.camera-snapshot-frame');
      const classify = () => {
        const ratio = image.naturalWidth && image.naturalHeight ? image.naturalWidth / image.naturalHeight : 0;
        frame?.classList.toggle('is-placeholder', ratio < 1.2 || ratio > 2.4);
      };
      image.addEventListener('load', classify);
      image.addEventListener('error', () => frame?.classList.add('is-placeholder'));
      if (image.complete) classify();
    });
    host.querySelectorAll('[data-camera-snapshot]').forEach(button => button.onclick = async () => {
      const label = button.textContent;
      button.disabled = true;
      button.textContent = 'Refreshing…';
      await refreshSnapshot(button.closest('.camera-card')?.querySelector('.camera-latest-snapshot'), true);
      button.textContent = label;
      button.disabled = false;
    });
    host.querySelectorAll('[data-camera-live]').forEach(button => button.onclick = () => {
      const camera = available.find(item => encodeURIComponent(item.id || '') === button.dataset.cameraLive);
      if (camera) openLiveView(camera, button.dataset.cameraLive);
    });
    host.querySelectorAll('[data-camera-ptz]').forEach(button => button.onclick = () => sendPtz(button));
  }

  async function load() {
    const host = document.getElementById('cameraPage');
    if (!host) return;
    try {
      const payload = await window.get('/api/camera-control/devices');
      cameras = Array.isArray(payload?.cameras) ? payload.cameras : [];
      if (window.S) window.S.cameras = cameras;
      render();
    } catch (error) {
      host.innerHTML = '<div class="card camera-empty">Camera status is temporarily unavailable.</div>';
    }
  }

  install();
  const originalRenderPage = window.renderPage;
  window.renderPage = function renderPageWithCameras(page = window.currentPage()) {
    originalRenderPage(page);
    document.querySelector('.sc-dashboard-shell')?.classList.toggle('camera-page-active', page === 'camera');
    if (page === 'camera') {
      render();
      startSnapshotRefresh();
    } else {
      stopSnapshotRefresh();
    }
  };
  window.DashboardDataLifecycle?.register('camera-page', ['camera'], load);
  document.querySelectorAll('[data-nav]').forEach(button => button.onclick = () => window.nav(button.dataset.nav));
  window.DashboardCameras = Object.freeze({status, load, render});
})();
