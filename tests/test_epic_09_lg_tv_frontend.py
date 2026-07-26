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
    for item in ("lgTvStatus", "lgTvSource", "lgTvVolume", "lgTvUpdated"):
        assert item in UI
    assert "function mount()" in UI
    assert "lg-tv-compact-card" in UI
    assert "MutationObserver" not in UI
    assert "TV IP" not in UI and "client key" not in UI.lower()


def test_background_polling_is_single_and_bounded():
    assert "state.timer = setTimeout(refresh, 15000)" in UI
    assert "setInterval" not in UI
    assert "if (state.busy) return" in UI


def test_pairing_actions_are_inside_compact_details():
    assert 'class="lg-tv-details"' in UI
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
    assert "@media(max-width:760px)" in CSS
    assert "@media(max-width:520px)" in CSS
    assert 'aria-live="polite"' in UI
    assert 'role="status"' in UI


def test_remote_has_supported_commands_without_hardcoded_inputs_or_apps():
    assert INDEX.index("dashboard_lg_remote.js") < INDEX.index("dashboard_lg_status.js")
    for command in ("power_on", "power_off", "volume_up", "set_input", "launch_app"):
        assert command in REMOTE
    for unsupported_assumption in ("hdmi1", "hdmi2", "netflix", "youtube"):
        assert unsupported_assumption not in REMOTE.lower()
