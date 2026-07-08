(function () {
  function sonoffLatestDevices() {
    const response = typeof deviceState !== 'undefined' && deviceState.sonoffResponse ? deviceState.sonoffResponse : {};
    return Array.isArray(response.devices) ? response.devices : [];
  }

  async function refreshAfterBulk(response) {
    if (response && Array.isArray(response.devices)) {
      sonoffSetResponse(response);
      renderSonoff();
    }
    const fresh = await getJson('/api/sonoff');
    sonoffSetResponse(fresh);
    renderSonoff();
  }

  async function sonoffBulkAll(action, btn) {
    if (action === 'off' && !window.confirm('Turn OFF all Sonoff switches?')) return;
    if (sonoffBusy) return;
    sonoffBusy = true;
    if (btn) btn.disabled = true;
    try {
      const response = await post('/api/sonoff/all', { action });
      await refreshAfterBulk(response);
      show('Sonoff ALL ' + action.toUpperCase());
    } catch (e) {
      show('ERR: ' + e.message);
    } finally {
      sonoffBusy = false;
      if (btn) btn.disabled = false;
    }
  }

  async function sonoffBulkDevice(deviceid, action, btn) {
    if (sonoffBusy) return;
    sonoffBusy = true;
    if (btn) btn.disabled = true;
    try {
      const response = await post('/api/sonoff/device', { deviceid, action });
      await refreshAfterBulk(response);
      show('Sonoff ' + deviceid + ' ALL ' + action.toUpperCase());
    } catch (e) {
      show('ERR: ' + e.message);
    } finally {
      sonoffBusy = false;
      if (btn) btn.disabled = false;
    }
  }

  function button(label, className, handler) {
    const el = document.createElement('button');
    el.textContent = label;
    el.className = className || '';
    el.onclick = function () { handler(el); };
    return el;
  }

  function addSonoffTopControls() {
    const section = document.getElementById('sonoffSection');
    const cards = document.getElementById('sonoffCards');
    if (!section || !cards || section.querySelector('.sonoff-bulk-top')) return;
    const row = document.createElement('div');
    row.className = 'actions sonoff-bulk-top';
    row.style.margin = '0 0 12px 0';
    row.appendChild(button('Refresh', 'ghost', async function (btn) {
      if (btn) btn.disabled = true;
      try { await loadSonoff(); show('Sonoff refreshed'); } catch (e) { show('ERR: ' + e.message); }
      finally { if (btn) btn.disabled = false; }
    }));
    row.appendChild(button('ALL ON', 'primary', function (btn) { sonoffBulkAll('on', btn); }));
    row.appendChild(button('ALL OFF', 'danger', function (btn) { sonoffBulkAll('off', btn); }));
    section.insertBefore(row, cards);
  }

  function addPerDeviceBulkControls() {
    const cards = document.getElementById('sonoffCards');
    if (!cards) return;
    const devices = sonoffLatestDevices();
    const deviceCards = Array.from(cards.querySelectorAll('.metric')).filter(function (card) {
      return card.querySelector('.presence-state');
    });
    devices.forEach(function (device, idx) {
      const channels = sonoffChannels(device);
      if (channels.length <= 1) return;
      const card = deviceCards[idx];
      if (!card || card.querySelector('.sonoff-device-bulk')) return;
      const row = document.createElement('div');
      row.className = 'actions sonoff-device-bulk';
      row.style.marginTop = '10px';
      row.appendChild(button('ALL ON', 'primary', function (btn) { sonoffBulkDevice(device.deviceid, 'on', btn); }));
      row.appendChild(button('ALL OFF', 'danger', function (btn) { sonoffBulkDevice(device.deviceid, 'off', btn); }));
      const age = card.querySelector('.presence-small');
      if (age && age.parentNode === card) {
        card.insertBefore(row, age.nextSibling);
      } else {
        card.appendChild(row);
      }
    });
  }

  function enhanceSonoffControls() {
    addSonoffTopControls();
    addPerDeviceBulkControls();
  }

  const originalRenderSonoff = window.renderSonoff;
  window.renderSonoff = function () {
    originalRenderSonoff();
    enhanceSonoffControls();
  };

  window.sonoffBulkAll = sonoffBulkAll;
  window.sonoffBulkDevice = sonoffBulkDevice;

  setTimeout(function () {
    try {
      renderSonoff();
      loadSonoff();
    } catch (e) {
      console.warn('sonoff bulk init failed', e);
    }
  }, 0);
})();
