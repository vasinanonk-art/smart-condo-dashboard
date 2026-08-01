import json
from pathlib import Path

import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import household_device_registry
from backend import smartlife_ir_discovery as discovery


def _clear(monkeypatch):
    for key in (
        "SMARTLIFE_IR_PROVIDER",
        "SMARTLIFE_IR_HA_ENTITY_IDS",
        "TUYA_CLOUD_ACCESS_ID",
        "TUYA_CLOUD_ACCESS_SECRET",
        "TUYA_CLOUD_DEVICE_ID",
        "TUYA_CLOUD_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    discovery.invalidate_cache()


def test_smartlife_cloud_provider_is_explicitly_unavailable(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "smartlife_cloud")

    payload = discovery.inventory(force=True)

    assert payload["provider"] == "smartlife_cloud"
    assert payload["provider_detected"] is False
    assert payload["online"] is None
    assert payload["health"] == "unknown"
    assert payload["state_quality"] == "unknown"
    assert payload["devices"] == []
    assert payload["discovery_reason"] == "tuya_cloud_not_configured"


def test_homeassistant_unavailable_is_safe(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "homeassistant")
    monkeypatch.setattr(
        discovery,
        "_homeassistant_states",
        lambda: ([], "not_configured", None, False),
    )

    payload = discovery.inventory(force=True)

    assert payload["provider"] == "homeassistant"
    assert payload["provider_detected"] is False
    assert payload["devices"] == []
    assert payload["discovery_reason"] == "homeassistant_unavailable"


def test_configured_homeassistant_with_empty_inventory_stays_unknown(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "homeassistant")
    monkeypatch.setattr(
        discovery,
        "_homeassistant_states",
        lambda: ([], None, 1.0, True),
    )

    payload = discovery.inventory(force=True)

    assert payload["provider_detected"] is True
    assert payload["online"] is None
    assert payload["health"] == "unknown"
    assert payload["devices"] == []
    assert payload["discovery_reason"] == "empty_inventory"


def test_unsupported_or_unset_provider_fails_closed(monkeypatch):
    _clear(monkeypatch)
    unset = discovery.inventory(force=True)
    assert unset["provider"] == "unsupported"
    assert unset["discovery_reason"] == "provider_not_configured"

    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "invented-provider")
    invalid = discovery.inventory(force=True)
    assert invalid["provider"] == "unsupported"
    assert invalid["provider_detected"] is False
    assert invalid["discovery_reason"] == "unsupported_provider"


