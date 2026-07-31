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
    monkeypatch.setattr(
        registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {
            "bridge_online": True,
            "authenticated": True,
            "remotes": [
                {"display_name": "Sound Bar", "reported_state": {}, "stored_commands_present": True},
                {"display_name": "Air Conditioner Remote Control", "reported_state": {"on": 1}, "stored_commands_present": True},
                {"display_name": "Fan Remote Control", "reported_state": {"on": 1}, "stored_commands_present": True},
                {"display_name": "TV Remote Control", "reported_state": {"on": 1}, "stored_commands_present": True},
            ],
        },
    )
    devices = registry._ir_devices()
    assert all(item["state_quality"] == "unknown" for item in devices)
    assert all(item["capabilities"] == {} for item in devices)
    soundbar = next(item for item in devices if item["id"] == "living-room-samsung-soundbar")
    bedroom = next(item for item in devices if item["id"] == "bed-room-air-conditioner")
    television = next(item for item in devices if item["id"] == "living-room-configured-tv-ir")
    assert "transmit interface is not verified" in soundbar["unavailable_reason"]
    assert bedroom["unavailable_reason"] == "provider_not_configured"
    assert television["online"] is True
    assert television["state"]["ir_diagnostics"]["remote_discovered"] is True
    assert television["state"]["ir_diagnostics"]["verified_controls"] == []


def test_discovered_remote_projection_contains_no_vendor_ids_or_private_ir_data(monkeypatch):
    monkeypatch.setattr(registry, "_tapo_detected", lambda: True)
    monkeypatch.setattr(
        registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {
            "bridge_online": True,
            "authenticated": True,
            "remotes": [{
                "id": "configured-ir-remote-1",
                "display_name": "Fan Remote Control",
                "reported_state": {"on": 1, "speed_level": 2},
                "stored_commands_present": True,
                "verified_controls": [],
                "control_available": False,
            }],
        },
    )

    payload = registry._ir_devices()
    fan = next(item for item in payload if item["id"] == "living-room-fan")
    serialized = json.dumps(fan)

    assert fan["capabilities"] == {}
    assert fan["state"]["ir_diagnostics"]["reported_state"] == {"on": 1, "speed_level": 2}
    assert fan["state"]["ir_diagnostics"]["verified_controls"] == []
    assert "configured-ir-remote-1" not in serialized
    for forbidden in ("child_id", "remote_id", "hexData", "ir_code", "password", "token"):
        assert forbidden not in serialized
