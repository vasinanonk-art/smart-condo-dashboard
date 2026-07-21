(() => {
  'use strict';
  if (window.__dashboardLgRemoteInstalled) return;
  window.__dashboardLgRemoteInstalled = true;

  const COMMAND_GROUPS = [
    ['Power', [['Power On','power_on','primary'],['Power Off','power_off','danger']]],
    ['Volume', [['Volume +','volume_up'],['Volume −','volume_down'],['Mute','mute'],['Unmute','unmute']]],
    ['Inputs', [['HDMI 1','hdmi1'],['HDMI 2','hdmi2'],['HDMI 3','hdmi3'],['HDMI 4','hdmi4']]],
    ['Apps', [['Netflix','netflix'],['YouTube','youtube'],['Disney+','disney'],['Prime Video','prime'],['Apple TV','appletv'],['Live TV','livetv'],['Browser','browser'],['Viu','viu'],['HBO Max','hbo']]],
  ];
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');

  function key(label, command, cls = '') {
    return `<button type="button" class="lg-remote-key ${cls}" data-lg-command="${safe(command)}">${safe(label)}</button>`;
  }

  function bindOnce(host) {
    if (host.dataset.lgRemoteBound === '1') return;
    host.dataset.lgRemoteBound = '1';
    host.onclick = event => {
      const button = event.target.closest('[data-lg-command]');
      if (!button || !host.contains(button)) return;
      event.preventDefault();
      event.stopPropagation();
      const command = button.dataset.lgCommand;
      if (command && typeof window.tv === 'function') window.tv(command);
    };
  }

  function renderLgRemoteControls() {
    const host = document.getElementById('tvButtons');
    if (!host) return;
    bindOnce(host);
    if (host.dataset.lgRemoteRendered === '1') return;

    const navigation = [
      '<span class="lg-remote-key empty"></span>', key('▲','up'), '<span class="lg-remote-key empty"></span>',
      key('◀','left'), key('OK','ok','primary nav-ok'), key('▶','right'),
      key('Back','back'), key('▼','down'), key('Home','home_key'),
    ].join('');
    const groups = COMMAND_GROUPS.map(([title, commands]) =>
      `<section class="lg-remote-command-group"><h4>${safe(title)}</h4><div class="lg-remote-command-list">${commands.map(([label, command, cls]) => key(label, command, cls || '')).join('')}</div></section>`
    ).join('');

    host.className = 'lg-remote-panel';
    host.innerHTML = `<div class="lg-remote-sections"><section class="lg-remote-card"><h3>Navigation</h3><div class="lg-remote-grid">${navigation}</div></section><section class="lg-remote-card"><h3>Commands</h3><div class="lg-remote-command-groups">${groups}</div></section></div>`;
    host.dataset.lgRemoteRendered = '1';
  }

  // Controls only. Telemetry and pairing are owned exclusively by mountLgTvPage().
  window.renderEntertainment = renderLgRemoteControls;
})();