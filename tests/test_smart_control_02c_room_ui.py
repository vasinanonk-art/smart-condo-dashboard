from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE = (ROOT / "frontend/assets/dashboard_household_devices.js").read_text()


def test_real_entertainment_and_climate_devices_are_visible():
    for identifier in (
        "living-room-samsung-soundbar",
        "living-room-air-conditioner",
        "living-room-fan",
        "bed-room-air-conditioner",
    ):
        assert identifier in SOURCE
    for label in ("Power", "Volume +", "Source", "Temperature", "Oscillation", "Timer"):
        assert label in SOURCE
    assert "window.renderLgTvCompact?.()" in SOURCE


def test_unsupported_controls_are_disabled_with_reason():
    assert 'disabled title="${safe(reason)}"' in SOURCE
    assert "device.unavailable_reason" in SOURCE
    assert "No IR provider" not in SOURCE


def test_three_camera_placeholders_remain_visible_without_false_offline():
    from backend import household_device_registry as registry
    devices = registry._camera_placeholders()
    assert [item["display_name"] for item in devices] == ["Tapo C220", "Xiaomi Camera 1", "Xiaomi Camera 2"]
    assert all(item["online"] is None for item in devices)
    assert all(item["unavailable_reason"] == "Configuration unavailable" for item in devices)
    assert all(not any(item["capabilities"].values()) for item in devices)
    assert "device.unavailable_reason ?" in SOURCE


def test_room_ui_uses_registry_without_provider_or_secret_fields():
    assert "/api/devices" in SOURCE
    for forbidden in ("rtsp_url", "client_key", "deviceid", "app_secret", "ir_code"):
        assert forbidden not in SOURCE.lower()


def test_state_quality_is_labeled_honestly():
    assert "State quality:" in SOURCE
    assert "IR has no feedback" in SOURCE
    assert "Confirmed" in SOURCE and "Assumed" in SOURCE and "Unknown" in SOURCE
