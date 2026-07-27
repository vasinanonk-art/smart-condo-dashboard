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
    /netflix/i, /youtube/i, /disney/i, /prime video|amazon prime/i,
    /apple tv/i, /plex/i,
  ];
  const escape = UI.safe;

  function button(command, label, disabled = false, reason = '', attributes = '', className = '') {
    return UI.actionButton({
      label, disabled, reason, className,
      attributes:`data-lg-command="${escape(command)}"${attributes ? ` ${attributes}` : ''}`,
    });
  }

  function renderInputs(host, items, available) {
    const inputs = items.filter(item => item && item.id && item.label);
    const section = document.createElement('section');
    section.className = 'household-lg-section';
    if (!available) {
      section.innerHTML = '<h3>Inputs</h3><p class="household-lg-option-unavailable">Live input enumeration is unavailable.</p>';
    } else if (!inputs.length) {
      section.innerHTML = '<h3>Inputs</h3><p class="household-lg-option-unavailable">No supported inputs were reported by the TV.</p>';
    } else {
      section.innerHTML = `<h3>Inputs</h3><select data-lg-option="set_input" aria-label="Inputs"><option value="">Select…</option>${inputs.map(item => `<option value="${escape(item.id)}">${escape(item.label)}</option>`).join('')}</select>`;
      section.querySelector('select').addEventListener('change', async event => {
        const select = event.currentTarget;
        const value = select.value;
        if (!value || select.dataset.lgPending === 'true') return;
        select.dataset.lgPending = 'true';
        select.disabled = true;
        try {
          await window.tv('set_input', value);
        } finally {
          select.value = '';
          select.disabled = false;
          delete select.dataset.lgPending;
        }
      });
    }
    host.appendChild(section);
  }

  function renderApplications(host, items, available) {
    const applications = commonApplications
      .map(pattern => items.find(item => pattern.test(String(item.label || ''))))
      .filter((item, index, values) => item && values.indexOf(item) === index)
      .slice(0, 6);
    const section = document.createElement('section');
    section.className = 'household-lg-section';
    if (!available) {
      section.innerHTML = '<h3>Applications</h3><p class="household-lg-option-unavailable">Live application enumeration is unavailable.</p>';
    } else if (!applications.length) {
      section.innerHTML = '<h3>Applications</h3><p class="household-lg-option-unavailable">No common applications were reported by the TV.</p>';
    } else {
      section.innerHTML = `<h3>Applications</h3><div class="household-action-grid household-lg-button-row">${applications.map(item => button('launch_app', item.label, false, '', `data-lg-value="${escape(item.id)}"`)).join('')}</div>`;
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
        <section class="household-lg-section"><h3>Power</h3><div class="household-action-grid household-lg-button-row">
          ${button('power_on', 'Power On', !supported.has('power_on'), supported.has('power_on') ? '' : wolReason)}
          ${button('power_off', 'Power Off', !supported.has('power_off'), 'Power Off is unavailable.')}
        </div>${supported.has('power_on') ? '' : UI.warningBox(wolReason, 'lg-power-on-reason')}</section>
        <section class="household-lg-section"><h3>Navigation</h3><div class="household-action-grid household-lg-navigation">
          ${['up','left','ok','right','down','back','home'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name), '', '', `household-lg-nav-${name}`)).join('')}
        </div></section>
        <section class="household-lg-section"><h3>Volume</h3><div class="household-action-grid household-lg-button-row">
          ${['volume_up','volume_down','mute','unmute'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
          <label class="household-lg-volume-set">Set volume <input type="range" min="0" max="100" value="20" data-lg-volume ${supported.has('set_volume') ? '' : 'disabled'} aria-label="LG TV volume"><output>20</output></label>
          ${UI.actionButton({label:'Set', disabled:!supported.has('set_volume'), reason:supported.has('set_volume') ? '' : 'Set volume is unavailable.', attributes:'data-lg-set-volume'})}
        </div></section>
        <section class="household-lg-section"><h3>Playback</h3><div class="household-action-grid household-lg-button-row">
          ${['play','pause','stop','rewind','fast_forward'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
        </div></section>
      </div>`;
    const controls = host.querySelector('.household-lg-controls');
    if (capabilities.inventory_refreshing === true) {
      controls.insertAdjacentHTML('beforeend', '<p class="household-lg-option-refreshing" role="status">Refreshing applications and inputs…</p>');
    }
    renderInputs(controls, capabilities.inputs || [], capabilities.inputs_available === true || capabilities.enumeration_available === true);
    renderApplications(controls, capabilities.applications || [], capabilities.applications_available === true || capabilities.enumeration_available === true);
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
    slider?.addEventListener('input', () => { slider.nextElementSibling.value = slider.value; });
    host.querySelector('[data-lg-set-volume]')?.addEventListener('click', async event => {
      const element = event.currentTarget;
      if (element.dataset.lgPending === 'true') return;
      element.dataset.lgPending = 'true';
      element.disabled = true;
      try {
        await window.tv('set_volume', Number(slider.value));
      } finally {
        if (element.isConnected) {
          element.disabled = false;
          delete element.dataset.lgPending;
        }
      }
    });
  };
})();
