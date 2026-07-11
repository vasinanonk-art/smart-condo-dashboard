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

  // dashboard_v3.js binds application buttons inside renderEntertainment() while
  // a local variable named `tv` shadows the global tv(command) function. Handle
  // those buttons in capture phase so the existing backend command/API remains
  // unchanged and the broken local onclick is never reached.
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
