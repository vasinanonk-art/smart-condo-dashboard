import sys
import time
from pathlib import Path
from types import SimpleNamespace

from backend import lg_tv_control as control


def _reset_inventory():
    with control.ENUMERATION_LOCK:
        control.ENUMERATION_RAW.update(input={}, app={})
        control._INVENTORY.update(
            inputs=[],
            applications=[],
            inputs_available=False,
            applications_available=False,
            last_success_at=None,
            last_attempt_at=None,
            refreshing=False,
            last_error=None,
        )


def test_normal_commands_reuse_one_connection_and_never_refresh(monkeypatch):
    control._discard_persistent_client()
    opened = []
    fake = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control, "_open_client", lambda key: opened.append(key) or fake)
    monkeypatch.setattr(control, "_execute", lambda *args: None)
    monkeypatch.setattr(control, "_refresh", lambda key: (_ for _ in ()).throw(AssertionError("status refresh")))
    monkeypatch.setattr(control.status, "_public_status", lambda: {"online": True})

    first = control.lg_tv_command(control.TvCommand(command="volume_up"))
    second = control.lg_tv_command(control.TvCommand(command="play"))

    assert first["ok"] is True and second["ok"] is True
    assert first["state_refreshed"] is False
    assert second["connection_reused"] is True
    assert opened == ["key"]
    control._discard_persistent_client()


def test_cached_inventory_is_returned_while_background_refresh_is_scheduled(monkeypatch):
    _reset_inventory()
    now = time.time()
    with control.ENUMERATION_LOCK:
        control._INVENTORY.update(
            inputs=[{"id": "input-safe", "label": "HDMI 1"}],
            applications=[{"id": "app-safe", "label": "YouTube"}],
            inputs_available=True,
            applications_available=True,
            last_success_at=now - control.INVENTORY_TTL_SEC - 1,
        )
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    scheduled = []
    background = SimpleNamespace(add_task=lambda task: scheduled.append(task))

    started = time.monotonic()
    payload = control.lg_tv_capabilities(background)

    assert time.monotonic() - started < 0.2
    assert payload["inputs"][0]["label"] == "HDMI 1"
    assert payload["applications"][0]["label"] == "YouTube"
    assert payload["inventory_refreshing"] is True
    assert scheduled == [control._refresh_inventory]
    with control.ENUMERATION_LOCK:
        control._INVENTORY["refreshing"] = False


def test_valid_inventory_survives_temporary_enumeration_failure(monkeypatch):
    _reset_inventory()
    with control.ENUMERATION_LOCK:
        control._INVENTORY.update(
            inputs=[{"id": "input-old", "label": "HDMI 1"}],
            applications=[{"id": "app-old", "label": "Netflix"}],
            inputs_available=True,
            applications_available=True,
            refreshing=True,
        )
    fake = SimpleNamespace(close=lambda: None)

    class SourceControl:
        def __init__(self, client):
            pass

        def list_sources(self, timeout):
            raise TimeoutError()

    class ApplicationControl:
        def __init__(self, client):
            pass

        def list_apps(self, timeout):
            raise TimeoutError()

    monkeypatch.setitem(
        sys.modules,
        "pywebostv.controls",
        SimpleNamespace(SourceControl=SourceControl, ApplicationControl=ApplicationControl),
    )
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control, "_open_client", lambda key: fake)

    control._refresh_inventory()
    payload = control.capabilities()

    assert payload["inputs"] == [{"id": "input-old", "label": "HDMI 1"}]
    assert payload["applications"] == [{"id": "app-old", "label": "Netflix"}]
    assert payload["enumeration_available"] is True


def test_app_and_input_commands_use_cached_raw_objects_without_reenumeration(monkeypatch):
    source = SimpleNamespace(data={"id": "hdmi-1", "label": "HDMI 1"})
    app = SimpleNamespace(data={"id": "youtube", "title": "YouTube"})
    with control.ENUMERATION_LOCK:
        control.ENUMERATION_RAW["input"] = {"input-safe": source}
        control.ENUMERATION_RAW["app"] = {"app-safe": app}
    calls = []

    class SourceControl:
        def __init__(self, client):
            pass

        def list_sources(self, timeout):
            raise AssertionError("pre-command enumeration")

        def set_source(self, selected, timeout):
            calls.append(("input", selected))

    class ApplicationControl:
        def __init__(self, client):
            pass

        def list_apps(self, timeout):
            raise AssertionError("pre-command enumeration")

        def launch(self, selected, timeout):
            calls.append(("app", selected))

    monkeypatch.setitem(
        sys.modules,
        "pywebostv.controls",
        SimpleNamespace(
            SourceControl=SourceControl,
            ApplicationControl=ApplicationControl,
            MediaControl=object,
            SystemControl=object,
        ),
    )
    control._execute(object(), "set_input", "input-safe")
    control._execute(object(), "launch_app", "app-safe")
    assert calls == [("input", source), ("app", app)]


def test_frontend_debounces_duplicate_submissions_and_only_disables_clicked_control():
    root = Path(__file__).resolve().parents[1]
    status_source = (root / "frontend/assets/dashboard_lg_status.js").read_text()
    remote_source = (root / "frontend/assets/dashboard_lg_remote.js").read_text()

    assert "state.pendingCommands.has(requestKey)" in status_source
    assert "duplicate_ignored:true" in status_source
    assert "element.dataset.lgPending === 'true'" in remote_source
    assert "element.disabled = true" in remote_source
    assert "host.disabled" not in remote_source


def test_wol_path_remains_isolated_from_persistent_command_connection():
    source = Path(control.__file__).read_text()
    power_path = source[source.index('if command == "power_on":'):source.index("if command not in DIRECT_COMMANDS:")]
    normal_path = source[source.index("if command not in DIRECT_COMMANDS:"):]

    assert "_reconnect_after_wol" in power_path
    assert "COMMAND_LOCK.acquire(blocking=False)" in power_path
    assert "_reconnect_after_wol" not in normal_path
    assert "_acquire_persistent_client" in normal_path
    assert "_refresh(key)" not in normal_path
