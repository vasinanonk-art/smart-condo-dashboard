import json
import subprocess
from pathlib import Path

import pytest

from backend.camera_inventory_schema import CameraConfigError, validate_camera_config


ROOT = Path(__file__).parents[1]
TEMPLATE = ROOT / "recovery/templates/root/.smart-condo-dashboard/cameras.local.json.example"


def _camera(**updates):
    value = {
        "id": "camera-one", "display_name": "Camera One", "room": "unknown",
        "vendor": None, "model": None, "host": None, "enabled": False,
        "provider": "auto", "rtsp_port": None, "onvif_port": None,
        "stream_path": None, "credentials": None,
        "declared_capabilities": [], "verification_status": "unverified",
    }
    value.update(updates)
    return value


def test_migration_template_contains_known_inventory_without_invented_values():
    payload = json.loads(TEMPLATE.read_text())
    validated = validate_camera_config(payload, placeholder_mode=True)
    assert [item["display_name"] for item in validated["cameras"]] == [
        "Bedroom Camera", "Living Room Camera",
    ]
    tapo, xiaomi = validated["cameras"]
    assert tapo["model"] == "C200"
    assert tapo["provider"] == "onvif"
    assert tapo["onvif_port"] == 2020
    assert tapo["credentials"] == {
        "password_env": "TAPO_C200_PASSWORD",
        "username_env": "TAPO_C200_USERNAME",
    }
    assert xiaomi["model"] == "chuangmi.camera.ipc019"
    assert xiaomi["provider"] == "auto"
    assert xiaomi["credentials"] is None
    assert all(item["enabled"] is True for item in validated["cameras"])
    assert all(item["declared_capabilities"] == [] for item in validated["cameras"])


def test_literal_credentials_and_urls_are_rejected():
    for credentials in (
        {"username": "literal", "password": "literal"},
        {"username_env": "literal-user", "password_env": "literal-password"},
    ):
        with pytest.raises(CameraConfigError):
            validate_camera_config({"schema_version": 1, "cameras": [_camera(credentials=credentials)]})
    with pytest.raises(CameraConfigError):
        validate_camera_config({"schema_version": 1, "cameras": [_camera(host="rtsp://user:pass@host/live")]})


def test_host_path_ports_capabilities_and_extra_fields_are_strict():
    invalid = (
        _camera(host="host/path"),
        _camera(stream_path="relative"),
        _camera(rtsp_port=0),
        _camera(onvif_port=70000),
        _camera(declared_capabilities=["invented"]),
        {**_camera(), "password": "forbidden"},
    )
    for camera in invalid:
        with pytest.raises(CameraConfigError):
            validate_camera_config({"schema_version": 1, "cameras": [camera]})


def test_duplicate_ids_and_malformed_root_are_rejected():
    with pytest.raises(CameraConfigError):
        validate_camera_config({"schema_version": 1, "cameras": [_camera(), _camera()]})
    with pytest.raises(CameraConfigError):
        validate_camera_config({"cameras": []})


def test_valid_secure_reference_config_is_normalized():
    payload = {"schema_version": 1, "cameras": [_camera(
        host="camera.local", enabled=True, provider="rtsp", rtsp_port=554,
        stream_path="/stream1",
        credentials={"username_env": "CAMERA_ONE_USERNAME", "password_env": "CAMERA_ONE_PASSWORD"},
        declared_capabilities=["live_stream", "snapshot"], verification_status="verified",
    )]}
    result = validate_camera_config(payload)
    assert result["cameras"][0]["host"] == "camera.local"
    assert result["cameras"][0]["declared_capabilities"] == ["live_stream", "snapshot"]


def test_validator_never_prints_file_contents_or_secret_values(tmp_path):
    path = tmp_path / "camera.json"
    secret = "must-not-appear"
    path.write_text(json.dumps({"schema_version": 1, "cameras": [_camera(password=secret)]}))
    result = subprocess.run(
        [str(ROOT / "scripts/validate_camera_config.py"), str(path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 1
    assert secret not in result.stdout + result.stderr


def test_placeholder_validator_accepts_template():
    result = subprocess.run(
        [str(ROOT / "scripts/validate_camera_config.py"), "--placeholder-mode", str(TEMPLATE)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0
    assert "2 camera entries" in result.stdout


def test_existing_recovery_validator_accepts_secure_template():
    result = subprocess.run(
        [str(ROOT / "recovery/validate_recovery_config.py"), "--placeholder-mode"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "no configuration values were printed" in result.stdout
