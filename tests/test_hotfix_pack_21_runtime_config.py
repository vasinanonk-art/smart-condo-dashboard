import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import bcrypt
import pytest
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
    environment = {
        **os.environ,
        "GO2RTC_PROVISION_ENABLED": "0",
        "APP_SRC": str(source),
        "APP_RUN": str(run_root),
        "PERSISTENT_CONFIG_ROOT": str(persistent),
        "INSTALL_LOCK_FILE": str(lock_file),
    }
    if (Path(source) / ".git").exists():
        environment["RELEASE_COMMIT"] = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True,
        ).strip()
    else:
        environment.update({
            "RELEASE_COMMIT": "1" * 40,
            "RELEASE_GENERATED_AT": "2026-08-11T00:00:00+07:00",
        })
    return environment


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


@pytest.mark.parametrize("arguments", [
    ("--dry-run",),
    ("--runtime-only", "--dry-run"),
    ("--dry-run", "--runtime-only"),
])
def test_dry_run_is_strictly_non_mutating(tmp_path, arguments):
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    (run_root / "config").mkdir(parents=True)
    (run_root / "venv").mkdir(parents=True)
    persistent.mkdir()
    (run_root / "VERSION").write_text("1.0.9\n", encoding="utf-8")
    (run_root / "sentinel").write_text("runtime-unchanged", encoding="utf-8")
    (run_root / "venv" / "sentinel").write_text("venv-unchanged", encoding="utf-8")
    (persistent / "state.json").write_text('{"unchanged":true}\n', encoding="utf-8")
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (run_root / "VERSION", run_root / "sentinel", run_root / "venv" / "sentinel", persistent / "state.json")
    }
    systemctl_log = tmp_path / "systemctl.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{systemctl_log}"\n', encoding="utf-8")
    fake_systemctl.chmod(0o755)

    result = subprocess.run(
        ["sh", str(INSTALL), *arguments],
        cwd=ROOT,
        env={**_install_env(ROOT, run_root, persistent, tmp_path / "install.lock"), "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "no production lock, runtime, venv, systemd, go2rtc, or persistent state" in result.stdout
    assert "VERSION=1.0.22" in result.stdout
    assert not (tmp_path / "install.lock").exists()
    assert not systemctl_log.exists()
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_dry_run_explicit_app_src_and_release_worktree_are_non_mutating(tmp_path):
    release = tmp_path / "smart-condo-dashboard-v1.0.10"
    shutil.copytree(ROOT, release, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    (release / "VERSION").write_text("1.0.10\n", encoding="utf-8")
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    (run_root / "venv").mkdir(parents=True)
    persistent.mkdir()
    (run_root / "VERSION").write_text("1.0.9\n", encoding="utf-8")
    (persistent / "state").write_text("preserve", encoding="utf-8")
    result = subprocess.run(
        ["sh", str(release / "install.sh"), "--runtime-only", "--dry-run"],
        cwd=tmp_path,
        env={
            **os.environ,
            "APP_SRC": str(release),
            "APP_RUN": str(run_root),
            "PERSISTENT_CONFIG_ROOT": str(persistent),
            "INSTALL_LOCK_FILE": str(tmp_path / "install.lock"),
            "RELEASE_COMMIT": "2" * 40,
            "RELEASE_GENERATED_AT": "2026-08-10T00:00:00+07:00",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "isolated source snapshot" in result.stdout
    assert "VERSION=1.0.10" in result.stdout
    assert (run_root / "VERSION").read_text(encoding="utf-8") == "1.0.9\n"
    assert (persistent / "state").read_text(encoding="utf-8") == "preserve"
    assert not (tmp_path / "install.lock").exists()


@pytest.mark.parametrize("release_commit", (None, "b" * 40, "not-a-sha"))
def test_release_commit_gate_fails_before_runtime_replacement(tmp_path, release_commit):
    source = tmp_path / "source"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    (run_root / "backend").mkdir(parents=True)
    (run_root / "backend" / "sentinel").write_text("runtime-unchanged")
    (run_root / "VERSION").write_text("1.0.10\n")
    persistent.mkdir()
    (persistent / "sentinel").write_text("persistent-unchanged")
    systemctl_log = tmp_path / "systemctl.log"
    fake_bin = tmp_path / "bin"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "release-gate@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Release Gate Test"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "release gate fixture"], cwd=source, check=True)
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{systemctl_log}"\n',
        encoding="utf-8",
    )
    fake_systemctl.chmod(0o755)
    environment = _install_env(source, run_root, persistent, tmp_path / "install.lock")
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    if release_commit is None:
        environment.pop("RELEASE_COMMIT")
    else:
        environment["RELEASE_COMMIT"] = release_commit

    result = subprocess.run(
        ["sh", str(source / "install.sh"), "--runtime-only"],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert (run_root / "backend" / "sentinel").read_text() == "runtime-unchanged"
    assert (run_root / "VERSION").read_text() == "1.0.10\n"
    assert (persistent / "sentinel").read_text() == "persistent-unchanged"
    assert not systemctl_log.exists()


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
    assert (run_root / "VERSION").read_text() == "1.0.0\n"
    assert venv_marker.read_text() == "unchanged"
    assert local_config.read_text() == '{"preserve": true}'
    assert subprocess.check_output(
        ["git", "status", "--short"], cwd=source, text=True,
    ) == source_status_before
    assert "virtual environment and dependencies were not modified" in result.stdout
    assert "isolated snapshot of Git HEAD" in result.stdout
    assert "restart smart-condo-dashboard" in systemctl_log.read_text()
    marker = json.loads((run_root / ".smart-condo-release.json").read_text(encoding="utf-8"))
    expected_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
    assert marker == {
        "version": "1.0.0",
        "commit": expected_commit,
        "source": "git-archive",
        "generated_at": subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", "HEAD"], cwd=source, text=True,
        ).strip(),
    }
    assert (run_root / ".smart-condo-release.json").stat().st_mode & 0o777 == 0o444

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


def test_runtime_only_uses_invoked_release_worktree_and_propagates_version(tmp_path):
    release = tmp_path / "smart-condo-dashboard-v1.0.2"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    fake_bin = tmp_path / "bin"
    shutil.copytree(ROOT, release, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    (release / "VERSION").write_text("1.0.2\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(["git", "config", "user.email", "release-test@example.invalid"], cwd=release, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=release, check=True)
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(["git", "commit", "-qm", "v1.0.2 fixture"], cwd=release, check=True)
    (run_root / "config").mkdir(parents=True)
    (run_root / "venv").mkdir()
    (run_root / "venv" / "preserve-me").write_text("unchanged", encoding="utf-8")
    (run_root / "VERSION").write_text("1.0.1\n", encoding="utf-8")
    local_config = run_root / "config" / "camera.local.json"
    local_config.write_text('{"preserve": true}', encoding="utf-8")
    persistent.mkdir()
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_systemctl.chmod(0o755)
    fake_flock = fake_bin / "flock"
    fake_flock.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_flock.chmod(0o755)
    environment = {
        **os.environ,
        "GO2RTC_PROVISION_ENABLED": "0",
        "APP_RUN": str(run_root),
        "PERSISTENT_CONFIG_ROOT": str(persistent),
        "INSTALL_LOCK_FILE": str(tmp_path / "install.lock"),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_COMMIT": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=release, text=True,
        ).strip(),
    }

    result = subprocess.run(
        ["sh", str(release / "install.sh"), "--runtime-only"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (run_root / "VERSION").read_text(encoding="utf-8") == "1.0.2\n"
    reported = subprocess.check_output(
        [sys.executable, "-c", "from backend.version import __version__; print(__version__)"],
        cwd=run_root,
        env={**os.environ, "PYTHONPATH": str(run_root)},
        text=True,
    ).strip()
    assert reported == "1.0.2"
    assert (run_root / "venv" / "preserve-me").read_text(encoding="utf-8") == "unchanged"
    assert local_config.read_text(encoding="utf-8") == '{"preserve": true}'
    marker = json.loads((run_root / ".smart-condo-release.json").read_text(encoding="utf-8"))
    assert marker["version"] == "1.0.2"
    assert marker["commit"] == subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=release, text=True,
    ).strip()


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
    (source / "VERSION").write_text("1.0.11\n", encoding="utf-8")
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


def test_dry_run_ignores_deployment_lock_and_touches_nothing(tmp_path):
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

    assert result.returncode == 0
    assert "no production lock" in result.stdout
    assert local_config.read_bytes() == original
    assert sorted(path.name for path in config.iterdir()) == [local_config.name]


def test_dry_run_does_not_create_deployment_lock(tmp_path):
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
    assert "Deployment lock acquired." not in result.stdout
    assert not lock_file.exists()


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
    persistent = tmp_path / "persistent"
    persistent.mkdir()
    (persistent / "state.json").write_text('{"untouched":true}\n')
    (run_root / "sonoff_client.py").write_text("old mirror")
    (run_root / "VERSION").write_text("1.0.1\n")
    prior_marker = '{"version":"1.0.1","commit":"' + "a" * 40 + '","source":"git-archive","generated_at":"2026-08-01T00:00:00+07:00"}\n'
    (run_root / ".smart-condo-release.json").write_text(prior_marker)

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
    (run_root / "VERSION").write_text("1.0.2\n")
    (run_root / ".smart-condo-release.json").write_text('{"version":"1.0.2"}\n')

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
    assert (run_root / "VERSION").read_text() == "1.0.1\n"
    assert (run_root / ".smart-condo-release.json").read_text() == prior_marker
    assert (persistent / "state.json").read_text() == '{"untouched":true}\n'


def test_installer_failure_restores_prior_runtime_version_marker_and_persistent_state(tmp_path):
    source = tmp_path / "source"
    run_root = tmp_path / "run"
    persistent = tmp_path / "persistent"
    fake_bin = tmp_path / "bin"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "rollback-test@example.invalid"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Rollback Test"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "release candidate"], cwd=source, check=True)
    (run_root / "backend").mkdir(parents=True)
    (run_root / "backend" / "prior.txt").write_text("prior-runtime")
    (run_root / "config").mkdir()
    (run_root / "venv").mkdir()
    (run_root / "VERSION").write_text("1.0.10\n")
    persistent.mkdir()
    (persistent / "state.json").write_text("persistent-unchanged")
    fake_bin.mkdir()
    fake_systemctl = fake_bin / "systemctl"
    fake_systemctl.write_text('#!/bin/sh\n[ "$1" != restart ]\n', encoding="utf-8")
    fake_systemctl.chmod(0o755)
    environment = _install_env(source, run_root, persistent, tmp_path / "install.lock")
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(source / "install.sh"), "--runtime-only"],
        cwd=source,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "restoring the previous managed runtime" in result.stderr
    assert (run_root / "backend" / "prior.txt").read_text() == "prior-runtime"
    assert (run_root / "VERSION").read_text() == "1.0.10\n"
    assert not (run_root / ".smart-condo-release.json").exists()
    assert (persistent / "state.json").read_text() == "persistent-unchanged"


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
