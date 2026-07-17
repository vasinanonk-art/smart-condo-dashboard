"""Persistent non-secret dashboard settings, maintenance actions, and one daily task.

Secrets remain environment-only. This module creates one bounded daily maintenance
thread and no device, MQTT, or electricity polling loop.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Body, Query
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import electricity_billing_cycle as billing_cycle
from backend import electricity_history as history

app = app_module.app
DATA_DIR = Path(os.getenv("SMART_CONDO_DATA_DIR", str(Path.home() / ".smart-condo-dashboard"))).expanduser()
SETTINGS_PATH = DATA_DIR / "settings.json"
MAINTENANCE_PATH = DATA_DIR / "maintenance_state.json"
IMPORT_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_electricity_history.py"
_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None

_FORBIDDEN_TERMS = ("password", "secret", "token", "cookie", "session", "credential", "local_key", "auth")
_DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "electricity": {
        "billing_cycle_day": 2,
        "timezone": "Asia/Bangkok",
        "tariff": {
            "tariff_name": "",
            "source": "manual",
            "effective_date": "",
            "version": "",
            "tiers": [],
            "ft_rate": 0.0,
            "service_charge": 0.0,
            "vat_percent": 7.0,
            "minimum_charge": 0.0,
        },
    },
    "dashboard": {
        "timezone": "Asia/Bangkok",
    },
    "maintenance": {
        "daily_hour": 3,
        "history_retention_days": 400,
        "tariff_sync_enabled": False,
        "tariff_sync_interval_days": 1,
    },
}


def _safe_error(exc: BaseException) -> str:
    return type(exc).__name__


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _contains_forbidden(value: Any, path: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = f"{path}.{key}".lower()
            if any(term in name for term in _FORBIDDEN_TERMS):
                return True
            if _contains_forbidden(item, name):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden(item, path) for item in value)
    return False


def _number(value: Any, name: str, minimum: float = 0.0, maximum: Optional[float] = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{name}") from exc
    if result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"invalid_{name}")
    return result


def _validate_timezone(value: Any) -> str:
    name = str(value or "Asia/Bangkok").strip()
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("invalid_timezone") from exc
    return name


def _validate_tariff(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_tariff")
    name = str(raw.get("tariff_name") or "").strip()[:120]
    source = str(raw.get("source") or "manual").strip()[:80] or "manual"
    effective = str(raw.get("effective_date") or "").strip()
    version = str(raw.get("version") or "").strip()[:80]
    tiers_raw = raw.get("tiers") or []
    if effective:
        try:
            datetime.strptime(effective, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("invalid_effective_date") from exc
    if not isinstance(tiers_raw, list):
        raise ValueError("invalid_tiers")
    tiers = []
    previous = 0.0
    unlimited = False
    for index, item in enumerate(tiers_raw):
        if not isinstance(item, Mapping):
            raise ValueError("invalid_tiers")
        rate = _number(item.get("rate"), "tier_rate")
        limit_raw = item.get("up_to_kwh")
        limit = None if limit_raw in (None, "") else _number(limit_raw, "tier_limit", minimum=0.000001)
        if unlimited or (limit is not None and limit <= previous):
            raise ValueError("tiers_not_ascending")
        if limit is None:
            unlimited = True
        else:
            previous = limit
        tiers.append({"up_to_kwh": limit, "rate": rate})
    if tiers and not unlimited:
        raise ValueError("final_tier_must_be_unlimited")
    return {
        "tariff_name": name,
        "source": source,
        "effective_date": effective,
        "version": version,
        "tiers": tiers,
        "ft_rate": _number(raw.get("ft_rate", 0), "ft_rate"),
        "service_charge": _number(raw.get("service_charge", 0), "service_charge"),
        "vat_percent": _number(raw.get("vat_percent", 7), "vat_percent", maximum=100),
        "minimum_charge": _number(raw.get("minimum_charge", 0), "minimum_charge"),
    }


def validate_settings(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping) or _contains_forbidden(raw):
        raise ValueError("invalid_or_forbidden_settings")
    merged = _deep_merge(_DEFAULTS, raw)
    electricity = merged.get("electricity")
    dashboard = merged.get("dashboard")
    maintenance = merged.get("maintenance")
    if not isinstance(electricity, Mapping) or not isinstance(dashboard, Mapping) or not isinstance(maintenance, Mapping):
        raise ValueError("invalid_settings_sections")
    day = int(_number(electricity.get("billing_cycle_day", 2), "billing_cycle_day", 1, 31))
    timezone = _validate_timezone(electricity.get("timezone"))
    dashboard_timezone = _validate_timezone(dashboard.get("timezone"))
    hour = int(_number(maintenance.get("daily_hour", 3), "daily_hour", 0, 23))
    retention = int(_number(maintenance.get("history_retention_days", 400), "history_retention_days", 1, 3650))
    interval = int(_number(maintenance.get("tariff_sync_interval_days", 1), "tariff_sync_interval_days", 1, 365))
    return {
        "version": 1,
        "electricity": {
            "billing_cycle_day": day,
            "timezone": timezone,
            "tariff": _validate_tariff(electricity.get("tariff") or {}),
        },
        "dashboard": {"timezone": dashboard_timezone},
        "maintenance": {
            "daily_hour": hour,
            "history_retention_days": retention,
            "tariff_sync_enabled": bool(maintenance.get("tariff_sync_enabled", False)),
            "tariff_sync_interval_days": interval,
        },
    }


def load_settings() -> Dict[str, Any]:
    with _LOCK:
        if not SETTINGS_PATH.exists():
            return validate_settings(_DEFAULTS)
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return validate_settings(raw)
        except Exception:
            return validate_settings(_DEFAULTS)


def _atomic_json_write(path: Path, payload: Mapping[str, Any], backup: bool = True) -> Optional[Path]:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        if backup and path.exists():
            backup_path = path.with_name(f"{path.name}.backup-{int(time.time())}")
            shutil.copy2(path, backup_path)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return backup_path
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        if backup_path and backup_path.exists() and not path.exists():
            shutil.copy2(backup_path, path)
        raise


def _apply_runtime(settings: Mapping[str, Any]) -> None:
    electricity = settings["electricity"]
    billing_cycle.BILLING_CYCLE_DAY = int(electricity["billing_cycle_day"])
    billing_cycle.TIMEZONE_NAME = str(electricity["timezone"])
    billing_cycle.BILLING_TZ = ZoneInfo(billing_cycle.TIMEZONE_NAME)


def save_settings(raw: Any) -> Dict[str, Any]:
    validated = validate_settings(raw)
    with _LOCK:
        backup = _atomic_json_write(SETTINGS_PATH, validated, backup=True)
        try:
            _apply_runtime(validated)
        except Exception:
            if backup and backup.exists():
                shutil.copy2(backup, SETTINGS_PATH)
            raise
    return validated


def _settings_tariff_config() -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    tariff = load_settings()["electricity"]["tariff"]
    if not tariff.get("tariff_name") or not tariff.get("effective_date") or not tariff.get("tiers"):
        return None, "tariff_not_configured"
    return {
        "tariff_name": tariff["tariff_name"],
        "effective_date": tariff["effective_date"],
        "tiers": tariff["tiers"],
        "ft_rate": tariff["ft_rate"],
        "service_charge": tariff["service_charge"],
        "vat_percent": tariff["vat_percent"],
        "minimum_charge": tariff["minimum_charge"],
    }, None


# Make billing and status routes use saved settings immediately, without restart.
history._tariff_config = _settings_tariff_config
_apply_runtime(load_settings())


def _load_maintenance() -> Dict[str, Any]:
    try:
        raw = json.loads(MAINTENANCE_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_maintenance(payload: Mapping[str, Any]) -> None:
    _atomic_json_write(MAINTENANCE_PATH, payload, backup=False)


def _notification(kind: str, title: str, detail: str, severity: str = "warning") -> Dict[str, Any]:
    return {"id": f"{kind}-{int(time.time())}", "kind": kind, "title": title, "detail": detail, "severity": severity, "created_ts": int(time.time()), "dismissed": False}


def _run_import(apply: bool) -> Dict[str, Any]:
    command = [sys.executable, str(IMPORT_SCRIPT)] + (["--apply"] if apply else [])
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0:
        return {"ok": False, "error": "HistoryImportFailed", "exit_code": completed.returncode}
    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "error": "InvalidImportSummary"}
    return {"ok": True, **payload}


def _maintenance_once() -> Dict[str, Any]:
    now = int(time.time())
    settings = load_settings()
    previous = _load_maintenance()
    notifications = [item for item in previous.get("notifications", []) if isinstance(item, dict) and not item.get("dismissed")][-50:]
    tariff = settings["electricity"]["tariff"]
    sync_enabled = settings["maintenance"]["tariff_sync_enabled"]
    if tariff.get("source", "manual") == "manual" or not sync_enabled:
        tariff_status = "manual_update_required"
    else:
        tariff_status = "provider_not_configured"
        notifications.append(_notification("tariff", "Tariff requires manual update", "No verified official tariff provider is configured."))

    history.RETENTION_DAYS = int(settings["maintenance"]["history_retention_days"])
    try:
        history._prune_if_due(now)
        prune_error = None
    except Exception as exc:
        prune_error = _safe_error(exc)
        notifications.append(_notification("retention", "History retention warning", "History maintenance could not complete."))

    rows = history.read_samples()
    first = rows[0]["ts"] if rows else None
    last = rows[-1]["ts"] if rows else None
    if last and now - int(last) > max(3600, history.MAX_INTEGRATION_GAP_SEC * 4):
        notifications.append(_notification("history_stopped", "Electricity history stopped", "No recent electricity history sample is available."))
    try:
        import_status = _run_import(False)
        if import_status.get("records_would_import", 0) > 0:
            notifications.append(_notification("import", "History import available", f"{import_status['records_would_import']} legitimate rows are ready to review."))
    except Exception:
        import_status = {"ok": False, "error": "HistoryAnalyzeFailed"}

    try:
        cycle = billing_cycle.billing_cycle_payload("current_billing_cycle")
        coverage = cycle.get("coverage") or {}
        if not coverage.get("complete"):
            notifications.append(_notification("billing_incomplete", "Billing history incomplete", f"Current billing coverage is {coverage.get('coverage_percent', 0)}%."))
    except Exception as exc:
        cycle = {"coverage": {}, "error": _safe_error(exc)}

    snapshot = {
        "last_run_ts": now,
        "last_tariff_check_ts": now,
        "last_tariff_update_ts": previous.get("last_tariff_update_ts"),
        "last_history_prune_ts": now if prune_error is None else previous.get("last_history_prune_ts"),
        "tariff_status": tariff_status,
        "tariff_version": tariff.get("version") or None,
        "tariff_effective_date": tariff.get("effective_date") or None,
        "history_size_bytes": history.HISTORY_PATH.stat().st_size if history.HISTORY_PATH.exists() else 0,
        "history_first_ts": first,
        "history_last_ts": last,
        "history_sample_count": len(rows),
        "billing_coverage_percent": (cycle.get("coverage") or {}).get("coverage_percent"),
        "billing_coverage_complete": (cycle.get("coverage") or {}).get("complete"),
        "projection_status": cycle.get("projection_status"),
        "history_import": import_status,
        "last_prune_error": prune_error,
        "notifications": notifications[-100:],
    }
    _save_maintenance(snapshot)
    return snapshot


def _seconds_until_next_run(settings: Mapping[str, Any]) -> float:
    timezone = ZoneInfo(settings["electricity"]["timezone"])
    now = datetime.now(timezone)
    target = now.replace(hour=int(settings["maintenance"]["daily_hour"]), minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(60.0, (target - now).total_seconds())


def _maintenance_worker() -> None:
    while not _STOP.is_set():
        if _STOP.wait(_seconds_until_next_run(load_settings())):
            return
        try:
            _maintenance_once()
        except Exception as exc:
            state = _load_maintenance()
            state["last_run_ts"] = int(time.time())
            state["last_error"] = _safe_error(exc)
            _save_maintenance(state)


def start_daily_maintenance() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_maintenance_worker, name="dashboard-daily-maintenance", daemon=True)
        _THREAD.start()


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    return load_settings()


@app.put("/api/settings")
def put_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        return {"ok": True, "settings": save_settings(payload)}
    except ValueError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=422)
    except Exception as exc:
        return JSONResponse({"ok": False, "detail": _safe_error(exc)}, status_code=500)


@app.get("/api/settings/electricity")
def get_electricity_settings() -> Dict[str, Any]:
    return load_settings()["electricity"]


@app.put("/api/settings/electricity")
def put_electricity_settings(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    current = load_settings()
    current["electricity"] = payload
    return put_settings(current)


@app.get("/api/settings/export")
def export_settings() -> JSONResponse:
    return JSONResponse(load_settings(), headers={"Content-Disposition": "attachment; filename=settings.json"})


@app.post("/api/settings/import")
def import_settings(payload: Dict[str, Any] = Body(...), confirm: bool = Query(False)) -> Dict[str, Any]:
    if not confirm:
        return JSONResponse({"ok": False, "detail": "confirmation_required"}, status_code=409)
    return put_settings(payload)


@app.post("/api/electricity/history/analyze")
def analyze_history() -> Dict[str, Any]:
    result = _run_import(False)
    state = _load_maintenance()
    state["history_import"] = result
    _save_maintenance(state)
    return result


@app.post("/api/electricity/history/import")
def import_history(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    if payload.get("confirm") is not True:
        return JSONResponse({"ok": False, "detail": "confirmation_required"}, status_code=409)
    result = _run_import(True)
    state = _load_maintenance()
    state["history_import"] = result
    state["last_history_import_ts"] = int(time.time()) if result.get("ok") else state.get("last_history_import_ts")
    _save_maintenance(state)
    return result


@app.get("/api/electricity/history/import/status")
def import_history_status() -> Dict[str, Any]:
    return _load_maintenance().get("history_import") or {"ok": True, "status": "not_analyzed"}


@app.get("/api/maintenance/status")
def maintenance_status() -> Dict[str, Any]:
    state = _load_maintenance()
    settings = load_settings()
    return {**state, "daily_hour": settings["maintenance"]["daily_hour"], "timezone": settings["electricity"]["timezone"]}


@app.post("/api/maintenance/run")
def run_maintenance() -> Dict[str, Any]:
    return _maintenance_once()


@app.get("/api/notifications")
def get_notifications() -> Dict[str, Any]:
    notifications = [item for item in _load_maintenance().get("notifications", []) if isinstance(item, dict) and not item.get("dismissed")]
    return {"notifications": notifications}


@app.post("/api/notifications/{notification_id}/dismiss")
def dismiss_notification(notification_id: str) -> Dict[str, Any]:
    state = _load_maintenance()
    for item in state.get("notifications", []):
        if isinstance(item, dict) and item.get("id") == notification_id:
            item["dismissed"] = True
    _save_maintenance(state)
    return {"ok": True}


start_daily_maintenance()
