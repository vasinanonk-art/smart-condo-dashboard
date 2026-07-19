"""Regression tests for pywebostv 0.8.9 registration state handling."""
from pathlib import Path

from pywebostv.connection import WebOSClient

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "lg_tv_pairing.py").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")


def test_real_pywebostv_089_registration_constants():
    assert WebOSClient.PROMPTED == 1
    assert WebOSClient.REGISTERED == 2


def test_pairing_uses_webosclient_constants_not_removed_model_enum():
    register = SOURCE.split("def _webos_register", 1)[1].split("def _validate_key", 1)[0]
    assert "WebOSClient.PROMPTED" in register
    assert "WebOSClient.REGISTERED" in register
    assert "from pywebostv.model import Registration" not in SOURCE
    assert "Registration.PROMPTED" not in SOURCE
    assert "Registration.REGISTERED" not in SOURCE


def test_runtime_dependency_is_pinned_and_verified_in_dashboard_venv():
    assert "pywebostv==0.8.9" in REQUIREMENTS
    assert 'pip install -r "$APP_RUN/backend/requirements.txt"' in INSTALL
    assert "WebOSClient.PROMPTED == 1" in INSTALL
    assert "WebOSClient.REGISTERED == 2" in INSTALL


def test_successful_validation_clears_connection_error():
    validate = SOURCE.split("def _validate_key", 1)[1].split("def _pair_worker", 1)[0]
    assert 'valid = _webos_register(store) == "registered"' in validate
    assert '_RUNTIME["last_connection_error"] = None' in validate
    assert '_RUNTIME["last_error"] = None' in validate


def test_status_uses_valid_stored_key_without_exposing_it():
    status = SOURCE.split("def pairing_status", 1)[1].split('@app.post("/api/lg-tv/pairing/request")', 1)[0]
    assert "paired = bool(key and _validate_key(key))" in status
    assert '"paired": paired' in status
    assert '"client_key"' not in status


def test_key_security_rollback_and_lg_commands_remain_untouched():
    assert "0o700" in SOURCE and "0o600" in SOURCE
    assert "os.replace" in SOURCE and "os.fsync" in SOURCE
    assert "_restore_key(backup, previous)" in SOURCE
    lowered = SOURCE.lower()
    for forbidden in ("mqtt_state_topic", "power_on", "volume_up", "hdmi1"):
        assert forbidden not in lowered
