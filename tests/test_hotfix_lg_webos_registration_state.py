"""Regression tests for pywebostv 0.8.9 LG registration handling."""
from pathlib import Path

from pywebostv.connection import WebOSClient

ROOT = Path(__file__).resolve().parents[1]
PAIRING = (ROOT / "backend" / "lg_tv_pairing.py").read_text(encoding="utf-8")
HOTFIX = (ROOT / "backend" / "lg_tv_pairing_hotfix.py").read_text(encoding="utf-8")
REQUIREMENTS = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")
INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
ENTRY = (ROOT / "backend" / "app_entry.py").read_text(encoding="utf-8")


def test_real_pywebostv_089_registration_constants():
    assert WebOSClient.PROMPTED == 1
    assert WebOSClient.REGISTERED == 2


def test_runtime_uses_webosclient_constants_not_registration_model():
    combined = PAIRING + HOTFIX
    assert "from pywebostv.model import Registration" not in combined
    assert "WebOSClient.PROMPTED" in HOTFIX
    assert "WebOSClient.REGISTERED" in HOTFIX


def test_dependency_is_pinned_and_installed_in_runtime_venv():
    assert "pywebostv==0.8.9" in REQUIREMENTS
    assert 'pip install -r "$APP_RUN/backend/requirements.txt"' in INSTALL
    assert "WebOSClient.PROMPTED == 1" in INSTALL
    assert "WebOSClient.REGISTERED == 2" in INSTALL


def test_hotfix_loads_after_pairing_module():
    assert ENTRY.index("lg_tv_pairing as") < ENTRY.index("lg_tv_pairing_hotfix")


def test_successful_validation_clears_connection_error_and_status_uses_validator():
    validation = HOTFIX.split("def _validate_key_089", 1)[1]
    assert 'pairing._RUNTIME["last_connection_error"] = None' in validation
    assert 'pairing._RUNTIME["last_error"] = None' in validation
    status = PAIRING.split("def pairing_status", 1)[1].split("@app.post", 1)[0]
    assert "paired = bool(key and _validate_key(key))" in status


def test_key_security_and_existing_integration_contracts_remain_untouched():
    assert "pending_key" not in PAIRING.split("def _job_public", 1)[1].split("@app.on_event", 1)[0]
    assert "/root/.smart-condo-dashboard/secrets/lg_tv_client_key" in PAIRING
    lowered = HOTFIX.lower()
    for forbidden in ("mqtt", "sonoff", "presence", "tariff", "camera", "tapo"):
        assert forbidden not in lowered
