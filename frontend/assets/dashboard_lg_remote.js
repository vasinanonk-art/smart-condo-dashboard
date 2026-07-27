(() => {
  'use strict';
  if (window.__lgCompactRemoteInstalled) return;
  window.__lgCompactRemoteInstalled = true;

  const UI = window.HouseholdUI;
  const commands = [
    ['power_on', 'Power On'], ['power_off', 'Power Off'],
    ['up', '▲'], ['left', '◀'], ['ok', 'OK'], ['right', '▶'], ['down', '▼'],
    ['back', 'Back'], ['home', 'Home'],
    ['volume_up', 'Volume +'], ['volume_down', 'Volume -'], ['mute', 'Mute'], ['unmute', 'Unmute'],
    ['play', 'Play'], ['pause', 'Pause'], ['stop', 'Stop'],
    ['rewind', 'Rewind'], ['fast_forward', 'Fast Forward']
  ];
  const commonApplications = [
    {pattern:/netflix/i, icon:'▶', label:'Netflix'},
    {pattern:/youtube/i, icon:'▶', label:'YouTube'},
    {pattern:/disney/i, icon:'★', label:'Disney+'},
    {pattern:/prime video|amazon prime/i, icon:'▶', label:'Prime Video'},
    {pattern:/apple tv/i, icon:'●', label:'Apple TV'},
    {pattern:/plex/i, icon:'▶', label:'Plex'},
  ];
  const escape = UI.safe;
  let volumeTimer = null;
  let volumeSending = false;
  let volumeDragging = false;
  let queuedVolume = null;

  function button(command, label, disabled = false, reason = '', attributes = '', className = '') {
    return UI.actionButton({
      label, disabled, reason, className,
      attributes:`data-lg-command="${escape(command)}"${attributes ? ` ${attributes}` : ''}`,
    });
  }

  function renderInputs(host, items, available) {
    const inputs = items.filter(item => item && item.id && item.label);
    const section = document.createElement('section');
    section.className = 'household-lg-section household-lg-inputs';
    if (!available) {
      section.innerHTML = '<h3>Inputs</h3><p class="household-lg-option-unavailable">Live input enumeration is unavailable.</p>';
    } else if (!inputs.length) {
      section.innerHTML = '<h3>Inputs</h3><p class="household-lg-option-unavailable">No supported inputs were reported by the TV.</p>';
    } else {
      section.innerHTML = `<h3>Inputs</h3><div class="household-action-grid household-lg-input-grid">${inputs.map(item => button('set_input', item.label, false, '', `data-lg-value="${escape(item.id)}"`)).join('')}</div>`;
    }
    host.appendChild(section);
  }

  function renderApplications(host, items, available) {
    const applications = commonApplications
      .map(({pattern, icon, label}) => {
        const item = items.find(candidate => pattern.test(String(candidate.label || '')));
        return item ? {...item, compactLabel:`${icon} ${label}`} : null;
      })
      .filter((item, index, values) => item && values.findIndex(value => value?.id === item.id) === index)
      .slice(0, 6);
    const section = document.createElement('section');
    section.className = 'household-lg-section household-lg-applications';
    if (!available) {
      section.innerHTML = '<h3>Applications</h3><p class="household-lg-option-unavailable">Live application enumeration is unavailable.</p>';
    } else if (!applications.length) {
      section.innerHTML = '<h3>Applications</h3><p class="household-lg-option-unavailable">No common applications were reported by the TV.</p>';
    } else {
      section.innerHTML = `<h3>Applications</h3><div class="household-action-grid household-lg-app-grid">${applications.map(item => button('launch_app', item.compactLabel, false, '', `data-lg-value="${escape(item.id)}"`)).join('')}</div>`;
    }
    host.appendChild(section);
  }

  window.renderLgCompactRemote = function renderLgCompactRemote(capabilities = {}) {
    const host = document.getElementById('tvButtons');
    if (!host) return;
    const supported = new Set(capabilities.supported || []);
    const wolReason = 'Wake-on-LAN is not configured. Add the TV MAC address to enable Power On.';
    host.innerHTML = `
      <div class="household-lg-controls">
        <section class="household-lg-section household-lg-power"><h3>Power</h3><div class="household-action-grid household-lg-button-row">
          ${button('power_on', 'Power On', !supported.has('power_on'), supported.has('power_on') ? '' : wolReason)}
          ${button('power_off', 'Power Off', !supported.has('power_off'), 'Power Off is unavailable.')}
        </div>${supported.has('power_on') ? '' : UI.warningBox(wolReason, 'lg-power-on-reason')}</section>
        <section class="household-lg-section household-lg-navigation-section"><h3>Navigation</h3><div class="household-action-grid household-lg-navigation">
          <span class="household-lg-nav-empty" aria-hidden="true"></span>
          ${button('up', '▲', !supported.has('up'), '', '', 'household-lg-nav-up')}
          <span class="household-lg-nav-empty" aria-hidden="true"></span>
          ${button('left', '◀', !supported.has('left'), '', '', 'household-lg-nav-left')}
          ${button('ok', 'OK', !supported.has('ok'), '', '', 'household-lg-nav-ok')}
          ${button('right', '▶', !supported.has('right'), '', '', 'household-lg-nav-right')}
          ${button('back', 'Back', !supported.has('back'), '', '', 'household-lg-nav-back')}
          ${button('down', '▼', !supported.has('down'), '', '', 'household-lg-nav-down')}
          ${button('home', 'Home', !supported.has('home'), '', '', 'household-lg-nav-home')}
        </div></section>
        <section class="household-lg-section household-lg-playback"><h3>Playback</h3><div class="household-action-grid household-lg-playback-grid">
          ${['play','pause','stop','rewind','fast_forward'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
        </div></section>
        <section class="household-lg-section household-lg-volume"><h3>Volume</h3><div class="household-action-grid household-lg-volume-grid">
          ${['volume_up','volume_down','mute','unmute'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
          <label class="household-lg-volume-set">Volume <input type="range" min="0" max="100" value="20" data-lg-volume ${supported.has('set_volume') ? '' : 'disabled'} aria-label="LG TV volume"><output>20</output></label>
        </div></section>
      </div>`;
    const controls = host.querySelector('.household-lg-controls');
    if (capabilities.inventory_refreshing === true) {
      controls.insertAdjacentHTML('beforeend', '<p class="household-lg-option-refreshing" role="status">Refreshing applications and inputs…</p>');
    }
    renderApplications(controls, capabilities.applications || [], capabilities.applications_available === true || capabilities.enumeration_available === true);
    renderInputs(controls, capabilities.inputs || [], capabilities.inputs_available === true || capabilities.enumeration_available === true);
    host.querySelectorAll('[data-lg-command]').forEach(element => element.addEventListener('click', async () => {
      if (element.dataset.lgPending === 'true') return;
      element.dataset.lgPending = 'true';
      element.disabled = true;
      try {
        await window.tv(element.dataset.lgCommand, element.dataset.lgValue);
      } finally {
        if (element.isConnected) {
          element.disabled = false;
          delete element.dataset.lgPending;
        }
      }
    }));
    const slider = host.querySelector('[data-lg-volume]');
    const sendQueuedVolume = async () => {
      if (volumeSending || queuedVolume === null) return;
      const value = queuedVolume;
      queuedVolume = null;
      volumeSending = true;
      try {
        await window.tv('set_volume', value);
      } catch (_) {
        // window.tv owns user-visible command failure feedback.
      } finally {
        volumeSending = false;
        if (queuedVolume !== null) {
          clearTimeout(volumeTimer);
          volumeTimer = setTimeout(sendQueuedVolume, 400);
        }
      }
    };
    const scheduleVolume = () => {
      clearTimeout(volumeTimer);
      volumeTimer = setTimeout(sendQueuedVolume, 400);
    };
    slider?.addEventListener('pointerdown', () => {
      volumeDragging = true;
      clearTimeout(volumeTimer);
    });
    slider?.addEventListener('pointerup', () => {
      volumeDragging = false;
      scheduleVolume();
    });
    slider?.addEventListener('pointercancel', () => {
      volumeDragging = false;
      scheduleVolume();
    });
    slider?.addEventListener('change', () => {
      volumeDragging = false;
      scheduleVolume();
    });
    slider?.addEventListener('input', () => {
      slider.nextElementSibling.value = slider.value;
      queuedVolume = Number(slider.value);
      if (!volumeDragging) scheduleVolume();
    });
  };
})();
