import sys
import threading
import time
from types import SimpleNamespace

import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import camera_control as control


def _config(cameras):
    return {"loaded": True, "path": "/redacted", "cameras": cameras}


def test_mixed_inventory_and_xiaomi_ptz_is_unsupported(monkeypatch):
    cameras = [
        {"name": "Tapo", "brand": "Tapo", "model": "C220", "enabled": True, "rtsp_url": "secret"},
        {"name": "Xiaomi 1", "brand": "Xiaomi", "model": "Unknown", "enabled": True},
        {"name": "Xiaomi 2", "brand": "Xiaomi", "model": "Unknown", "enabled": True},
    ]
    monkeypatch.setattr(control.app_module, "camera_config_payload", lambda: _config(cameras))
    monkeypatch.setattr(control.importlib.util, "find_spec", lambda name: None)
    payload = control.camera_devices()
    assert len(payload["cameras"]) == 3
    assert all(camera["provider"] == "read_only" for camera in payload["cameras"])
    assert payload["cameras"][1]["capabilities"]["ptz_move"] is False
    result = control.camera_command("camera-2", control.CameraCommand(command="move", direction="left"))
    assert result.status_code == 422


def test_tapo_c220_detects_explicit_onvif_support(monkeypatch):
    camera = {
        "name": "Tapo", "brand": "Tapo", "model": "C220", "onvif_enabled": True,
        "control_capabilities": ["ptz_move", "ptz_stop"],
    }
    monkeypatch.setattr(control.app_module, "camera_config_payload", lambda: _config([camera]))
    monkeypatch.setattr(control.importlib.util, "find_spec", lambda name: object() if name == "onvif" else None)
    item = control.camera_devices()["cameras"][0]
    assert item["provider"] == "onvif"
    assert item["capabilities"]["ptz_move"] is True
    assert item["capabilities"]["zoom"] is False


def test_public_inventory_redacts_credentials_urls_and_device_ids(monkeypatch):
    camera = {
        "id": "real-device-id", "name": "Camera", "ip": "private-host",
        "username": "private-user", "password": "private-pass",
        "rtsp_url": "rtsp://private-user:private-pass@private-host/live",
    }
    monkeypatch.setattr(control.app_module, "camera_config_payload", lambda: _config([camera]))
    rendered = repr(control.camera_devices())
    for secret in ("real-device-id", "private-host", "private-user", "private-pass", "rtsp://"):
        assert secret not in rendered


def test_ptz_move_always_stops(monkeypatch):
    calls = []
    ptz = SimpleNamespace(ContinuousMove=lambda request: calls.append("move"), Stop=lambda request: calls.append("stop"))
    media = SimpleNamespace(GetProfiles=lambda: [SimpleNamespace(token="profile")])
    camera = SimpleNamespace(create_media_service=lambda: media, create_ptz_service=lambda: ptz)
    monkeypatch.setitem(sys.modules, "onvif", SimpleNamespace(ONVIFCamera=lambda *args, **kwargs: camera))
    monkeypatch.setitem(sys.modules, "zeep.transports", SimpleNamespace(Transport=lambda **kwargs: object()))
    monkeypatch.setattr(control.time, "sleep", lambda duration: None)
    record = control.CameraRecord("camera-1", {"ip": "host"}, "onvif", {"ptz_move": True}, None)
    control._onvif_ptz(record, control.CameraCommand(command="move", direction="left", duration=99))
    assert calls == ["move", "stop"]


def test_ptz_operation_timeout_still_attempts_stop(monkeypatch):
    calls = []
    def move(request):
        calls.append("move")
        raise TimeoutError()
    ptz = SimpleNamespace(ContinuousMove=move, Stop=lambda request: calls.append("stop"))
    media = SimpleNamespace(GetProfiles=lambda: [SimpleNamespace(token="profile")])
    camera = SimpleNamespace(create_media_service=lambda: media, create_ptz_service=lambda: ptz)
    monkeypatch.setitem(sys.modules, "onvif", SimpleNamespace(ONVIFCamera=lambda *args, **kwargs: camera))
    monkeypatch.setitem(sys.modules, "zeep.transports", SimpleNamespace(Transport=lambda **kwargs: object()))
    record = control.CameraRecord("camera-1", {"ip": "host"}, "onvif", {"ptz_move": True}, None)
    try:
        control._onvif_ptz(record, control.CameraCommand(command="move", direction="left"))
    except TimeoutError:
        pass
    assert calls == ["move", "stop"]


def test_different_camera_commands_are_independent_and_same_camera_serializes(monkeypatch):
    records = [
        control.CameraRecord("camera-1", {}, "test", {"ptz_move": True}, None),
        control.CameraRecord("camera-2", {}, "test", {"ptz_move": True}, None),
    ]
    monkeypatch.setattr(control, "inventory", lambda: records)
    entered = []
    gate = threading.Event()
    def execute(record, payload):
        entered.append(record.public_id)
        gate.wait(1)
    monkeypatch.setattr(control, "_execute", execute)
    threads = [
        threading.Thread(target=control.camera_command, args=(camera_id, control.CameraCommand(command="move", direction="left")))
        for camera_id in ("camera-1", "camera-2")
    ]
    for thread in threads: thread.start()
    deadline = time.time() + 1
    while len(entered) < 2 and time.time() < deadline: time.sleep(0.01)
    assert set(entered) == {"camera-1", "camera-2"}
    busy = control.camera_command("camera-1", control.CameraCommand(command="move", direction="left"))
    assert busy.status_code == 409
    gate.set()
    for thread in threads: thread.join(1)


def test_camera_write_requires_authentication_and_csrf(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "camera-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "camera-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/api/camera-control/camera-1/command", json={"command": "move"}).status_code == 401
    login = client.post("/api/auth/login", json={"username": "camera-test", "password": "password"})
    assert login.status_code == 200
    assert client.post("/api/camera-control/camera-1/command", json={"command": "move"}).status_code == 403


def test_camera_timeout_is_safe(monkeypatch):
    record = control.CameraRecord("camera-1", {}, "test", {"ptz_move": True}, None)
    monkeypatch.setattr(control, "inventory", lambda: [record])
    monkeypatch.setattr(control, "_execute", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    result = control.camera_command("camera-1", control.CameraCommand(command="move", direction="left"))
    assert result.status_code == 504
    assert b'"stop_attempted":true' in result.body
    assert b'"movement_stopped":false' in result.body
