window.DashboardModules = (() => {
  const state = {
    zones: {configured: false, zones: []},
    automations: {configured: false, automations: []},
    zoneTimers: {},
    picker: {}
  };

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const zoneLabel = value => String(value || '').replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
  const uniqueValues = (zone, key) => [...new Set((zone.devices || []).map(device => device.values?.[key]).filter(value => value !== null && value !== undefined).map(String))];
  const zoneValue = (zone, key, fallback) => {
    const values = uniqueValues(zone, key);
    const numeric = values.map(Number).filter(Number.isFinite);
    return {mixed: values.length > 1, value: numeric.length ? Math.round(numeric.reduce((sum, value) => sum + value, 0) / numeric.length) : fallback};
  };

  async function loadZones(apiGet) {
    try {
      const payload = await apiGet('/api/lighting/zones');
      if (payload && Array.isArray(payload.zones)) state.zones = payload;
    } catch (error) {
      console.warn('Lighting zones refresh failed:', error.message);
    }
    return state.zones;
  }

  async function loadAutomations(apiGet) {
    try {
      const payload = await apiGet('/api/ha/automations');
      if (payload && Array.isArray(payload.automations)) state.automations = payload;
    } catch (error) {
      console.warn('Automation refresh failed:', error.message);
    }
    return state.automations;
  }

  async function sendZone(zone, action, extra, apiPost, notify, rerender) {
    try {
      const result = await apiPost('/api/lighting/zone', {zone, action, ...extra});
      const total = (result.results || []).length;
      const success = (result.results || []).filter(item => item.ok).length;
      notify(`${zoneLabel(zone)}: ${success}/${total} devices updated`);
      window.setTimeout(async () => {
        await loadZones(window.get);
        rerender();
      }, 600);
    } catch (error) {
      notify(error.message);
    }
  }

  function debounceZone(zone, action, extra, apiPost, notify, rerender) {
    const key = `${zone}:${action}`;
    clearTimeout(state.zoneTimers[key]);
    state.zoneTimers[key] = window.setTimeout(() => sendZone(zone, action, extra, apiPost, notify, rerender), 180);
  }

  function rgb(h, s, v) {
    const c = v * s;
    const x = c * (1 - Math.abs((h / 60) % 2 - 1));
    const m = v - c;
    let r = 0, g = 0, b = 0;
    if (h < 60) [r,g,b] = [c,x,0];
    else if (h < 120) [r,g,b] = [x,c,0];
    else if (h < 180) [r,g,b] = [0,c,x];
    else if (h < 240) [r,g,b] = [0,x,c];
    else if (h < 300) [r,g,b] = [x,0,c];
    else [r,g,b] = [c,0,x];
    return [r,g,b].map(value => Math.round((value + m) * 255));
  }

  function pickerState(zone) {
    return state.picker[zone] || (state.picker[zone] = {h: 0, s: 0, v: 1000});
  }

  function drawHue(canvas, zone) {
    const context = canvas.getContext('2d');
    const width = canvas.width, height = canvas.height, cx = width / 2, cy = height / 2, radius = width / 2 - 2;
    const image = context.createImageData(width, height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const dx = x - cx, dy = y - cy, distance = Math.sqrt(dx * dx + dy * dy), index = (y * width + x) * 4;
        if (distance > radius || distance < radius * 0.62) continue;
        let hue = Math.atan2(dy, dx) * 180 / Math.PI;
        if (hue < 0) hue += 360;
        const [r,g,b] = rgb(hue, 1, 1);
        image.data[index] = r; image.data[index + 1] = g; image.data[index + 2] = b; image.data[index + 3] = 255;
      }
    }
    context.putImageData(image, 0, 0);
    const current = pickerState(zone), angle = current.h * Math.PI / 180, markerRadius = radius * 0.81;
    context.beginPath(); context.arc(cx + Math.cos(angle) * markerRadius, cy + Math.sin(angle) * markerRadius, 6, 0, Math.PI * 2);
    context.strokeStyle = '#fff'; context.lineWidth = 3; context.stroke();
  }

  function drawSv(canvas, zone) {
    const context = canvas.getContext('2d');
    const current = pickerState(zone), [r,g,b] = rgb(current.h, 1, 1);
    const horizontal = context.createLinearGradient(0, 0, canvas.width, 0);
    horizontal.addColorStop(0, '#fff'); horizontal.addColorStop(1, `rgb(${r},${g},${b})`);
    context.fillStyle = horizontal; context.fillRect(0, 0, canvas.width, canvas.height);
    const vertical = context.createLinearGradient(0, 0, 0, canvas.height);
    vertical.addColorStop(0, 'rgba(0,0,0,0)'); vertical.addColorStop(1, '#000');
    context.fillStyle = vertical; context.fillRect(0, 0, canvas.width, canvas.height);
    context.beginPath(); context.arc(current.s / 1000 * canvas.width, (1 - current.v / 1000) * canvas.height, 7, 0, Math.PI * 2);
    context.strokeStyle = '#fff'; context.lineWidth = 3; context.stroke();
  }

  function updatePicker(zone, send, apiPost, notify, rerender) {
    const current = pickerState(zone);
    const hue = document.getElementById(`hue-${zone}`), sv = document.getElementById(`sv-${zone}`), preview = document.getElementById(`preview-${zone}`), values = document.getElementById(`pickerv-${zone}`);
    if (hue) drawHue(hue, zone);
    if (sv) drawSv(sv, zone);
    if (preview) preview.style.background = `rgb(${rgb(current.h, current.s / 1000, current.v / 1000).join(',')})`;
    if (values) values.textContent = `H ${current.h}° · S ${Math.round(current.s / 10)}% · V ${Math.round(current.v / 10)}%`;
    if (send) debounceZone(zone, 'rgb', {h: current.h, s: current.s, v: current.v}, apiPost, notify, rerender);
  }

  function bindPicker(zone, apiPost, notify, rerender) {
    const hue = document.getElementById(`hue-${zone}`), sv = document.getElementById(`sv-${zone}`);
    if (!hue || !sv || hue.dataset.bound === '1') return;
    const bind = (element, handler) => {
      let dragging = false;
      element.onpointerdown = event => { dragging = true; element.setPointerCapture(event.pointerId); handler(event, false); };
      element.onpointermove = event => { if (dragging) handler(event, false); };
      element.onpointerup = event => { dragging = false; handler(event, true); };
    };
    bind(hue, (event, send) => {
      const rect = hue.getBoundingClientRect();
      const x = event.clientX - rect.left - rect.width / 2, y = event.clientY - rect.top - rect.height / 2;
      pickerState(zone).h = Math.round((Math.atan2(y, x) * 180 / Math.PI + 360) % 360);
      updatePicker(zone, send, apiPost, notify, rerender);
    });
    bind(sv, (event, send) => {
      const rect = sv.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
      const current = pickerState(zone);
      current.s = Math.round(x / rect.width * 1000);
      current.v = Math.round((1 - y / rect.height) * 1000);
      updatePicker(zone, send, apiPost, notify, rerender);
    });
    hue.dataset.bound = '1'; sv.dataset.bound = '1';
    updatePicker(zone, false, apiPost, notify, rerender);
  }

  function zoneCard(zone) {
    const total = (zone.devices || []).length;
    const brightness = zoneValue(zone, 'brightness', 500), temperature = zoneValue(zone, 'temperature', 500);
    const support = zone.support || {};
    return `<article class="zone-card"><div class="zone-head"><div><div class="zone-name">${esc(zoneLabel(zone.zone))}</div><div class="zone-meta"><span class="status-pill">${total} Lights</span>${zone.partial_support ? '<span class="status-pill warn">Partial Support</span>' : '<span class="status-pill ok">Full Support</span>'}</div></div><button class="btn ghost" data-zone-refresh="${esc(zone.zone)}">Refresh Zone</button></div>${Number(support.brightness || 0) ? `<div class="zone-control"><label>Brightness <strong id="zb-${esc(zone.zone)}" class="${brightness.mixed ? 'mixed' : ''}">${brightness.mixed ? 'Mixed' : brightness.value}</strong></label><input data-zone="${esc(zone.zone)}" data-action="brightness" type="range" min="10" max="1000" value="${brightness.value}"></div>` : ''}${Number(support.temperature || 0) ? `<div class="zone-control"><label>Color Temperature <strong id="zt-${esc(zone.zone)}" class="${temperature.mixed ? 'mixed' : ''}">${temperature.mixed ? 'Mixed' : temperature.value}</strong></label><input data-zone="${esc(zone.zone)}" data-action="temperature" type="range" min="0" max="1000" value="${temperature.value}"></div>` : ''}${Number(support.rgb || 0) ? `<div class="zone-control"><label>RGB</label><div class="picker-pro"><canvas id="hue-${esc(zone.zone)}" class="hue-wheel" width="150" height="150"></canvas><canvas id="sv-${esc(zone.zone)}" class="sv-square" width="160" height="150"></canvas><div class="picker-preview-wrap"><div id="preview-${esc(zone.zone)}" class="picker-preview"></div><div id="pickerv-${esc(zone.zone)}" class="picker-values"></div></div></div></div>` : ''}<div class="zone-control"><label>Presets</label><div class="preset-grid">${(zone.presets || []).map(preset => `<button class="btn ghost preset-chip" data-zone="${esc(zone.zone)}" data-preset="${esc(preset.key)}">${esc(preset.label || preset.key)}</button>`).join('') || '<span class="muted">No supported presets</span>'}</div></div></article>`;
  }

  function renderZones(host, apiPost, notify) {
    if (!host) return;
    if (!state.zones.configured) {
      host.className = 'zone-grid';
      host.innerHTML = '<div class="empty">No zones configured. Configure TUYA_LIGHT_ZONES_JSON to enable zone controls.</div>';
      return;
    }
    host.className = 'zone-grid';
    host.innerHTML = (state.zones.zones || []).map(zoneCard).join('');
    const rerender = () => renderZones(host, apiPost, notify);
    host.querySelectorAll('input[data-action]').forEach(input => {
      input.oninput = () => {
        const output = document.getElementById(`${input.dataset.action === 'brightness' ? 'zb' : 'zt'}-${input.dataset.zone}`);
        if (output) { output.textContent = input.value; output.className = ''; }
      };
      input.onchange = () => debounceZone(input.dataset.zone, input.dataset.action, {value: Number(input.value)}, apiPost, notify, rerender);
    });
    host.querySelectorAll('[data-preset]').forEach(button => button.onclick = () => sendZone(button.dataset.zone, 'preset', {preset: button.dataset.preset}, apiPost, notify, rerender));
    host.querySelectorAll('[data-zone-refresh]').forEach(button => button.onclick = async () => { await loadZones(window.get); rerender(); });
    (state.zones.zones || []).forEach(zone => bindPicker(zone.zone, apiPost, notify, rerender));
  }

  function automationGroup(automation) {
    const text = `${automation.name} ${automation.entity_id}`.toLowerCase();
    if (/air|pm2|purifier|filter/.test(text)) return 'Air Quality';
    if (/night|sleep|bed/.test(text)) return 'Night Mode';
    if (/presence|home|away|arriv|person/.test(text)) return 'Presence';
    if (/security|alarm|camera|door|lock/.test(text)) return 'Security';
    return 'Other';
  }

  function renderAutomations(host, actionHandler) {
    if (!host) return;
    const payload = state.automations;
    if (!payload.configured) { host.innerHTML = '<div class="empty">Home Assistant is not configured.</div>'; return; }
    if (!(payload.automations || []).length) { host.innerHTML = '<div class="empty">No Home Assistant automations available.</div>'; return; }
    const groups = {};
    payload.automations.forEach(item => (groups[automationGroup(item)] ||= []).push(item));
    host.innerHTML = Object.entries(groups).map(([name, items]) => `<section class="automation-group"><h3 class="automation-group-title">${esc(name)}</h3><div class="automation-grid">${items.map(item => `<article class="automation-card"><div class="automation-head"><div class="automation-name">${esc(item.name)}</div><span class="status-pill ${item.enabled ? 'ok' : ''}">${item.enabled ? 'Enabled' : 'Disabled'}</span></div><div class="automation-meta"><span>Last Triggered: ${esc(item.last_triggered || 'Never')}</span><span>Mode: ${esc(item.mode || 'Unknown')}</span><span>Current Runs: ${Number(item.current || 0)}</span></div><div class="automation-actions"><button class="btn primary" data-entity="${esc(item.entity_id)}" data-action="enable" ${item.enabled || !item.available ? 'disabled' : ''}>Enable</button><button class="btn danger" data-entity="${esc(item.entity_id)}" data-action="disable" ${!item.enabled || !item.available ? 'disabled' : ''}>Disable</button><button class="btn ghost" data-entity="${esc(item.entity_id)}" data-action="trigger" ${item.available ? '' : 'disabled'}>Run</button></div></article>`).join('')}</div></section>`).join('');
    host.querySelectorAll('[data-entity][data-action]').forEach(button => button.onclick = () => actionHandler(button.dataset.entity, button.dataset.action));
  }

  return {state, loadZones, loadAutomations, renderZones, renderAutomations};
})();