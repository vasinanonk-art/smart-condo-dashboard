import logging
import threading
import time

import pytest

from backend import ir_framework as ir


def _bridge_status(*, online=True, error=None):
    return {
        "configured": True,
        "online": online,
        "model": "H110",
        "firmware": "verified-firmware",
        "diagnostics": {
            "latency_ms": 125.5,
            "last_error": error,
        },
    }


def _profile():
    command = ir.IRCommandDefinition(
        "power", "power", "Power", "power", "private-ir-code"
    )
    return ir.IRProfile(
        "fixture",
        1,
        "Fixture",
        "Fixture",
        "television",
        (),
        {"power": command},
        {},
    )


def _dispatch(code="private-ir-code"):
    return ir.IRDispatchCommand("living-room-tv", "power", "power", code, 0.2)


def test_production_driver_lifecycle_and_verified_bridge_health():
    driver = ir.TapoIRDriver(lambda: _bridge_status())
    driver.initialize()

    health = driver.health()

    assert health["online"] is True
    assert health["authenticated"] is True
    assert health["firmware_version"] == "verified-firmware"
    assert health["model"] == "H110"
    assert health["latency_ms"] == 125.5
    assert health["ready"] is False
    assert health["last_error"] == "tapo_ir_send_unsupported"
    assert driver.supports(_profile()) is False
    with pytest.raises(NotImplementedError, match="ir_learning_not_implemented"):
        driver.learn(1)
    driver.shutdown()
    assert driver.health()["ready"] is False


def test_verified_sender_updates_safe_health_without_exposing_response():
    calls = []
    driver = ir.TapoIRDriver(lambda: _bridge_status())
    driver.register_verified_sender(lambda code, timeout: calls.append((code, timeout)))
    driver.initialize()

    driver.send(_dispatch())
    health = driver.health()

    assert calls == [("private-ir-code", 0.2)]
    assert health["ready"] is True
    assert health["last_command"] == "power"
    assert health["last_response"] == "sent"
    assert health["last_command_latency_ms"] >= 0
    assert "private-ir-code" not in repr(health)


def test_bridge_send_lock_prevents_parallel_transmission():
    active = 0
    maximum = 0
    guard = threading.Lock()

    def sender(code, timeout):
        nonlocal active, maximum
        del code, timeout
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.02)
        with guard:
            active -= 1

    driver = ir.TapoIRDriver(lambda: _bridge_status())
    driver.register_verified_sender(sender)
    driver.initialize()
    threads = [
        threading.Thread(target=driver.send, args=(_dispatch(f"code-{index}"),))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert maximum == 1


def test_authentication_failure_and_offline_bridge_are_not_ready():
    auth = ir.TapoIRDriver(
        lambda: {
            **_bridge_status(online=False, error="AuthenticationError"),
            "configured": True,
        }
    )
    auth.register_verified_sender(lambda code, timeout: None)
    auth.initialize()

    health = auth.health()

    assert health["online"] is False
    assert health["authenticated"] is False
    assert health["ready"] is False
    assert health["last_error"] == "AuthenticationError"


def test_command_logging_is_structured_and_never_contains_ir_code(caplog, monkeypatch):
    driver = ir.TapoIRDriver(lambda: _bridge_status())
    driver.register_verified_sender(lambda code, timeout: None)
    driver.initialize()
    monkeypatch.setattr(ir, "_RUNTIME", {
        "living-room-tv": {
            "retry_count": 0,
            "last_command": None,
            "last_success": None,
            "last_failure": None,
        }
    })
    job = ir.QueuedCommand(_dispatch("never-log-this-code"), _profile())

    with caplog.at_level(logging.INFO, logger="smart_condo.ir.command"):
        result = ir._execute_job(driver, job)

    assert result["ok"] is True
    message = caplog.messages[-1]
    assert "device=living-room-tv" in message
    assert "command=power" in message
    assert "duration_ms=" in message
    assert "result=sent" in message
    assert "error_reason=none" in message
    assert "never-log-this-code" not in caplog.text


def test_rejected_command_is_logged_once_with_untrusted_values_redacted(caplog, monkeypatch):
    monkeypatch.setattr(ir, "_device", lambda device_id: None)

    with caplog.at_level(logging.INFO, logger="smart_condo.ir.command"):
        response = ir.execute_command(
            "invalid device/account@example.test",
            ir.IRCommandRequest(command="invalid command/token-value"),
        )

    assert response.status_code == 404
    messages = [message for message in caplog.messages if message.startswith("ir_command ")]
    assert len(messages) == 1
    assert "device=invalid_device" in messages[0]
    assert "command=invalid_command" in messages[0]
    assert "result=rejected" in messages[0]
    assert "error_reason=ir_device_not_found" in messages[0]
    assert "example.test" not in caplog.text
    assert "token-value" not in caplog.text


def test_timeout_retries_once_and_logs_one_final_failure(caplog, monkeypatch):
    attempts = 0

    def timeout_sender(code, timeout):
        nonlocal attempts
        del code, timeout
        attempts += 1
        raise TimeoutError

    driver = ir.TapoIRDriver(lambda: _bridge_status())
    driver.register_verified_sender(timeout_sender)
    driver.initialize()
    monkeypatch.setattr(ir, "_RUNTIME", {
        "living-room-tv": {
            "retry_count": 0,
            "last_command": None,
            "last_success": None,
            "last_failure": None,
        }
    })
    job = ir.QueuedCommand(_dispatch(), _profile())

    with caplog.at_level(logging.INFO, logger="smart_condo.ir.command"):
        result = ir._execute_job(driver, job)

    assert result.status_code == 504
    assert attempts == 2
    assert ir._RUNTIME["living-room-tv"]["retry_count"] == 1
    messages = [message for message in caplog.messages if message.startswith("ir_command ")]
    assert len(messages) == 1
    assert "result=timeout" in messages[0]


def test_default_registry_enables_no_unverified_ir_commands():
    devices = ir.public_devices()

    assert devices
    assert all(device["capabilities"] == [] for device in devices)
    assert all(device["controllable"] is False for device in devices)
