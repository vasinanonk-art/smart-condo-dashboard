import json
import sys
import threading
import time
from types import SimpleNamespace

import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import camera_control as control
from backend import camera_read_providers as providers


def _config(cameras):
    return {"loaded": True, "path": "/redacted", "cameras": cameras}


def test_mixed_inventory_and_xiaomi_ptz_is_unsupported(monkeypatch):
    records = [
        control.CameraRecord("camera-1", {}, "read_only", {"ptz_move": False}, "control_provider_not_configured"),
        control.CameraRecord("camera-2", {}, "read_only", {"ptz_move": False}, "control_provider_not_configured"),
        control.CameraRecord("camera-3", {}, "read_only", {"ptz_move": False}, "control_provider_not_configured"),
    ]
    monkeypatch.setattr(control, "inventory", lambda: records)
    payload = {"cameras": [control._public(item) for item in records]}
    assert len(payload["cameras"]) == 3
    assert all(camera["provider"] == "read_only" for camera in payload["cameras"])
    assert payload["cameras"][1]["capabilities"]["ptz_move"] is False
    result = control._camera_command("camera-2", control.CameraCommand(command="move", direction="left"))
    assert result.status_code == 422


def test_tapo_c220_detects_explicit_onvif_support(monkeypatch):
    camera = providers.CameraSpec(
        id="tapo-c220", display_name="Tapo", room="bed_room", vendor="Tapo", model="C200",
        host="camera.local", enabled=True, provider="onvif", rtsp_port=554, onvif_port=2020,
        stream_path=None, username_env="CAMERA_USER", password_env="CAMERA_PASSWORD",
        declared_capabilities=frozenset({"ptz_move", "ptz_stop"}), verification_status="verified",
    )
    monkeypatch.setattr(providers, "load_inventory", lambda: ("configured", [camera]))
    monkeypatch.setattr(providers, "_credentials", lambda spec: ("user", "password"))
    monkeypatch.setattr(control.importlib.util, "find_spec", lambda name: object() if name == "onvif" else None)
    item = control._public(control.inventory()[0])
    assert item["provider"] == "onvif"
    assert item["capabilities"]["ptz_move"] is True
    assert item["capabilities"]["zoom"] is False


def test_public_inventory_redacts_credentials_urls_and_device_ids(monkeypatch):
    camera = control.CameraRecord(
        "camera-1",
        {"name": "Camera", "ip": "private-host", "username": "private-user", "password": "private-pass"},
        "onvif",
        {"ptz_move": True},
        None,
    )
    rendered = repr(control._public(camera))
    for secret in ("real-device-id", "private-host", "private-user", "private-pass", "rtsp://"):
        assert secret not in rendered


def test_ptz_move_always_stops(monkeypatch):
    calls = []
    sleeps = []
    ptz = SimpleNamespace(ContinuousMove=lambda request: calls.append(("move", request)), Stop=lambda request: calls.append(("stop", request)))
    media = SimpleNamespace(GetProfiles=lambda: [SimpleNamespace(token="profile")])
    camera = SimpleNamespace(create_media_service=lambda: media, create_ptz_service=lambda: ptz)
    monkeypatch.setitem(sys.modules, "onvif", SimpleNamespace(ONVIFCamera=lambda *args, **kwargs: camera))
    monkeypatch.setitem(sys.modules, "zeep.transports", SimpleNamespace(Transport=lambda **kwargs: object()))
    monkeypatch.setattr(control.time, "sleep", lambda duration: sleeps.append(duration))
    record = control.CameraRecord("camera-1", {"ip": "host"}, "onvif", {"ptz_move": True}, None)
    control._onvif_ptz(record, control.CameraCommand(command="move", direction="left", duration=99))
    assert [item[0] for item in calls] == ["move", "stop"]
    assert calls[0][1]["Velocity"]["PanTilt"] == {"x": -0.35, "y": 0.0}
    assert calls[0][1]["Timeout"].total_seconds() == 0.5
    assert sleeps == [0.5]


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
        threading.Thread(target=control._camera_command, args=(camera_id, control.CameraCommand(command="move", direction="left")))
        for camera_id in ("camera-1", "camera-2")
    ]
    for thread in threads: thread.start()
    deadline = time.time() + 1
    while len(entered) < 2 and time.time() < deadline: time.sleep(0.01)
    assert set(entered) == {"camera-1", "camera-2"}
    busy = control._camera_command("camera-1", control.CameraCommand(command="move", direction="left"))
    assert busy.status_code == 409
    gate.set()
    for thread in threads: thread.join(1)


def test_stop_ptz_bypasses_busy_move_lock(monkeypatch):
    record = control.CameraRecord("camera-1", {}, "test", {"ptz_stop": True}, None)
    monkeypatch.setattr(control, "inventory", lambda: [record])
    commands = []
    monkeypatch.setattr(control, "_execute", lambda camera, payload: commands.append(payload.command))
    lock = control._lock("camera-1")
    assert lock.acquire(blocking=False)
    try:
        result = control._camera_command("camera-1", control.CameraCommand(command="stop_ptz"))
    finally:
        lock.release()
    assert result["ok"] is True
    assert result["movement_stopped"] is True
    assert commands == ["stop_ptz"]


def test_camera_write_requires_authentication_and_csrf(monkeypatch, capsys):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "camera-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "camera-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/api/camera-control/camera-1/command", json={"command": "move"}).status_code == 401
    login = client.post("/api/auth/login", json={"username": "camera-test", "password": "password"})
    assert login.status_code == 200
    assert client.post("/api/camera-control/camera-1/command", json={"command": "move"}).status_code == 403
    audits = [
        json.loads(line.split(" ", 1)[1])
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("camera_command_audit ")
    ]
    assert [(item["http_status"], item["outcome"]) for item in audits] == [
        (401, "rejected"),
        (403, "rejected"),
    ]


def test_authenticated_ptz_route_is_bounded_and_audited(monkeypatch, capsys):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "camera-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "camera-session-secret-long-enough")
    record = control.CameraRecord(
        "camera-1", {}, "onvif", {"ptz_move": True, "ptz_stop": True}, None,
    )
    commands = []
    monkeypatch.setattr(control, "inventory", lambda: [record])
    monkeypatch.setattr(control, "_execute", lambda camera, payload: commands.append(payload))
    client = TestClient(app, base_url="http://testserver")
    login = client.post("/api/auth/login", json={"username": "camera-test", "password": "password"})
    csrf = login.json()["csrf_token"]
    response = client.post(
        "/api/camera-control/camera-1/command",
        json={"command": "move", "direction": "up", "duration": 0.2},
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert response.json()["movement_stopped"] is True
    assert [(item.command, item.direction, item.duration) for item in commands] == [("move", "up", 0.2)]
    line = next(item for item in capsys.readouterr().out.splitlines() if item.startswith("camera_command_audit "))
    audit = json.loads(line.split(" ", 1)[1])
    assert audit["event"] == "camera_command_audit"
    assert audit["outcome"] == "success"
    assert audit["outbound_attempts"] == 1
    assert audit["device"] == "camera-1"


def test_camera_timeout_is_safe(monkeypatch):
    record = control.CameraRecord("camera-1", {}, "test", {"ptz_move": True}, None)
    monkeypatch.setattr(control, "inventory", lambda: [record])
    monkeypatch.setattr(control, "_execute", lambda *args: (_ for _ in ()).throw(TimeoutError()))
    result = control._camera_command("camera-1", control.CameraCommand(command="move", direction="left"))
    assert result.status_code == 504
    assert b'"stop_attempted":true' in result.body
    assert b'"movement_stopped":false' in result.body
