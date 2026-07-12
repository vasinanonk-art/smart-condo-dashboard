(() => {
  async function sendCommand(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `${url} ${response.status}`);
    return data;
  }

  function notify(message) {
    if (typeof window.toast === 'function') {
      window.toast(message);
      return;
    }
    const host = document.getElementById('toast');
    if (!host) return;
    host.textContent = message;
    host.style.display = 'block';
    window.setTimeout(() => { host.style.display = 'none'; }, 2200);
  }

  // Keep one capture handler for TV commands. The command route and MQTT bridge
  // remain unchanged.
  document.addEventListener('click', async event => {
    const button = event.target.closest('button[data-tv-command]');
    if (!button) return;

    event.preventDefault();
    event.stopImmediatePropagation();

    const command = button.dataset.tvCommand;
    if (!command || button.disabled) return;

    button.disabled = true;
    try {
      await sendCommand('/api/command', {cmd: command});
      notify(`TV: ${command}`);
    } catch (error) {
      notify(error.message || 'TV command failed');
    } finally {
      button.disabled = false;
    }
  }, true);
})();

// This file is parser-loaded after dashboard_v3.js. Insert the topology module
// synchronously so it extends the existing renderer and refresh timer without
// creating a second polling interval.
if (!window.__smartCondoTopologyLoaded) {
  window.__smartCondoTopologyLoaded = true;
  document.write('<script src="/assets/dashboard_topology.js"><\/script>');
}
