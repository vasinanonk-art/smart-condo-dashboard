from pathlib import Path

from backend import household_device_registry as registry


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "frontend/assets/dashboard_household_devices.js").read_text()
CSS = (ROOT / "frontend/assets/dashboard_household_devices.css").read_text()


def _payload(*cameras):
    return {"config_loaded": True, "configuration_status": "configured", "cameras": list(cameras)}


def test_missing_configuration_keeps_known_cameras_unknown(monkeypatch):
    monkeypatch.setattr(
        registry.camera_read_providers,
        "_inventory_payload",
        lambda **kwargs: {"config_loaded": False, "cameras": []},
    )
    cameras = registry._camera_placeholders()
    assert [camera["display_name"] for camera in cameras] == [
        "Bedroom Camera", "Living Room Camera",
    ]
    assert all(camera["online"] is None for camera in cameras)
    assert all(camera["room"] == "unknown" for camera in cameras)
    assert all(camera["unavailable_reason"] == "Configuration unavailable" for camera in cameras)


def test_verified_camera_projects_safe_status_and_capabilities(monkeypatch):
    camera = {
        "id": "tapo-c220",
        "display_name": "Bedroom Camera",
        "room": "bed_room",
        "vendor": "Tapo",
        "model": "C220",
        "online": True,
        "health": "healthy",
        "verification_status": "verified",
        "capabilities": {"snapshot": True, "live_stream": False, "ptz_move": False},
        "discovered_capabilities": ["onvif_profiles"],
        "last_update": 123,
        "unavailable_reason": None,
    }
    monkeypatch.setattr(
        registry.camera_read_providers,
        "_inventory_payload",
        lambda **kwargs: _payload(camera),
    )
    item = registry._camera_placeholders()[0]
    assert item["id"] == "camera-1"
    assert item["online"] is True
    assert item["state_quality"] == "confirmed"
    assert item["capabilities"]["snapshot"] is True
    assert item["state"] == {
        "vendor": "Tapo",
        "model": "C220",
        "firmware": None,
        "serial": None,
        "last_update": 123,
        "provider_verified": True,
        "profiles_available": False,
        "ptz_capability": False,
        "snapshot_capability": False,
        "discovered_capabilities": ["onvif_profiles"],
    }
    rendered = repr(item).lower()
    assert "host" not in rendered
    assert "password" not in rendered
    assert "rtsp" not in rendered


def test_camera_ui_is_capability_driven_and_has_no_write_commands():
    camera_source = SOURCE[
        SOURCE.index("function renderCameras()"):
        SOURCE.index("function render()", SOURCE.index("function renderCameras()"))
    ]
    assert "if (capabilities.snapshot)" in SOURCE
    assert "if (capabilities.live_stream)" in SOURCE
    assert "capabilities.presets" in SOURCE
    assert "household-camera-preview" in SOURCE
    assert "Configuration unavailable." in SOURCE
    assert "Location unknown" in SOURCE
    assert "/command" not in camera_source
    assert "method:'POST'" not in camera_source
    assert "data-camera-action=\"move\"" not in SOURCE
    assert "data-camera-action=\"zoom\"" not in SOURCE


def test_live_view_is_on_demand_and_releases_media_on_close():
    assert "function openCameraLiveView(device, identifier)" in SOURCE
    assert "video.src = `/api/camera-control/${identifier}/live`" in SOURCE
    assert "if (button.dataset.cameraAction === 'live')" in SOURCE
    assert "video.removeAttribute('src')" in SOURCE
    assert "video.load()" in SOURCE
    assert "dialog.addEventListener('cancel'" in SOURCE
    assert "/api/streams" not in SOURCE
    assert "rtsp://" not in SOURCE.lower()


def test_camera_ui_shows_verified_details_and_exact_unavailable_reasons():
    for label in ("Vendor", "Model", "Last update", "Capabilities"):
        assert f"<span>{label}</span>" in SOURCE
    for reason in (
        "Camera discovery timed out.",
        "ONVIF connectivity is unavailable.",
        "Camera stream connectivity is unavailable.",
        "No verified read-only camera provider is configured.",
    ):
        assert reason in SOURCE


def test_snapshot_preview_is_responsive_without_fixed_height_truncation():
    compact = "".join(CSS.split())
    block = compact.split(".household-camera-preview{", 1)[1].split("}", 1)[0]
    assert "width:100%" in block
    assert "height:auto" in block
    assert "grid-column:1/-1" in block
