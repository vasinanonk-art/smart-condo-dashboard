import asyncio
import importlib

import pytest

from backend import app as app_module
from backend import tplink_camera_provider as camera_provider
from backend import tplink_connector as connector


def run(awaitable):
    return asyncio.run(awaitable)


def observation(**overrides):
    values = {
        "id": "living_camera",
        "alias": "Living Camera",
        "model": "Verified Model",
        "device_type": "camera",
        "serial": "SERIAL-12345678",
        "firmware": "1.2.3",
        "hardware_version": "2.0",
        "online": True,
    }
    values.update(overrides)
    return camera_provider.TPLinkCameraObservation(**values)


def test_import_has_no_routes_or_runtime_activation():
    routes_before = tuple(
        (route.path, getattr(route, "name", None))
        for route in app_module.app.routes
    )

    imported = importlib.import_module("backend.tplink_camera_provider")

    routes_after = tuple(
        (route.path, getattr(route, "name", None))
        for route in app_module.app.routes
    )
    assert routes_after == routes_before
    assert imported is camera_provider
    assert not hasattr(imported, "client")
    assert not hasattr(imported, "connector")


def test_explicit_provider_registration_and_duplicate_protection():
    registry = connector.TPLinkConnector()
    provider = camera_provider.TPLinkCameraProvider()

    camera_provider.register_camera_provider(registry, provider)

    assert registry.provider_ids() == ("tplink_camera",)
    with pytest.raises(
        connector.TPLinkConfigurationError,
        match="duplicate_provider",
    ):
        camera_provider.register_camera_provider(
            registry,
            camera_provider.TPLinkCameraProvider(),
        )


def test_camera_inventory_mapping_and_redaction():
    provider = camera_provider.TPLinkCameraProvider((observation(),))

    payload = run(provider.inventory())[0].to_dict()

    assert payload == {
        "id": "living_camera",
        "provider_id": "tplink_camera",
        "display_name": "Living Camera",
        "kind": "camera",
        "model": "Verified Model",
        "firmware_version": "1.2.3",
        "online": True,
        "capabilities": [],
        "state": {},
        "metadata": {
            "device_type": "camera",
            "serial_redacted": "***5678",
            "hardware_version": "2.0",
        },
    }
    serialized = str(payload)
    assert "SERIAL-12345678" not in serialized
    assert "password" not in serialized.casefold()
    assert "token" not in serialized.casefold()


def test_short_serial_is_fully_redacted():
    provider = camera_provider.TPLinkCameraProvider(
        (observation(serial="1234"),)
    )

    assert run(provider.inventory())[0].metadata["serial_redacted"] == "****"


def test_health_reports_readiness_inventory_and_refresh_time():
    provider = camera_provider.TPLinkCameraProvider((observation(),))

    before = run(provider.health()).to_dict()
    run(provider.initialize())
    run(provider.inventory())
    after = run(provider.health()).to_dict()
    run(provider.shutdown())

    assert before["ready"] is False
    assert before["details"] == {"inventory_available": False}
    assert after["status"] == "healthy"
    assert after["ready"] is True
    assert after["details"] == {"inventory_available": True}
    assert after["last_checked_at"] is not None
    assert after["latency_ms"] is not None
    assert after["latency_ms"] >= 0


def test_inventory_mapping_failure_is_reported_without_leaking_input():
    provider = camera_provider.TPLinkCameraProvider(
        (observation(id="../unsafe", serial="PRIVATE-SERIAL"),)
    )

    with pytest.raises(connector.TPLinkConfigurationError):
        run(provider.inventory())

    health = run(provider.health()).to_dict()
    assert health["status"] == "degraded"
    assert health["last_error"] == "inventory_mapping_failed"
    assert health["last_checked_at"] is not None
    assert "PRIVATE-SERIAL" not in str(health)


@pytest.mark.parametrize(
    "capability",
    (
        "snapshot",
        "livestream",
        "ptz",
        "recordings",
        "motion",
        "microphone",
        "speaker",
        "scenes",
    ),
)
def test_operational_capabilities_fail_closed(capability):
    provider = camera_provider.TPLinkCameraProvider()

    result = run(provider.capability(capability))

    assert result.status is connector.TPLinkSupportStatus.NOT_SUPPORTED
    assert result.reason == "provider_capability_not_supported"
    assert result.data is None


def test_only_inventory_and_health_are_declared_supported():
    provider = camera_provider.TPLinkCameraProvider()

    assert provider.capabilities.supported == frozenset({"inventory", "health"})
    assert (
        run(provider.capability("inventory")).status
        is connector.TPLinkSupportStatus.SUPPORTED
    )
    assert (
        run(provider.capability("health")).status
        is connector.TPLinkSupportStatus.SUPPORTED
    )


def test_camera_capability_discovery_is_complete_and_fail_closed():
    provider = camera_provider.TPLinkCameraProvider()

    assert provider.capability_discovery() == {
        "inventory": "Supported",
        "health": "Supported",
        "snapshot": "Not Supported",
        "livestream": "Not Supported",
        "recordings": "Not Supported",
        "motion": "Not Supported",
        "microphone": "Not Supported",
        "speaker": "Not Supported",
        "ptz": "Not Supported",
    }


def test_provider_self_description_is_safe_and_explicit():
    provider = camera_provider.TPLinkCameraProvider()

    assert provider.describe().to_dict() == {
        "provider_name": "TP-Link Camera Provider",
        "provider_version": "1.0.0",
        "api_version": "inventory-v1",
        "implementation_status": "read_only_skeleton",
    }


def test_provider_diagnostics_track_lifecycle_without_runtime_details():
    provider = camera_provider.TPLinkCameraProvider()

    before = provider.diagnostics()
    run(provider.initialize())
    initialized = provider.diagnostics()
    run(provider.initialize())
    initialized_again = provider.diagnostics()
    run(provider.shutdown())
    after = provider.diagnostics()

    assert before == {
        "supported_capability_count": 2,
        "unsupported_capability_count": 7,
        "provider_uptime_seconds": None,
        "initialization_timestamp": None,
    }
    assert initialized["provider_uptime_seconds"] is not None
    assert initialized["provider_uptime_seconds"] >= 0
    assert initialized["initialization_timestamp"] is not None
    assert (
        initialized_again["initialization_timestamp"]
        == initialized["initialization_timestamp"]
    )
    assert after["provider_uptime_seconds"] is None
    assert (
        after["initialization_timestamp"]
        == initialized["initialization_timestamp"]
    )
