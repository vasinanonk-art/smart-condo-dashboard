"""HOTFIX PACK 10: notification lifecycle and dashboard status metadata.

This module wraps the existing maintenance task and status endpoints only. It adds
no polling loop, scheduler, MQTT client, or device integration changes.
"""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict
from zoneinfo import ZoneInfo

from backend import app as app_module
from backend import dashboard_settings as settings

app = app_module.app
_ORIGINAL_MAINTENANCE_ONCE = settings._maintenance_once


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


def _stable_billing_notification(state: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = copy.deepcopy(state)
    coverage = snapshot.get("billing_coverage_percent")
    complete = snapshot.get("billing_coverage_complete") is True or (coverage is not None and float(coverage) >= 100.0)
    notifications = [item for item in snapshot.get("notifications", []) if isinstance(item, dict)]
    kept = []
    billing = None
    for item in notifications:
        if item.get("kind") == "billing_incomplete" or item.get("id") == "billing_incomplete":
            if billing is None:
                billing = item
            continue
        kept.append(item)
    if not complete:
        now = int(time.time())
        value = round(float(coverage or 0.0), 2)
        billing = billing or {
            "id": "billing_incomplete",
            "kind": "billing_incomplete",
            "title": "Billing history incomplete",
            "dismissed": False,
        }
        billing.update({
            "id": "billing_incomplete",
            "kind": "billing_incomplete",
            "title": "Billing history incomplete",
            "detail": f"Current billing coverage is {value}%.",
            "coverage": value,
            "created_ts": now,
            "severity": "warning",
            "dismissed": False,
        })
        kept.append(billing)
    snapshot["notifications"] = kept[-100:]
    return snapshot


def maintenance_once_polished() -> Dict[str, Any]:
    snapshot = _stable_billing_notification(_ORIGINAL_MAINTENANCE_ONCE())
    settings._save_maintenance(snapshot)
    return snapshot


settings._maintenance_once = maintenance_once_polished


def _next_maintenance_run(config: Dict[str, Any]) -> int:
    timezone = ZoneInfo(config["electricity"]["timezone"])
    now = datetime.now(timezone)
    target = now.replace(hour=int(config["maintenance"]["daily_hour"]), minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int(target.timestamp())


def _status_payload() -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    try:
        cycle_status = settings.billing_cycle.billing_cycle_status()
        current_period = cycle_status.get("current_period") or {}
    except Exception:
        current_period = {}
    active_notifications = [item for item in state.get("notifications", []) if isinstance(item, dict) and not item.get("dismissed")]
    return {
        "billing_cycle": current_period,
        "billing_cycle_day": config["electricity"]["billing_cycle_day"],
        "timezone": config["electricity"]["timezone"],
        "timezone_display": "Bangkok (UTC+7)" if config["electricity"]["timezone"] == "Asia/Bangkok" else config["electricity"]["timezone"],
        "coverage_percent": state.get("billing_coverage_percent"),
        "coverage_complete": state.get("billing_coverage_complete"),
        "history_starts": state.get("history_first_ts"),
        "history_ends": state.get("history_last_ts"),
        "history_sample_count": state.get("history_sample_count"),
        "history_size_bytes": state.get("history_size_bytes"),
        "history_retention_days": config["maintenance"]["history_retention_days"],
        "history_import": state.get("history_import"),
        "tariff_version": state.get("tariff_version") or config["electricity"]["tariff"].get("version") or None,
        "tariff_source": config["electricity"]["tariff"].get("source") or "manual",
        "last_tariff_check": state.get("last_tariff_check_ts"),
        "last_history_prune": state.get("last_history_prune_ts"),
        "projection_status": state.get("projection_status"),
        "last_run": state.get("last_run_ts"),
        "last_successful_run": state.get("last_successful_run"),
        "last_failed_run": state.get("last_failed_run"),
        "history_import_duration_ms": state.get("history_import_duration_ms"),
        "tariff_check_duration_ms": state.get("tariff_check_duration_ms"),
        "history_prune_duration_ms": state.get("history_prune_duration_ms"),
        "next_billing_reset_ts": current_period.get("to_ts"),
        "next_maintenance_run_ts": _next_maintenance_run(config),
        "current_notification_count": len(active_notifications),
    }


def electricity_status_polished() -> Dict[str, Any]:
    return _status_payload()


def maintenance_status_polished() -> Dict[str, Any]:
    return {**settings._load_maintenance(), **_status_payload(), "daily_hour": settings.load_settings()["maintenance"]["daily_hour"]}


def notifications_polished() -> Dict[str, Any]:
    notifications = [item for item in settings._load_maintenance().get("notifications", []) if isinstance(item, dict) and not item.get("dismissed")]
    notifications.sort(key=lambda item: int(item.get("created_ts") or 0), reverse=True)
    return {"notifications": notifications, "count": len(notifications)}


@app.post("/api/notifications/dismiss-all")
def dismiss_all_notifications() -> Dict[str, Any]:
    state = settings._load_maintenance()
    for item in state.get("notifications", []):
        if isinstance(item, dict):
            item["dismissed"] = True
    settings._save_maintenance(state)
    return {"ok": True, "dismissed": True}


_replace_endpoint("/api/settings/electricity/status", {"GET"}, electricity_status_polished)
_replace_endpoint("/api/maintenance/status", {"GET"}, maintenance_status_polished)
_replace_endpoint("/api/notifications", {"GET"}, notifications_polished)
