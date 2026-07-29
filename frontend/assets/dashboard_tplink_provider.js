(() => {
  'use strict';
  if (window.TPLinkProviderDashboard) return;

  const endpointNames = Object.freeze({
    status:'/api/tplink/providers/status',
    metadata:'/api/tplink/providers/metadata',
    capabilities:'/api/tplink/providers/capabilities',
    diagnostics:'/api/tplink/providers/diagnostics',
    inventory:'/api/tplink/cameras',
  });
  const state = {loading:false};
  const safe = value => window.safeText
    ? window.safeText(value)
    : String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  const label = value => String(value || '')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, character => character.toUpperCase());

  function localTimestamp(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : new Intl.DateTimeFormat('en-GB', {
      timeZone:'Asia/Bangkok',
      day:'2-digit',
      month:'short',
      year:'numeric',
      hour:'2-digit',
      minute:'2-digit',
      second:'2-digit',
      hour12:false,
    }).format(date);
  }

  function displayValue(name, value) {
    if (value === null || value === undefined || value === '') return 'Not available';
    if (/(?:timestamp|_at|_time|last_seen|initialization)/i.test(name)) {
      const formatted = localTimestamp(value);
      if (formatted) return formatted;
    }
    if (typeof value === 'object') {
      const meaningful = ['label', 'name', 'status', 'state', 'message', 'result']
        .map(key => value?.[key])
        .find(item => ['string', 'number', 'boolean'].includes(typeof item));
      if (meaningful !== undefined) return String(meaningful);
      try {
        const serialized = JSON.stringify(value);
        return serialized && serialized !== '{}' && serialized !== '[]'
          ? serialized
          : 'Not available';
      } catch (_error) {
        return 'Not available';
      }
    }
    return String(value);
  }

  function capabilityRows(capabilities) {
    return Object.entries(capabilities || {}).map(([name, status]) => ({
      name,
      status,
      supported: status === 'Supported',
    }));
  }

  function definitionRows(values) {
    return Object.entries(values || {}).map(([name, value]) => (
      `<div class="tplink-provider-row"><dt>${safe(label(name))}</dt><dd>${safe(displayValue(name, value))}</dd></div>`
    )).join('');
  }

  function renderCapabilities(capabilities) {
    const rows = capabilityRows(capabilities);
    return rows.length ? rows.map(item => (
      `<div class="tplink-capability-row"><span class="tplink-capability-name">${safe(label(item.name))}</span><span class="tplink-capability-status" data-supported="${item.supported}">${safe(item.status)}</span></div>`
    )).join('') : '<p class="tplink-provider-empty">No capabilities reported.</p>';
  }

  function renderCameras(cameras) {
    if (!Array.isArray(cameras) || !cameras.length) {
      return '<p class="tplink-provider-empty">No camera inventory is available.</p>';
    }
    return `<div class="tplink-camera-list">${cameras.map(camera => (
      `<article class="tplink-camera-item"><strong>${safe(camera.display_name)}</strong><div class="device-meta">${safe(camera.model || 'Model unavailable')} · ${camera.online === true ? 'Online' : camera.online === false ? 'Offline' : 'Unknown'}</div></article>`
    )).join('')}</div>`;
  }

  function render(host, payloads) {
    const providerId = Object.keys(payloads.metadata?.providers || {})[0];
    if (!providerId) {
      host.innerHTML = '<p class="tplink-provider-empty">No TP-Link provider is registered.</p>';
      return;
    }
    const metadata = payloads.metadata.providers[providerId] || {};
    const health = payloads.status?.providers?.[providerId] || {};
    const capabilities = payloads.capabilities?.providers?.[providerId] || {};
    const diagnostics = payloads.diagnostics?.providers?.[providerId] || {};
    host.innerHTML = `<div class="tplink-provider-grid">
      <section class="tplink-provider-card"><h3>Provider Health</h3><dl class="tplink-provider-list">${definitionRows(health)}</dl></section>
      <section class="tplink-provider-card"><h3>Provider Metadata</h3><dl class="tplink-provider-list">${definitionRows(metadata)}</dl></section>
      <section class="tplink-provider-card"><h3>Capabilities</h3><div class="tplink-capability-list">${renderCapabilities(capabilities)}</div></section>
      <section class="tplink-provider-card"><h3>Diagnostics</h3><dl class="tplink-provider-list">${definitionRows(diagnostics)}</dl></section>
      <section class="tplink-provider-card"><h3>Camera Inventory</h3>${renderCameras(payloads.inventory?.cameras)}</section>
    </div>`;
  }

  async function request(url) {
    const response = await fetch(url, {credentials:'same-origin'});
    if (!response.ok) throw new Error(`provider_request_failed_${response.status}`);
    return response.json();
  }

  async function load() {
    const host = document.getElementById('tplinkProviderDashboard');
    if (!host || state.loading) return;
    state.loading = true;
    host.setAttribute('aria-busy', 'true');
    try {
      const values = await Promise.all(
        Object.entries(endpointNames).map(
          async ([name, url]) => [name, await request(url)]
        )
      );
      render(host, Object.fromEntries(values));
    } catch (error) {
      host.innerHTML = `<p class="tplink-provider-error">Provider information is unavailable: ${safe(error.message)}</p>`;
    } finally {
      state.loading = false;
      host.removeAttribute('aria-busy');
    }
  }

  window.TPLinkProviderDashboard = Object.freeze({
    capabilityRows,
    displayValue,
    render,
    load,
    endpoints:endpointNames,
  });
  document.addEventListener('DOMContentLoaded', load);
})();
