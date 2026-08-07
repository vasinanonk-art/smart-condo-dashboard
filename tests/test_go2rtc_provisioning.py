import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from scripts import render_go2rtc_config as renderer

ROOT = Path(__file__).resolve().parents[1]
PROVISIONER = ROOT / "scripts" / "provision_go2rtc.sh"
UNIT = ROOT / "systemd" / "smart-condo-go2rtc.service"
OFFICIAL_SHA = "4d7e1639af5a2722a28e864468fd8099b3c1682565446c798bf9e3b38fde12e4"


def _camera(path: Path):
    path.write_text(json.dumps({"schema_version": 1, "cameras": [{
        "id": "tapo-c220", "display_name": "Bedroom Camera", "room": "bed_room",
        "vendor": "TP-Link Tapo", "model": "C200", "host": "192.168.1.43",
        "enabled": True, "provider": "onvif", "rtsp_port": 554, "onvif_port": 2020,
        "stream_path": None,
        "credentials": {"username_env": "TAPO_C200_USERNAME", "password_env": "TAPO_C200_PASSWORD"},
        "declared_capabilities": ["live_stream", "snapshot"], "verification_status": "verified",
    }]}), encoding="utf-8")


def _fixture(tmp_path: Path, artifact: bytes = b"official-binary"):
    camera = tmp_path / "cameras.json"; _camera(camera)
    environment = tmp_path / "dashboard.env"
    environment.write_text(
        f"CAMERA_CONFIG_FILE={camera}\nTAPO_C200_USERNAME=camera user\nTAPO_C200_PASSWORD=camera-secret\n",
        encoding="utf-8",
    )
    binary, config, unit = tmp_path / "managed/go2rtc", tmp_path / "private/go2rtc.yaml", tmp_path / "systemd/go2rtc.service"
    fake_systemctl, log = tmp_path / "systemctl", tmp_path / "systemctl.log"
    fake_systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\ncase "$1" in is-active|is-enabled) exit 0;; esac\nexit 0\n',
        encoding="utf-8",
    ); fake_systemctl.chmod(0o755)
    artifact_file = tmp_path / "artifact"; artifact_file.write_bytes(artifact)
    fake_renderer = tmp_path / "renderer.py"
    fake_renderer.write_text(
        "import pathlib,sys\nif '--validate-only' not in sys.argv:\n"
        " p=pathlib.Path(sys.argv[sys.argv.index('--output')+1]);p.write_text('api:\\n  listen: \\\"127.0.0.1:1984\\\"\\nrtsp:\\n  listen: \\\"127.0.0.1:8554\\\"\\nstreams:\\n  tapo_c200_main: [\\\"redacted-source\\\"]\\n')\n",
        encoding="utf-8",
    )
    env = {**os.environ, "GO2RTC_ARCH_OVERRIDE": "armv7l", "GO2RTC_BINARY": str(binary),
           "GO2RTC_CONFIG": str(config), "GO2RTC_UNIT": str(unit), "GO2RTC_SYSTEMCTL": str(fake_systemctl),
           "GO2RTC_ENV_FILE": str(environment), "GO2RTC_CAMERA_CONFIG": str(camera),
           "GO2RTC_RENDERER": str(fake_renderer), "GO2RTC_UNIT_SOURCE": str(UNIT),
           "GO2RTC_ARTIFACT_FILE": str(artifact_file)}
    return env, binary, config, unit, log, artifact_file


def _run(env, *args, script=PROVISIONER):
    return subprocess.run(["sh", str(script), *args], env=env, text=True, capture_output=True)


def test_official_pin_and_armv7_artifact_selection(tmp_path):
    source = PROVISIONER.read_text(encoding="utf-8")
    assert "GO2RTC_VERSION=1.9.14" in source
    assert "GO2RTC_ARTIFACT=go2rtc_linux_arm" in source
    assert f"GO2RTC_SHA256={OFFICIAL_SHA}" in source
    env, *_ = _fixture(tmp_path)
    assert _run(env, "dry-run").returncode == 0
    env["GO2RTC_ARCH_OVERRIDE"] = "aarch64"
    assert _run(env, "dry-run").returncode != 0


