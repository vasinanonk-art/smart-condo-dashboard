"""Route/status integration for the official MEA provider."""
from __future__ import annotations

import copy
import time
from typing import Any, Callable, Dict, Mapping

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_provider as mea

app = app_module.app


def _replace(path: str, methods: set[str], endpoint: Callable[..., Any]) -> None:
    for route in app.routes:
        if getattr(route, "path", None) == path and methods.issubset(set(getattr(route, "methods", set()) or set())):
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint


def _active_ft(active: Mapping[str, Any]) -> Dict[str, Any]:
    return {"rate": active.get("ft_rate"), "effective_from": active.get("effective_date"), "effective_to": None}


def tariff_status_071() -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    runtime = copy.deepcopy(state.get("tariff_sync") or {})
    active = config["electricity"]["tariff"]
    candidate = runtime.get("candidate") if isinstance(runtime.get("candidate"), Mapping) else None
    source = None
    if candidate:
        source = candidate.get("ft_source") or candidate.get("base_tariff_source")
    last_error = (runtime.get("diagnostics") or {}).get("error") or runtime.get("last_error")
    return {
        "provider": config["maintenance"].get("tariff_provider", "mea"),
        "provider_available": not bool(last_error),
        "enabled": bool(config["maintenance"].get("tariff_sync_enabled", True)),
        "interval_days": int(config["maintenance"].get("tariff_sync_interval_days", 1)),
        "auto_apply_mode": config["maintenance"].get("tariff_auto_apply_mode", "never"),
        "last_check": runtime.get("last_check_ts") or state.get("last_tariff_check_ts"),
        "next_check": runtime.get("next_check_ts") or sync._next_check_ts(config, runtime.get("last_check_ts")),
        "last_success": runtime.get("last_success_ts"),
        "last_error": last_error,
        "active_tariff": active,
        "active_ft": _active_ft(active),
        "candidate_status": mea.candidate_effective_status(candidate) if candidate else runtime.get("status") or "none",
        "effective_period": {"from": candidate.get("effective_from"), "to": candidate.get("effective_to")} if candidate else None,
        "parser_confidence": candidate.get("parser_confidence") if candidate else None,
        "source_title": (source or {}).get("source_title") if isinstance(source, Mapping) else None,
        "source_url": (source or {}).get("source_url") if isinstance(source, Mapping) else None,
        "source_checksum": (source or {}).get("checksum") if isinstance(source, Mapping) else None,
        "approved_future_tariff": runtime.get("approved_future_tariff") or mea._load_approval(),
        "candidate_available": bool(candidate),
        "diagnostics": runtime.get("diagnostics") or {},
        "official_source_only": True,
    }


def tariff_candidate_071() -> Dict[str, Any]:
    runtime = settings._load_maintenance().get("tariff_sync") or {}
    candidate = copy.deepcopy(runtime.get("candidate"))
    return {
        "available": bool(candidate),
        "status": mea.candidate_effective_status(candidate) if candidate else runtime.get("status") or "none",
        "candidate": candidate,
        "comparison": copy.deepcopy(runtime.get("comparison")),
        "diagnostics": copy.deepcopy(runtime.get("diagnostics") or {}),
        "requires_warning_confirmation": bool(candidate and candidate.get("parser_confidence") == "medium"),
        "apply_allowed": bool(candidate and candidate.get("parser_confidence") in {"high", "medium"} and mea.candidate_effective_status(candidate) == "currently_effective"),
    }


def tariff_check_071() -> Dict[str, Any]:
    config = settings.load_settings()
    provider = str(config["maintenance"].get("tariff_provider") or "mea")
    if provider not in {"mea", "manual", "local_candidate"}:
        return {"checked": False, "status": "provider_unavailable", "provider": provider}
    result = sync.check_tariff(force=True, request_path=False)
    state = settings._load_maintenance()
    runtime = state.setdefault("tariff_sync", {})
    if result.get("status") in {"candidate_available", "unchanged", "older_ignored", "manual", "candidate_not_found"}:
        runtime["last_success_ts"] = int(time.time())
        runtime["last_error"] = None
    else:
        runtime["last_error"] = (result.get("diagnostics") or {}).get("error")
    settings._save_maintenance(state)
    return result


def tariff_apply_071(payload: Dict[str, Any] = Body(default={})):
    runtime = settings._load_maintenance().get("tariff_sync") or {}
    candidate = runtime.get("candidate")
    if not isinstance(candidate, Mapping):
        return JSONResponse({"detail": "tariff_candidate_not_available"}, status_code=409)
    confidence = str(candidate.get("parser_confidence") or "low")
    if confidence == "low":
        return JSONResponse({"detail": "parser_confidence_low"}, status_code=422)
    if confidence == "medium" and payload.get("confirm_medium_confidence") is not True:
        return JSONResponse({"detail": "medium_confidence_confirmation_required"}, status_code=409)
    try:
        return mea._apply_candidate(candidate, scheduled=False)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"detail": "tariff_apply_failed"}, status_code=503)


def tariff_reject_071(payload: Dict[str, Any] = Body(default={})):
    state = settings._load_maintenance()
    runtime = state.get("tariff_sync") or {}
    candidate = runtime.get("candidate")
    if not isinstance(candidate, Mapping):
        return JSONResponse({"detail": "tariff_candidate_not_available"}, status_code=409)
    fingerprint = str(runtime.get("fingerprint") or sync._tariff_fingerprint(candidate))
    sync._dismiss_notifications(state, fingerprint)
    runtime.update({"status": "rejected", "candidate": None, "comparison": None, "rejected_fingerprint": fingerprint, "last_rejected_ts": int(time.time())})
    state["tariff_sync"] = runtime
    settings._save_maintenance(state)
    sync._audit("candidate_rejected", "ok", "provider=mea", str(candidate.get("version") or ""))
    return {"ok": True, "rejected": True}


_replace("/api/tariff/status", {"GET"}, tariff_status_071)
_replace("/api/tariff/candidate", {"GET"}, tariff_candidate_071)
_replace("/api/tariff/check", {"POST"}, tariff_check_071)
_replace("/api/tariff/apply", {"POST"}, tariff_apply_071)
_replace("/api/tariff/reject", {"POST"}, tariff_reject_071)
