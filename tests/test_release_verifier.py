import json

import pytest

from scripts.verify_release import count_journal_json_entries, verified_tapo_camera


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


def _journal_record(message):
    return json.dumps({"PRIORITY": "3", "MESSAGE": message})


def test_release_verifier_counts_no_journal_errors():
    assert count_journal_json_entries("") == 0


def test_release_verifier_counts_one_real_journal_error():
    assert count_journal_json_entries(_journal_record("one error")) == 1


def test_release_verifier_counts_multiple_real_journal_errors():
    output = "\n".join((_journal_record("first error"), _journal_record("second error")))
    assert count_journal_json_entries(output) == 2
