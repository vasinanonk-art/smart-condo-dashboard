(() => {
  'use strict';
  const nativeFetch = window.fetch.bind(window);
  let csrfToken = null;
  let statusPromise = null;

  const sameOrigin = input => {
    try {
      const url = new URL(typeof input === 'string' ? input : input.url, location.href);
      return url.origin === location.origin;
    } catch (_) {
      return false;
    }
  };

  const isStateChanging = options => ['POST','PUT','PATCH','DELETE'].includes(String(options?.method || 'GET').toUpperCase());

  async function loadAuthStatus(force = false) {
    if (!force && statusPromise) return statusPromise;
    statusPromise = nativeFetch('/api/auth/status', {credentials:'same-origin', cache:'no-store'})
      .then(async response => {
        const payload = await response.json().catch(() => ({}));
        csrfToken = payload.csrf_token || null;
        if (!payload.authenticated && location.pathname !== '/login') {
          const next = encodeURIComponent(location.pathname + location.search + location.hash);
          location.replace(`/login?expired=1&next=${next}`);
        }
        return payload;
      })
      .catch(() => ({configured:false, authenticated:false}));
    return statusPromise;
  }

  window.fetch = async function authenticatedFetch(input, init = {}) {
    const options = {...init, credentials: init.credentials || 'same-origin'};
    if (sameOrigin(input) && isStateChanging(options)) {
      if (!csrfToken) await loadAuthStatus(true);
      const headers = new Headers(options.headers || {});
      if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
      options.headers = headers;
    }
    const response = await nativeFetch(input, options);
    if (response.status === 401 && location.pathname !== '/login') {
      const next = encodeURIComponent(location.pathname + location.search + location.hash);
      location.replace(`/login?expired=1&next=${next}`);
    }
    return response;
  };

  window.dashboardLogout = async function dashboardLogout() {
    const button = document.getElementById('dashboardLogout');
    if (button) { button.disabled = true; button.textContent = 'Signing out…'; }
    try {
      await window.fetch('/api/auth/logout', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    } finally {
      location.replace('/login');
    }
  };

  loadAuthStatus();
})();