def test_verified_homeassistant_inventory_is_normalized_and_redacted(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("SMARTLIFE_IR_PROVIDER", "homeassistant")
    monkeypatch.setenv(
        "SMARTLIFE_IR_HA_ENTITY_IDS",
        "climate.bedroom_ir,remote.unrelated",
    )
    monkeypatch.setattr(
        discovery,
        "_homeassistant_states",
        lambda: ([
            {
                "entity_id": "climate.bedroom_ir",
                "state": "cool",
                "attributes": {
                    "friendly_name": "Bedroom Air Conditioner",
                    "model": "Configured model",
                    "firmware_version": "1.2.3",
                },
            },
            {
                "entity_id": "remote.not_allowlisted",
                "state": "on",
                "attributes": {"friendly_name": "Private remote"},
            },
        ], None, 2.0, True),
    )

    payload = discovery.inventory(force=True)
    item = payload["devices"][0]

    assert payload["provider_detected"] is True
    assert payload["online"] is True
    assert payload["health"] == "healthy"
    assert payload["available_capabilities"] == ["climate"]
    assert item == {
        "provider": "homeassistant",
        "product_name": "Bedroom Air Conditioner",
        "model": "Configured model",
        "device_id": item["device_id"],
        "firmware": "1.2.3",
        "online": True,
        "health": "healthy",
        "state_quality": "confirmed",
        "supported_command_categories": ["climate"],
        "discovery_reason": "verified_homeassistant_entity",
    }
    assert item["device_id"].startswith("ir-")
    rendered = json.dumps(payload).lower()
    assert "climate.bedroom_ir" not in rendered
    assert "remote.not_allowlisted" not in rendered
    assert "password" not in rendered
    assert "token" not in rendered
    assert "ir_code" not in rendered


def test_household_registry_stays_unknown_until_inventory_is_verified(
    monkeypatch,
):
    _clear(monkeypatch)
    monkeypatch.setattr(household_device_registry, "_tapo_detected", lambda: False)
    monkeypatch.setattr(
        household_device_registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {"bridge_online": None, "remotes": []},
    )
    monkeypatch.setattr(
        household_device_registry.smartlife_ir_discovery,
        "inventory",
        lambda: {
            "provider": "unsupported",
            "provider_detected": False,
            "online": None,
            "health": "unknown",
            "state_quality": "unknown",
            "available_capabilities": [],
            "discovery_reason": "provider_not_configured",
            "devices": [],
            "count": 0,
            "read_only": True,
        },
    )

    bedroom = next(
        device
        for device in household_device_registry._ir_devices()
        if device["id"] == "bed-room-air-conditioner"
    )

    assert bedroom["online"] is None
    assert bedroom["health"] == "unknown"
    assert bedroom["state_quality"] == "unknown"
    assert bedroom["capabilities"] == {}
    assert bedroom["unavailable_reason"] == "provider_not_configured"


def test_verified_inventory_updates_registry_without_enabling_controls(
    monkeypatch,
):
    _clear(monkeypatch)
    monkeypatch.setattr(household_device_registry, "_tapo_detected", lambda: False)
    monkeypatch.setattr(
        household_device_registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {"bridge_online": None, "remotes": []},
    )
    monkeypatch.setattr(
        household_device_registry.smartlife_ir_discovery,
        "inventory",
        lambda: {
            "provider": "homeassistant",
            "provider_detected": True,
            "online": True,
            "health": "healthy",
            "state_quality": "confirmed",
            "available_capabilities": ["climate"],
            "discovery_reason": "verified_inventory",
            "devices": [{
                "provider": "homeassistant",
                "product_name": "Bedroom Air Conditioner",
                "model": "Configured model",
                "device_id": "ir-redacted123",
                "firmware": "1.2.3",
                "online": True,
                "health": "healthy",
                "state_quality": "confirmed",
                "supported_command_categories": ["climate"],
                "discovery_reason": "verified_homeassistant_entity",
            }],
            "count": 1,
            "read_only": True,
        },
    )

    bedroom = next(
        device
        for device in household_device_registry._ir_devices()
        if device["id"] == "bed-room-air-conditioner"
    )

    assert bedroom["online"] is True
    assert bedroom["health"] == "healthy"
    assert bedroom["state_quality"] == "confirmed"
    assert bedroom["capabilities"] == {}
    assert bedroom["state"]["ir_diagnostics"]["provider"] == "homeassistant"
    assert bedroom["state"]["ir_diagnostics"]["device_id"] == "ir-redacted123"


def test_inventory_endpoint_is_authenticated_and_read_only(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "ir-discovery")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"ir-password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv(
        "DASHBOARD_SESSION_SECRET",
        "ir-discovery-session-secret-with-sufficient-entropy",
    )
    client = TestClient(app)
    assert client.get("/api/ir/inventory").status_code == 401
    login = client.post(
        "/api/auth/login",
        json={
            "username": "ir-discovery",
            "password": "ir-password",
            "next": "/",
        },
    )
    assert login.status_code == 200

    response = client.get("/api/ir/inventory")

    assert response.status_code == 200
    assert response.json()["read_only"] is True
    assert not any(
        route.path.startswith("/api/ir/inventory")
        and set(route.methods or ()) & {"POST", "PUT", "PATCH", "DELETE"}
        for route in app.routes
    )


def test_registry_configuration_no_longer_contains_t3_placeholder():
    payload = json.loads(
        (
            Path(__file__).parents[1]
            / "config"
            / "ir"
            / "devices.json"
        ).read_text()
    )
    bedroom = next(
        item for item in payload["devices"]
        if item["id"] == "bed-room-air-conditioner"
    )
    assert bedroom["driver"] == "unsupported"
    assert "t3_ir" not in json.dumps(payload)
