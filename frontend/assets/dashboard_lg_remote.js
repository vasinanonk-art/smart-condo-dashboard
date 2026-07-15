(() => {
  'use strict';
  if (window.__dashboardLgRemoteInstalled) return;
  window.__dashboardLgRemoteInstalled = true;

  const COMMAND_GROUPS = [
    ['Power', [['Power On','power_on','primary'],['Power Off','power_off','danger']]],
    ['Volume', [['Volume +','volume_up'],['Volume −','volume_down'],['Mute','mute'],['Unmute','unmute']]],
    ['Inputs', [['HDMI 1','hdmi1'],['HDMI 2','hdmi2'],['HDMI 3','hdmi3'],['HDMI 4','hdmi4']]],
    ['Apps', [['Netflix','netflix'],['YouTube','youtube'],['Disney+','disney'],['Prime Video','prime'],['Apple TV','appletv'],['Live TV','livetv'],['Browser','browser'],['Viu','viu'],['HBO Max','hbo']]]
  ];
  const safe = value => window.safeText ? window.safeText(value) : String(value ?? '');
  const value = (input, fallback='Not available') => input === null || input === undefined || input === '' ? fallback : input;

  function key(label, command, cls='') {
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

  function renderLgRemote() {
    const host = document.getElementById('tvButtons');
    if (!host) return;
    const tv = window.S?.tv?.lastValid || {};
    const online = tv.tv_online === true || !['off','false','0','offline','unknown',''].includes(String(tv.power ?? '').toLowerCase());
    const status = online ? 'Online' : tv.tv_online === false ? 'Offline' : 'Unknown';
    const statusItems = [
      ['Status', status], ['Current App', value(tv.app)], ['Input', value(tv.input)],
      ['Volume', value(tv.volume)], ['Mute', tv.mute === true ? 'Muted' : tv.mute === false ? 'Sound on' : 'Unknown'],
      ['Last Update', window.when ? window.when(tv.last_update_ts || tv.last_heartbeat_ts || tv.ts) : 'Not available']
    ];
    const navigation = [
      '<span class="lg-remote-key empty"></span>', key('▲','up'), '<span class="lg-remote-key empty"></span>',
      key('◀','left'), key('OK','ok','primary nav-ok'), key('▶','right'),
      key('Back','back'), key('▼','down'), key('Home','home_key')
    ].join('');
    const groups = COMMAND_GROUPS.map(([title, commands]) => `<section class="lg-remote-command-group"><h4>${safe(title)}</h4><div class="lg-remote-command-list">${commands.map(([label,command,cls])=>key(label,command,cls||'')).join('')}</div></section>`).join('');
    host.className = 'lg-remote-panel';
    host.innerHTML = `<div class="lg-remote-status">${statusItems.map(([label,item])=>`<div class="lg-remote-status-item"><span>${safe(label)}</span><strong>${safe(item)}</strong></div>`).join('')}</div><div class="lg-remote-sections"><section class="lg-remote-card"><h3>Navigation</h3><div class="lg-remote-grid">${navigation}</div></section><section class="lg-remote-card"><h3>Commands</h3><div class="lg-remote-command-groups">${groups}</div></section></div>`;
    bindOnce(host);
  }

  window.renderEntertainment = renderLgRemote;
})();
