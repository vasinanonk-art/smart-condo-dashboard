import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import climate_control as control


class FakeDevice(control.ClimateDevice):
    sent = None
    feedback_value = None

    def send(self, command):
        self.sent = dict(command)

    def read_feedback(self):
        return self.feedback_value


def device(**kwargs):
    result = FakeDevice(
        id="climate-1", name="AC", provider="test", controllable=True,
        modes=("cool", "dry", "fan", "auto"), fans=("auto", "low", "medium", "high"),
        temperature_min=16, temperature_max=30, swing_supported=True,
    )
    for key, value in kwargs.items():
        setattr(result, key, value)
    return result


def test_production_provider_detection_is_explicitly_unsupported(monkeypatch):
    monkeypatch.setattr(control.tapo_ir_local_bridge, "local_tapo_ir_status", lambda: {
        "configured": True, "supported_actions": [],
        "diagnostics": {"local_control_supported": False},
    })
    detected = control.detect_devices()
    assert detected[0].provider == "tapo_local"
    assert detected[0].controllable is False
    assert "not_supported" in detected[0].reason


def test_unsupported_provider_fails_safely(monkeypatch):
    unsupported = control.ClimateDevice("climate-1", "IR", "unknown", False, reason="unsupported")
    monkeypatch.setattr(control, "detect_devices", lambda: [unsupported])
    result = control.climate_command("climate-1", control.ClimateCommand(power=True))
    assert result.status_code == 422


def test_valid_command_persists_assumed_state(monkeypatch, tmp_path):
    current = device()
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    monkeypatch.setattr(control, "STATE_PATH", tmp_path / "state.json")
    control._STATE.clear()
    result = control.climate_command(
        "climate-1",
        control.ClimateCommand(power=True, mode="cool", temperature=24, fan="auto", swing=True),
    )
    assert result["state_confidence"] == "assumed"
    assert result["state"]["temperature"] == 24
    assert current.sent["mode"] == "cool"
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600


def test_confirmed_state_when_feedback_exists(monkeypatch, tmp_path):
    current = device(feedback=True)
    current.feedback_value = {"power": True, "temperature": 23}
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    monkeypatch.setattr(control, "STATE_PATH", tmp_path / "state.json")
    result = control.climate_command("climate-1", control.ClimateCommand(power=True))
    assert result["state_confidence"] == "confirmed"
    assert result["state"]["temperature"] == 23


def test_invalid_temperature_and_mode(monkeypatch):
    current = device()
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    assert control.climate_command("climate-1", control.ClimateCommand(temperature=31)).status_code == 422
    assert control.climate_command("climate-1", control.ClimateCommand(mode="heat")).status_code == 422


def test_command_timeout_does_not_assume_state(monkeypatch):
    current = device()
    current.send = lambda command: (_ for _ in ()).throw(TimeoutError())
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    result = control.climate_command("climate-1", control.ClimateCommand(power=True))
    assert result.status_code == 504
    assert b'"state_confidence":"unknown"' in result.body


def test_climate_payload_contains_no_secret_or_ir_code(monkeypatch):
    current = device()
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    rendered = repr(control.climate_devices()).lower()
    for forbidden in ("password", "token", "secret", "ir_code", "deviceid", "client_key"):
        assert forbidden not in rendered


def test_feedback_is_allowlisted_and_frontend_labels_assumed_state(monkeypatch):
    current = device(feedback=True)
    current.feedback_value = {"power": True, "password": "must-not-leak", "ir_code": "must-not-leak"}
    monkeypatch.setattr(control, "detect_devices", lambda: [current])
    rendered = repr(control.climate_devices())
    assert "must-not-leak" not in rendered
    source = (control.app_module.FRONTEND_DIR + "/assets/dashboard_v3.js")
    text = __import__("pathlib").Path(source).read_text(encoding="utf-8")
    assert "last command, not device feedback" in text


def test_climate_write_requires_authentication_and_csrf(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "climate-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "climate-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    assert client.post("/api/climate/climate-1/command", json={"power": True}).status_code == 401
    login = client.post("/api/auth/login", json={"username": "climate-test", "password": "password"})
    assert login.status_code == 200
    assert client.post("/api/climate/climate-1/command", json={"power": True}).status_code == 403
