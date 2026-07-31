import json
from types import SimpleNamespace

from backend import camera_read_providers as providers
from backend import device_registration
from backend import household_device_registry
from backend.device_registry import DeviceRegistry


def camera(identifier="camera-one", **updates):
    value = {
        "id": identifier,
        "display_name": identifier.replace("-", " ").title(),
        "room": "unknown",
        "vendor": None,
        "model": None,
        "host": None,
        "enabled": False,
        "provider": "auto",
        "rtsp_port": None,
        "onvif_port": None,
        "stream_path": None,
        "credentials": None,
        "declared_capabilities": [],
        "verification_status": "unverified",
    }
    value.update(updates)
    return value


def configured_onvif(identifier="camera-one", **updates):
    return camera(
        identifier,
        host=f"{identifier}.local",
        enabled=True,
        provider="onvif",
        onvif_port=2020,
        credentials={
            "username_env": f"{identifier.upper().replace('-', '_')}_USERNAME",
            "password_env": f"{identifier.upper().replace('-', '_')}_PASSWORD",
        },
        verification_status="verified",
        **updates,
    )


def install(monkeypatch, tmp_path, entries):
    path = tmp_path / "cameras.local.json"
    path.write_text(json.dumps({"schema_version": 1, "cameras": entries}))
    monkeypatch.setattr(providers, "_config_path", lambda: path)
    return path


def onvif_camera(manufacturer, model, serial):
    profile = SimpleNamespace(token="private-profile-token")
    return SimpleNamespace(
        devicemgmt=SimpleNamespace(GetDeviceInformation=lambda: SimpleNamespace(
            Manufacturer=manufacturer,
            Model=model,
            FirmwareVersion="1.0.0",
            SerialNumber=serial,
        )),
        create_media_service=lambda: SimpleNamespace(
            GetProfiles=lambda: [profile],
            GetSnapshotUri=lambda request: SimpleNamespace(
                Uri=f"http://{model.casefold()}.local/snapshot"
            ),
        ),
        create_ptz_service=lambda: SimpleNamespace(
            GetConfigurations=lambda: [SimpleNamespace(token="private-ptz-token")]
        ),
    )


def test_partial_configuration_keeps_valid_cameras(monkeypatch, tmp_path):
    valid = configured_onvif("valid-camera")
    invalid = camera("invalid-camera", host="https://invalid/path")
    install(monkeypatch, tmp_path, [invalid, valid])

    status, specs, failures = providers.load_inventory_details()

    assert status == "configuration_partial"
    assert [item.id for item in specs] == ["valid-camera"]
    assert failures == [{"index": 0, "error": "host_invalid"}]
    payload = providers._inventory_payload(discover_live=False)
    assert payload["config_loaded"] is True
    assert payload["invalid_camera_count"] == 1
    assert [item["id"] for item in payload["cameras"]] == ["valid-camera"]


def test_invalid_credentials_are_offline_and_secret_safe(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, [configured_onvif()])
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        providers, "_onvif_client",
        lambda spec: (_ for _ in ()).throw(PermissionError("private password")),
    )

    item = providers.camera_devices_readonly()["cameras"][0]

    assert item["online"] is False
    assert item["health"] == "offline"
    assert item["unavailable_reason"] == "invalid_credentials"
    assert "private password" not in repr(item)


def test_provider_authentication_exception_is_classified_without_message(
    monkeypatch, tmp_path,
):
    class AuthenticationError(Exception):
        pass

    install(monkeypatch, tmp_path, [configured_onvif()])
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        providers, "_onvif_client",
        lambda spec: (_ for _ in ()).throw(
            AuthenticationError("private credential detail")
        ),
    )

    item = providers.camera_devices_readonly()["cameras"][0]

    assert item["online"] is False
    assert item["unavailable_reason"] == "invalid_credentials"
    assert "private credential detail" not in repr(item)


def test_unreachable_host_and_timeout_are_offline(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path, [
        configured_onvif("unreachable-camera"),
        configured_onvif("timeout-camera"),
    ])
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())

    def connect(spec):
        if spec.id == "timeout-camera":
            raise TimeoutError("private timeout detail")
        raise ConnectionRefusedError("private host detail")

    monkeypatch.setattr(providers, "_onvif_client", connect)
    payload = providers.camera_devices_readonly()
    by_id = {item["id"]: item for item in payload["cameras"]}

    assert by_id["unreachable-camera"]["online"] is False
    assert by_id["unreachable-camera"]["unavailable_reason"] == "onvif_unavailable"
    assert by_id["timeout-camera"]["online"] is False
    assert by_id["timeout-camera"]["unavailable_reason"] == "camera_timeout"
    assert "private" not in repr(payload)


