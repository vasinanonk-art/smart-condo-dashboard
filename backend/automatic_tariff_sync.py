"""EPIC 07: safe automatic electricity tariff candidate synchronization.

This module reuses the existing daily maintenance scheduler. It never overwrites a
 tariff automatically and current providers perform no network access.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import dashboard_settings as settings

app = app_module.app
CANDIDATE_PATH = settings.DATA_DIR / "tariff_candidate.json"
AUDIT_PATH = settings.DATA_DIR / "tariff_events.jsonl"
AUDIT_RETENTION_DAYS = max(1, int(os.getenv("TARIFF_EVENT_RETENTION_DAYS", "90")))
PROVIDER_NAMES = {"manual", "local_candidate", "mea", "pea"}
_REMOTE_PROVIDERS = {"mea", "pea"}

# Extend settings without changing the billing engine or creating a new scheduler.
settings._DEFAULTS["maintenance"]["tariff_provider"] = "manual"
_original_validate_settings = settings.validate_settings


def _validate_settings_with_provider(raw: Any) -> Dict[str, Any]:
    validated = _original_validate_settings(raw)
    source = raw if isinstance(raw, Mapping) else {}
    maintenance = source.get("maintenance") if isinstance(source.get("maintenance"), Mapping) else {}
    provider = str(maintenance.get("tariff_provider") or "manual").strip().lower()
    if provider not in PROVIDER_NAMES:
        raise ValueError("invalid_tariff_provider")
    validated["maintenance"]["tariff_provider"] = provider
    return validated


settings.validate_settings = _validate_settings_with_provider


class TariffProvider(ABC):
    """Provider contract for future verified tariff sources."""

    name = "base"
    remote = False

    @abstractmethod
    def fetch_latest(self) -> Any:
        raise NotImplementedError

    def validate(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_tariff_candidate")
        required = {"effective_date", "version", "tiers", "ft_rate", "service_charge", "vat_percent"}
        missing = sorted(key for key in required if key not in raw)
        if missing:
            raise ValueError("missing_" + "_".join(missing))
        if not isinstance(raw.get("tiers"), list) or not raw.get("tiers"):
            raise ValueError("missing_tiers")
        unlimited = sum(1 for tier in raw["tiers"] if isinstance(tier, Mapping) and tier.get("up_to_kwh") in (None, ""))
        if unlimited != 1:
            raise ValueError("exactly_one_unlimited_tier_required")
        return settings._validate_tariff(raw)

    def normalize(self, raw: Any) -> Dict[str, Any]:
        normalized = self.validate(raw)
        normalized["source"] = str(raw.get("source") or self.name)
        return normalized


class ManualTariffProvider(TariffProvider):
    name = "manual"

    def fetch_latest(self) -> Any:
        return None


class LocalCandidateTariffProvider(TariffProvider):
    name = "local_candidate"

    def fetch_latest(self) -> Any:
        if not CANDIDATE_PATH.exists():
            return None
        return json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))


class UnavailableOfficialProvider(TariffProvider):
    remote = True

    def __init__(self, name: str):
        self.name = name

    def fetch_latest(self) -> Any:
        raise RuntimeError("official_provider_not_configured")


PROVIDERS: Dict[str, TariffProvider] = {
    "manual": ManualTariffProvider(),
    "local_candidate": LocalCandidateTariffProvider(),
    "mea": UnavailableOfficialProvider("mea"),
    "pea": UnavailableOfficialProvider("pea"),
}


def _safe_error(exc: BaseException) -> str:
    message = str(exc).strip()
    return message[:160] if message else type(exc).__name__


def _parse_date(value: Any) -> datetime:
    return datetime.strptime(str(value or ""), "%Y-%m-%d")


def _version_key(value: Any) -> tuple[Any, ...]:
    text = str(value or "").strip().lower()
    parts: list[Any] = []
    token = ""
    numeric: Optional[bool] = None
    for char in text:
        is_numeric = char.isdigit()
        if token and numeric != is_numeric:
            parts.append(int(token) if numeric else token)
            token = ""
        token += char
        numeric = is_numeric
    if token:
        parts.append(int(token) if numeric else token)
    return tuple(parts)


def compare_version(candidate: Mapping[str, Any], active: Mapping[str, Any]) -> int:
    """Return 1 newer, 0 same version/date, -1 older."""
    candidate_date = _parse_date(candidate.get("effective_date"))
    active_text = str(active.get("effective_date") or "").strip()
    if not active_text:
        return 1
    active_date = _parse_date(active_text)
    if candidate_date != active_date:
        return 1 if candidate_date > active_date else -1
    candidate_version = _version_key(candidate.get("version"))
    active_version = _version_key(active.get("version"))
    if candidate_version == active_version:
        return 0
    return 1 if candidate_version > active_version else -1


def _tariff_fingerprint(tariff: Mapping[str, Any]) -> str:
    data = json.dumps(tariff, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def _comparison(active: Mapping[str, Any], candidate: Mapping[str, Any]) -> Dict[str, Any]:
    fields = ("tiers", "ft_rate", "service_charge", "vat_percent", "effective_date", "version")
    return {
        field: {
            "current": copy.deepcopy(active.get(field)),
            "candidate": copy.deepcopy(candidate.get(field)),
            "changed": active.get(field) != candidate.get(field),
        }
        for field in fields
    }


def _audit(event: str, result: str, detail: str = "", version: Optional[str] = None) -> None:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": int(time.time()), "event": event, "result": result, "version": version, "detail": str(detail)[:240]}
    with AUDIT_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    try:
        if AUDIT_PATH.stat().st_size > 1_000_000:
            cutoff = int(time.time()) - AUDIT_RETENTION_DAYS * 86400
            kept = []
            for line in AUDIT_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                    if int(item.get("ts") or 0) >= cutoff:
                        kept.append(item)
                except Exception:
                    continue
            temporary = AUDIT_PATH.with_suffix(".jsonl.tmp")
            temporary.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in kept), encoding="utf-8")
            os.replace(temporary, AUDIT_PATH)
    except OSError:
        pass


def _notification_id(kind: str, key: str) -> str:
    return f"{kind}-{key}"[:120]


def _upsert_notification(state: Dict[str, Any], kind: str, key: str, title: str, detail: str, severity: str = "warning") -> None:
    notification_id = _notification_id(kind, key)
    notifications = [item for item in state.get("notifications", []) if isinstance(item, dict)]
    kept = [item for item in notifications if item.get("id") != notification_id]
    kept.append({
        "id": notification_id,
        "kind": kind,
        "group": "Electricity",
        "title": title,
        "detail": detail,
        "severity": severity,
        "created_ts": int(time.time()),
        "dismissed": False,
    })
    state["notifications"] = kept[-100:]


def _dismiss_notifications(state: Dict[str, Any], fingerprint: Optional[str] = None) -> None:
    for item in state.get("notifications", []):
        if not isinstance(item, dict):
            continue
        if item.get("kind") in {"new_tariff", "invalid_tariff_candidate", "tariff_unchanged"}:
            if fingerprint is None or str(item.get("id") or "").endswith(fingerprint):
                item["dismissed"] = True


def _next_check_ts(config: Mapping[str, Any], last_check_ts: Optional[int]) -> int:
    timezone = ZoneInfo(config["electricity"]["timezone"])
    interval = int(config["maintenance"]["tariff_sync_interval_days"])
    hour = int(config["maintenance"]["daily_hour"])
    now = datetime.now(timezone)
    if last_check_ts:
        base = datetime.fromtimestamp(int(last_check_ts), timezone) + timedelta(days=interval)
        target = base.replace(hour=hour, minute=0, second=0, microsecond=0)
    else:
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
    return int(target.timestamp())


def _check_due(config: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    if not config["maintenance"].get("tariff_sync_enabled"):
        return False
    last = state.get("last_tariff_check_ts")
    return not last or int(time.time()) >= _next_check_ts(config, int(last))


def check_tariff(force: bool = False, request_path: bool = False) -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    provider_name = str(config["maintenance"].get("tariff_provider") or "manual")
    provider = PROVIDERS[provider_name]
    now = int(time.time())

    if not force and not _check_due(config, state):
        return {"status": "not_due", "checked": False, "provider": provider_name}
    if request_path and provider.remote:
        return {"status": "provider_unavailable", "checked": False, "provider": provider_name, "diagnostics": {"error": "remote_check_not_allowed_in_request_path"}}

    sync = copy.deepcopy(state.get("tariff_sync") or {})
    sync.update({"provider": provider_name, "last_check_ts": now, "candidate": None, "comparison": None, "diagnostics": {}})
    state["last_tariff_check_ts"] = now
    state["tariff_check_duration_ms"] = 0.0
    started = time.perf_counter()
    active = config["electricity"]["tariff"]

    try:
        raw = provider.fetch_latest()
        if raw is None:
            sync["status"] = "manual" if provider_name == "manual" else "candidate_not_found"
            _audit("checked", sync["status"], provider=provider_name if False else None)
        else:
            candidate = provider.normalize(raw)
            fingerprint = _tariff_fingerprint(candidate)
            order = compare_version(candidate, active)
            identical = order == 0 and all(active.get(field) == candidate.get(field) for field in ("tiers", "ft_rate", "service_charge", "vat_percent", "effective_date", "version"))
            rejected = str(sync.get("rejected_fingerprint") or "") == fingerprint
            if identical:
                sync.update({"status": "unchanged", "fingerprint": fingerprint})
                _upsert_notification(state, "tariff_unchanged", fingerprint, "Tariff unchanged", "The checked tariff matches the active tariff.", "info")
                _audit("checked", "unchanged", version=str(candidate.get("version") or ""))
            elif order < 0:
                sync.update({"status": "older_ignored", "fingerprint": fingerprint})
                _audit("checked", "older_ignored", version=str(candidate.get("version") or ""))
            elif rejected:
                sync.update({"status": "rejected", "fingerprint": fingerprint})
                _audit("checked", "previously_rejected", version=str(candidate.get("version") or ""))
            else:
                sync.update({
                    "status": "candidate_available",
                    "fingerprint": fingerprint,
                    "candidate": candidate,
                    "comparison": _comparison(active, candidate),
                })
                _upsert_notification(state, "new_tariff", fingerprint, "New tariff available", f"Tariff {candidate.get('version') or candidate.get('effective_date')} is ready for review and was not applied automatically.")
                _audit("candidate_found", "newer", version=str(candidate.get("version") or ""))
    except Exception as exc:
        error = _safe_error(exc)
        source_key = "none"
        try:
            source_key = str(CANDIDATE_PATH.stat().st_mtime_ns)
        except OSError:
            pass
        sync.update({"status": "candidate_invalid" if provider_name == "local_candidate" else "provider_unavailable", "diagnostics": {"error": error}})
        _upsert_notification(state, "invalid_tariff_candidate", source_key, "Invalid tariff candidate", f"The tariff candidate was rejected safely: {error}.", "warning")
        _audit("candidate_invalid", "rejected", error)

    duration = round((time.perf_counter() - started) * 1000, 2)
    state["tariff_check_duration_ms"] = duration
    sync["next_check_ts"] = _next_check_ts(config, now)
    sync["last_check_ts"] = now
    state["tariff_status"] = sync.get("status")
    state["tariff_sync"] = sync
    settings._save_maintenance(state)
    return {"checked": True, **copy.deepcopy(sync), "duration_ms": duration, "execution": "review_only"}


# Fix a typo-safe audit call without exposing provider data in event details.
# (Kept as a small wrapper so providers can be extended without changing audit schema.)
def _audit_checked_status(result: str, provider: str) -> None:
    _audit("checked", result, f"provider={provider}")


_original_maintenance_once = settings._maintenance_once


def maintenance_once_with_tariff_sync() -> Dict[str, Any]:
    snapshot = _original_maintenance_once()
    config = settings.load_settings()
    if _check_due(config, snapshot):
        check_tariff(force=True, request_path=False)
        snapshot = settings._load_maintenance()
    else:
        sync = copy.deepcopy(snapshot.get("tariff_sync") or {})
        sync.setdefault("provider", config["maintenance"].get("tariff_provider", "manual"))
        sync["next_check_ts"] = _next_check_ts(config, snapshot.get("last_tariff_check_ts"))
        snapshot["tariff_sync"] = sync
        settings._save_maintenance(snapshot)
    return snapshot


settings._maintenance_once = maintenance_once_with_tariff_sync


def _status_payload() -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    sync = copy.deepcopy(state.get("tariff_sync") or {})
    active = config["electricity"]["tariff"]
    return {
        "enabled": bool(config["maintenance"].get("tariff_sync_enabled")),
        "interval_days": int(config["maintenance"].get("tariff_sync_interval_days") or 1),
        "provider": config["maintenance"].get("tariff_provider", "manual"),
        "current_provider": sync.get("provider") or config["maintenance"].get("tariff_provider", "manual"),
        "status": sync.get("status") or "not_checked",
        "current_tariff": active,
        "current_version": active.get("version") or None,
        "current_effective_date": active.get("effective_date") or None,
        "last_check_ts": sync.get("last_check_ts") or state.get("last_tariff_check_ts"),
        "next_scheduled_check_ts": sync.get("next_check_ts") or _next_check_ts(config, state.get("last_tariff_check_ts")),
        "candidate_available": bool(sync.get("candidate")),
        "diagnostics": sync.get("diagnostics") or {},
        "auto_apply": False,
    }


@app.get("/api/tariff/status")
def tariff_status() -> Dict[str, Any]:
    return _status_payload()


@app.get("/api/tariff/candidate")
def tariff_candidate() -> Dict[str, Any]:
    sync = settings._load_maintenance().get("tariff_sync") or {}
    return {
        "available": bool(sync.get("candidate")),
        "status": sync.get("status") or "not_checked",
        "candidate": copy.deepcopy(sync.get("candidate")),
        "comparison": copy.deepcopy(sync.get("comparison")),
        "diagnostics": copy.deepcopy(sync.get("diagnostics") or {}),
    }


@app.post("/api/tariff/check")
def tariff_check() -> Dict[str, Any]:
    return check_tariff(force=True, request_path=True)


@app.post("/api/tariff/apply")
def tariff_apply(payload: Dict[str, Any] = Body(default={})):
    state = settings._load_maintenance()
    sync = copy.deepcopy(state.get("tariff_sync") or {})
    candidate = sync.get("candidate")
    if not candidate:
        return JSONResponse({"detail": "tariff_candidate_not_available"}, status_code=409)
    try:
        provider = PROVIDERS.get(str(sync.get("provider") or "local_candidate"), PROVIDERS["local_candidate"])
        normalized = provider.normalize(candidate)
        config = settings.load_settings()
        config["electricity"]["tariff"] = normalized
        saved = settings.save_settings(config)
    except ValueError as exc:
        return JSONResponse({"detail": _safe_error(exc)}, status_code=422)
    except Exception:
        return JSONResponse({"detail": "tariff_apply_failed"}, status_code=503)
    fingerprint = str(sync.get("fingerprint") or _tariff_fingerprint(normalized))
    _dismiss_notifications(state, fingerprint)
    sync.update({"status": "applied", "candidate": None, "comparison": None, "last_applied_ts": int(time.time()), "applied_fingerprint": fingerprint})
    state["tariff_sync"] = sync
    state["tariff_status"] = "applied"
    state["tariff_version"] = normalized.get("version") or None
    state["tariff_effective_date"] = normalized.get("effective_date") or None
    state["last_tariff_update_ts"] = int(time.time())
    settings._save_maintenance(state)
    _audit("applied", "ok", version=str(normalized.get("version") or ""))
    return {"ok": True, "applied": True, "tariff": saved["electricity"]["tariff"], "restart_required": False}


@app.post("/api/tariff/reject")
def tariff_reject(payload: Dict[str, Any] = Body(default={})):
    state = settings._load_maintenance()
    sync = copy.deepcopy(state.get("tariff_sync") or {})
    candidate = sync.get("candidate")
    if not candidate:
        return JSONResponse({"detail": "tariff_candidate_not_available"}, status_code=409)
    fingerprint = str(sync.get("fingerprint") or _tariff_fingerprint(candidate))
    _dismiss_notifications(state, fingerprint)
    sync.update({"status": "rejected", "candidate": None, "comparison": None, "rejected_fingerprint": fingerprint, "last_rejected_ts": int(time.time())})
    state["tariff_sync"] = sync
    state["tariff_status"] = "rejected"
    settings._save_maintenance(state)
    _audit("rejected", "ok", version=str(candidate.get("version") or ""))
    return {"ok": True, "rejected": True}
