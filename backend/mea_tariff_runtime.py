"""Route/status integration and compatibility fixes for the official MEA provider."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, Mapping
from zoneinfo import ZoneInfo

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_provider as mea

app = app_module.app
sync.AUDIT_RETENTION_DAYS = 180


def _replace(path: str, methods: set[str], endpoint: Callable[..., Any]) -> None:
    for route in app.routes:
        if getattr(route, "path", None) == path and methods.issubset(set(getattr(route, "methods", set()) or set())):
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint


def _fetch_latest_mea(self) -> Dict[str, Any]:
    """Rate-limit one explicit check, not each document inside that check."""
    now = time.monotonic()
    if mea._LAST_REMOTE_FETCH and now - mea._LAST_REMOTE_FETCH < mea.MIN_FETCH_INTERVAL_SEC:
        raise RuntimeError("provider_rate_limited")
    sync._audit("remote_check_started", "started", "provider=mea")
    try:
        mea._LAST_REMOTE_FETCH = 0.0
        base_source = mea._fetch(mea.MEA_TARIFF_PAGE, {"text/html", "application/pdf"})
        base = mea.parse_mea_base_document(base_source["body"], base_source["content_type"], base_source["url"])
        mea._LAST_REMOTE_FETCH = 0.0
        metadata_source = mea._fetch(mea.MEA_FT_DATASET_API, {"application/json", "text/json"})
        metadata = json.loads(metadata_source["body"].decode("utf-8"))
        package = metadata.get("result") if isinstance(metadata, Mapping) else None
        if not isinstance(package, Mapping):
            raise ValueError("invalid_official_ft_metadata")
        ft_url = mea._pick_ft_resource(package)
        mea._LAST_REMOTE_FETCH = 0.0
        ft_source = mea._fetch(ft_url, {"text/csv", "application/csv", "text/plain", "application/octet-stream"})
        ft = mea.parse_ft_csv(ft_source["body"], ft_source["url"])
        base_archive = mea._archive_source("base", base_source, base)
        ft_archive = mea._archive_source("ft", ft_source, ft)
        effective = max(base.get("effective_date") or "", ft.get("effective_from") or "")
        result = {
            **base,
            "ft_rate": ft["ft_rate"],
            "vat_percent": 7.0,
            "provider": "mea",
            "source": "mea",
            "effective_from": effective,
            "effective_to": ft.get("effective_to"),
            "effective_date": effective,
            "base_tariff_source": {key: base_archive.get(key) for key in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
            "ft_source": {key: ft_archive.get(key) for key in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
            "fetched_at": int(time.time()),
            "checksum": hashlib.sha256((base_archive["checksum"] + ft_archive["checksum"]).encode()).hexdigest(),
        }
        result["version"] = result.get("version") or f"MEA-{effective}-FT-{ft['effective_from']}"
        result["matched_fields"] = sorted(set(base.get("matched_fields", [])) | {"ft_rate", "vat_percent", "effective_period", "source_documents"})
        result["missing_fields"] = sorted(set(base.get("missing_fields", [])))
        result["parser_confidence"] = "high" if base.get("parser_confidence") == "high" and ft.get("parser_confidence") == "high" and not result["missing_fields"] else "medium"
        mea._LAST_REMOTE_FETCH = now
        sync._audit("remote_check_succeeded", "ok", f"checksum={result['checksum'][:16]}", result["version"])
        return result
    except Exception as exc:
        mea._LAST_REMOTE_FETCH = now
        sync._audit("remote_check_failed", "error", str(exc)[:160])
        raise


mea.MEATariffProvider.fetch_latest = _fetch_latest_mea
sync.PROVIDERS["mea"] = mea.MEATariffProvider()


def _save_history_fixed(tariff: Mapping[str, Any], applied_ts: int) -> None:
    try:
        raw = json.loads(mea.TARIFF_HISTORY_PATH.read_text(encoding="utf-8"))
        rows = raw.get("tariffs", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    except Exception:
        rows = []
    effective_ts = int(datetime.strptime(str(tariff.get("effective_date")), "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Bangkok")).timestamp())
    record = {"applied_ts": applied_ts, "effective_ts": effective_ts, "tariff": copy.deepcopy(dict(tariff))}
    rows = [row for row in rows if isinstance(row, dict) and (row.get("tariff") or {}).get("version") != tariff.get("version")]
    rows.append(record)
    rows.sort(key=lambda row: int(row.get("effective_ts") or 0))
    settings._atomic_json_write(mea.TARIFF_HISTORY_PATH, {"version": 1, "tariffs": rows[-100:]}, backup=True)


mea._save_history = _save_history_fixed


def _active_ft(active: Mapping[str, Any]) -> Dict[str, Any]:
    return {"rate": active.get("ft_rate"), "effective_from": active.get("effective_date"), "effective_to": None}


def tariff_status_071() -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    runtime = copy.deepcopy(state.get("tariff_sync") or {})
    active = config["electricity"]["tariff"]
    candidate = runtime.get("candidate") if isinstance(runtime.get("candidate"), Mapping) else None
    source = candidate.get("ft_source") or candidate.get("base_tariff_source") if candidate else None
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
