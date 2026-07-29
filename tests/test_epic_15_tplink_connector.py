import asyncio
import importlib

import pytest

from backend import app as app_module
from backend import tplink_connector as connector


class FakeProvider(connector.TPLinkProvider):
    def __init__(
        self,
        provider_id="camera_provider",
        devices=(),
        health=None,
        capabilities=None,
    ):
        self._provider_id = provider_id
        self._devices = tuple(devices)
        self._health = health or connector.TPLinkHealth()
        self._capabilities = capabilities or connector.TPLinkProviderCapabilities(
            frozenset({"inventory", "health"})
        )
        self.events = []

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def supported_kinds(self):
        return frozenset({
            connector.TPLinkDeviceKind.CAMERA,
            connector.TPLinkDeviceKind.HUB,
        })

    @property
    def metadata(self):
        return connector.TPLinkProviderMetadata(
            provider_name="Test Provider",
            provider_version="1.0",
            api_version="test-v1",
        )

    @property
    def capabilities(self):
        return self._capabilities

    async def initialize(self):
        self.events.append("initialize")

    async def shutdown(self):
        self.events.append("shutdown")

    async def health(self):
        return self._health

    async def inventory(self):
        return self._devices


class FakeSceneProvider(FakeProvider, connector.TPLinkSceneProvider):
    def __init__(self):
        super().__init__(
            capabilities=connector.TPLinkProviderCapabilities(
                frozenset({"inventory", "health", "scenes"})
            )
        )

    async def scenes(self):
        return (
            connector.TPLinkScene(
                id="evening",
                provider_id=self.provider_id,
                display_name="Evening",
                trigger_method="documented_scene_activate",
                execution_scope=connector.TPLinkExecutionScope.CLOUD,
            ),
        )


def run(awaitable):
    return asyncio.run(awaitable)


def device(device_id="living_camera", provider_id="camera_provider", **kwargs):
    return connector.TPLinkDevice(
        id=device_id,
        provider_id=provider_id,
        display_name="Living Camera",
        kind=connector.TPLinkDeviceKind.CAMERA,
        capabilities=("snapshot", "snapshot"),
        **kwargs,
    )


def test_import_has_no_routes_or_feature_activation():
    before = [
        (
            route.path,
            tuple(sorted(getattr(route, "methods", None) or ())),
        )
        for route in app_module.app.routes
    ]
    imported = importlib.import_module("backend.tplink_connector")
    after = [
        (
            route.path,
            tuple(sorted(getattr(route, "methods", None) or ())),
        )
        for route in app_module.app.routes
    ]

    assert after == before
    assert imported is connector
    assert not hasattr(imported, "app")


def test_inventory_model_is_safe_and_deduplicated():
    item = device(
        state={"online": True, "token": "must-not-leak"},
        metadata={
            "room": "living_room",
            "credentials": {"username": "must-not-leak"},
            "rtsp_url": "must-not-leak",
            "nested": {"model_family": "tapo"},
        },
    )

    payload = item.to_dict()

    assert payload["capabilities"] == ["snapshot"]
    assert payload["state"] == {"online": True}
    assert payload["metadata"] == {
        "room": "living_room",
        "nested": {"model_family": "tapo"},
    }
    assert "must-not-leak" not in str(payload)


def test_health_model_exposes_safe_status_only():
    health = connector.TPLinkHealth(
        status=connector.TPLinkHealthState.HEALTHY,
        online=True,
        ready=True,
        authenticated=True,
        latency_ms=12.5,
        details={"firmware": "1.0", "account_id": "must-not-leak"},
    )

    assert health.to_dict() == {
        "status": "healthy",
        "online": True,
        "ready": True,
        "authenticated": True,
        "latency_ms": 12.5,
        "last_checked_at": None,
        "last_error": None,
        "details": {"firmware": "1.0"},
    }
    unsafe = connector.TPLinkHealth(
        last_error="Login failed for user@example.com with secret",
    )
    assert unsafe.last_error == "provider_error"


def test_provider_capability_model_supports_extensions_and_explicit_status():
    capabilities = connector.TPLinkProviderCapabilities(frozenset({
        "inventory",
        "health",
        "camera_stream",
        "future_diagnostics",
    }))

    payload = capabilities.to_dict()

    assert capabilities.supports(
        connector.TPLinkProviderCapability.CAMERA_STREAM
    )
    assert capabilities.extensions == ("future_diagnostics",)
    assert payload["camera_stream"] == "Supported"
    assert payload["scenes"] == "Not Supported"
    assert payload["ir"] == "Not Supported"
    assert payload["future_diagnostics"] == "Supported"


def test_provider_metadata_is_explicit_and_bounded():
    metadata = connector.TPLinkProviderMetadata(
        provider_name="Documented Camera SDK",
        provider_version="2.1.0",
        api_version="2026-01",
    )

    assert metadata.to_dict() == {
        "provider_name": "Documented Camera SDK",
        "provider_version": "2.1.0",
        "api_version": "2026-01",
    }
    with pytest.raises(
        connector.TPLinkConfigurationError,
        match="invalid_api_version",
    ):
        connector.TPLinkProviderMetadata("Provider", "1", "")


