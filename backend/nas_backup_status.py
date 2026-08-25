"""Read-only, secret-safe NAS backup status projection."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.app_runtime import app


SCHEMA_VERSION = 1
MAX_STATUS_BYTES = 64 * 1024
MAX_HISTORY = 30
ACTIVE_PHASES = {"readiness", "snapshot", "checksum", "transfer", "verify"}
PHASES = {"idle", *ACTIVE_PHASES, "complete", "failed"}
RESULTS = {"success", "failed", "running", "unknown"}
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SAFE_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
ERROR_MESSAGES = {
    "nas_offline": "NAS was unavailable during the backup window.",
    "mount_timeout": "NAS connection timed out.",
    "mount_read_only": "NAS storage is read-only.",
    "insufficient_space": "NAS storage does not have enough free space.",
    "sqlite_integrity_failed": "Database verification failed.",
    "checksum_failed": "Backup checksum verification failed.",
    "transfer_failed": "Backup transfer did not complete.",
    "partial_backup": "An incomplete backup needs review.",
    "timer_disabled": "The scheduled backup timer is disabled.",
    "status_invalid": "Backup status data is invalid.",
}
BACKUP_SERVICE = "beer-nas-backup.service"
BACKUP_TIMER = "beer-nas-backup.timer"


def _status_path() -> Path:
    configured = os.getenv("NAS_BACKUP_STATUS_PATH", "").strip()
    if configured:
        return Path(configured)
    data_dir = Path(os.getenv("SMART_CONDO_DATA_DIR", "/root/.smart-condo-dashboard"))
    return data_dir / "state" / "nas_backup_status.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _systemd_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or value in {"", "n/a"} or len(value) > 64:
        return None
    try:
        timestamp = value[4:] if re.match(r"^[A-Z][a-z]{2} ", value) else value
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.isoformat()


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _integer(value: Any, *, maximum: int = 2**63 - 1) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > maximum:
        return None
    return value


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not SAFE_IDENTIFIER.fullmatch(value):
        return None
    return value


def _phase(value: Any) -> str:
    candidate = str(value or "unknown").lower()
    return candidate if candidate in PHASES else "idle"


def _result(value: Any) -> str:
    candidate = str(value or "unknown").lower()
    return candidate if candidate in RESULTS else "unknown"


def _latest_backup(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    checksum = source.get("checksum_verified")
    return {
        "id": _identifier(source.get("id")),
        "size_bytes": _integer(source.get("size_bytes")),
        "file_count": _integer(source.get("file_count"), maximum=10_000_000),
        "checksum_verified": checksum if isinstance(checksum, bool) else None,
    }


def _storage(value: Any) -> dict[str, int | None]:
    source = value if isinstance(value, Mapping) else {}
    total = _integer(source.get("total_bytes"))
    free = _integer(source.get("free_bytes"))
    if total is not None and free is not None and free > total:
        free = None
    return {"free_bytes": free, "total_bytes": total}


def _progress(value: Any) -> dict[str, int | float | None]:
    source = value if isinstance(value, Mapping) else {}
    files_done = _integer(source.get("files_done"), maximum=10_000_000)
    files_total = _integer(source.get("files_total"), maximum=10_000_000)
    bytes_done = _integer(source.get("bytes_done"))
    bytes_total = _integer(source.get("bytes_total"))
    percent: float | None = None
    if bytes_total and bytes_done is not None and bytes_done <= bytes_total:
        percent = round(bytes_done * 100 / bytes_total, 1)
    elif files_total and files_done is not None and files_done <= files_total:
        percent = round(files_done * 100 / files_total, 1)
    return {
        "percent": percent,
        "files_done": files_done,
        "files_total": files_total,
        "bytes_done": bytes_done,
        "bytes_total": bytes_total,
    }


def _last_error(value: Any) -> dict[str, str | None] | None:
    if not isinstance(value, Mapping):
        return None
    raw_code = value.get("code")
    code = raw_code if isinstance(raw_code, str) and SAFE_ERROR_CODE.fullmatch(raw_code) else "status_invalid"
    return {
        "code": code,
        "message": ERROR_MESSAGES.get(code, "Backup needs operator review."),
        "at": _timestamp(value.get("at")),
    }


def _history(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[-MAX_HISTORY:]:
        if not isinstance(item, Mapping):
            continue
        rows.append(
            {
                "id": _identifier(item.get("id")),
                "started_at": _timestamp(item.get("started_at")),
                "finished_at": _timestamp(item.get("finished_at")),
                "result": _result(item.get("result")),
                "size_bytes": _integer(item.get("size_bytes")),
                "file_count": _integer(item.get("file_count"), maximum=10_000_000),
                "checksum_verified": item.get("checksum_verified")
                if isinstance(item.get("checksum_verified"), bool)
                else None,
                "error_code": item.get("error_code")
                if isinstance(item.get("error_code"), str)
                and SAFE_ERROR_CODE.fullmatch(item.get("error_code"))
                else None,
            }
        )
    return rows


def _overall(payload: Mapping[str, Any], now: datetime) -> tuple[str, str]:
    phase = payload["phase"]
    updated = _datetime(payload["updated_at"])
    last_attempt = _datetime(payload["last_attempt_at"])
    last_success = _datetime(payload["last_success_at"])
    checksum = payload["latest_backup"]["checksum_verified"]
    error = payload["last_error"]
    stale_seconds = max(3600, int(os.getenv("NAS_BACKUP_STALE_SEC", "108000")))
    running_stale_seconds = max(300, int(os.getenv("NAS_BACKUP_RUNNING_STALE_SEC", "1800")))

    if payload["timer_enabled"] is False:
        return "failed", "timer_disabled"
    if phase in ACTIVE_PHASES and updated and (now - updated).total_seconds() <= running_stale_seconds:
        return "running", phase
    if phase in ACTIVE_PHASES:
        return "warning", "running_status_stale"
    if checksum is False:
        return "failed", "checksum_failed"
    if error and (not last_success or (last_attempt and last_attempt >= last_success)):
        return "failed", error["code"]
    if not last_success:
        return "unknown", "no_success_record"
    if (now - last_success).total_seconds() > stale_seconds:
        return "warning", "last_success_stale"
    storage = payload["storage"]
    if storage["free_bytes"] is not None and storage["total_bytes"]:
        if storage["free_bytes"] / storage["total_bytes"] < 0.15:
            return "warning", "storage_low"
    return "healthy", "latest_backup_verified" if checksum is True else "latest_backup_successful"


def project_status(raw: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _timestamp(raw.get("updated_at")),
        "phase": _phase(raw.get("phase")),
        "nas_reachable": raw.get("nas_reachable") if isinstance(raw.get("nas_reachable"), bool) else None,
        "timer_enabled": raw.get("timer_enabled") if isinstance(raw.get("timer_enabled"), bool) else None,
        "last_attempt_at": _timestamp(raw.get("last_attempt_at")),
        "last_success_at": _timestamp(raw.get("last_success_at")),
        "next_run_at": _timestamp(raw.get("next_run_at")),
        "latest_backup": _latest_backup(raw.get("latest_backup")),
        "storage": _storage(raw.get("storage")),
        "progress": _progress(raw.get("progress")),
        "last_error": _last_error(raw.get("last_error")),
        "history": _history(raw.get("history")),
        "read_only": True,
        "commands_available": [],
    }
    overall, reason = _overall(payload, now or _utc_now())
    payload["overall"] = overall
    payload["reason"] = reason
    return payload


def unknown_status(reason: str) -> dict[str, Any]:
    payload = project_status({}, now=_utc_now())
    payload["overall"] = "unknown"
    payload["reason"] = reason
    return payload


def _systemctl_show(unit: str, properties: tuple[str, ...]) -> dict[str, str]:
    if unit not in {BACKUP_SERVICE, BACKUP_TIMER}:
        return {}
    command = ["systemctl", "show", unit, *(f"--property={name}" for name in properties), "--no-pager"]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in properties:
            values[key] = value.strip()
    return values


def collect_systemd_status(*, now: datetime | None = None) -> dict[str, Any]:
    service = _systemctl_show(
        BACKUP_SERVICE,
        ("LoadState", "ActiveState", "Result", "ExecMainStatus", "ActiveEnterTimestamp", "InactiveEnterTimestamp"),
    )
    timer = _systemctl_show(
        BACKUP_TIMER,
        ("LoadState", "ActiveState", "UnitFileState", "NextElapseUSecRealtime", "LastTriggerUSec"),
    )
    if service.get("LoadState") != "loaded" or timer.get("LoadState") != "loaded":
        return unknown_status("status_missing")
    active = service.get("ActiveState") == "active"
    result = service.get("Result")
    attempt_at = _systemd_timestamp(
        service.get("ActiveEnterTimestamp") if active else service.get("InactiveEnterTimestamp")
    )
    success = bool(result == "success" and service.get("ExecMainStatus") == "0" and not active)
    raw = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": attempt_at,
        "phase": "transfer" if active else "complete" if success else "failed" if result == "exit-code" else "idle",
        "nas_reachable": None,
        "timer_enabled": timer.get("UnitFileState") == "enabled" and timer.get("ActiveState") == "active",
        "last_attempt_at": attempt_at,
        "last_success_at": attempt_at if success else None,
        "next_run_at": _systemd_timestamp(timer.get("NextElapseUSecRealtime")),
        "latest_backup": {"checksum_verified": None},
        "last_error": {"code": "transfer_failed", "at": attempt_at}
        if result == "exit-code"
        else None,
    }
    payload = project_status(raw, now=now)
    payload["source"] = "local_systemd"
    return payload


def read_status(path: Path | None = None) -> dict[str, Any]:
    target = path or _status_path()
    try:
        if target.is_symlink() or not target.is_file():
            return unknown_status("status_missing") if path is not None else collect_systemd_status()
        if target.stat().st_size > MAX_STATUS_BYTES:
            return unknown_status("status_invalid")
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
            return unknown_status("status_invalid")
        return project_status(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return unknown_status("status_invalid")


@app.get("/api/nas-backup/status")
def get_nas_backup_status() -> dict[str, Any]:
    return read_status()
