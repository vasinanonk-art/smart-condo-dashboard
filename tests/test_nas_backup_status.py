import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import bcrypt
from fastapi.testclient import TestClient

from backend import nas_backup_status as backup
from backend.app_entry import app


NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _record(**overrides):
    record = {
        "schema_version": 1,
        "updated_at": "2026-08-25T02:00:44+07:00",
        "phase": "complete",
        "nas_reachable": False,
        "timer_enabled": True,
        "last_attempt_at": "2026-08-25T02:00:20+07:00",
        "last_success_at": "2026-08-25T02:00:44+07:00",
        "next_run_at": "2026-08-26T02:00:00+07:00",
        "latest_backup": {
            "id": "20260825-020020",
            "size_bytes": 12_582_912,
            "file_count": 27,
            "checksum_verified": True,
        },
        "storage": {"free_bytes": 800, "total_bytes": 1000},
        "progress": {},
        "last_error": None,
        "history": [],
    }
    record.update(overrides)
    return record


def _auth_client(monkeypatch, path):
    monkeypatch.setenv("NAS_BACKUP_STATUS_PATH", str(path))
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "backup-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "backup-test-session-secret-long-enough")
    client = TestClient(app)
    response = client.post(
        "/api/auth/login",
        json={"username": "backup-test", "password": "password"},
    )
    assert response.status_code == 200
    return client


def test_verified_backup_is_healthy_even_when_nas_is_currently_offline():
    projected = backup.project_status(_record(), now=NOW)

    assert projected["overall"] == "healthy"
    assert projected["reason"] == "latest_backup_verified"
    assert projected["nas_reachable"] is False
    assert projected["read_only"] is True
    assert projected["commands_available"] == []


def test_running_progress_is_calculated_only_from_reliable_totals():
    record = _record(
        updated_at="2026-08-25T11:55:00+00:00",
        phase="transfer",
        progress={"bytes_done": 250, "bytes_total": 1000, "percent": 99},
    )

    projected = backup.project_status(record, now=NOW)

    assert projected["overall"] == "running"
    assert projected["reason"] == "transfer"
    assert projected["progress"]["percent"] == 25.0


def test_phase_without_totals_never_fabricates_percentage():
    projected = backup.project_status(
        _record(updated_at="2026-08-25T11:59:00+00:00", phase="verify", progress={"percent": 80}),
        now=NOW,
    )

    assert projected["overall"] == "running"
    assert projected["progress"]["percent"] is None


def test_checksum_failure_and_disabled_timer_are_failures():
    checksum = backup.project_status(
        _record(latest_backup={"id": "run-1", "checksum_verified": False}),
        now=NOW,
    )
    timer = backup.project_status(_record(timer_enabled=False), now=NOW)

    assert (checksum["overall"], checksum["reason"]) == ("failed", "checksum_failed")
    assert (timer["overall"], timer["reason"]) == ("failed", "timer_disabled")


def test_stale_success_and_low_storage_are_warnings(monkeypatch):
    monkeypatch.setenv("NAS_BACKUP_STALE_SEC", "3600")
    stale = backup.project_status(_record(), now=NOW)
    low = backup.project_status(
        _record(
            last_success_at="2026-08-25T11:30:00+00:00",
            storage={"free_bytes": 10, "total_bytes": 1000},
        ),
        now=NOW,
    )

    assert (stale["overall"], stale["reason"]) == ("warning", "last_success_stale")
    assert (low["overall"], low["reason"]) == ("warning", "storage_low")


def test_projection_drops_paths_secrets_and_untrusted_error_messages():
    raw = _record(
        latest_backup={
            "id": "../../root/.credentials",
            "path": "//192.168.1.48/backup",
            "size_bytes": 10,
            "file_count": 2,
            "checksum_verified": True,
        },
        last_error={
            "code": "transfer_failed",
            "message": "password=secret at //192.168.1.48/backup",
            "at": "2026-08-25T02:00:00+07:00",
        },
        password="secret",
        mount_options="credentials=/root/.nas",
    )

    projected = backup.project_status(raw, now=NOW)
    encoded = json.dumps(projected)

    assert projected["latest_backup"]["id"] is None
    assert projected["last_error"]["message"] == "Backup transfer did not complete."
    assert "192.168.1.48" not in encoded
    assert "password" not in encoded
    assert "credentials" not in encoded


def test_read_status_handles_missing_invalid_oversized_and_symlink(tmp_path):
    missing = backup.read_status(tmp_path / "missing.json")
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text("not-json")
    oversized_path = tmp_path / "oversized.json"
    oversized_path.write_text("x" * (backup.MAX_STATUS_BYTES + 1))
    valid_path = tmp_path / "valid.json"
    valid_path.write_text(json.dumps(_record()))
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(valid_path)

    assert missing["reason"] == "status_missing"
    assert backup.read_status(invalid_path)["reason"] == "status_invalid"
    assert backup.read_status(oversized_path)["reason"] == "status_invalid"
    assert backup.read_status(symlink)["reason"] == "status_missing"


