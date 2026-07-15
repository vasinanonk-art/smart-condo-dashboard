(() => {
  'use strict';
  const form = document.getElementById('loginForm');
  const username = document.getElementById('username');
  const password = document.getElementById('password');
  const button = document.getElementById('loginButton');
  const label = button?.querySelector('.button-label');
  const loading = button?.querySelector('.button-loading');
  const errorBox = document.getElementById('loginError');
  const sessionMessage = document.getElementById('sessionMessage');
  const toggle = document.getElementById('togglePassword');
  const params = new URLSearchParams(location.search);
  const destination = params.get('next') || '/';

  function safeDestination(value) {
    return typeof value === 'string' && value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : '/';
  }

  function showError(message) {
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function setLoading(active) {
    if (!button) return;
    button.disabled = active;
    if (label) label.hidden = active;
    if (loading) loading.hidden = !active;
  }

  if (params.get('expired') === '1' && sessionMessage) {
    sessionMessage.textContent = 'Your session expired. Sign in again to continue.';
    sessionMessage.hidden = false;
  }

  toggle?.addEventListener('click', () => {
    const showing = password.type === 'text';
    password.type = showing ? 'password' : 'text';
    toggle.textContent = showing ? 'Show' : 'Hide';
    toggle.setAttribute('aria-label', showing ? 'Show password' : 'Hide password');
  });

  form?.addEventListener('submit', async event => {
    event.preventDefault();
    if (errorBox) errorBox.hidden = true;
    setLoading(true);
    try {
      const response = await fetch('/api/auth/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: username.value, password: password.value, next: safeDestination(destination)})
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const wait = payload.retry_after_sec ? ` Try again in ${payload.retry_after_sec} seconds.` : '';
        throw new Error(`${payload.detail || 'Login failed.'}${wait}`);
      }
      location.replace(safeDestination(payload.next || destination));
    } catch (error) {
      showError(error.message || 'Login failed.');
      password.select();
    } finally {
      setLoading(false);
    }
  });
})();
