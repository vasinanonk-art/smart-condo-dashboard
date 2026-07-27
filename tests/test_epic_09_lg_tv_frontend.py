from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "frontend/assets/dashboard_lg_status.js").read_text()
REMOTE = (ROOT / "frontend/assets/dashboard_lg_remote.js").read_text()
CSS = (
    (ROOT / "frontend/assets/dashboard_lg_status.css").read_text()
    + (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()
)
INDEX = (ROOT / "frontend/index.html").read_text()


def test_compact_live_status_and_stable_mount():
    for item in ("lgTvStatusBadge", "lgTvSource", "lgTvVolume", "lgTvUpdated"):
        assert item in UI
    assert "function mount()" in UI
    assert "household-lg-card" in UI
    assert "MutationObserver" not in UI
    assert "TV IP" not in UI and "client key" not in UI.lower()


def test_background_polling_is_single_and_bounded():
    assert "state.timer = setTimeout(refresh, 15000)" in UI
    assert "setInterval" not in UI
    assert "if (state.busy) return" in UI


def test_pairing_actions_are_inside_compact_details():
    assert "UI.deviceDetails" in UI
    for path in ("test", "request", "save", "cancel", "forget"):
        assert f"/api/lg-tv/pairing/{path}" in UI
    assert "confirm('Forget the saved LG TV pairing key?')" in UI


def test_command_uses_post_response_without_duplicate_get():
    command_handler = UI[UI.index("window.tv = async"):]
    assert "/api/lg-tv/command" in command_handler
    assert "state.status = output.state" in command_handler
    assert "/api/lg-tv/status/refresh" not in command_handler


def test_audio_and_source_rendering_are_null_safe():
    assert "const audio = value.audio || {}" in UI
    assert "source.get" not in UI
    assert "value.current_input" in UI and "value.current_app" in UI
    assert "volumeValue == null ? 'Unavailable'" in UI


def test_responsive_and_accessible():
    assert "@media (max-width: 760px)" in CSS
    assert "@media (max-width: 390px)" in CSS
    assert 'aria-live="polite"' in UI
    assert "UI.stateQualityBadge" in UI


def test_remote_has_supported_commands_without_hardcoded_inputs_or_apps():
    assert INDEX.index("dashboard_lg_remote.js") < INDEX.index("dashboard_lg_status.js")
    for command in ("power_on", "power_off", "volume_up", "set_input", "launch_app"):
        assert command in REMOTE
    for unsupported_assumption in ("hdmi1", "hdmi2", "com.webos.app", "youtube.leanback"):
        assert unsupported_assumption not in REMOTE.lower()


def test_live_applications_render_at_most_six_common_installed_buttons():
    assert "const commonApplications" in REMOTE
    assert ".slice(0, 6)" in REMOTE
    assert "items.find(candidate => pattern.test" in REMOTE
    assert 'data-lg-value="${escape(item.id)}"' in REMOTE
    assert "window.tv(element.dataset.lgCommand, element.dataset.lgValue)" in REMOTE
    assert "renderApplications(" in REMOTE
    assert 'data-lg-option="launch_app"' not in REMOTE


def test_inputs_are_separate_live_inventory_and_switch_immediately():
    assert "const allowedInputs" not in REMOTE
    assert "items.filter(item => item && item.id && item.label)" in REMOTE
    assert "household-lg-input-grid" in REMOTE
    assert "button('set_input', item.label" in REMOTE
    assert "<select" not in REMOTE
    assert "data-lg-option" not in REMOTE


def test_navigation_is_a_dpad_and_media_controls_are_complete():
    css = (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()
    assert 'class="household-lg-nav-empty"' in REMOTE
    for command in ("up", "left", "ok", "right", "down", "back", "home"):
        assert f"'household-lg-nav-{command}'" in REMOTE
    for command in ("play", "pause", "stop", "rewind", "fast_forward"):
        assert command in REMOTE


def test_compact_desktop_grid_matches_control_group_order():
    css = (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()
    assert '"power navigation playback"' in css
    assert '"volume applications applications"' in css
    assert '"inputs inputs inputs"' in css
    for class_name in (
        "household-lg-power", "household-lg-navigation-section",
        "household-lg-playback", "household-lg-volume",
        "household-lg-applications", "household-lg-inputs",
    ):
        assert class_name in REMOTE
        assert f".{class_name}" in css


def test_volume_slider_is_debounced_without_set_button():
    assert "data-lg-set-volume" not in REMOTE
    assert "label:'Set'" not in REMOTE
    assert "queuedVolume = Number(slider.value)" in REMOTE
    assert "clearTimeout(volumeTimer)" in REMOTE
    assert "setTimeout(sendQueuedVolume, 400)" in REMOTE
    assert "if (volumeSending || queuedVolume === null) return" in REMOTE
    assert "volumeDragging = true" in REMOTE
    assert "if (!volumeDragging) scheduleVolume()" in REMOTE


def test_dpad_keeps_directions_around_ok_and_back_home_in_footer():
    css = (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()
    navigation = REMOTE[REMOTE.index('class="household-lg-nav-empty"'):]
    expected_order = (
        'household-lg-nav-empty',
        'household-lg-nav-up',
        'household-lg-nav-empty',
        'household-lg-nav-left',
        'household-lg-nav-ok',
        'household-lg-nav-right',
        'household-lg-nav-down',
        'household-lg-nav-empty',
        'household-lg-navigation-footer',
        'household-lg-nav-back',
        'household-lg-nav-home',
    )
    offsets = []
    cursor = 0
    for class_name in expected_order:
        cursor = navigation.index(class_name, cursor)
        offsets.append(cursor)
        cursor += len(class_name)
    assert offsets == sorted(offsets)
    assert "grid-template-columns: repeat(3, 48px)" in css
    assert "grid-template-rows: repeat(3, 40px)" in css
    for forbidden in ("position: absolute", "transform:", "translate", "float:", "margin: -"):
        assert forbidden not in css


def test_compact_groups_have_required_column_contracts():
    css = (ROOT / "frontend/assets/dashboard_lg_remote.css").read_text()
    for selector in (".household-lg-playback-grid", ".household-lg-app-grid", ".household-lg-volume-grid"):
        block = css[css.rindex(selector):]
        block = block[:block.index("}")]
        assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in block
    assert ".slice(0, 6)" in REMOTE
    assert "<select" not in REMOTE


def test_visual_regression_covers_required_desktop_viewports():
    spec = (ROOT / "tests/browser/lg_remote_layout.spec.js").read_text()
    for dimensions in ("1920, height: 1080", "1440, height: 900", "1366, height: 768"):
        assert dimensions in spec