def test_default_missing_status_falls_back_to_local_systemd(monkeypatch):
    service = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "Result": "success",
        "ExecMainStatus": "0",
        "InactiveEnterTimestamp": "Tue 2026-08-25 02:00:44 +07",
    }
    timer = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "NextElapseUSecRealtime": "Wed 2026-08-26 02:00:00 +07",
    }
    monkeypatch.setattr(
        backup,
        "_systemctl_show",
        lambda unit, _properties: service if unit == backup.BACKUP_SERVICE else timer,
    )

    projected = backup.collect_systemd_status(now=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc))

    assert (projected["overall"], projected["reason"]) == ("healthy", "latest_backup_successful")
    assert projected["last_success_at"] == "2026-08-25T02:00:44+07:00"
    assert projected["next_run_at"] == "2026-08-26T02:00:00+07:00"
    assert projected["source"] == "local_systemd"
    assert projected["commands_available"] == []

    monkeypatch.setenv("NAS_BACKUP_STATUS_PATH", "/definitely/missing/nas_backup_status.json")
    monkeypatch.setattr(backup, "collect_systemd_status", lambda: {"source": "fallback-used"})
    assert backup.read_status() == {"source": "fallback-used"}


def test_systemd_active_service_reports_running(monkeypatch):
    service = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "Result": "success",
        "ExecMainStatus": "0",
        "ActiveEnterTimestamp": "Tue 2026-08-25 11:55:00 +0000",
    }
    timer = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "NextElapseUSecRealtime": "Wed 2026-08-26 02:00:00 +0000",
    }
    monkeypatch.setattr(
        backup,
        "_systemctl_show",
        lambda unit, _properties: service if unit == backup.BACKUP_SERVICE else timer,
    )

    projected = backup.collect_systemd_status(now=NOW)

    assert (projected["overall"], projected["reason"]) == ("running", "transfer")
    assert projected["progress"]["percent"] is None


def test_systemd_missing_units_report_unknown(monkeypatch):
    monkeypatch.setattr(backup, "_systemctl_show", lambda _unit, _properties: {})

    projected = backup.collect_systemd_status(now=NOW)

    assert (projected["overall"], projected["reason"]) == ("unknown", "status_missing")


def test_systemctl_fallback_uses_fixed_local_command_without_shell(monkeypatch):
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="LoadState=loaded\nActiveState=inactive\n")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    values = backup._systemctl_show(backup.BACKUP_SERVICE, ("LoadState", "ActiveState"))

    assert values == {"LoadState": "loaded", "ActiveState": "inactive"}
    assert observed["command"] == [
        "systemctl",
        "show",
        backup.BACKUP_SERVICE,
        "--property=LoadState",
        "--property=ActiveState",
        "--no-pager",
    ]
    assert "shell" not in observed["kwargs"]
    assert observed["kwargs"]["timeout"] == 2
    assert backup._systemctl_show("untrusted.service", ("LoadState",)) == {}


def test_history_is_bounded_and_sanitized():
    rows = [
        {
            "id": f"run-{index}",
            "started_at": "2026-08-25T02:00:00+07:00",
            "finished_at": "2026-08-25T02:00:30+07:00",
            "result": "success",
            "size_bytes": index,
            "file_count": 3,
            "checksum_verified": True,
            "secret": "drop-me",
        }
        for index in range(40)
    ]

    projected = backup.project_status(_record(history=rows), now=NOW)

    assert len(projected["history"]) == backup.MAX_HISTORY
    assert projected["history"][0]["id"] == "run-10"
    assert all("secret" not in row for row in projected["history"])


def test_authenticated_read_only_endpoint(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(_record()))
    client = _auth_client(monkeypatch, path)

    response = client.get("/api/nas-backup/status")
    csrf = client.get("/api/auth/status").json()["csrf_token"]
    command = client.post(
        "/api/nas-backup/status",
        json={"action": "backup"},
        headers={"origin": "http://testserver", "x-csrf-token": csrf},
    )

    assert response.status_code == 200
    assert response.json()["overall"] in {"healthy", "warning"}
    assert response.json()["commands_available"] == []
    assert command.status_code == 405


def test_endpoint_requires_authentication(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps(_record()))
    monkeypatch.setenv("NAS_BACKUP_STATUS_PATH", str(path))
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "backup-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "backup-test-session-secret-long-enough")

    response = TestClient(app).get("/api/nas-backup/status")

    assert response.status_code == 401


def test_frontend_has_backup_route_mobile_layout_and_no_command_controls():
    html = (ROOT / "frontend" / "index.html").read_text()
    script = (ROOT / "frontend" / "assets" / "dashboard_nas_backup.js").read_text()
    css = (ROOT / "frontend" / "assets" / "dashboard_nas_backup.css").read_text()

    assert 'data-more-route="backup"' in html
    assert 'data-page="backup"' in html
    assert "dashboard_nas_backup.js" in html
    assert "backup: {page:'more', label:'More'}" in (ROOT / "frontend" / "assets" / "dashboard_page_chrome.js").read_text()
    assert "@media(max-width:600px)" in css
    assert "safe-area-inset-bottom" in css
    assert "/api/nas-backup/status" in script
    assert "window.get('/api/nas-backup/status')" in script
    for forbidden in ("Start Backup", "Restore", "Delete Backup", "mount -t", "method:'POST'"):
        assert forbidden not in script
