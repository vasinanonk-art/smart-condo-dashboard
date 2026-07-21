(() => {
  'use strict';
  // dashboard_v3.js historically owns a status/command renderer named
  // renderEntertainment(). Disable that global binding before DOMContentLoaded;
  // dashboard_lg_remote.js immediately replaces it with the controls-only owner.
  window.__dashboardLgLegacyRendererDisabled = true;
  window.renderEntertainment = function disabledLegacyLgRenderer() {};
})();
