(() => {
  'use strict';
  if (window.DashboardElectricitySummaryStore) return;

  const TTL_MS = 15000;
  let cached = null;
  let cachedAt = 0;
  let inFlight = null;
  let controller = null;
  let disposed = false;
  const listeners = new Set();

  function notify(payload) {
    listeners.forEach(listener => {
      try { listener(payload); } catch (_) {}
    });
  }

  async function getSummary() {
    if (disposed) return cached;
    const now = Date.now();
    if (cached && now - cachedAt < TTL_MS) return cached;
    if (inFlight) return inFlight;

    controller = typeof AbortController === 'function' ? new AbortController() : null;
    const options = controller ? {signal: controller.signal} : undefined;
    inFlight = Promise.resolve(window.get('/api/electricity/summary', options))
      .then(payload => {
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
          throw new Error('invalid electricity summary response');
        }
        cached = payload;
        cachedAt = Date.now();
        notify(cached);
        return cached;
      })
      .finally(() => {
        inFlight = null;
        controller = null;
      });
    return inFlight;
  }

  function subscribe(listener) {
    if (typeof listener !== 'function' || disposed) return () => {};
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    controller?.abort();
    controller = null;
    inFlight = null;
    listeners.clear();
    window.removeEventListener?.('beforeunload', dispose);
  }

  window.addEventListener?.('beforeunload', dispose);
  window.DashboardElectricitySummaryStore = {
    get: getSummary,
    peek: () => cached,
    subscribe,
    dispose,
  };
})();
