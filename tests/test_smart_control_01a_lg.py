import time
import sys
from pathlib import Path
from types import SimpleNamespace

import bcrypt
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import lg_tv_control as control


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "control-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"control-password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "control-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    login = client.post(
        "/api/auth/login",
        json={"username": "control-test", "password": "control-password"},
    )
    assert login.status_code == 200
    return client, login.json()["csrf_token"]


def test_lg_command_requires_authentication_and_csrf(monkeypatch):
    client, csrf = _auth_client(monkeypatch)
    client.cookies.clear()
    assert client.post("/api/lg-tv/command", json={"command": "unsupported"}).status_code == 401
    client, csrf = _auth_client(monkeypatch)
    assert client.post("/api/lg-tv/command", json={"command": "unsupported"}).status_code == 403
    response = client.post(
        "/api/lg-tv/command",
        json={"command": "unsupported"},
        headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"},
    )
    assert response.status_code == 422


def test_successful_command_closes_client_and_returns_refreshed_state(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: True)
    monkeypatch.setattr(control, "_open_client", lambda key: client)
    executed = []
    monkeypatch.setattr(control, "_execute", lambda opened, command, value: executed.append((opened, command, value)))
    monkeypatch.setattr(control, "_refresh", lambda key: ({"online": True, "audio": {"volume": 12}}, True))
    monkeypatch.setattr(control.status, "_public_status", lambda: {"online": True, "audio": {"volume": 12}})

    result = control.lg_tv_command(control.TvCommand(command="volume_up"))

    assert result["ok"] is True
    assert result["state_refreshed"] is True
    assert result["state"]["audio"]["volume"] == 12
    assert executed == [(client, "volume_up", None)]
    assert client.closed is True


def test_timeout_is_bounded_and_client_is_closed(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: True)
    monkeypatch.setattr(control, "_open_client", lambda key: client)
    monkeypatch.setattr(control, "_execute", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    started = time.monotonic()

    result = control.lg_tv_command(control.TvCommand(command="volume_down"))

    assert isinstance(result, JSONResponse)
    assert result.status_code == 504
    assert time.monotonic() - started < 0.5
    assert client.closed is True


def test_library_timeout_message_is_reported_as_timeout(monkeypatch):
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: True)
    monkeypatch.setattr(control, "_open_client", lambda key: (_ for _ in ()).throw(Exception("Timeout.")))

    result = control.lg_tv_command(control.TvCommand(command="mute"))

    assert result.status_code == 504


def test_unreachable_and_unsupported_are_explicit(monkeypatch):
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "test"))
    monkeypatch.setattr(control.status, "_reachable", lambda *args, **kwargs: False)
    unreachable = control.lg_tv_command(control.TvCommand(command="mute"))
    unsupported = control.lg_tv_command(control.TvCommand(command="teleport"))

    assert unreachable.status_code == 503
    assert unsupported.status_code == 422


def test_power_on_is_reported_unsupported_without_mac(monkeypatch):
    monkeypatch.setattr(control, "TV_MAC", "")
    result = control.lg_tv_command(control.TvCommand(command="power_on"))
    assert result.status_code == 422
    assert control.capabilities()["power_on"]["reason"] == "mac_not_configured"


def test_frontend_consumes_post_state_without_duplicate_refresh():
    source = (Path(control.app_module.FRONTEND_DIR) / "assets" / "dashboard_lg_status.js").read_text(encoding="utf-8")
    command_handler = source[source.index("window.tv = async"):]
    assert "/api/lg-tv/command" in command_handler
    assert "state.status = output.state" in command_handler
    assert "/api/lg-tv/status/refresh" not in command_handler


def test_navigation_cursor_commands_use_pointer_socket_and_close_it(monkeypatch):
    calls = []

    class Pointer:
        def __init__(self):
            self.sock = SimpleNamespace(settimeout=lambda timeout: calls.append(("timeout", timeout)))
            self.closed = False
            self._th = None

        def connect(self):
            calls.append("connect")

        def close(self):
            self.closed = True
            calls.append("close")

    pointers = []

    class InputControl:
        ws_class = lambda self, path: pointers.append(Pointer()) or pointers[-1]

        def __init__(self, client):
            self.client = client

        def request(self, *args, **kwargs):
            return {"payload": {"socketPath": "wss://pointer"}}

    for command_name in ("up", "down", "left", "right", "ok", "back", "home"):
        setattr(InputControl, command_name, lambda self, name=command_name: calls.append(name))

    monkeypatch.setitem(sys.modules, "pywebostv.controls", SimpleNamespace(InputControl=InputControl))
    for command in ("up", "down", "left", "right", "ok", "back", "home"):
        control._pointer_command(object(), command)
    assert [value for value in calls if value in {"up", "down", "left", "right", "ok", "back", "home"}] == [
        "up", "down", "left", "right", "ok", "back", "home",
    ]
    assert len(pointers) == 7
    assert all(pointer.closed for pointer in pointers)


def test_live_application_and_input_enumeration_populates_capabilities(monkeypatch):
    client = FakeClient()
    sources = [SimpleNamespace(data={"id": "hdmi-live", "label": "Game Console"})]
    applications = [SimpleNamespace(data={"id": "app-live", "title": "Streaming App"})]

    class SourceControl:
        def __init__(self, opened):
            assert opened is client

        def list_sources(self, timeout):
            return sources

    class ApplicationControl:
        def __init__(self, opened):
            assert opened is client

        def list_apps(self, timeout):
            return applications

    monkeypatch.setitem(
        sys.modules,
        "pywebostv.controls",
        SimpleNamespace(SourceControl=SourceControl, ApplicationControl=ApplicationControl),
    )
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "secure"))
    monkeypatch.setattr(control, "_open_client", lambda key: client)
    payload = control.capabilities()
    assert payload["enumeration_available"] is True
    assert payload["inputs"] == [{"id": control._option_token("input", "hdmi-live"), "label": "Game Console"}]
    assert payload["applications"] == [{"id": control._option_token("app", "app-live"), "label": "Streaming App"}]
    assert client.closed is True


def test_capability_response_keeps_commands_and_live_options(monkeypatch):
    monkeypatch.setenv("LG_TV_MAC", "58:96:0A:9D:1C:0F")
    monkeypatch.setattr(
        control,
        "_enumerated_options",
        lambda: {
            "inputs": [{"id": "input-safe", "label": "Console"}],
            "applications": [{"id": "app-safe", "label": "Video"}],
            "enumeration_available": True,
            "enumeration_reason": None,
        },
    )
    payload = control.lg_tv_capabilities()
    for command in ("power_on", "up", "down", "left", "right", "ok", "back", "home", "set_input", "launch_app"):
        assert command in payload["supported"]
    assert payload["inputs"] and payload["applications"]
