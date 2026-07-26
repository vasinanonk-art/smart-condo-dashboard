"""Regression contracts for the compact, single-owner LG frontend."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend/index.html").read_text()
UI = (ROOT / "frontend/assets/dashboard_lg_status.js").read_text()
REMOTE = (ROOT / "frontend/assets/dashboard_lg_remote.js").read_text()
CSS = (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()


def test_only_one_lg_ui_bundle_is_loaded():
    assert INDEX.count("dashboard_lg_status.js") == 1
    assert INDEX.count("dashboard_lg_status.css") == 1
    assert "dashboard_lg_pairing.js" not in INDEX


def test_legacy_pairing_assets_are_deleted():
    assert not (ROOT / "frontend/assets/dashboard_lg_pairing.js").exists()
    assert not (ROOT / "frontend/assets/dashboard_lg_pairing.css").exists()


def test_single_mount_without_observer_or_remount_loop():
    assert UI.count("function mount()") == 1
    assert "MutationObserver" not in UI
    assert "outerHTML" not in UI
    assert "window.renderLgTvCompact" in UI


def test_only_one_timer_owns_status_refresh():
    assert "state.timer" in UI
    assert "setInterval" not in UI
    assert UI.count("setTimeout(refresh, 15000)") == 1


def test_compact_summary_has_no_always_visible_diagnostics():
    for field in ("lgTvStatusBadge", "lgTvSource", "lgTvVolume", "lgTvUpdated"):
        assert field in UI
    for technical in ("lgTvFirmware", "lgTvWebos", "lgTvDeviceName", "TV IP", "key source"):
        assert technical not in UI
    assert "UI.deviceDetails" in UI and "summary:'TV Details'" in UI


def test_live_only_input_and_application_enumeration():
    assert "capabilities.inputs" in REMOTE
    assert "capabilities.applications" in REMOTE
    assert "enumeration_available" in REMOTE
    assert "Live input enumeration is unavailable." in REMOTE
    assert "Live application enumeration is unavailable." in REMOTE


def test_responsive_grid_without_fixed_width():
    assert "display: grid" in CSS
    assert "grid-template-columns:" in CSS
    assert "@media (max-width: 760px)" in CSS
    assert "@media (max-width: 390px)" in CSS
    assert "width:300px" not in CSS.replace(" ", "")


def test_lg_status_is_the_only_remote_render_owner():
    household = (ROOT / "frontend/assets/dashboard_household_devices.js").read_text()
    assert "renderLgTvCompact" not in household
    assert "renderLgCompactRemote" not in household
    assert "window.renderLgCompactRemote?.(state.capabilities || {})" in UI
