import json
import threading
import time
from types import SimpleNamespace

import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import camera_read_providers as providers


def _camera(**updates):
    value = {
        "id": "camera-one",
        "display_name": "Camera One",
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


def _install_config(monkeypatch, tmp_path, *cameras):
    path = tmp_path / "cameras.local.json"
    path.write_text(json.dumps({"schema_version": 1, "cameras": list(cameras)}))
    monkeypatch.setattr(providers, "_config_path", lambda: path)
    return path


def _verified_onvif(**updates):
    return _camera(
        display_name="Tapo C220",
        vendor="Tapo",
        model="C220",
        host="camera.local",
        enabled=True,
        provider="onvif",
        onvif_port=2020,
        credentials={
            "username_env": "CAMERA_ONE_USERNAME",
            "password_env": "CAMERA_ONE_PASSWORD",
        },
        declared_capabilities=[
            "snapshot", "live_stream", "onvif_profiles", "ptz_move",
            "ptz_stop", "presets", "firmware_info",
        ],
        verification_status="verified",
        **updates,
    )


def _fake_onvif():
    profile = SimpleNamespace(
        token="vendor-profile-token",
        Name="Main stream",
        VideoEncoderConfiguration=SimpleNamespace(
            Encoding="H264",
            Resolution=SimpleNamespace(Width=1920, Height=1080),
        ),
    )
    media = SimpleNamespace(GetProfiles=lambda: [profile])
    ptz = SimpleNamespace(
        GetConfigurations=lambda: [SimpleNamespace(token="secret-configuration-token")],
        GetPresets=lambda request: [SimpleNamespace(token="secret-preset-token", Name="Home")],
    )
    information = SimpleNamespace(Manufacturer="Tapo", Model="C220", FirmwareVersion="1.2.3")
    return SimpleNamespace(
        devicemgmt=SimpleNamespace(GetDeviceInformation=lambda: information),
        create_media_service=lambda: media,
        create_ptz_service=lambda: ptz,
    )


def test_missing_and_malformed_configuration_are_distinct(monkeypatch, tmp_path):
    monkeypatch.setattr(providers, "_config_path", lambda: None)
    assert providers.camera_devices_readonly() == {
        "config_loaded": False,
        "configuration_status": "configuration_missing",
        "cameras": [],
    }
    path = tmp_path / "cameras.local.json"
    path.write_text("{invalid")
    monkeypatch.setattr(providers, "_config_path", lambda: path)
    assert providers.camera_devices_readonly()["configuration_status"] == "configuration_invalid"


def test_tapo_onvif_discovery_is_per_camera_and_secret_safe(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, _verified_onvif())
    monkeypatch.setenv("CAMERA_ONE_USERNAME", "private-user")
    monkeypatch.setenv("CAMERA_ONE_PASSWORD", "private-password")
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(providers, "_onvif_client", lambda spec: _fake_onvif())

    payload = providers.camera_devices_readonly()
    item = payload["cameras"][0]
    assert item["online"] is True
    assert item["vendor"] == "Tapo" and item["model"] == "C220"
    assert item["capabilities"]["onvif_profiles"] is True
    assert item["capabilities"]["ptz_move"] is True
    assert item["capabilities"]["presets"] is True
    assert item["capabilities"]["live_stream"] is False
    assert item["stream"]["access"] == "metadata_only"
    rendered = repr(payload)
    for secret in (
        "camera.local", "private-user", "private-password",
        "vendor-profile-token", "secret-preset-token", "secret-configuration-token",
    ):
        assert secret not in rendered


def test_xiaomi_without_verified_provider_is_unknown_not_offline(monkeypatch, tmp_path):
    _install_config(
        monkeypatch,
        tmp_path,
        _camera(
            display_name="Xiaomi Camera 1",
            vendor="Xiaomi",
            enabled=True,
            provider="unsupported",
            verification_status="unsupported",
        ),
    )
    item = providers.camera_devices_readonly()["cameras"][0]
    assert item["online"] is None
    assert item["health"] == "unknown"
    assert item["unavailable_reason"] == "read_only_provider_unavailable"
    assert not any(item["capabilities"].values())


def test_timeout_is_safe_and_does_not_expose_exception(monkeypatch, tmp_path):
    _install_config(monkeypatch, tmp_path, _verified_onvif())
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        providers,
        "_onvif_client",
        lambda spec: (_ for _ in ()).throw(TimeoutError("secret camera address")),
    )
    item = providers.camera_devices_readonly()["cameras"][0]
    assert item["online"] is None
    assert item["unavailable_reason"] == "camera_timeout"
    assert "secret camera address" not in repr(item)