def test_checksum_failure_replaces_nothing(tmp_path):
    env, binary, config, unit, _, artifact = _fixture(tmp_path)
    binary.parent.mkdir(); binary.write_bytes(b"previous"); artifact.write_bytes(b"wrong")
    result = _run(env, "provision")
    assert result.returncode != 0
    assert binary.read_bytes() == b"previous"
    assert not config.exists() and not unit.exists()


def test_renderer_uses_loopback_and_root_only_file(monkeypatch, tmp_path):
    camera = tmp_path / "cameras.json"; _camera(camera)
    environment = tmp_path / "dashboard.env"
    environment.write_text(f"CAMERA_CONFIG_FILE={camera}\nTAPO_C200_USERNAME=user name\nTAPO_C200_PASSWORD=secret\n")
    monkeypatch.setattr(renderer, "bounded_main_uri", lambda *_: "rtsp://safe-source")
    output = tmp_path / "private/go2rtc.yaml"
    monkeypatch.setattr(os.sys, "argv", ["render", "--environment-file", str(environment), "--output", str(output)])
    assert renderer.main() == 0
    rendered = output.read_text()
    assert 'listen: "127.0.0.1:1984"' in rendered and 'listen: "127.0.0.1:8554"' in rendered
    assert 'listen: ""' in rendered
    assert output.stat().st_mode & 0o777 == 0o600
    assert output.parent.stat().st_mode & 0o777 == 0o700


def test_environment_parser_never_executes_secret_value(tmp_path):
    marker, environment = tmp_path / "executed", tmp_path / "dashboard.env"
    environment.write_text(f"TAPO_C200_PASSWORD=$(touch {marker})\n")
    values = renderer.read_environment_file(environment)
    assert values["TAPO_C200_PASSWORD"].startswith("$(touch ")
    assert not marker.exists()


def test_idempotent_reinstall_does_not_restart(tmp_path):
    payload = b"official-binary"
    env, binary, config, unit, log, _ = _fixture(tmp_path, payload)
    patched = tmp_path / "provision.sh"
    patched.write_text(PROVISIONER.read_text().replace(OFFICIAL_SHA, hashlib.sha256(payload).hexdigest()))
    first = _run(env, "provision", script=patched)
    assert first.returncode == 0, first.stderr
    hashes = tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (binary, config, unit))
    log.write_text("")
    second = _run(env, "provision", script=patched)
    assert second.returncode == 0, second.stderr
    assert hashes == tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (binary, config, unit))
    assert "restart" not in log.read_text()


def test_backup_restore_recovers_files_and_state(tmp_path):
    env, binary, config, unit, log, _ = _fixture(tmp_path)
    for path, value in ((binary, b"old-bin"), (config, b"old-config"), (unit, b"old-unit")):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
    backup = tmp_path / "backup"
    assert _run(env, "backup", str(backup)).returncode == 0
    binary.write_bytes(b"new"); config.write_bytes(b"new"); unit.write_bytes(b"new")
    assert _run(env, "restore", str(backup)).returncode == 0
    assert (binary.read_bytes(), config.read_bytes(), unit.read_bytes()) == (b"old-bin", b"old-config", b"old-unit")
    assert "daemon-reload" in log.read_text()


