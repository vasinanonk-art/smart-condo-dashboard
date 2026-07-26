import json
from pathlib import Path

import bcrypt
from fastapi.testclient import TestClient

from backend import dashboard_settings as settings
from backend import lg_tv_control as control
from backend.app_entry import app


def _client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "control-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "control-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    login = client.post("/api/auth/login", json={"username": "control-test", "password": "password"})
    return client, login.json()["csrf_token"]


def _write(client, csrf, method, path):
    return client.request(method, path, headers={"X-CSRF-Token": csrf, "Origin": "http://testserver"})


def test_notification_lifecycle_and_unread_count(monkeypatch):
    state = {"notifications": [
        {"id": "one", "title": "First", "detail": "Message", "severity": "warning", "created_ts": 2},
        {"id": "two", "title": "Second", "detail": "Message", "severity": "info", "created_ts": 1, "read": True},
    ]}
    monkeypatch.setattr(settings, "_load_maintenance", lambda: json.loads(json.dumps(state)))
    monkeypatch.setattr(settings, "_save_maintenance", lambda value: state.update(json.loads(json.dumps(value))))
    client, csrf = _client(monkeypatch)

    assert client.get("/api/notifications").json()["unread_count"] == 1
    assert _write(client, csrf, "POST", "/api/notifications/one/read").status_code == 200
    assert client.get("/api/notifications").json()["unread_count"] == 0
    assert _write(client, csrf, "DELETE", "/api/notifications/two").status_code == 200
    assert len(client.get("/api/notifications").json()["notifications"]) == 1
    assert _write(client, csrf, "DELETE", "/api/notifications/clear-all").status_code == 200
    assert client.get("/api/notifications").json()["count"] == 0


def test_notification_writes_require_authentication_and_csrf(monkeypatch):
    client, csrf = _client(monkeypatch)
    client.cookies.clear()
    assert client.post("/api/notifications/mark-all-read").status_code == 401
    client, csrf = _client(monkeypatch)
    assert client.post("/api/notifications/mark-all-read").status_code == 403


def test_notification_projection_deduplicates_and_bounds(monkeypatch):
    from backend import notification_center
    items = [{"id": "same", "title": str(index), "created_ts": index} for index in range(120)]
    items += [{"id": f"n-{index}", "title": "safe", "created_ts": index} for index in range(120)]
    monkeypatch.setattr(settings, "_load_maintenance", lambda: {"notifications": items})
    result = notification_center.notifications()
    assert len(result["notifications"]) == 100
    assert len({item["id"] for item in result["notifications"]}) == 100


def test_compact_lg_frontend_has_live_only_options_and_no_duplicate_get():
    root = Path(control.app_module.FRONTEND_DIR) / "assets"
    remote = (root / "dashboard_lg_remote.js").read_text()
    status = (root / "dashboard_lg_status.js").read_text()
    assert "Power On" in remote
    assert "Wake-on-LAN is not configured. Add the TV MAC address to enable Power On." in remote
    assert "capabilities.inputs" in remote and "capabilities.applications" in remote
    assert "HDMI1" not in remote and "HDMI2" not in remote
    command_handler = status[status.index("window.tv = async"):]
    assert "output.state" in command_handler
    assert "/api/lg-tv/status/refresh" not in command_handler


def test_notification_panel_escapes_values_and_only_loads_when_open():
    source = (Path(__file__).parents[1] / "frontend/assets/dashboard_notifications.js").read_text()
    assert "${safe(item.title)}" in source
    assert "${safe(item.message)}" in source
    assert "if (!panel.hidden) await load()" in source
    assert "setInterval" not in source


def test_live_enumeration_returns_safe_tokens_and_closes(monkeypatch):
    class Item:
        def __init__(self, data):
            self.data = data
    class Client:
        closed = False
        def close(self):
            self.closed = True
    class Sources:
        def __init__(self, client): pass
        def list_sources(self, **kwargs): return [Item({"id": "vendor-input", "label": "Game console"})]
    class Apps:
        def __init__(self, client): pass
        def list_apps(self, **kwargs): return [Item({"id": "vendor-app", "title": "Video app"})]

    client = Client()
    monkeypatch.setattr(control.pairing, "_current_key", lambda: ("key", "source"))
    monkeypatch.setattr(control, "_open_client", lambda key: client)
    monkeypatch.setattr("pywebostv.controls.SourceControl", Sources)
    monkeypatch.setattr("pywebostv.controls.ApplicationControl", Apps)
    result = control.capabilities()
    assert result["enumeration_available"] is True
    assert result["inputs"][0] == {"id": control._option_token("input", "vendor-input"), "label": "Game console"}
    assert "vendor-input" not in json.dumps(result)
    assert client.closed is True