def test_rtsp_fallback_reports_metadata_without_raw_url_or_live_proxy(monkeypatch, tmp_path):
    camera = _camera(
        host="camera.local",
        enabled=True,
        provider="auto",
        onvif_port=2020,
        rtsp_port=554,
        stream_path="/private-stream",
        credentials={
            "username_env": "CAMERA_ONE_USERNAME",
            "password_env": "CAMERA_ONE_PASSWORD",
        },
        declared_capabilities=["live_stream"],
        verification_status="verified",
    )
    _install_config(monkeypatch, tmp_path, camera)
    monkeypatch.setenv("CAMERA_ONE_USERNAME", "private-user")
    monkeypatch.setenv("CAMERA_ONE_PASSWORD", "private-password")
    monkeypatch.setattr(providers.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(providers, "_tcp", lambda host, port: True)

    item = providers.camera_devices_readonly()["cameras"][0]
    assert item["provider"] == "rtsp"
    assert item["online"] is True
    assert item["stream"] == {"available": False, "access": "metadata_only"}
    assert item["capabilities"]["live_stream"] is False
    assert "camera.local" not in repr(item)
    assert "/private-stream" not in repr(item)


def test_read_discovery_for_different_cameras_is_concurrent(monkeypatch):
    specs = [
        providers.CameraSpec(
            id=f"camera-{number}", display_name=f"Camera {number}", room="unknown",
            vendor=None, model=None, host=None, enabled=True, provider="unsupported",
            rtsp_port=None, onvif_port=None, stream_path=None, username_env=None,
            password_env=None, declared_capabilities=frozenset(),
            verification_status="unsupported",
        )
        for number in (1, 2)
    ]
    monkeypatch.setattr(providers, "load_inventory", lambda: ("configured", specs))
    entered = []
    gate = threading.Event()

    def discover(spec):
        entered.append(spec.id)
        gate.wait(1)
        return providers._base_result(spec, "unsupported", "read_only_provider_unavailable")

    monkeypatch.setattr(providers, "discover", discover)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(providers.camera_devices_readonly()))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    deadline = time.time() + 1
    while len(entered) < 4 and time.time() < deadline:
        time.sleep(0.01)
    assert len(entered) == 4
    gate.set()
    for thread in threads:
        thread.join(1)
    assert len(results) == 2


def test_snapshot_rejects_device_supplied_cross_host_uri(monkeypatch):
    spec = providers.CameraSpec(
        id="camera-one", display_name="Camera One", room="unknown",
        vendor=None, model=None, host="camera.local", enabled=True, provider="onvif",
        rtsp_port=None, onvif_port=2020, stream_path=None,
        username_env="CAMERA_ONE_USERNAME", password_env="CAMERA_ONE_PASSWORD",
        declared_capabilities=frozenset({"snapshot"}), verification_status="verified",
    )
    profile = SimpleNamespace(token="profile")
    media = SimpleNamespace(
        GetProfiles=lambda: [profile],
        GetSnapshotUri=lambda request: SimpleNamespace(Uri="http://unexpected.local/snapshot"),
    )
    monkeypatch.setattr(
        providers, "_onvif_client",
        lambda camera: SimpleNamespace(create_media_service=lambda: media),
    )
    monkeypatch.setattr(providers, "_credentials", lambda camera: ("user", "password"))
    try:
        providers._snapshot_onvif(spec)
    except LookupError as exc:
        assert str(exc) == "snapshot_unavailable"
    else:
        raise AssertionError("cross-host snapshot URI was accepted")


def test_camera_read_routes_require_dashboard_authentication(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "camera-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "camera-session-secret-long-enough")
    client = TestClient(app)
    for path in (
        "/api/camera-control/devices",
        "/api/camera-control/camera-one/status",
        "/api/camera-control/camera-one/stream",
        "/api/camera-control/camera-one/profiles",
        "/api/camera-control/camera-one/presets",
        "/api/camera-control/camera-one/snapshot",
    ):
        assert client.get(path).status_code == 401
