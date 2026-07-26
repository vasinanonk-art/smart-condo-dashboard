(() => {
  'use strict';
  if (window.__lgCompactRemoteInstalled) return;
  window.__lgCompactRemoteInstalled = true;

  const UI = window.HouseholdUI;
  const commands = [
    ['power_on', 'Power On'], ['power_off', 'Power Off'],
    ['up', 'Up'], ['left', 'Left'], ['ok', 'OK'], ['right', 'Right'], ['down', 'Down'],
    ['back', 'Back'], ['home', 'Home'],
    ['volume_up', 'Volume +'], ['volume_down', 'Volume -'], ['mute', 'Mute'], ['unmute', 'Unmute'],
    ['play', 'Play'], ['pause', 'Pause'], ['stop', 'Stop']
  ];
  const escape = UI.safe;

  function button(command, label, disabled = false, reason = '') {
    return UI.actionButton({
      label, disabled, reason,
      attributes:`data-lg-command="${escape(command)}"`,
    });
  }

  function renderOptions(host, title, command, items, available) {
    const section = document.createElement('section');
    section.className = 'household-lg-section';
    if (!available) {
      section.innerHTML = `<h3>${escape(title)}</h3><p class="household-lg-option-unavailable">Live ${escape(title.toLowerCase())} enumeration is unavailable.</p>`;
    } else if (!items.length) {
      section.innerHTML = `<h3>${escape(title)}</h3><p class="household-lg-option-unavailable">No ${escape(title.toLowerCase())} were reported by the TV.</p>`;
    } else {
      section.innerHTML = `<h3>${escape(title)}</h3><select data-lg-option="${escape(command)}" aria-label="${escape(title)}"><option value="">Select…</option>${items.map(item => `<option value="${escape(item.id)}">${escape(item.label)}</option>`).join('')}</select>`;
      section.querySelector('select').addEventListener('change', event => {
        if (event.target.value) window.tv(command, event.target.value);
        event.target.value = '';
      });
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
          ${['up','left','ok','right','down','back','home'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
        </div></section>
        <section class="household-lg-section"><h3>Volume</h3><div class="household-action-grid household-lg-button-row">
          ${['volume_up','volume_down','mute','unmute'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
          <label class="household-lg-volume-set">Set volume <input type="range" min="0" max="100" value="20" data-lg-volume ${supported.has('set_volume') ? '' : 'disabled'} aria-label="LG TV volume"><output>20</output></label>
          ${UI.actionButton({label:'Set', disabled:!supported.has('set_volume'), reason:supported.has('set_volume') ? '' : 'Set volume is unavailable.', attributes:'data-lg-set-volume'})}
        </div></section>
        <section class="household-lg-section"><h3>Playback</h3><div class="household-action-grid household-lg-button-row">
          ${['play','pause','stop'].map(name => button(name, commands.find(item => item[0] === name)[1], !supported.has(name))).join('')}
        </div></section>
      </div>`;
    renderOptions(host.querySelector('.household-lg-controls'), 'Inputs', 'set_input', capabilities.inputs || [], capabilities.enumeration_available === true);
    renderOptions(host.querySelector('.household-lg-controls'), 'Applications', 'launch_app', capabilities.applications || [], capabilities.enumeration_available === true);
    host.querySelectorAll('[data-lg-command]').forEach(element => element.addEventListener('click', () => window.tv(element.dataset.lgCommand)));
    const slider = host.querySelector('[data-lg-volume]');
    slider?.addEventListener('input', () => { slider.nextElementSibling.value = slider.value; });
    host.querySelector('[data-lg-set-volume]')?.addEventListener('click', () => window.tv('set_volume', Number(slider.value)));
  };
})();
