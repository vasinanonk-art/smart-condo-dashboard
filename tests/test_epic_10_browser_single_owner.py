from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT / "frontend" / "assets" / "dashboard_lg_remote.js"
STATUS = ROOT / "frontend" / "assets" / "dashboard_lg_status.js"


def test_real_production_bundles_mount_one_lg_ui():
    with playwright.sync_playwright() as api:
        browser = api.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
          <section class="page" data-page="entertainment">
            <div class="grid">
              <div class="card span-12"><div class="card-head"><h2>LG TV Controls</h2></div><div id="tvButtons"></div></div>
            </div>
          </section>
        """)
        page.evaluate("""
          window.safeText = value => String(value ?? '');
          window.currentPage = () => 'entertainment';
          window.tv = () => Promise.resolve({ok:true});
        """)
        page.route("**/api/**", lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"online":true,"paired":true,"connection_state":"connected","connection_status":"connected","service_active":true,"current_app":{"id":"youtube.leanback.v4","name":"YouTube"},"current_input":{"id":"webos","name":"App / webOS"},"audio":{"volume":18,"muted":false,"sound_output":"tv_speaker"},"device":{"name":"Living Room TV","model":"OLED","product_name":"LG webOS TV","software_version":"1.0","firmware_version":"2.0","webos_version":"24"},"last_update_ts":1,"last_success_ts":1,"data_age_sec":0,"key_source":"secure_file","state":"idle"}'
        ))
        page.add_script_tag(path=str(REMOTE))
        page.evaluate("window.renderEntertainment()")
        page.add_script_tag(path=str(STATUS))
        page.wait_for_selector("#lgTvPage")
        page.wait_for_timeout(50)

        assert page.locator("#lgTvPage").count() == 1
        assert page.locator('[data-lg-card="status"]').count() == 1
        assert page.locator(".lg-tv-pairing-panel").count() == 1
        assert page.locator(".lg-remote-status").count() == 0
        assert page.locator("#lgPairingCard, #lgPairingCardV9, #lgLiveShell").count() == 0
        assert page.locator('[data-lg-command="power_on"]').count() == 1

        diagnostics = page.evaluate("window.dashboardLgDiagnostics()")
        assert diagnostics["active_mounts"] == 1
        assert diagnostics["active_pollers"] == 1
        assert diagnostics["duplicate_cards"] == 0
        assert diagnostics["legacy_renderer_detected"] is False
        browser.close()
