import pytest

from scripts.verify_release import verified_tapo_camera


def test_release_verifier_uses_camera_api_cameras_envelope():
    payload = {
        "config_loaded": True,
        "configuration_status": "configured",
        "invalid_camera_count": 0,
        "cameras": [
            {
                "id": "tapo-c220",
                "verification_status": "verified",
                "capabilities": {"snapshot": True, "live_stream": True},
            }
        ],
    }

    assert verified_tapo_camera(payload)["id"] == "tapo-c220"

    incompatible = dict(payload, cameras=None, devices=payload["cameras"])
    with pytest.raises(ValueError, match="camera_inventory_invalid"):
        verified_tapo_camera(incompatible)
