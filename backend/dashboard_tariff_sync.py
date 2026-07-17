"""Controlled tariff candidate comparison for the single daily maintenance task.

No internet source is contacted here. A future verified provider may atomically write
`tariff_candidate.json`; this module validates and compares it, but never applies it.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from backend import dashboard_settings as settings

CANDIDATE_PATH = settings.DATA_DIR / "tariff_candidate.json"
_original_maintenance_once = settings._maintenance_once


def _newer(candidate: Dict[str, Any], active: Dict[str, Any]) -> bool:
    candidate_version = str(candidate.get("version") or "")
    active_version = str(active.get("version") or "")
    if candidate_version and candidate_version != active_version:
        return True
    candidate_date = str(candidate.get("effective_date") or "")
    active_date = str(active.get("effective_date") or "")
    try:
        return datetime.strptime(candidate_date, "%Y-%m-%d") > datetime.strptime(active_date, "%Y-%m-%d")
    except ValueError:
        return bool(candidate_date and candidate_date != active_date)


def _candidate_status(config: Dict[str, Any]) -> Dict[str, Any]:
    maintenance = config["maintenance"]
    active = config["electricity"]["tariff"]
    source = str(active.get("source") or "manual")
    if source == "manual" or not maintenance.get("tariff_sync_enabled"):
        return {"status": "manual_update_required", "candidate": None}
    if not CANDIDATE_PATH.exists():
        return {"status": "verified_provider_not_configured", "candidate": None}
    try:
        raw = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
        candidate = settings._validate_tariff(raw)
    except Exception as exc:
        return {"status": "candidate_invalid", "error": settings._safe_error(exc), "candidate": None}
    return {"status": "new_tariff_available" if _newer(candidate, active) else "up_to_date", "candidate": candidate}


def maintenance_once_with_tariff_check() -> Dict[str, Any]:
    snapshot = _original_maintenance_once()
    config = settings.load_settings()
    checked = _candidate_status(config)
    snapshot["tariff_status"] = checked["status"]
    snapshot["last_tariff_check_ts"] = snapshot.get("last_run_ts")
    snapshot["tariff_candidate"] = None
    if checked.get("candidate"):
        candidate = checked["candidate"]
        snapshot["tariff_candidate"] = {
            "tariff_name": candidate.get("tariff_name"),
            "effective_date": candidate.get("effective_date"),
            "version": candidate.get("version"),
            "source": candidate.get("source"),
        }
    if checked["status"] == "new_tariff_available":
        existing = [item for item in snapshot.get("notifications", []) if item.get("kind") != "new_tariff"]
        candidate = checked["candidate"]
        existing.append(settings._notification(
            "new_tariff",
            "New tariff available",
            f"{candidate.get('tariff_name') or 'Validated tariff'} {candidate.get('version') or candidate.get('effective_date') or ''} is ready for review. It was not applied automatically.",
            "warning",
        ))
        snapshot["notifications"] = existing[-100:]
    if checked.get("error"):
        snapshot["tariff_check_error"] = checked["error"]
    settings._save_maintenance(snapshot)
    return snapshot


# The existing scheduler resolves this global at run time, so there remains exactly
# one daily thread and one maintenance execution path.
settings._maintenance_once = maintenance_once_with_tariff_check