def test_runtime_deployment_rolls_back_go2rtc_and_dashboard_on_failure(tmp_path):
    payload = b"test-arm-binary"
    expected = hashlib.sha256(payload).hexdigest()
    source, run = tmp_path / "source", tmp_path / "run"
    for directory in ("backend", "frontend/assets", "config", "scripts", "systemd"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "install.sh", source / "install.sh")
    shutil.copy2(ROOT / "scripts/runtime_config_guard.sh", source / "scripts/runtime_config_guard.sh")
    provision_source = PROVISIONER.read_text().replace(OFFICIAL_SHA, expected)
    (source / "scripts/provision_go2rtc.sh").write_text(provision_source)
    (source / "scripts/provision_go2rtc.sh").chmod(0o755)
    shutil.copy2(UNIT, source / "systemd/smart-condo-go2rtc.service")
    (source / "backend/version.txt").write_text("new-runtime")
    (source / "config/defaults.json").write_text("{}")
    (source / "frontend/index.html").write_text("<html></html>")
    for asset in ("dashboard_v3.css", "dashboard_v3_layout.css", "dashboard_upgrade.css",
                  "dashboard_polish.css", "dashboard_upgrade.js", "dashboard_v3.js", "dashboard_command_fixes.js"):
        (source / "frontend/assets" / asset).write_text("")
    (source / "sonoff_client.py").write_text("# source")
    (source / "VERSION").write_text("1.0.0\n")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)

    (run / "backend").mkdir(parents=True); (run / "backend/version.txt").write_text("old-runtime")
    (run / "config").mkdir(); (run / "venv/bin").mkdir(parents=True)
    (run / "venv/bin/python").symlink_to(sys.executable)
    binary, config, unit = tmp_path / "old/go2rtc", tmp_path / "old/go2rtc.yaml", tmp_path / "old/go2rtc.service"
    for path, content in ((binary, b"old-bin"), (config, b"old-config"), (unit, b"old-unit")):
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    artifact = tmp_path / "artifact"; artifact.write_bytes(payload)
    environment_file = tmp_path / "environment"; environment_file.write_text("SAFE=1\n")
    camera_file = tmp_path / "cameras.json"; camera_file.write_text("{}")
    renderer_script = tmp_path / "renderer.py"
    renderer_script.write_text(
        "import pathlib,sys\nif '--validate-only' not in sys.argv: pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text('safe')\n"
    )
    systemctl_log, fake_systemctl = tmp_path / "systemctl.log", tmp_path / "systemctl"
    fake_systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{systemctl_log}"\n'
        'case "$1:$2" in is-active:*|is-enabled:*) exit 0;; restart:smart-condo-go2rtc.service) exit 1;; esac\nexit 0\n'
    ); fake_systemctl.chmod(0o755)
    env = {**os.environ, "APP_SRC": str(source), "APP_RUN": str(run),
           "PERSISTENT_CONFIG_ROOT": str(tmp_path / "persistent"), "INSTALL_LOCK_FILE": str(tmp_path / "lock"),
           "GO2RTC_PROVISION_ENABLED": "1", "GO2RTC_ARCH_OVERRIDE": "armv7l",
           "GO2RTC_BINARY": str(binary), "GO2RTC_CONFIG": str(config), "GO2RTC_UNIT": str(unit),
           "GO2RTC_SYSTEMCTL": str(fake_systemctl), "GO2RTC_ENV_FILE": str(environment_file),
           "GO2RTC_CAMERA_CONFIG": str(camera_file), "GO2RTC_RENDERER": str(renderer_script),
           "GO2RTC_UNIT_SOURCE": str(source / "systemd/smart-condo-go2rtc.service"),
           "GO2RTC_ARTIFACT_FILE": str(artifact)}
    (tmp_path / "persistent").mkdir()
    result = subprocess.run(["sh", str(ROOT / "install.sh"), "--runtime-only"], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert (run / "backend/version.txt").read_text() == "old-runtime"
    assert (binary.read_bytes(), config.read_bytes(), unit.read_bytes()) == (b"old-bin", b"old-config", b"old-unit")
    assert "restoring the previous go2rtc installation" in result.stderr
    assert "restoring the previous managed runtime" in result.stderr


def test_no_secret_or_public_listener_in_managed_files():
    rendered = UNIT.read_text() + PROVISIONER.read_text()
    assert "TAPO_C200_PASSWORD=" not in rendered and "rtsp://" not in rendered
    assert "0.0.0.0:1984" not in rendered and "0.0.0.0:8554" not in rendered
    assert "-config /etc/smart-condo-dashboard/go2rtc.yaml" in rendered
