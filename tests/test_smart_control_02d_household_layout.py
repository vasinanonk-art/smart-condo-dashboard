import re
from pathlib import Path


ROOT = Path(__file__).parents[1]
CSS = (ROOT / "frontend/assets/dashboard_household_devices.css").read_text()
JS = (ROOT / "frontend/assets/dashboard_household_devices.js").read_text()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_household_grid_owns_responsive_columns():
    compact = _compact(CSS)
    assert "grid-template-columns:repeat(auto-fit,minmax(320px,1fr))" in compact
    assert "@media(max-width:1100px)" in compact
    assert "grid-template-columns:repeat(2,minmax(320px,1fr))" in compact
    assert "@media(max-width:990px)" in compact
    assert "grid-template-columns:minmax(320px,1fr)" in compact


def test_every_device_card_has_required_intrinsic_layout():
    compact = _compact(CSS)
    card = compact.split(".household-device-card{", 1)[1].split("}", 1)[0]
    for declaration in (
        "min-width:320px",
        "display:flex",
        "flex-direction:column",
        "gap:16px",
        "height:auto",
    ):
        assert declaration in card


def test_controls_are_two_column_equal_height_grid():
    compact = _compact(CSS)
    controls = compact.split(".household-controls{", 1)[1].split("}", 1)[0]
    assert "display:grid" in controls
    assert "grid-template-columns:repeat(2,minmax(120px,1fr))" in controls
    button = compact.split(".household-control-button{", 1)[1].split("}", 1)[0]
    assert "min-height:44px" in button
    assert "height:100%" in button


def test_warning_box_is_yellow_and_has_no_fixed_height():
    compact = _compact(CSS)
    warning = compact.split(".household-device-reason{", 1)[1].split("}", 1)[0]
    assert "height:auto" in warning
    assert "rgba(242,184,75" in warning
    assert not re.search(r"(?<!min-)height:\\d", warning)


def test_camera_and_climate_use_same_grid_and_card_contract():
    assert "host.className = 'household-grid'" in JS
    assert JS.count("host.className = 'household-grid'") == 2
    assert 'class="household-device-card household-camera-card"' in JS
    assert "household-camera-grid" not in JS


def test_household_markup_does_not_attach_legacy_layout_classes():
    forbidden = (
        'class="card household-',
        'class="btn ghost"',
        'class="status-pill"',
        "className = 'span-12'",
    )
    assert not any(value in JS for value in forbidden)
    assert "flex-wrap" not in CSS
    assert "inline-block" not in CSS


def test_all_household_styles_are_namespaced():
    without_media = re.sub(r"@media[^{]+\\{", "", CSS)
    selectors = re.findall(r"(?:^|\\})\\s*([^@][^{]+)\\{", without_media)
    for selector_group in selectors:
        for selector in selector_group.split(","):
            assert selector.strip().startswith(".household-"), selector


def test_requested_viewport_widths_have_non_overlapping_column_plan():
    # Available content widths follow the current 250px sidebar/24px main padding,
    # with the existing mobile sidebar breakpoint accounted for.
    expected_columns = {1920: 4, 1440: 3, 1280: 2, 1024: 2, 768: 1}
    for viewport, columns in expected_columns.items():
        available = min(1600, viewport - 250 - 48) - 34
        if viewport <= 760:
            available = viewport - 30 - 34
        if viewport <= 990:
            columns = 1
        required = columns * 320 + (columns - 1) * 16
        assert required <= available, (viewport, columns, required, available)
