import json
from pathlib import Path

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend import ir_framework as ir
from backend.app_entry import app


class FakeDriver(ir.IRDriver):
    def __init__(self, outcomes=()):
        self.outcomes = list(outcomes)
        self.calls = []
        self.initialized = False
        self.stopped = False
        self.ready = True

    def initialize(self):
        self.initialized = True

    def shutdown(self):
        self.stopped = True

    def health(self):
        return {
            "online": True,
            "ready": self.ready,
            "last_error": None,
            "driver_version": "test-1",
            "firmware_version": "fixture-2",
        }

    def supports(self, profile):
        return bool(profile.commands)

    def send(self, command):
        self.calls.append(command)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if outcome:
                raise outcome


def _profile_payload():
    return {
        "schema_version": 1,
        "id": "test_profile",
        "brand": "Fixture",
        "model": "Fixture One",
        "device_type": "television",
        "capabilities": [
            {
                "id": "power",
                "type": "button",
                "label": "Power",
                "icon": "power",
                "group": "main",
                "confirm": False,
            },
            {
                "id": "volume",
                "type": "range",
                "label": "Volume",
                "icon": "volume",
                "group": "audio",
                "confirm": False,
                "min": 1,
                "max": 2,
                "step": 1,
                "unit": "",
            },
        ],
        "commands": {
            "power_on": {
                "capability": "power",
                "label": "Power On",
                "icon": "power",
                "code": "secret-code-power",
            },
            "volume_1": {
                "capability": "volume",
                "label": "Volume 1",
                "icon": "volume",
                "value": 1,
                "code": "secret-code-volume-1",
            },
            "volume_2": {
                "capability": "volume",
                "label": "Volume 2",
                "icon": "volume",
                "value": 2,
                "code": "secret-code-volume-2",
            },
        },
        "metadata": {"fixture": True},
    }


