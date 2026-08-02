import json
import time

from backend import app as app_module
from backend import household_device_registry
from backend import smartlife_ir_discovery as discovery


def _cloud_inventory(*, online=True):
    device = discovery.IRInventoryDevice(
        provider="smartlife_cloud",
        product_name="Smart remote control with TH sensor",
        model="T3-Smart-301",
        device_id="ir-redacted",
        firmware=None,
        online=online,
        health="healthy" if online else "offline",
        state_quality="confirmed",
        supported_command_categories=(),
        discovery_reason="tuya_cloud_dp_metadata_incomplete",
    )
    return discovery.ProviderInventory(
        provider="smartlife_cloud",
        provider_detected=True,
        online=online,
        health=device.health,
        state_quality="confirmed",
        available_capabilities=(),
        discovery_reason="verified_inventory",
        devices=(device,),
    )


def test_mqtt_state_normalizes_hub_and_virtual_air(monkeypatch):
    monkeypatch.setitem(app_module.state, "condo_sensor", {
        "temperature": 25.4,
        "humidity": 63,
        "ts": int(time.time()),
        "topic": "condo/t3/state",
        "ip": "must-not-leak",
        "raw": "must-not-leak",
    })
    payload = discovery._public_inventory(
        discovery._with_t3_inventory(_cloud_inventory())
    )

    assert payload["count"] == 2
    hub, air = payload["devices"]
    assert hub["state"] == {"temperature_c": 25.4, "humidity_percent": 63}
    assert hub["online"] is True
    assert hub["last_seen"] is not None
    assert air["category"] == "infrared_ac"
    assert air["virtual"] is True
    assert air["parent_id"] == "ir-t3-hub"
    assert air["controllable"] is False
    assert air["supported_command_categories"] == []
    rendered = json.dumps(payload)
    assert "must-not-leak" not in rendered
    assert "condo/t3/state" not in rendered


def test_stale_mqtt_state_marks_hub_offline(monkeypatch):
    monkeypatch.setitem(app_module.state, "condo_sensor", {
        "temperature": 25.4,
        "humidity": 63,
        "ts": int(time.time()) - discovery._T3_SENSOR_MAX_AGE_SEC - 1,
    })
    hub = discovery._with_t3_inventory(_cloud_inventory()).devices[0]
    assert hub.online is False
    assert hub.health == "offline"
    assert hub.state_quality == "unknown"
    assert hub.discovery_reason == "mqtt_sensor_state_stale"


def test_cloud_unavailable_does_not_hide_fresh_mqtt_hub(monkeypatch):
    monkeypatch.setitem(app_module.state, "condo_sensor", {
        "temperature": 24.0,
        "humidity": 55,
        "ts": int(time.time()),
    })
    unavailable = discovery.UnavailableProvider(
        "smartlife_cloud", "tuya_cloud_unavailable"
    ).discover()
    result = discovery._mqtt_only_t3_inventory(unavailable)
    hub = result.devices[0]
    assert hub.online is True
    assert hub.health == "healthy"
    assert hub.discovery_reason == "verified_mqtt_sensor_state_cloud_unavailable"
    assert len(result.devices) == 1


def test_non_t3_or_missing_air_child_is_not_invented(monkeypatch):
    monkeypatch.setitem(app_module.state, "condo_sensor", {})
    other = _cloud_inventory()
    device = discovery.IRInventoryDevice(
        **{**other.devices[0].__dict__, "model": "Unknown model"}
    )
    unchanged = discovery._with_t3_inventory(discovery.ProviderInventory(
        **{**other.__dict__, "devices": (device,)}
    ))
    assert len(unchanged.devices) == 1


def test_registry_exposes_two_cards_without_commands(monkeypatch):
    inventory = discovery._public_inventory(discovery._with_t3_inventory(_cloud_inventory()))
    monkeypatch.setattr(household_device_registry.smartlife_ir_discovery, "inventory", lambda: inventory)
    monkeypatch.setattr(household_device_registry, "_tapo_detected", lambda: False)
    monkeypatch.setattr(
        household_device_registry.tapo_ir_local_bridge,
        "existing_ir_remote_inventory",
        lambda: {"bridge_online": None, "remotes": []},
    )
    devices = household_device_registry._ir_devices()
    air = next(item for item in devices if item["id"] == "bed-room-air-conditioner")
    hub = next(item for item in devices if item["id"] == "bed-room-t3-hub")
    assert air["online"] is True
    assert air["health"] == "healthy"
    assert air["capabilities"] == {}
    assert air["state"]["ir_diagnostics"]["virtual"] is True
    assert air["state"]["ir_diagnostics"]["controllable"] is False
    assert hub["capabilities"] == {}


def test_public_shape_keeps_legacy_fields_and_has_no_commands(monkeypatch):
    monkeypatch.setitem(app_module.state, "condo_sensor", {})
    payload = discovery._public_inventory(discovery._with_t3_inventory(_cloud_inventory()))
    for field in (
        "provider", "provider_detected", "online", "health", "state_quality",
        "available_capabilities", "discovery_reason", "devices", "count", "read_only",
    ):
        assert field in payload
    assert payload["available_capabilities"] == []
    assert all(device["controllable"] is False for device in payload["devices"])


def test_frontend_has_no_additional_polling_owner():
    source = open("frontend/assets/dashboard_household_devices.js", encoding="utf-8").read()
    assert "bed-room-t3-hub" in source
    assert "Virtual Device · Controls unavailable" in source
    assert "setInterval" not in source
    assert "setTimeout" not in source
