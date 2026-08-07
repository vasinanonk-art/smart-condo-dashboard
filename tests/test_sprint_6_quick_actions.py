from pathlib import Path

from frontend_runtime import quick_actions_behavior


ROOT = Path(__file__).resolve().parents[1]


def test_quick_actions_reuse_verified_command_and_navigation_owners():
    result = quick_actions_behavior()

    assert result["enabledCount"] == 6
    assert result["calls"]["nav"] == ["electricity", "topology"]
    assert result["calls"]["open"] == [{
        "url": "/api/camera-control/bedroom-camera/snapshot",
        "target": "_blank",
        "features": "noopener",
    }]
    assert result["calls"]["ir"] == [
        {
            "device": "bed-room-air-conditioner",
            "confirm": "true",
            "body": {"command": "power_on"},
            "sameRoot": True,
        },
        {
            "device": "bed-room-air-conditioner",
            "confirm": "true",
            "body": {"command": "power_off"},
            "sameRoot": True,
        },
        {
            "device": "bed-room-air-conditioner",
            "confirm": "false",
            "body": {"capability": "temperature", "value": 26},
            "sameRoot": True,
        },
    ]


def test_unavailable_device_actions_are_disabled_but_navigation_remains():
    result = quick_actions_behavior()

    assert result["disabledCount"] == 4
    assert result["labels"] == [
        "AC On", "AC Off", "AC 26°", "Bedroom Camera",
        "Electricity", "Home Status",
    ]


def test_quick_actions_add_no_command_contract_or_polling_owner():
    home = (ROOT / "frontend/assets/dashboard_home.js").read_text(encoding="utf-8")
    household = (
        ROOT / "frontend/assets/dashboard_household_devices.js"
    ).read_text(encoding="utf-8")
    html = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    css = (ROOT / "frontend/assets/dashboard_home.css").read_text(encoding="utf-8")

    assert 'id="homeQuickActions"' in html
    assert "/api/ir/" not in home
    assert "setInterval" not in home
    assert "household?.sendIrCommand?.(button, body, host)" in home
    assert household.count(
        "fetch(`/api/ir/${encodeURIComponent(target)}/command`"
    ) == 1
    assert "state.inFlight.has(target)" in household
    assert "grid-template-columns: repeat(6, minmax(0, 1fr))" in css
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