def _write_configuration(tmp_path: Path, monkeypatch, profile=None, device_capabilities=("power", "volume")):
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    payload = profile or _profile_payload()
    (profile_dir / "test_profile.json").write_text(json.dumps(payload), encoding="utf-8")
    registry = tmp_path / "devices.json"
    registry.write_text(json.dumps({
        "schema_version": 1,
        "devices": [
            {
                "id": "living-room-device",
                "display_name": "Living Room Device",
                "room": "living_room",
                "type": "television",
                "driver": "test_driver",
                "profile": "test_profile",
                "capabilities": list(device_capabilities),
                "enabled": True,
            },
            {
                "id": "bed-room-device",
                "display_name": "Bed Room Device",
                "room": "bed_room",
                "type": "air_conditioner",
                "driver": "test_driver",
                "profile": "test_profile",
                "capabilities": list(device_capabilities),
                "enabled": True,
            },
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("IR_DEVICE_REGISTRY_FILE", str(registry))
    monkeypatch.setenv("IR_PROFILE_DIR", str(profile_dir))
    monkeypatch.setattr(ir, "_RUNTIME", {})
    monkeypatch.setattr(ir, "_DEVICE_QUEUES", {})
    return registry, profile_dir


def _job(identifier):
    return ir.QueuedCommand(
        ir.IRDispatchCommand("queue-device", identifier, "power", f"code-{identifier}", 1),
        ir.IRProfile("profile", 1, "brand", "model", "custom", (), {}, {}),
    )


def test_capability_metadata_and_public_api_are_declarative_and_safe(tmp_path, monkeypatch):
    _write_configuration(tmp_path, monkeypatch)
    driver = FakeDriver()
    monkeypatch.setitem(ir.DRIVERS, "test_driver", driver)

    devices = ir.public_devices()

    assert len(devices) == 2
    first = devices[0]
    assert first["device"]["friendly_name"] == "Living Room Device"
    assert first["capabilities"][0] == {
        "id": "power",
        "type": "button",
        "label": "Power",
        "icon": "power",
        "group": "main",
        "confirm": False,
        "commands": [{"id": "power_on", "label": "Power On", "icon": "power", "value": None}],
    }
    assert first["capabilities"][1]["min"] == 1
    assert first["capabilities"][1]["max"] == 2
    serialized = json.dumps(devices).lower()
    for forbidden in ("secret-code", '"driver"', '"profile"', "bridge_ip", "password", "token"):
        assert forbidden not in serialized


def test_driver_lifecycle_and_health_contract():
    driver = FakeDriver()
    driver.initialize()
    assert driver.initialized is True
    assert driver.supports(ir.IRProfile("p", 1, "b", "m", "custom", (), {"x": object()}, {}))
    assert driver.health() == {
        "online": True,
        "ready": True,
        "last_error": None,
        "driver_version": "test-1",
        "firmware_version": "fixture-2",
    }
    driver.shutdown()
    assert driver.stopped is True


def test_driver_registration_initializes_and_replacement_shuts_down(monkeypatch):
    monkeypatch.setattr(ir, "DRIVERS", {})
    first = FakeDriver()
    second = FakeDriver()

    ir.register_driver("fixture", first)
    ir.register_driver("fixture", second)

    assert first.initialized is True
    assert first.stopped is True
    assert second.initialized is True
    assert ir.DRIVERS["fixture"] is second


def test_driver_health_failure_degrades_safely():
    driver = FakeDriver()
    driver.health = lambda: (_ for _ in ()).throw(RuntimeError("private detail"))

    health = ir._driver_health(driver)

    assert health == {
        "online": None,
        "authenticated": False,
        "ready": False,
        "last_error": "RuntimeError",
        "driver_version": None,
        "firmware_version": None,
        "model": None,
        "latency_ms": None,
        "last_command": None,
        "last_response": None,
        "last_command_latency_ms": None,
    }
    assert "private detail" not in repr(health)


def test_runtime_registry_tracks_required_internal_and_public_fields(tmp_path, monkeypatch):
    _write_configuration(tmp_path, monkeypatch)
    monkeypatch.setitem(ir.DRIVERS, "test_driver", FakeDriver())

    public = ir.public_devices()[0]
    internal = ir._RUNTIME["living-room-device"]

    assert set(internal) >= {
        "enabled", "online", "healthy", "driver", "profile", "firmware_version",
        "last_seen", "last_command", "last_success", "last_failure",
        "pending_queue", "retry_count", "authenticated", "model", "latency_ms",
        "last_response", "last_error",
    }
    assert set(public["runtime_status"]) == {
        "enabled", "online", "healthy", "firmware_version", "last_seen",
        "last_command", "last_success", "last_failure", "pending_queue", "retry_count",
        "authenticated", "model", "latency_ms", "last_response", "last_error",
    }
    assert public["runtime_status"]["online"] is True
    assert public["runtime_status"]["healthy"] is True
    assert public["runtime_status"]["firmware_version"] == "fixture-2"


def test_queue_is_fifo_and_never_has_two_owners():
    queue = ir.DeviceCommandQueue("queue-device")
    owners = []
    for identifier in ("one", "two", "three"):
        owner, dropped = queue.put(_job(identifier))
        owners.append(owner)
        assert dropped is None
    observed = []
    while current := queue.pop():
        observed.append(current.dispatch.command_id)
    assert owners == [True, False, False]
    assert observed == ["one", "two", "three"]


def test_queue_overflow_drops_oldest_and_keeps_depth_twenty():
    queue = ir.DeviceCommandQueue("queue-device")
    jobs = [_job(str(index)) for index in range(21)]
    dropped = None
    for job in jobs:
        _, dropped_now = queue.put(job)
        dropped = dropped_now or dropped
    assert queue.pending == 20
    assert dropped is jobs[0]
    assert dropped.done.is_set()
    assert dropped.result.status_code == 429
    observed = []
    while current := queue.pop():
        observed.append(current.dispatch.command_id)
    assert observed == [str(index) for index in range(1, 21)]


def test_transient_failure_retries_once_then_succeeds(tmp_path, monkeypatch):
    _write_configuration(tmp_path, monkeypatch)
    driver = FakeDriver([ir.IRTransientError(), None])
    monkeypatch.setitem(ir.DRIVERS, "test_driver", driver)

    response = ir.execute_command("living-room-device", "power_on", timeout=0.75)

    assert response["ok"] is True
    assert response["attempts"] == 2
    assert len(driver.calls) == 2
    assert all(command.timeout == 0.75 for command in driver.calls)
    assert ir._RUNTIME["living-room-device"]["retry_count"] == 1


def test_timeout_is_bounded_to_two_attempts(tmp_path, monkeypatch):
    _write_configuration(tmp_path, monkeypatch)
    driver = FakeDriver([TimeoutError(), TimeoutError(), None])
    monkeypatch.setitem(ir.DRIVERS, "test_driver", driver)

    response = ir.execute_command("living-room-device", "power_on", timeout=0.25)

    assert response.status_code == 504
    assert len(driver.calls) == 2
    assert all(command.timeout == 0.25 for command in driver.calls)


def test_unknown_command_missing_profile_and_unsupported_capability(tmp_path, monkeypatch):
    registry, _ = _write_configuration(tmp_path, monkeypatch)
    driver = FakeDriver()
    monkeypatch.setitem(ir.DRIVERS, "test_driver", driver)
    assert ir.execute_command("living-room-device", "not_real").status_code == 422

    payload = json.loads(registry.read_text(encoding="utf-8"))
    payload["devices"][0]["capabilities"] = ["mute"]
    registry.write_text(json.dumps(payload), encoding="utf-8")
    response = ir.execute_command("living-room-device", "power_on")
    assert response.status_code == 422
    assert b"ir_capability_unsupported" in response.body

    payload["devices"][0]["profile"] = "missing"
    registry.write_text(json.dumps(payload), encoding="utf-8")
    assert ir.execute_command("living-room-device", "power_on").status_code == 422


@pytest.mark.parametrize(
    ("mutate", "error"),
    (
        (lambda value: value.pop("schema_version"), "missing_schema_version"),
        (lambda value: value.update(schema_version=2), "unknown_profile_schema"),
        (
            lambda value: value["capabilities"].append(dict(value["capabilities"][0])),
            "duplicate_capability_id",
        ),
        (
            lambda value: value["capabilities"][0].update(type="telepathy"),
            "unknown_capability_type",
        ),
        (
            lambda value: value["capabilities"][1].update(min=30, max=16),
            "invalid_capability_range",
        ),
    ),
)
def test_profile_validation_is_descriptive(tmp_path, mutate, error):
    payload = _profile_payload()
    mutate(payload)
    (tmp_path / "test_profile.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ir.IRConfigurationError, match=error):
        ir.load_profile("test_profile", tmp_path)


def test_duplicate_command_json_key_is_rejected(tmp_path):
    raw = json.dumps(_profile_payload())
    duplicate = raw.replace(
        '"commands": {',
        '"commands": {"power_on":{"capability":"power","code":"one"},'
        '"power_on":{"capability":"power","code":"two"},',
        1,
    )
    (tmp_path / "test_profile.json").write_text(duplicate, encoding="utf-8")
    with pytest.raises(ir.IRConfigurationError, match="duplicate_json_key:power_on"):
        ir.load_profile("test_profile", tmp_path)


def test_learning_lifecycle_is_interface_only():
    driver = FakeDriver()
    for method, args in (
        (driver.learn, (1,)),
        (driver.save, ("name", "code")),
        (driver.delete, ("name",)),
        (driver.rename, ("old", "new")),
    ):
        with pytest.raises(NotImplementedError, match="ir_learning_not_implemented"):
            method(*args)


def test_ir_write_route_requires_authentication_and_csrf(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "ir-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "ir-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/api/ir/device/command", json={"command": "power_on"}).status_code == 401
    assert client.post(
        "/api/auth/login", json={"username": "ir-test", "password": "password"}
    ).status_code == 200
    assert client.post("/api/ir/device/command", json={"command": "power_on"}).status_code == 403


def test_frontend_renders_widgets_from_metadata_only():
    source = Path("frontend/assets/dashboard_household_devices.js").read_text(encoding="utf-8")
    assert "device.capabilities?.ir" in source
    assert "capability.type === 'select'" in source
    assert "capability.type === 'range'" in source
    assert "capability.min" in source and "capability.values" in source
    assert "capability.group" in source and "capability.confirm" in source
    assert "/api/ir/" in source
    for forbidden in ("Netflix", "HDMI1", "Samsung", "Air Conditioner", "Volume +"):
        assert forbidden not in source
