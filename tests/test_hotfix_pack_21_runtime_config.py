import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import bcrypt
from fastapi.testclient import TestClient

from backend import app as app_module
from backend import dashboard_auth as auth
from backend import sonoff_client
from backend.app_entry import app


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
GUARD = ROOT / "scripts" / "runtime_config_guard.sh"


def _auth_env():
    return {
        "DASHBOARD_AUTH_USERNAME": "hotfix21",
        "DASHBOARD_AUTH_PASSWORD_HASH": bcrypt.hashpw(b"runtime-config-test", bcrypt.gensalt(rounds=4)).decode(),
        "DASHBOARD_SESSION_SECRET": "hotfix21-runtime-config-session-secret",
    }


def _authenticated_client(monkeypatch):
    for key, value in _auth_env().items():
        monkeypatch.setenv(key, value)
    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/api/auth/login",
        json={"username": "hotfix21", "password": "runtime-config-test"},
    )
    assert response.status_code == 200
    return client


def _install_env(source, run_root, persistent, lock_file):
    return {
        **os.environ,
        "APP_SRC": str(source),
        "APP_RUN": str(run_root),
        "PERSISTENT_CONFIG_ROOT": str(persistent),
        "INSTALL_LOCK_FILE": str(lock_file),
    }


def _flock_available(lock_file):
    return subprocess.run(
        ["flock", "-n", str(lock_file), "true"],
        check=False,
        capture_output=True,
    ).returncode == 0


def test_environment_paths_take_precedence(monkeypatch, tmp_path):
    camera = tmp_path / "camera-env.json"
    sonoff = tmp_path / "sonoff-env.json"
    camera.write_text('{"cameras": []}', encoding="utf-8")
    sonoff.write_text('{"region": "as"}', encoding="utf-8")
    monkeypatch.setenv("CAMERA_CONFIG_FILE", str(camera))
    monkeypatch.setenv("EWELINK_CONFIG_FILE", str(sonoff))

    assert app_module.camera_config_paths()[0] == str(camera)
    assert app_module.camera_config_payload()["path"] == str(camera)
    assert sonoff_client.config_paths()[0] == str(sonoff)
    assert sonoff_client.config_payload()["path"] == str(sonoff)


def test_legacy_fallback_paths_remain_compatible(monkeypatch, tmp_path):
    camera = tmp_path / "cameras.local.json"
    sonoff = tmp_path / "ewelink.local.json"
    camera.write_text('[{"name": "Camera"}]', encoding="utf-8")
    sonoff.write_text('{"region": "as"}', encoding="utf-8")
    monkeypatch.delenv("CAMERA_CONFIG_FILE", raising=False)
    monkeypatch.delenv("EWELINK_CONFIG_FILE", raising=False)
    monkeypatch.setattr(app_module, "CAMERA_CONFIG_PATHS", [str(camera)])
    monkeypatch.setattr(sonoff_client, "CONFIG_PATHS", [str(sonoff)])

    camera_payload = app_module.load_camera_config()
    sonoff_payload = sonoff_client.config_payload()

    assert camera_payload["loaded"] is True
    assert len(camera_payload["cameras"]) == 1
    assert sonoff_payload["loaded"] is True
    assert sonoff_payload["config"]["region"] == "as"


