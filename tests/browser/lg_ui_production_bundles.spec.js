const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const remoteBundle = fs.readFileSync(path.join(ROOT, 'frontend/assets/dashboard_lg_remote.js'), 'utf8');
const statusBundle = fs.readFileSync(path.join(ROOT, 'frontend/assets/dashboard_lg_status.js'), 'utf8');

const apiPayloads = {
  '/api/lg-tv/status': {
    tv_ip: '192.168.1.33', online: true, power_state: 'on', connection_state: 'connected',
    paired: true, pairing_required: false, service_active: true,
    current_app: {id: 'youtube.leanback.v4', name: 'YouTube'},
    current_input: {id: 'webos', name: 'App / webOS'},
    audio: {volume: 18, muted: false, sound_output: 'tv_speaker'},
    device: {name: 'Living Room TV', model: 'OLED55', product_name: 'LG webOS TV', software_version: '1.2.3', firmware_version: '4.5.6', webos_version: '8.0'},
    last_update_ts: Math.floor(Date.now()/1000), last_success_ts: Math.floor(Date.now()/1000), data_age_sec: 0,
    key_source: 'secure_file', last_command: null, last_command_success: null,
  },
  '/api/lg-tv/pairing/status': {tv_ip:'192.168.1.33', paired:true, pairing_required:false, connection_status:'connected', service_active:true, key_source:'secure_file', last_connection_success:Math.floor(Date.now()/1000), last_pair_success:Math.floor(Date.now()/1000), last_error:null},
  '/api/lg-tv/pairing/job': {state:'idle'},
  '/api/auth/status': {csrf_token:'test'},
};

test('real production bundles mount one LG owner and remove legacy renderer output', async ({ page }) => {
  await page.setContent(`<!doctype html><html><body>
    <section class="page active" data-page="entertainment"><div class="grid">
      <div class="card span-12"><div class="card-head"><h2>LG TV Controls</h2></div>
        <div id="tvButtons" data-lg-remote-rendered="1"><div class="tv-status-card">legacy</div><div class="tv-command-grid"><button data-tv-command="power_on">old</button></div></div>
      </div>
      <section id="lgPairingCard" class="lg-pairing-card">legacy pairing</section>
    </div></section>
  </body></html>`);
  await page.addInitScript(payloads => {
    window.currentPage = () => 'entertainment';
    window.safeText = value => String(value ?? '');
    window.tv = () => Promise.resolve();
    window.CSS = window.CSS || {escape:value=>String(value)};
    window.fetch = async url => ({ok:true,json:async()=>payloads[new URL(url, location.href).pathname] || {ok:true}});
  }, apiPayloads);
  await page.addScriptTag({content: remoteBundle});
  await page.addScriptTag({content: statusBundle});
  await page.waitForFunction(() => window.dashboardLgDiagnostics && window.dashboardLgDiagnostics().last_refresh);

  await expect(page.locator('#lgTvPage')).toHaveCount(1);
  await expect(page.locator('[data-lg-card="status"]')).toHaveCount(1);
  await expect(page.locator('.lg-tv-pairing-panel')).toHaveCount(1);
  await expect(page.locator('.tv-status-card,[data-tv-command],#lgPairingCard,#lgPairingCardV9,#lgLiveShell')).toHaveCount(0);
  await expect(page.locator('#lgTvVolume')).toHaveText('18');
  await expect(page.locator('#lgTvDeviceName')).toHaveText('Living Room TV');
  const diagnostics = await page.evaluate(() => window.dashboardLgDiagnostics());
  expect(diagnostics).toMatchObject({active_mounts:1,active_pollers:1,legacy_renderer_detected:false,duplicate_cards:0});
});