def test_connector_lifecycle_inventory_and_health():
    provider = FakeProvider(
        devices=(device(),),
        health=connector.TPLinkHealth(
            status=connector.TPLinkHealthState.HEALTHY,
            online=True,
            ready=True,
        ),
    )
    registry = connector.TPLinkConnector()
    registry.register(provider)

    run(registry.initialize())
    items = run(registry.inventory())
    health = run(registry.health())
    run(registry.shutdown())

    assert registry.provider_ids() == ("camera_provider",)
    assert items == (device(),)
    assert health["camera_provider"].ready is True
    assert provider.events == ["initialize", "shutdown"]
    assert registry.provider_metadata()["camera_provider"].api_version == "test-v1"
    assert registry.provider_capabilities()["camera_provider"].supports("health")


def test_connector_rejects_provider_and_device_collisions():
    registry = connector.TPLinkConnector()
    registry.register(FakeProvider())
    with pytest.raises(connector.TPLinkConfigurationError, match="duplicate_provider"):
        registry.register(FakeProvider())

    duplicate = connector.TPLinkConnector()
    duplicate.register(FakeProvider("one", (device("same", "one"),)))
    duplicate.register(FakeProvider("two", (device("same", "two"),)))
    with pytest.raises(connector.TPLinkConfigurationError, match="duplicate_device"):
        run(duplicate.inventory())


def test_registration_requires_inventory_and_health_capabilities():
    registry = connector.TPLinkConnector()
    provider = FakeProvider(
        capabilities=connector.TPLinkProviderCapabilities(
            frozenset({"inventory"})
        )
    )

    with pytest.raises(
        connector.TPLinkConfigurationError,
        match="provider_missing_required_capability",
    ):
        registry.register(provider)


def test_inventory_must_belong_to_the_reporting_provider():
    registry = connector.TPLinkConnector()
    registry.register(FakeProvider("camera_provider", (device(provider_id="other"),)))

    with pytest.raises(
        connector.TPLinkConfigurationError,
        match="provider_inventory_mismatch",
    ):
        run(registry.inventory())


def test_scene_extension_is_inactive_by_default():
    registry = connector.TPLinkConnector()
    registry.register(FakeProvider())

    result = run(registry.scenes())["camera_provider"]

    assert result.status is connector.TPLinkSupportStatus.NOT_SUPPORTED
    assert result.reason == "provider_capability_not_supported"
    assert result.data is None


def test_scene_provider_can_describe_but_not_execute():
    provider = FakeSceneProvider()
    registry = connector.TPLinkConnector()
    registry.register(provider)

    result = run(registry.scenes())["camera_provider"]

    assert result.status is connector.TPLinkSupportStatus.SUPPORTED
    assert result.data[0] == {
        "id": "evening",
        "provider_id": "camera_provider",
        "display_name": "Evening",
        "trigger_method": "documented_scene_activate",
        "execution_scope": "cloud",
        "enabled": None,
    }
    with pytest.raises(
        connector.TPLinkUnsupportedOperation,
        match="scene_execution_not_implemented",
    ):
        run(provider.execute_scene("evening"))


def test_optional_operations_fail_closed_without_fallback():
    registry = connector.TPLinkConnector()
    registry.register(FakeProvider())

    stream = run(registry.capability(
        "camera_provider",
        connector.TPLinkProviderCapability.CAMERA_STREAM,
    ))
    missing_provider = run(registry.capability(
        "missing_provider",
        connector.TPLinkProviderCapability.FIRMWARE,
    ))

    assert stream.to_dict() == {
        "capability": "camera_stream",
        "status": "Not Supported",
        "reason": "provider_capability_not_supported",
        "data": None,
    }
    assert missing_provider.status is connector.TPLinkSupportStatus.NOT_SUPPORTED
    assert missing_provider.reason == "provider_not_registered"


def test_declared_extension_without_handler_still_fails_closed():
    provider = FakeProvider(
        capabilities=connector.TPLinkProviderCapabilities(frozenset({
            "inventory", "health", "future_operation",
        }))
    )

    result = run(provider.capability("future_operation"))

    assert result.status is connector.TPLinkSupportStatus.NOT_SUPPORTED
    assert result.reason == "capability_handler_not_implemented"


def test_capability_result_redacts_nested_data():
    result = connector.TPLinkCapabilityResult.supported(
        "future_status",
        {"ready": True, "token": "must-not-leak"},
    )

    assert result.data == {"ready": True}
    assert "must-not-leak" not in str(result.to_dict())


def test_invalid_identifiers_and_latency_fail_closed():
    with pytest.raises(connector.TPLinkConfigurationError):
        device("../unsafe")
    with pytest.raises(connector.TPLinkConfigurationError):
        connector.TPLinkHealth(latency_ms=-1)
