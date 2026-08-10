import json
import urllib.error

import pytest

from scripts.verify_release import (
    count_journal_json_entries,
    wait_for_dashboard_ready,
    verified_tapo_camera,
    verify_go2rtc_listener_output,
)


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.sleeps.append(duration)
        self.now += duration


class _Response:
    def __init__(self, status=200, content_type="application/json", payload=None):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.payload = json.dumps(payload if payload is not None else {"configured": True, "authenticated": False}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.payload


def test_readiness_handles_service_active_before_port_bind():
    clock = _Clock()
    responses = iter((urllib.error.URLError(ConnectionRefusedError()), _Response()))

    def open_url(_request, timeout):
        assert 0 < timeout <= 2
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    result = wait_for_dashboard_ready(
        timeout=2,
        retry_interval=0.1,
        service_active=lambda: True,
        open_url=open_url,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result == {"status": 200, "attempts": 2, "elapsed_sec": 0.1}


def test_readiness_retries_initial_connection_refusals_then_succeeds():
    clock = _Clock()
    attempts = {"count": 0}

    def open_url(_request, timeout):
        attempts["count"] += 1
        if attempts["count"] < 4:
            raise urllib.error.URLError(ConnectionRefusedError())
        return _Response()

    result = wait_for_dashboard_ready(
        timeout=2,
        retry_interval=0.25,
        service_active=lambda: True,
        open_url=open_url,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["attempts"] == 4
    assert clock.sleeps == [0.25, 0.25, 0.25]


def test_readiness_accepts_immediate_expected_http_response():
    clock = _Clock()
    result = wait_for_dashboard_ready(
        service_active=lambda: True,
        open_url=lambda _request, timeout: _Response(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result == {"status": 200, "attempts": 1, "elapsed_sec": 0.0}
    assert clock.sleeps == []


def test_readiness_timeout_is_bounded_without_infinite_retry():
    clock = _Clock()
    calls = {"count": 0}

    def refused(_request, timeout):
        calls["count"] += 1
        raise urllib.error.URLError(ConnectionRefusedError())

    with pytest.raises(RuntimeError, match="dashboard_readiness_timeout"):
        wait_for_dashboard_ready(
            timeout=0.5,
            retry_interval=0.2,
            service_active=lambda: True,
            open_url=refused,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert calls["count"] == 3
    assert len(clock.sleeps) == 3
    assert clock.now == 0.5


def test_readiness_fails_immediately_when_service_exits_during_wait():
    clock = _Clock()
    states = iter((True, False))
    with pytest.raises(RuntimeError, match="dashboard_service_exited_before_ready"):
        wait_for_dashboard_ready(
            timeout=2,
            retry_interval=0.1,
            service_active=lambda: next(states),
            open_url=lambda _request, timeout: (_ for _ in ()).throw(urllib.error.URLError(ConnectionRefusedError())),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert clock.sleeps == [0.1]


def test_readiness_rejects_unexpected_http_response_without_retry():
    clock = _Clock()
    with pytest.raises(RuntimeError, match="dashboard_readiness_unexpected_http:503"):
        wait_for_dashboard_ready(
            service_active=lambda: True,
            open_url=lambda _request, timeout: _Response(status=503),
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
    assert clock.sleeps == []


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
