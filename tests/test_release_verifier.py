import json

import pytest

from scripts.verify_release import (
    count_journal_json_entries,
    verified_tapo_camera,
    verify_go2rtc_listener_output,
)


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


def test_release_verifier_accepts_exact_go2rtc_loopback_listeners():
    output = "\n".join(
        (
            "LISTEN 0 4096 127.0.0.1:1984 0.0.0.0:* users:((go2rtc))",
            "LISTEN   0  4096   127.0.0.1:8554   0.0.0.0:*",
        )
    )
    assert verify_go2rtc_listener_output(output) == {
        1984: "127.0.0.1",
        8554: "127.0.0.1",
    }


@pytest.mark.parametrize("wildcard", ("0.0.0.0", "*", "[::]", "::"))
def test_release_verifier_rejects_go2rtc_wildcard_listener(wildcard):
    output = "\n".join(
        (
            f"LISTEN 0 4096 {wildcard}:1984 0.0.0.0:*",
            "LISTEN 0 4096 127.0.0.1:8554 0.0.0.0:*",
        )
    )
    with pytest.raises(ValueError, match="go2rtc_listener_not_loopback:1984"):
        verify_go2rtc_listener_output(output)


def test_release_verifier_rejects_missing_go2rtc_listener():
    with pytest.raises(ValueError, match="go2rtc_listener_not_loopback:8554"):
        verify_go2rtc_listener_output("LISTEN 0 4096 127.0.0.1:1984 0.0.0.0:*")
