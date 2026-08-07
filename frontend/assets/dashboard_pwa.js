(() => {
  'use strict';
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;

  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', {
      scope:'/',
      updateViaCache:'none',
    }).catch(error => {
      console.warn('Dashboard PWA shell is unavailable:', error.message);
    });
  }, {once:true});
})();