def test_mixed_onvif_vendors_use_observed_metadata_without_enabling_controls(
    monkeypatch, tmp_path,
):
    install(monkeypatch, tmp_path, [
        configured_onvif("tapo-camera", vendor="Configured Tapo", model="Configured"),
        configured_onvif("other-camera", vendor="Configured Vendor", model="Configured"),
    ])
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())

    def connect(spec):
        values = {
            "tapo-camera": ("TP-Link", "Tapo-C220", "SERIAL-1111"),
            "other-camera": ("Other Vendor", "Other-Model", "SERIAL-2222"),
        }[spec.id]
        camera_client = onvif_camera(*values)
        camera_client.create_media_service = lambda: SimpleNamespace(
            GetProfiles=lambda: [SimpleNamespace(token="private-profile-token")],
            GetSnapshotUri=lambda request: SimpleNamespace(
                Uri=f"http://{spec.host}/snapshot"
            ),
        )
        return camera_client

    monkeypatch.setattr(providers, "_onvif_client", connect)
    items = providers.camera_devices_readonly()["cameras"]

    assert [(item["manufacturer"], item["model"]) for item in items] == [
        ("TP-Link", "Tapo-C220"),
        ("Other Vendor", "Other-Model"),
    ]
    assert [item["serial"] for item in items] == ["***1111", "***2222"]
    assert all(item["online"] is True for item in items)
    assert all(item["ptz_capability"] is True for item in items)
    assert all(item["snapshot_capability"] is True for item in items)
    assert all(item["capabilities"]["ptz_move"] is False for item in items)
    assert all(item["capabilities"]["snapshot"] is False for item in items)


def test_household_and_unified_registries_expose_semantic_camera_health(monkeypatch):
    cameras = [
        {
            **providers._base_result(
                providers.CameraSpec(
                    id="tapo-c220", display_name="Tapo C220", room="living_room",
                    vendor="TP-Link", model="C220", host=None, enabled=True,
                    provider="onvif", rtsp_port=None, onvif_port=2020,
                    stream_path=None, username_env=None, password_env=None,
                    declared_capabilities=frozenset(), verification_status="verified",
                ),
                "onvif", None,
            ),
            "online": True,
            "health": "healthy",
            "last_update": 100,
        },
        {
            **providers._base_result(
                providers.CameraSpec(
                    id="xiaomi-camera-1", display_name="Xiaomi Camera 1",
                    room="bed_room", vendor="Xiaomi", model="Unknown", host=None,
                    enabled=True, provider="onvif", rtsp_port=None,
                    onvif_port=2020, stream_path=None, username_env=None,
                    password_env=None, declared_capabilities=frozenset(),
                    verification_status="verified",
                ),
                "onvif", "onvif_unavailable",
            ),
            "online": False,
            "health": "offline",
        },
    ]
    payload = {
        "config_loaded": True,
        "configuration_status": "configured",
        "invalid_camera_count": 0,
        "cameras": cameras,
    }
    monkeypatch.setattr(
        household_device_registry.camera_read_providers,
        "_inventory_payload",
        lambda **kwargs: payload,
    )
    household = household_device_registry._camera_placeholders()
    by_id = {item["id"]: item for item in household}
    assert (by_id["camera-1"]["online"], by_id["camera-1"]["health"]) == (
        True, "healthy",
    )
    assert (by_id["camera-2"]["online"], by_id["camera-2"]["health"]) == (
        False, "offline",
    )
    assert by_id["camera-3"]["online"] is None
    assert by_id["camera-3"]["health"] == "unknown"

    monkeypatch.setattr(
        providers, "_inventory_payload", lambda **kwargs: payload,
    )
    registry = DeviceRegistry()
    device_registration.install_default_device_registry(
        app_module=SimpleNamespace(
            state={}, camera_config_payload=lambda: {"loaded": False, "cameras": []}
        ),
        target_registry=registry,
    )
    cameras_snapshot = registry.snapshot(device_type="camera")
    registry_by_id = {item.id: item for item in cameras_snapshot}
    assert (
        registry_by_id["camera:tapo-c220"].online,
        registry_by_id["camera:tapo-c220"].health,
    ) == (True, "healthy")
    assert (
        registry_by_id["camera:xiaomi-camera-1"].online,
        registry_by_id["camera:xiaomi-camera-1"].health,
    ) == (False, "offline")
    assert all(item.actions == () for item in cameras_snapshot)
    assert all("ptz_move" not in item.capabilities for item in cameras_snapshot)


def test_legacy_camera_endpoint_projection_contains_verified_runtime_fields(
    monkeypatch,
):
    monkeypatch.setattr(providers, "_config_path", lambda: None)
    monkeypatch.setattr(
        providers,
        "camera_devices_readonly",
        lambda: {
            "config_loaded": True,
            "configuration_status": "configuration_partial",
            "invalid_camera_count": 1,
            "cameras": [{
                "id": "camera-one",
                "online": True,
                "health": "healthy",
                "model": "Model",
                "manufacturer": "Vendor",
                "firmware": "1.0",
                "serial": "***1234",
                "profiles_available": True,
                "ptz_capability": True,
                "snapshot_capability": True,
                "capabilities": {
                    "snapshot": False,
                    "live_stream": False,
                    "ptz_move": False,
                },
            }],
        },
    )

    payload = providers.cameras_runtime()

    assert payload["ok"] is True
    assert payload["camera_count"] == 1
    assert payload["invalid_camera_count"] == 1
    assert payload["configuration_status"] == "configuration_partial"
    assert payload["cameras"][0]["online"] is True
    assert not any(payload["cameras"][0]["capabilities"].values())