def test_dry_run_preserves_all_local_json_generically(tmp_path):
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    config = run_root / "config"
    config.mkdir(parents=True)
    persistent.mkdir()
    for name in ("cameras.local.json", "ewelink.local.json", "future-provider.local.json"):
        (config / name).write_text("{}", encoding="utf-8")

    result = subprocess.run(
        ["sh", str(INSTALL), "--dry-run"],
        cwd=ROOT,
        env=_install_env(ROOT, run_root, persistent, tmp_path / "install.lock"),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    for name in ("cameras.local.json", "ewelink.local.json", "future-provider.local.json"):
        assert f"Preserved local config: config/{name}" in result.stdout
        assert (config / name).is_file()
    assert "without rsync --delete" in result.stdout
    assert "local configuration preservation verified" in result.stdout


def test_runtime_only_deployment_updates_managed_tree_without_touching_venv(tmp_path):
    source = tmp_path / "source"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    fake_bin = tmp_path / "bin"
    for directory in ("backend", "frontend", "config", "scripts"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (run_root / "config").mkdir(parents=True)
    (run_root / "backend").mkdir()
    (run_root / "venv").mkdir()
    persistent.mkdir()
    fake_bin.mkdir()
    shutil.copy2(GUARD, source / "scripts" / GUARD.name)
    (source / "backend" / "version.txt").write_text("repository-version", encoding="utf-8")
    (source / "config" / "defaults.json").write_text("{}", encoding="utf-8")
    (source / "frontend" / "index.html").write_text("<html></html>", encoding="utf-8")
    for asset in (
        "dashboard_v3.css", "dashboard_v3_layout.css", "dashboard_upgrade.css",
        "dashboard_polish.css", "dashboard_upgrade.js", "dashboard_v3.js",
        "dashboard_command_fixes.js",
    ):
        (source / "frontend" / "assets").mkdir(exist_ok=True)
        (source / "frontend" / "assets" / asset).write_text("", encoding="utf-8")
    (source / "sonoff_client.py").write_text("# mirror", encoding="utf-8")
    (source / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "deployment-test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Deployment Test"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "source snapshot"], cwd=source, check=True)
    (source / "backend" / "version.txt").write_text("dirty-working-tree", encoding="utf-8")
    source_status_before = subprocess.check_output(
        ["git", "status", "--short"], cwd=source, text=True,
    )
    (run_root / "backend" / "version.txt").write_text("stale-runtime", encoding="utf-8")
    venv_marker = run_root / "venv" / "preserve-me"
    venv_marker.write_text("unchanged", encoding="utf-8")
    local_config = run_root / "config" / "camera room.local.json"
    local_config.write_text('{"preserve": true}', encoding="utf-8")
    systemctl_log = tmp_path / "systemctl.log"
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{systemctl_log}"\n',
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    environment = _install_env(
        source, run_root, persistent, tmp_path / "install.lock",
    )
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(INSTALL), "--runtime-only"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (run_root / "backend" / "version.txt").read_text() == "repository-version"
    assert venv_marker.read_text() == "unchanged"
    assert local_config.read_text() == '{"preserve": true}'
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=source, text=True,
    ) == source_status_before
    assert "virtual environment and dependencies were not modified" in result.stdout
    assert "isolated snapshot of Git HEAD" in result.stdout
    assert "restart smart-condo-dashboard" in systemctl_log.read_text()

    second = subprocess.run(
        ["sh", str(INSTALL), "--runtime-only"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=source, text=True,
    ) == source_status_before
    assert venv_marker.read_text() == "unchanged"
    assert local_config.read_text() == '{"preserve": true}'


def test_deploy_guard_fails_when_preserved_config_is_missing(tmp_path):
    run_root = tmp_path / "run"
    config = run_root / "config"
    config.mkdir(parents=True)
    manifest = tmp_path / "manifest"
    manifest.write_text(f"{hashlib.sha256(b'original').hexdigest()}\tprovider.local.json\n", encoding="utf-8")

    result = subprocess.run(
        [
            "sh",
            "-c",
            '. "$1"; verify_preserved_configs "$2" "$3"',
            "guard-test",
            str(GUARD),
            str(run_root),
            str(manifest),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "preserved local config is missing" in result.stderr


def test_failed_deploy_restores_local_config_before_exit(tmp_path):
    source = tmp_path / "source"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    (source / "scripts").mkdir(parents=True)
    (run_root / "config").mkdir(parents=True)
    persistent.mkdir()
    shutil.copy2(GUARD, source / "scripts" / GUARD.name)
    local_config = run_root / "config" / "provider.local.json"
    local_config.write_text('{"preserve": true}', encoding="utf-8")

    result = subprocess.run(
        ["sh", str(INSTALL)],
        cwd=ROOT,
        env=_install_env(source, run_root, persistent, tmp_path / "install.lock"),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Deployment exited early; restoring preserved local configuration." in result.stderr
    assert local_config.read_text(encoding="utf-8") == '{"preserve": true}'


def test_concurrent_deployment_fails_before_touching_runtime(tmp_path):
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    config = run_root / "config"
    config.mkdir(parents=True)
    persistent.mkdir()
    local_config = config / "camera room.local.json"
    original = b'{"preserve": true}'
    local_config.write_bytes(original)
    lock_file = tmp_path / "install.lock"
    holder = subprocess.Popen(
        ["flock", "-n", str(lock_file), "sh", "-c", "printf locked; sleep 30"],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.read(6) == "locked"
        result = subprocess.run(
            ["sh", str(INSTALL), "--dry-run"],
            cwd=ROOT,
            env=_install_env(ROOT, run_root, persistent, lock_file),
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        holder.terminate()
        holder.wait(timeout=5)

    assert result.returncode != 0
    assert "another smart-condo-dashboard deployment is active" in result.stderr
    assert "Preserved local config" not in result.stdout
    assert local_config.read_bytes() == original
    assert sorted(path.name for path in config.iterdir()) == [local_config.name]


def test_deployment_lock_released_after_dry_run_success(tmp_path):
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    (run_root / "config").mkdir(parents=True)
    persistent.mkdir()
    lock_file = tmp_path / "install.lock"

    result = subprocess.run(
        ["sh", str(INSTALL), "--dry-run"],
        cwd=ROOT,
        env=_install_env(ROOT, run_root, persistent, lock_file),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Deployment lock acquired." in result.stdout
    assert _flock_available(lock_file)


def test_deployment_lock_released_after_failure(tmp_path):
    source = tmp_path / "missing-source"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    run_root.mkdir()
    persistent.mkdir()
    lock_file = tmp_path / "install.lock"

    result = subprocess.run(
        ["sh", str(INSTALL), "--dry-run"],
        cwd=ROOT,
        env=_install_env(source, run_root, persistent, lock_file),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert _flock_available(lock_file)


def _run_guard(script, *args):
    return subprocess.run(
        ["sh", "-c", f'. "$1"; {script}', "guard-test", str(GUARD), *(str(arg) for arg in args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_checksum_manifest_handles_multiple_files_and_spaces(tmp_path):
    run_root = tmp_path / "run"
    backup = tmp_path / "backup"
    manifest = tmp_path / "manifest"
    config = run_root / "config"
    config.mkdir(parents=True)
    expected = {
        "camera room.local.json": b"camera-original",
        "ewelink.local.json": b"sonoff-original",
    }
    for name, content in expected.items():
        (config / name).write_bytes(content)

    preserve = _run_guard(
        'preserve_local_configs "$2" "$3" "$4"',
        run_root,
        backup,
        manifest,
    )
    assert preserve.returncode == 0, preserve.stderr
    assert len(manifest.read_text(encoding="utf-8").splitlines()) == 2

    shutil.rmtree(config)
    restore = _run_guard(
        'restore_local_configs "$2" "$3" "$4"; verify_preserved_configs "$2" "$4"',
        run_root,
        backup,
        manifest,
    )
    assert restore.returncode == 0, restore.stderr
    assert {name: (config / name).read_bytes() for name in expected} == expected


def test_interrupted_managed_runtime_can_be_restored_without_touching_venv(tmp_path):
    run_root = tmp_path / "run"
    backup = tmp_path / "rollback" / "files"
    manifest = tmp_path / "rollback" / "manifest"
    for directory in ("backend", "frontend", "config"):
        (run_root / directory).mkdir(parents=True, exist_ok=True)
        (run_root / directory / "old.txt").write_text(directory)
    (run_root / "venv").mkdir()
    (run_root / "venv" / "marker").write_text("preserved")
    (run_root / "sonoff_client.py").write_text("old mirror")

    preserve = _run_guard(
        'preserve_managed_runtime "$2" "$3" "$4"',
        run_root,
        backup,
        manifest,
    )
    assert preserve.returncode == 0, preserve.stderr

    shutil.rmtree(run_root / "backend")
    (run_root / "backend").mkdir()
    (run_root / "backend" / "partial.txt").write_text("partial deploy")
    (run_root / "scripts").mkdir()
    (run_root / "scripts" / "new.txt").write_text("must disappear")

    restore = _run_guard(
        'restore_managed_runtime "$2" "$3" "$4"',
        run_root,
        backup,
        manifest,
    )
    assert restore.returncode == 0, restore.stderr
    assert (run_root / "backend" / "old.txt").read_text() == "backend"
    assert not (run_root / "backend" / "partial.txt").exists()
    assert not (run_root / "scripts").exists()
    assert (run_root / "venv" / "marker").read_text() == "preserved"


def test_checksum_verification_rejects_altered_config(tmp_path):
    run_root = tmp_path / "run"
    backup = tmp_path / "backup"
    manifest = tmp_path / "manifest"
    config = run_root / "config"
    config.mkdir(parents=True)
    local_config = config / "provider.local.json"
    local_config.write_bytes(b"original")
    assert _run_guard('preserve_local_configs "$2" "$3" "$4"', run_root, backup, manifest).returncode == 0
    local_config.write_bytes(b"altered")

    result = _run_guard('verify_preserved_configs "$2" "$3"', run_root, manifest)

    assert result.returncode != 0
    assert "provider.local.json" in result.stderr
    assert "checksum mismatch" in result.stderr
    assert "original" not in result.stderr
    assert "altered" not in result.stderr


def test_checksum_verification_allows_extra_unrelated_config(tmp_path):
    run_root = tmp_path / "run"
    backup = tmp_path / "backup"
    manifest = tmp_path / "manifest"
    config = run_root / "config"
    config.mkdir(parents=True)
    (config / "provider.local.json").write_bytes(b"original")
    assert _run_guard('preserve_local_configs "$2" "$3" "$4"', run_root, backup, manifest).returncode == 0
    (config / "extra.local.json").write_bytes(b"extra")

    result = _run_guard('verify_preserved_configs "$2" "$3"', run_root, manifest)

    assert result.returncode == 0, result.stderr


def test_runtime_config_diagnostics_are_authenticated_and_secret_free(monkeypatch, tmp_path):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    camera = persistent / "cameras.local.json"
    sonoff = persistent / "ewelink.local.json"
    settings = persistent / "settings.json"
    camera.write_text(
        '{"cameras":[{"name":"Private","rtsp":"rtsp://user:camera-secret@example.invalid/live"}]}',
        encoding="utf-8",
    )
    sonoff.write_text(
        '{"email":"private@example.invalid","password":"sonoff-secret","at":"token-secret"}',
        encoding="utf-8",
    )
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SMART_CONDO_DATA_DIR", str(persistent))
    monkeypatch.setenv("CAMERA_CONFIG_FILE", str(camera))
    monkeypatch.setenv("EWELINK_CONFIG_FILE", str(sonoff))
    for key, value in _auth_env().items():
        monkeypatch.setenv(key, value)

    unauthenticated = TestClient(app).get("/api/health")
    assert unauthenticated.status_code == 401
    assert unauthenticated.json() == {"detail": "authentication required"}

    response = _authenticated_client(monkeypatch).get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["camera_config_present"] is True
    assert payload["sonoff_config_present"] is True
    assert payload["settings_present"] is True
    assert payload["local_config_files_count"] == 2
    for forbidden_field in ("runtime_config_root", "camera_config_path", "sonoff_config_path"):
        assert forbidden_field not in payload
    rendered = repr(payload)
    for secret in (
        "camera-secret",
        "sonoff-secret",
        "token-secret",
        "private@example.invalid",
        "rtsp://",
        str(persistent),
        str(camera),
        str(sonoff),
    ):
        assert secret not in rendered


def test_startup_log_reports_presence_without_paths_or_contents(monkeypatch, tmp_path, capsys):
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    camera = persistent / "cameras.local.json"
    sonoff = persistent / "ewelink.local.json"
    settings = persistent / "settings.json"
    camera.write_text('{"cameras": []}', encoding="utf-8")
    sonoff.write_text('{"region": "as"}', encoding="utf-8")
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("SMART_CONDO_DATA_DIR", str(persistent))
    monkeypatch.setenv("CAMERA_CONFIG_FILE", str(camera))
    monkeypatch.setenv("EWELINK_CONFIG_FILE", str(sonoff))

    app_module.log_runtime_config_startup()
    output = capsys.readouterr().out

    assert output.splitlines() == [
        "Runtime config:",
        "Camera: found",
        "Sonoff: found",
        "Settings: found",
    ]
    assert str(persistent) not in output
