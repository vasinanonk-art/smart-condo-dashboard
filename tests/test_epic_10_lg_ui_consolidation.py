"""EPIC 10 source-level regression contracts for the single LG TV frontend owner."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
UI = (ROOT / "frontend" / "assets" / "dashboard_lg_status.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "assets" / "dashboard_lg_status.css").read_text(encoding="utf-8")


def test_only_one_lg_ui_bundle_is_loaded():
    assert INDEX.count("dashboard_lg_status.js") == 1
    assert INDEX.count("dashboard_lg_status.css") == 1
    assert "dashboard_lg_pairing.js" not in INDEX
    assert "dashboard_lg_pairing.css" not in INDEX


def test_legacy_pairing_assets_are_deleted():
    assert not (ROOT / "frontend" / "assets" / "dashboard_lg_pairing.js").exists()
    assert not (ROOT / "frontend" / "assets" / "dashboard_lg_pairing.css").exists()


def test_single_mount_without_mutation_observer_or_poll_remount():
    assert UI.count("function mountLgTvPage()") == 1
    assert "MutationObserver" not in UI
    assert "outerHTML" not in UI
    assert "insertAdjacentHTML" not in UI
    assert "host.innerHTML" not in UI
    assert "state.mounted" in UI


def test_only_one_timer_owns_status_and_pairing_refresh():
    assert "pollTimer" in UI
    assert "pairTimer" not in UI
    assert "setInterval" not in UI
    assert UI.count("setTimeout(() => refreshAll(true)") == 1
    assert "['connecting', 'prompted']" in UI


def test_exactly_one_set_of_primary_status_cards():
    for card in ("status", "app", "input", "volume", "mute", "updated"):
        assert UI.count(f'data-lg-card="{card}"') == 1
    assert UI.count('class="lg-tv-pairing-panel"') == 1


def test_full_telemetry_fields_are_rendered():
    for field in (
        "lgTvVolume", "lgTvMute", "lgTvSoundOutput", "lgTvDeviceName",
        "lgTvModel", "lgTvProduct", "lgTvSoftware", "lgTvFirmware", "lgTvWebos",
    ):
        assert field in UI
    assert "audio?.sound_output" in UI
    assert "device?.product_name" in UI
    assert "device?.software_version" in UI
    assert "device?.firmware_version" in UI
    assert "device?.webos_version" in UI


def test_friendly_app_mapping_and_unknown_id_fallback():
    for label in ("Home", "Netflix", "YouTube", "Prime Video", "Disney+", "Browser", "HDMI"):
        assert label in UI
    assert "return mappings.find" in UI
    assert "|| id" in UI


def test_diagnostics_contract_and_duplicate_detection():
    assert "window.dashboardLgDiagnostics" in UI
    for field in (
        "active_mounts", "active_pollers", "legacy_renderer_detected",
        "duplicate_cards", "css_version", "last_refresh", "status_age",
    ):
        assert field in UI


def test_responsive_css_grid_without_absolute_or_fixed_widths():
    assert "display:grid" in CSS
    assert "grid-template-columns" in CSS
    assert "@media(max-width:1100px)" in CSS
    assert "@media(max-width:760px)" in CSS
    assert "@media(max-width:480px)" in CSS
    assert "position:absolute" not in CSS.replace(" ", "")
    assert "width:300px" not in CSS.replace(" ", "")


def test_remote_layout_is_preserved_and_not_rebuilt_by_consolidated_ui():
    assert "dashboard_lg_remote.js" in INDEX
    assert "tvButtons" in INDEX
    assert "remoteCard.classList.add" in UI
    assert "remote.innerHTML" not in UI
