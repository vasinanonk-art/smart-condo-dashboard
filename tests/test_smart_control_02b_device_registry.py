import json

import bcrypt
from fastapi.testclient import TestClient

from backend import household_device_registry as registry
from backend.app_entry import app


SAFE_FIELDS = {
    "id", "room", "display_name", "category", "online", "health",
    "capabilities", "state", "state_quality", "unavailable_reason",
}


def _auth_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "registry-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "registry-test-session-secret-long-enough")
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"username": "registry-test", "password": "password"}).status_code == 200
    return client


def test_registry_requires_authentication(monkeypatch):
    client = _auth_client(monkeypatch)
    client.cookies.clear()
    assert client.get("/api/devices").status_code == 401


def test_registry_has_stable_safe_contract_and_rooms(monkeypatch):
    monkeypatch.setattr(registry, "_tapo_detected", lambda: True)
    monkeypatch.setattr(
        registry.camera_read_providers,
        "_inventory_payload",
        lambda **kwargs: {"config_loaded": False, "cameras": []},
    )
    monkeypatch.setattr(registry.lg_tv_control, "capabilities", lambda **kwargs: {"supported": ["power_off"], "power_on": {"supported": False}})
    monkeypatch.setattr(registry.lg_tv_status, "_public_status", lambda: {"online": True, "last_success_ts": 1, "audio": {"volume": 10}})
    first = registry.registry()
    second = registry.registry()
    assert [item["id"] for item in first] == [item["id"] for item in second]
    assert all(set(item) == SAFE_FIELDS for item in first)
    assert {item["room"] for item in first} <= {"living_room", "bed_room", "unknown"}
    assert any(item["id"] == "living-room-air-conditioner" for item in first)
    assert any(item["id"] == "bed-room-air-conditioner" for item in first)


def test_registry_never_exposes_provider_secrets(monkeypatch):
    monkeypatch.setattr(registry, "_tapo_detected", lambda: True)
    monkeypatch.setattr(
        registry.camera_read_providers,
        "_inventory_payload",
        lambda **kwargs: {"config_loaded": False, "cameras": []},
    )
    monkeypatch.setattr(registry.lg_tv_control, "capabilities", lambda **kwargs: {"supported": []})
    monkeypatch.setattr(registry.lg_tv_status, "_public_status", lambda: {})
    serialized = json.dumps(registry.registry()).lower()
    forbidden = ("password", "token", "rtsp", "deviceid", "client_key", "mac_address", "account", "provider_payload")
    assert not any(word in serialized for word in forbidden)


def test_unsupported_devices_have_unknown_state_and_clear_reason(monkeypatch):
    monkeypatch.setattr(registry, "_tapo_detected", lambda: True)
    devices = registry._ir_devices()
    assert all(item["state_quality"] == "unknown" for item in devices)
    assert all(item["capabilities"] == {} for item in devices)
    assert "command mapping is not configured" in devices[0]["unavailable_reason"]
    assert "control path is not verified" in devices[-1]["unavailable_reason"]
