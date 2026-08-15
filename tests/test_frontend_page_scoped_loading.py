from pathlib import Path

from tests.frontend_runtime import page_scoped_loading_behavior


ROOT = Path(__file__).resolve().parents[1]
V3 = (ROOT / "frontend/assets/dashboard_v3.js").read_text()
ELECTRICITY = (ROOT / "frontend/assets/dashboard_electricity.js").read_text()
TOPOLOGY = (ROOT / "frontend/assets/dashboard_topology.js").read_text()
SETTINGS = (ROOT / "frontend/assets/dashboard_settings.js").read_text()
POLISH = (ROOT / "frontend/assets/dashboard_polish10.js").read_text()


def _core_page_source(page: str) -> str:
    marker = f"{page}:["
    return V3.split(marker, 1)[1].split("],", 1)[0]


def test_home_initial_load_is_scoped_and_history_details_are_lazy():
    overview = _core_page_source("overview")
    assert "loadStatus" in overview
    assert "loadAir" in overview
    assert "loadHealth" in overview
    assert "loadCameras" in overview
    for hidden_page_loader in (
        "loadHistory", "loadSonoff", "loadLights", "loadScenes",
        "loadSystem", "loadClimateDevices", "loadZones", "loadAutomations",
    ):
        assert hidden_page_loader not in overview
    assert "homeDetails?.addEventListener('toggle'" in V3
    assert "homeDetailsHistoryPromise = loadHistory()" in V3


def test_shell_renders_before_startup_data_and_refresh_is_single_flight():
    startup = V3.split("document.addEventListener('DOMContentLoaded'", 1)[1]
    assert startup.index("renderPage(currentPage())") < startup.index(
        "await refresh({page:currentPage(), reason:'startup'})"
    )
    assert "if (refreshInFlight)" in V3
    assert "queuedRefreshPage" in V3
    assert "if (!document.hidden) refresh({page:currentPage(), reason:'poll'})" in V3


def test_heavy_sections_register_for_only_their_own_pages():
    assert "'electricity', ['overview', 'electricity', 'history']" in ELECTRICITY
    assert "'topology', ['topology']" in TOPOLOGY
    assert "'settings', ['settings']" in SETTINGS
    assert "'polish-details', ['electricity','history','settings','system','more']" in POLISH
    for source in (ELECTRICITY, TOPOLOGY, SETTINGS):
        assert "window.refresh =" not in source
    command_fixes = (ROOT / "frontend/assets/dashboard_command_fixes.js").read_text()
    assert "document.write" not in command_fixes
    assert 'src="/assets/dashboard_topology.js"' not in command_fixes


def test_hidden_page_modules_do_not_eager_load_on_startup():
    expectations = {
        "frontend/assets/dashboard_automation.js": "'automation-editor', ['automation']",
        "frontend/assets/dashboard_automation_triggers.js": "'automation-runtime', ['automation']",
        "frontend/assets/dashboard_tplink_provider.js": "'tplink-provider', ['system']",
        "frontend/assets/dashboard_tariff_sync.js": "'tariff-sync', ['settings']",
        "frontend/assets/dashboard_mea_tariff.js": "'mea-tariff', ['settings']",
        "frontend/assets/dashboard_lg_status.js": "'lg-status', ['entertainment']",
        "frontend/assets/dashboard_cameras.js": "'camera-page', ['camera']",
    }
    for relative, registration in expectations.items():
        source = (ROOT / relative).read_text()
        assert registration in source


def test_device_health_polling_stops_outside_system_page():
    source = (ROOT / "frontend/assets/dashboard_device_health.js").read_text()
    assert "'device-health', ['system']" in source
    assert "if (event.detail?.page === 'system') start();" in source
    assert "else stop();" in source
    assert "window.clearInterval(state.timer)" in source


def test_runtime_home_refresh_is_deduplicated_and_settings_are_deferred():
    result = page_scoped_loading_behavior()
    assert result["homeCallCount"] == 4
    assert sorted(result["homeCalls"]) == sorted([
        "/api/condo/status",
        "/api/air-quality",
        "/api/health",
        "/api/camera-control/devices",
    ])
    assert result["settingsCalls"] == ["settings-probe"]
    assert result["diagnostics"]["refresh_in_flight"] is False
