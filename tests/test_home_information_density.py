from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "frontend/assets/dashboard_home.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/assets/dashboard_home.css").read_text(encoding="utf-8")
UI = (ROOT / "frontend/assets/dashboard_design_system.js").read_text(encoding="utf-8")
HTML = (ROOT / "frontend/index.html").read_text(encoding="utf-8")


def test_home_status_cards_use_dependency_free_inline_svg_icons():
    for name in ("zap", "thermometer", "wifi", "shield-check"):
        assert f"iconName:'{name}'" in HOME
        assert f"{name}:" in UI or f"'{name}':" in UI
    assert '<svg class="sc-icon"' in UI
    assert 'fill="none" stroke="currentColor"' in UI
    assert "https://" not in UI
    assert "unpkg" not in HTML and "cdnjs" not in HTML
    assert 'data-status="${safe(status || \'neutral\')}"' in UI
    assert '.sc-metric-card[data-status="neutral"] .sc-metric-icon' in CSS


def test_quick_actions_are_room_specific_and_preserve_exact_bedroom_contract():
    assert '<h3 id="homeBedroomAcActions">Bedroom AC</h3>' in HOME
    assert "Bedroom AC On" in HOME
    assert "Bedroom AC Off" in HOME
    assert "Bedroom AC 26°C" in HOME
    assert "button.dataset.householdIrDevice = 'bed-room-air-conditioner'" in HOME
    assert "{command:button.dataset.command}" in HOME
    assert "{capability:'temperature', value:26}" in HOME
    assert "Living Room AC" not in HOME
    assert "{label:'AC On'" not in HOME
    assert "{label:'AC Off'" not in HOME
    assert "{label:'AC 26°'" not in HOME


def test_energy_summary_uses_existing_authoritative_history_contract():
    for label in ("Current Power", "24h Usage", "Estimated Cost", "Peak (30m Avg)"):
        assert f"<span>{label}</span>" in HOME
    for unit in (" W", " kWh", " THB"):
        assert unit in HOME
    assert "electricity.status?.power" in HOME
    assert "summary.total_energy_kwh" in HOME
    assert "summary.total_cost_thb" in HOME
    assert "row?.energy_kwh" in HOME
    assert "row?.interval_start" in HOME and "row?.interval_end" in HOME
    assert "30-minute average" in HOME
    assert "Last 24 hours" in HOME
    assert "30-minute intervals" in HOME


def test_energy_chart_is_compact_readable_and_responsive():
    assert "home-energy-grid" in HOME
    assert "home-energy-axis-y" in HOME
    assert "home-energy-axis-x" in HOME
    assert "'Now'" in HOME
    assert "height: clamp(180px, 16vw, 220px)" in CSS
    assert "height: clamp(160px, 44vw, 180px)" in CSS
    assert "grid-template-columns: repeat(4, minmax(0, 1fr))" in CSS
    assert CSS.count("grid-template-columns: repeat(2, minmax(0, 1fr))") >= 2


def test_home_change_does_not_add_presence_or_navigation_ui():
    assert "Presence Automation" not in HOME
    assert "household_state" not in HOME
    assert "PRESENCE_AUTOMATION_MODE" not in HOME
    assert 'id="overviewPresence" hidden' in HTML
    assert "sc-bottom-navigation" not in HOME


def test_mobile_actions_preserve_tap_targets_and_no_horizontal_layout():
    assert "min-height: 64px" in CSS
    assert ".home-quick-action-row" in CSS
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in CSS
    assert "min-width: 0" in CSS
    assert "overflow-x: scroll" not in CSS
