"""HOTFIX PACK 09 for maintenance runtime state and history importer diagnostics.

This module patches the EPIC 05 settings runtime without adding a scheduler,
polling loop, MQTT client, or device integration changes.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping

from backend import app as app_module
from backend import dashboard_settings as settings

app = app_module.app
_STATE_LOCK = threading.RLock()
_ORIGINAL_LOAD = settings._load_maintenance
_ORIGINAL_SAVE = settings._save_maintenance
_RUNTIME_STATE: Dict[str, Any] = _ORIGINAL_LOAD()


def _runtime_state() -> Dict[str, Any]:
    """Return the single live maintenance state used by all status endpoints."""
    with _STATE_LOCK:
        return copy.deepcopy(_RUNTIME_STATE)


def _save_runtime_state(payload: Mapping[str, Any]) -> None:
    """Persist first, then publish the same successful snapshot in memory."""
    snapshot = copy.deepcopy(dict(payload))
    _ORIGINAL_SAVE(snapshot)
    with _STATE_LOCK:
        _RUNTIME_STATE.clear()
        _RUNTIME_STATE.update(snapshot)


settings._load_maintenance = _runtime_state
settings._save_maintenance = _save_runtime_state
settings.get_runtime_maintenance_state = _runtime_state


def _source_root() -> Path:
    configured = Path(os.getenv("SMART_CONDO_SOURCE_DIR", "/opt/smart-condo-dashboard")).expanduser()
    if configured.is_dir():
        return configured
    # Development/test fallback only. Production uses the exact manual CLI source path.
    return Path(__file__).resolve().parents[1]


def _history_import_invocation(apply: bool) -> tuple[list[str], Path, Path]:
    cwd = _source_root()
    script = cwd / "scripts" / "import_electricity_history.py"
    argv = [sys.executable, str(script)]
    if apply:
        argv.append("--apply")
    return argv, script, cwd


def _bounded_stderr(value: str) -> str:
    text = str(value or "").strip()
    return text[-8000:]


def _run_import(apply: bool) -> Dict[str, Any]:
    """Invoke the importer exactly like the documented manual CLI command."""
    argv, script, cwd = _history_import_invocation(apply)
    started = time.perf_counter()
    diagnostics = {
        "python_executable": str(Path(sys.executable)),
        "script_path": str(script),
        "cwd": str(cwd),
        "argv": list(argv),
        "stderr": "",
    }
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except Exception as exc:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        diagnostics["stderr"] = type(exc).__name__
        return {
            "ok": False,
            "error": "HistoryImportInvocationFailed",
            "exit_code": None,
            "duration_ms": duration_ms,
            "diagnostics": diagnostics,
        }

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    diagnostics["stderr"] = _bounded_stderr(completed.stderr)
    if completed.returncode != 0:
        return {
            "ok": False,
            "error": "HistoryImportFailed",
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "diagnostics": diagnostics,
        }

    try:
        payload = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except Exception as exc:
        diagnostics["stderr"] = diagnostics["stderr"] or type(exc).__name__
        return {
            "ok": False,
            "error": "InvalidImportSummary",
            "exit_code": completed.returncode,
            "duration_ms": duration_ms,
            "diagnostics": diagnostics,
        }
    return {
        "ok": True,
        **payload,
        "exit_code": completed.returncode,
        "duration_ms": duration_ms,
        "diagnostics": diagnostics,
    }


settings._run_import = _run_import


def _notification_once(notifications: list[Dict[str, Any]], kind: str, title: str, detail: str) -> None:
    if any(item.get("kind") == kind and not item.get("dismissed") for item in notifications if isinstance(item, dict)):
        return
    notifications.append(settings._notification(kind, title, detail))


def _maintenance_once() -> Dict[str, Any]:
    now = int(time.time())
    config = settings.load_settings()
    previous = _runtime_state()
    notifications = [item for item in previous.get("notifications", []) if isinstance(item, dict) and not item.get("dismissed")][-50:]

    tariff_started = time.perf_counter()
    tariff = config["electricity"]["tariff"]
    sync_enabled = config["maintenance"]["tariff_sync_enabled"]
    if tariff.get("source", "manual") == "manual" or not sync_enabled:
        tariff_status = "manual_update_required"
    else:
        tariff_status = "provider_not_configured"
        _notification_once(notifications, "tariff", "Tariff requires manual update", "No verified official tariff provider is configured.")
    tariff_duration_ms = round((time.perf_counter() - tariff_started) * 1000, 2)

    prune_started = time.perf_counter()
    settings.history.RETENTION_DAYS = int(config["maintenance"]["history_retention_days"])
    try:
        settings.history._prune_if_due(now)
        prune_error = None
    except Exception as exc:
        prune_error = settings._safe_error(exc)
        _notification_once(notifications, "retention", "History retention warning", "History maintenance could not complete.")
    prune_duration_ms = round((time.perf_counter() - prune_started) * 1000, 2)

    rows = settings.history.read_samples()
    first = rows[0]["ts"] if rows else None
    last = rows[-1]["ts"] if rows else None
    if last and now - int(last) > max(3600, settings.history.MAX_INTEGRATION_GAP_SEC * 4):
        _notification_once(notifications, "history_stopped", "Electricity history stopped", "No recent electricity history sample is available.")

    import_status = _run_import(False)
    import_duration_ms = import_status.get("duration_ms")
    if import_status.get("ok"):
        if import_status.get("records_would_import", 0) > 0:
            _notification_once(notifications, "import", "History import available", f"{import_status['records_would_import']} legitimate rows are ready to review.")
    else:
        detail = f"Importer exited with code {import_status.get('exit_code')}. Review maintenance diagnostics."
        _notification_once(notifications, "history_analysis_failed", "History analysis failed", detail)

    cycle_error = None
    try:
        cycle = settings.billing_cycle.billing_cycle_payload("current_billing_cycle")
        coverage = cycle.get("coverage") or {}
        if not coverage.get("complete"):
            _notification_once(notifications, "billing_incomplete", "Billing history incomplete", f"Current billing coverage is {coverage.get('coverage_percent', 0)}%.")
    except Exception as exc:
        cycle_error = settings._safe_error(exc)
        cycle = {"coverage": {}, "error": cycle_error}

    failed = bool(prune_error or not import_status.get("ok") or cycle_error)
    snapshot = {
        "last_run_ts": now,
        "last_successful_run": previous.get("last_successful_run") if failed else now,
        "last_failed_run": now if failed else previous.get("last_failed_run"),
        "last_tariff_check_ts": now,
        "last_tariff_update_ts": previous.get("last_tariff_update_ts"),
        "last_history_prune_ts": now if prune_error is None else previous.get("last_history_prune_ts"),
        "tariff_check_duration_ms": tariff_duration_ms,
        "history_prune_duration_ms": prune_duration_ms,
        "history_import_duration_ms": import_duration_ms,
        "tariff_status": tariff_status,
        "tariff_version": tariff.get("version") or None,
        "tariff_effective_date": tariff.get("effective_date") or None,
        "history_size_bytes": settings.history.HISTORY_PATH.stat().st_size if settings.history.HISTORY_PATH.exists() else 0,
        "history_first_ts": first,
        "history_last_ts": last,
        "history_sample_count": len(rows),
        "billing_coverage_percent": (cycle.get("coverage") or {}).get("coverage_percent"),
        "billing_coverage_complete": (cycle.get("coverage") or {}).get("complete"),
        "projection_status": cycle.get("projection_status"),
        "history_import": import_status,
        "last_prune_error": prune_error,
        "last_cycle_error": cycle_error,
        "notifications": notifications[-100:],
    }
    _save_runtime_state(snapshot)
    return snapshot


settings._maintenance_once = _maintenance_once


def _replace_endpoint(path: str, methods: set[str], endpoint: Callable[..., Any]) -> bool:
    replaced = False
    for route in app.routes:
        route_methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and methods.issubset(route_methods):
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint
            replaced = True
    return replaced


def electricity_status_from_runtime() -> Dict[str, Any]:
    config = settings.load_settings()
    state = _runtime_state()
    try:
        period = settings.billing_cycle.billing_cycle_status().get("current_period")
    except Exception:
        period = None
    return {
        "billing_cycle": period,
        "billing_cycle_day": config["electricity"]["billing_cycle_day"],
        "timezone": config["electricity"]["timezone"],
        "coverage_percent": state.get("billing_coverage_percent"),
        "coverage_complete": state.get("billing_coverage_complete"),
        "history_starts": state.get("history_first_ts"),
        "history_ends": state.get("history_last_ts"),
        "history_sample_count": state.get("history_sample_count"),
        "tariff_version": state.get("tariff_version"),
        "last_tariff_check": state.get("last_tariff_check_ts"),
        "last_history_prune": state.get("last_history_prune_ts"),
        "projection_status": state.get("projection_status"),
        "last_successful_run": state.get("last_successful_run"),
        "last_failed_run": state.get("last_failed_run"),
        "history_import_duration_ms": state.get("history_import_duration_ms"),
        "tariff_check_duration_ms": state.get("tariff_check_duration_ms"),
        "history_prune_duration_ms": state.get("history_prune_duration_ms"),
    }


_replace_endpoint("/api/settings/electricity/status", {"GET"}, electricity_status_from_runtime)
