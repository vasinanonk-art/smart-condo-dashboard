from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "frontend/assets/dashboard_household_devices.js").read_text()
DESIGN = (ROOT / "frontend/assets/dashboard_household_design_system.js").read_text()


def test_real_entertainment_and_climate_devices_are_visible():
    from backend import household_device_registry

    devices = household_device_registry._ir_devices()
    identifiers = {device["id"] for device in devices}
    for identifier in (
        "living-room-samsung-soundbar",
        "living-room-air-conditioner",
        "living-room-fan",
        "bed-room-air-conditioner",
    ):
        assert identifier in identifiers
    assert "device.capabilities?.ir" in SOURCE
    assert "capability.type" in SOURCE
    assert "renderLgTvCompact" not in SOURCE


def test_unsupported_controls_are_disabled_with_reason():
    assert "UI.actionButton({label, disabled:true, reason})" in SOURCE
    assert "title=\"${safe(reason)}\"" in DESIGN
    assert "device.unavailable_reason" in SOURCE
    assert "No IR provider" not in SOURCE


def test_three_camera_placeholders_remain_visible_without_false_offline():
    from backend import household_device_registry as registry
    devices = registry._camera_placeholders()
    assert [item["display_name"] for item in devices] == ["Tapo C220", "Xiaomi Camera 1", "Xiaomi Camera 2"]
    assert all(item["online"] is None for item in devices)
    assert all(item["unavailable_reason"] == "Configuration unavailable" for item in devices)
    assert all(not any(item["capabilities"].values()) for item in devices)
    assert "warning:userReason(device)" in SOURCE


def test_room_ui_uses_registry_without_provider_or_secret_fields():
    assert "/api/devices" in SOURCE
    for forbidden in ("rtsp_url", "client_key", "deviceid", "app_secret", "ir_code"):
        assert forbidden not in SOURCE.lower()


def test_state_quality_is_labeled_honestly():
    assert "IR has no device feedback" in SOURCE
    assert "confirmed:'Confirmed'" in DESIGN
    assert "assumed:'Assumed'" in DESIGN
    assert "unknown:'Unknown'" in DESIGN
