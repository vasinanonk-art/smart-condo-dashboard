"""Source-level regression checks for the UX clarity milestone."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
HOME = (ROOT / "frontend/assets/dashboard_home.js").read_text(encoding="utf-8")
CAMERAS = (ROOT / "frontend/assets/dashboard_cameras.js").read_text(encoding="utf-8")
ELECTRICITY = (ROOT / "frontend/assets/dashboard_electricity.js").read_text(encoding="utf-8")
ELECTRICITY_CSS = (ROOT / "frontend/assets/dashboard_electricity.css").read_text(encoding="utf-8")
SYSTEM = (ROOT / "frontend/assets/dashboard_v3.js").read_text(encoding="utf-8")
HOME_CSS = (ROOT / "frontend/assets/dashboard_home.css").read_text(encoding="utf-8")
CAMERA_CSS = (ROOT / "frontend/assets/dashboard_cameras.css").read_text(encoding="utf-8")
CHROME = (ROOT / "frontend/assets/dashboard_page_chrome.js").read_text(encoding="utf-8")


def test_home_action_required_and_healthy_state_exist_before_details():
    assert 'id="homeActionRequired"' in INDEX
    assert "function actionRequired(state)" in HOME
    assert "All systems look healthy" in HOME
    assert "Action Required" in HOME
    assert INDEX.index('id="homeActionRequired"') < INDEX.index('id="overviewMetrics"')
    assert '<details class="home-details">' in INDEX


def test_camera_view_uses_existing_api_and_capability_gates_actions():
    assert 'data-page="camera"' in CAMERAS
    assert "camera-control/devices" in CAMERAS
    assert "Online" in CAMERAS and "Attention" in CAMERAS and "Offline" in CAMERAS and "Unavailable" in CAMERAS
    assert "capabilities.snapshot" in CAMERAS
    assert "capabilities.live_stream" in CAMERAS
    assert "capabilities.ptz_move && capabilities.ptz_stop" in CAMERAS
    assert "data-camera-command=\"stop_ptz\"" in CAMERAS
    assert "duration:0.5" in CAMERAS
    assert "camera-advanced" in CAMERAS
    assert "dashboard_cameras.js" in INDEX and "dashboard_cameras.css" in INDEX


def test_camera_polling_preserves_loaded_snapshot_when_status_is_unchanged():
    assert "function stableSignature(available)" in CAMERAS
    assert "signature === renderedSignature" in CAMERAS
    assert "host.querySelector('.camera-grid,.camera-empty')" in CAMERAS
    assert ".camera-ptz-grid" in CAMERA_CSS
    assert "camera-primary-layout" in CAMERAS
    assert "grid-template-columns:minmax(0,680px) minmax(240px,300px)" in CAMERA_CSS
    assert "max-width:1040px" in CAMERA_CSS


def test_camera_placeholder_and_wide_layout_are_compact():
    assert 'class="camera-snapshot-frame"' in CAMERAS
    assert 'class="camera-snapshot-placeholder"' in CAMERAS
    assert "image.naturalWidth / image.naturalHeight" in CAMERAS
    assert "ratio < 1.2 || ratio > 2.4" in CAMERAS
    assert ".camera-snapshot-frame.is-placeholder .camera-latest-snapshot{display:none}" in CAMERA_CSS
    assert "max-width:680px" in CAMERA_CSS
    assert "max-width:260px" in CAMERA_CSS
    assert "camera-page-active" in CAMERAS
    assert ".sc-dashboard-shell.camera-page-active>.sc-bottom-navigation-container{display:none}" in CAMERA_CSS
    assert ".camera-page-head{display:flex;align-items:end;justify-content:space-between;width:100%;max-width:1040px;margin:0 auto" in CAMERA_CSS
    assert "justify-items:center" in CAMERA_CSS
    assert 'dashboard_cameras.css?v=__ASSET_VERSION__' in INDEX
    assert 'dashboard_cameras.js?v=__ASSET_VERSION__' in INDEX


def test_camera_interactions_refresh_inline_and_release_live_media():
    assert "const SNAPSHOT_REFRESH_MS = 10000" in CAMERAS
    assert "async function refreshSnapshot(image, announce = false)" in CAMERAS
    assert "URL.createObjectURL(blob)" in CAMERAS
    assert "if (document.hidden || window.currentPage?.() !== 'camera' || document.querySelector('.camera-live-dialog[open]')) return" in CAMERAS
    assert "function openLiveView(camera, identifier)" in CAMERAS
    assert 'class="camera-ptz-grid camera-live-ptz"' in CAMERAS
    assert "dialog.querySelectorAll('[data-camera-ptz]')" in CAMERAS
    assert "button.closest('.camera-card,.camera-live-dialog')" in CAMERAS
    assert "document.querySelector('.camera-live-dialog[open]')" in CAMERAS
    assert "{command:'move', direction:button.dataset.cameraDirection, duration:0.5}" in CAMERAS
    assert "if (!stopping && !document.querySelector('.camera-live-dialog[open]'))" in CAMERAS
    assert "video.canPlayType('application/vnd.apple.mpegurl')" in CAMERAS
    assert "`/api/camera-control/${identifier}/live.m3u8?refresh=${Date.now()}`" in CAMERAS
    assert "video.removeAttribute('src')" in CAMERAS
    assert "video.load()" in CAMERAS
    assert "window.open(`/api/camera-control/" not in CAMERAS
    assert ".camera-live-dialog" in CAMERA_CSS


def test_electricity_cycle_chart_is_primary_and_daily_power_integration_is_explicit():
    assert "Daily Energy Usage — Current Billing Cycle" in ELECTRICITY
    assert "bucket: 'day'" in ELECTRICITY
    assert "Today is partial" in ELECTRICITY
    assert "Cycle avg ${average.toFixed(2)} kWh/day" in ELECTRICITY
    assert "cycle-chart-average-line" in ELECTRICITY
    assert "Cycle average" in ELECTRICITY
    assert ELECTRICITY.index("${summaryCards()}") < ELECTRICITY.index("${cycleDailyChart()}") < ELECTRICITY.index("${dailySummaryCards()}")
    assert "power integration" in ELECTRICITY


def test_electricity_diagnostics_and_tariff_details_are_collapsed():
    assert '<details class="electricity-diagnostics"' in ELECTRICITY
    assert '<details class="electricity-cost-card electricity-collapsible"' in ELECTRICITY
    assert "electricity-history-coverage-details" in ELECTRICITY
    assert ".electricity-collapsible>summary" in ELECTRICITY_CSS


def test_system_action_first_and_technical_details_collapsed():
    assert "Overall Health" in SYSTEM
    assert "Action Required" in SYSTEM
    assert "Recent Incidents" in SYSTEM
    assert "Services" in SYSTEM
    assert "Storage / Backup" in SYSTEM
    assert "Advanced technical details" in SYSTEM
    assert "${actionSection}${incidentSection}<section class=\"system-service-summary\"" in SYSTEM
    assert "system-advanced" in INDEX


def test_dangerous_actions_are_visually_distinct_and_confirmed():
    assert "dangerous-action" in INDEX
    assert "Turn all Sonoff switches on?" in INDEX
    assert "Turn all Sonoff switches off?" in INDEX
    assert "Run maintenance now?" in (ROOT / "frontend/assets/dashboard_settings.js").read_text(encoding="utf-8")
    assert "Import legitimate electricity history now?" in (ROOT / "frontend/assets/dashboard_settings.js").read_text(encoding="utf-8")
    assert "Delete this automation rule?" in (ROOT / "frontend/assets/dashboard_automation.js").read_text(encoding="utf-8")


def test_mobile_layout_keeps_new_sections_single_column():
    assert "@media(max-width:640px)" in HOME_CSS
    assert ".home-action-state" in HOME_CSS
    assert "@media(max-width:720px)" in CAMERA_CSS
    assert "grid-template-columns:1fr" in CAMERA_CSS
    assert "overflow-x:hidden" in ELECTRICITY_CSS


def test_home_energy_period_is_explicit_without_changing_aggregation():
    assert "Last 24 hours" in HOME
    assert "30-minute intervals" in HOME
    assert "Recent interval consumption" not in HOME


def test_cameras_use_one_page_title_and_compact_unavailable_cards():
    assert "title: 'Cameras'" in CHROME
    assert "<h2>Cameras</h2>" not in CAMERAS
    assert "is-unavailable" in CAMERAS
    assert ".camera-card.is-unavailable" in CAMERA_CSS
    assert "camera-snapshot-unavailable" in CAMERAS


def test_system_hides_noop_sections_and_uses_neutral_backup_status():
    assert "const actionSection = required.length" in SYSTEM
    assert "const incidentSection = incidents.length" in SYSTEM
    assert "Not monitored" in SYSTEM
    assert "system-overall" in SYSTEM
