"""Persistent electricity bill reconciliation and daily reminder integration."""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import Body, HTTPException, Request

from backend import app as app_module
from backend import dashboard_settings as settings
from backend import electricity_billing_cycle as billing
from backend import ir_command_audit

app = app_module.app
TZ = ZoneInfo("Asia/Bangkok")
SCHEMA_VERSION = 1
RECONCILIATION_PATH = settings.DATA_DIR / "electricity_reconciliation.json"
_LOCK = threading.RLock()
_RATE_LOCK = threading.Lock()
_WRITE_ATTEMPTS: Dict[str, deque[float]] = defaultdict(deque)
_WRITE_LIMIT = 30
_WRITE_WINDOW_SEC = 60.0
_REMINDER_KINDS = {"cycle_closed", "due_soon_day_10", "due_today_day_13"}


def _local_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(int(timestamp), TZ)


def _date_text(timestamp: int) -> str:
    return _local_datetime(timestamp).date().isoformat()


def cycle_identity(start_ts: int, end_ts: int) -> str:
    """Return a deterministic identity from start and exclusive end dates."""
    if int(end_ts) <= int(start_ts):
        raise ValueError("invalid_cycle_bounds")
    return f"{_date_text(start_ts)}_{_date_text(end_ts)}"


def due_date_for_cycle_end(end_ts: int) -> str:
    end = _local_datetime(end_ts).date()
    return date(end.year, end.month, 13).isoformat()


def _iso_now(now_ts: Optional[int] = None) -> str:
    return datetime.fromtimestamp(int(now_ts or time.time()), TZ).isoformat(timespec="seconds")


def _finite_nonnegative(value: Any, field: str, *, nullable: bool = False) -> Optional[float]:
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"invalid_{field}")
    return round(result, 2 if field != "calculated_kwh" else 4)


def _valid_date(value: Any, field: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc


def _valid_timestamp(value: Any, field: str, *, nullable: bool = False) -> Optional[str]:
    if value is None and nullable:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid_{field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"invalid_{field}")
    return parsed.astimezone(TZ).isoformat(timespec="seconds")


def _differences(actual: Optional[float], calculated: float) -> tuple[Optional[float], Optional[float]]:
    if actual is None:
        return None, None
    difference = round(actual - calculated, 2)
    percent = None if calculated == 0 else round(difference / calculated * 100.0, 2)
    return difference, percent


def _validate_record(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_reconciliation_record")
    required = {
        "cycle_id", "cycle_start", "cycle_end", "due_date", "calculated_cost",
        "calculated_kwh", "actual_bill_amount", "difference_amount",
        "difference_percent", "payment_status", "paid_at", "recorded_at", "updated_at",
    }
    if not required.issubset(raw) or set(raw) - required - {"reminder_events"}:
        raise ValueError("invalid_reconciliation_fields")
    start = _valid_date(raw.get("cycle_start"), "cycle_start")
    end = _valid_date(raw.get("cycle_end"), "cycle_end")
    expected_id = f"{start}_{end}"
    if str(raw.get("cycle_id") or "") != expected_id or end <= start:
        raise ValueError("invalid_cycle_id")
    due = _valid_date(raw.get("due_date"), "due_date")
    calculated_cost = _finite_nonnegative(raw.get("calculated_cost"), "calculated_cost")
    calculated_kwh = _finite_nonnegative(raw.get("calculated_kwh"), "calculated_kwh")
    actual = _finite_nonnegative(raw.get("actual_bill_amount"), "actual_bill_amount", nullable=True)
    payment = str(raw.get("payment_status") or "")
    if payment not in {"paid", "unpaid"}:
        raise ValueError("invalid_payment_status")
    paid_at = _valid_timestamp(raw.get("paid_at"), "paid_at", nullable=True)
    if (payment == "paid") != (paid_at is not None):
        raise ValueError("invalid_paid_at")
    recorded = _valid_timestamp(raw.get("recorded_at"), "recorded_at")
    updated = _valid_timestamp(raw.get("updated_at"), "updated_at")
    events = raw.get("reminder_events") or []
    if not isinstance(events, list) or any(str(item) not in _REMINDER_KINDS for item in events):
        raise ValueError("invalid_reminder_events")
    difference, percent = _differences(actual, calculated_cost)
    raw_difference = raw.get("difference_amount")
    if raw_difference is not None:
        try:
            raw_difference = round(float(raw_difference), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_difference_amount") from exc
        if not math.isfinite(raw_difference) or difference is None or raw_difference != difference:
            raise ValueError("invalid_difference_amount")
    elif difference is not None:
        raise ValueError("invalid_difference_amount")
    raw_percent = raw.get("difference_percent")
    if raw_percent is not None:
        try:
            raw_percent = round(float(raw_percent), 2)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_difference_percent") from exc
        if not math.isfinite(raw_percent) or percent is None or raw_percent != percent:
            raise ValueError("invalid_difference_percent")
    elif percent is not None:
        raise ValueError("invalid_difference_percent")
    return {
        "cycle_id": expected_id,
        "cycle_start": start,
        "cycle_end": end,
        "due_date": due,
        "calculated_cost": calculated_cost,
        "calculated_kwh": calculated_kwh,
        "actual_bill_amount": actual,
        "difference_amount": difference,
        "difference_percent": percent,
        "payment_status": payment,
        "paid_at": paid_at,
        "recorded_at": recorded,
        "updated_at": updated,
        "reminder_events": sorted(set(str(item) for item in events)),
    }


def _empty_store() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": []}


def _load_store() -> Dict[str, Any]:
    if not RECONCILIATION_PATH.exists():
        return _empty_store()
    try:
        raw = json.loads(RECONCILIATION_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("invalid_reconciliation_schema")
        records = raw.get("records")
        if not isinstance(records, list):
            raise ValueError("invalid_reconciliation_records")
        validated = [_validate_record(item) for item in records]
        identifiers = [item["cycle_id"] for item in validated]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate_cycle_id")
        return {"schema_version": SCHEMA_VERSION, "records": validated}
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError("reconciliation_store_invalid") from exc


def _save_store(store: Mapping[str, Any]) -> None:
    validated = {
        "schema_version": SCHEMA_VERSION,
        "records": [_validate_record(item) for item in store.get("records", [])],
    }
    identifiers = [item["cycle_id"] for item in validated["records"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate_cycle_id")
    settings._atomic_json_write(RECONCILIATION_PATH, validated, backup=True)
    os.chmod(RECONCILIATION_PATH, 0o600)


def _projection_quality(payload: Mapping[str, Any]) -> Dict[str, Any]:
    coverage = payload.get("coverage") or {}
    available = payload.get("projection_status") == "available"
    missing_start = bool(coverage.get("missing_start"))
    return {
        "start_coverage": "incomplete" if missing_start else "complete",
        "projection_status": "available" if available else "unavailable",
        "projection_reliability": "limited_incomplete_start" if available and missing_start else "normal" if available else "unavailable",
        "coverage_percent": coverage.get("coverage_percent"),
    }


def _cycle_metadata(payload: Mapping[str, Any], now_ts: Optional[int] = None) -> Dict[str, Any]:
    now = datetime.fromtimestamp(int(now_ts or time.time()), TZ).date()
    start = _date_text(int(payload["billing_period_start"]))
    end = _date_text(int(payload["billing_period_end"]))
    due = due_date_for_cycle_end(int(payload["billing_period_end"]))
    return {
        "cycle_id": f"{start}_{end}",
        "cycle_start": start,
        "cycle_end": end,
        "due_date": due,
        "days_until_cycle_end": (date.fromisoformat(end) - now).days,
        "days_until_due": (date.fromisoformat(due) - now).days,
        "projection_quality": _projection_quality(payload),
    }


def _authoritative_record(period: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    payload = billing.billing_cycle_payload(period)
    start_ts = int(payload["billing_period_start"])
    end_ts = int(payload["billing_period_end"])
    calculated_cost = _finite_nonnegative(payload.get("actual_partial_cost"), "calculated_cost")
    calculated_kwh = _finite_nonnegative(payload.get("actual_partial_usage_kwh"), "calculated_kwh")
    timestamp = _iso_now(now_ts)
    return _validate_record({
        "cycle_id": cycle_identity(start_ts, end_ts),
        "cycle_start": _date_text(start_ts),
        "cycle_end": _date_text(end_ts),
        "due_date": due_date_for_cycle_end(end_ts),
        "calculated_cost": calculated_cost,
        "calculated_kwh": calculated_kwh,
        "actual_bill_amount": None,
        "difference_amount": None,
        "difference_percent": None,
        "payment_status": "unpaid",
        "paid_at": None,
        "recorded_at": timestamp,
        "updated_at": timestamp,
        "reminder_events": [],
    })


def _find(records: list[Dict[str, Any]], cycle_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in records if item["cycle_id"] == cycle_id), None)


def _upsert_authoritative(period: str, now_ts: Optional[int] = None) -> Dict[str, Any]:
    candidate = _authoritative_record(period, now_ts)
    with _LOCK:
        store = _load_store()
        existing = _find(store["records"], candidate["cycle_id"])
        if existing is None:
            store["records"].append(candidate)
            _save_store(store)
            return copy.deepcopy(candidate)
        return copy.deepcopy(existing)


def _audit(request: Request, action: str, cycle_id: str, result: str) -> None:
    user = ir_command_audit.stable_user_identifier(getattr(request.state, "dashboard_user", None))
    record = {"event": "electricity_reconciliation_audit", "ts": int(time.time()), "user": user, "action": action, "cycle_id": cycle_id, "result": result}
    print("electricity_reconciliation_audit " + json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)


def _rate_limit(request: Request) -> None:
    key = ir_command_audit.stable_user_identifier(getattr(request.state, "dashboard_user", None))
    now = time.monotonic()
    with _RATE_LOCK:
        attempts = _WRITE_ATTEMPTS[key]
        while attempts and attempts[0] <= now - _WRITE_WINDOW_SEC:
            attempts.popleft()
        if len(attempts) >= _WRITE_LIMIT:
            raise HTTPException(status_code=429, detail="reconciliation_rate_limited")
        attempts.append(now)


def _update_record(cycle_id: str, changes: Mapping[str, Any], now_ts: Optional[int] = None) -> Dict[str, Any]:
    allowed = {"actual_bill_amount", "due_date", "payment_status"}
    if not changes or set(changes) - allowed:
        raise ValueError("invalid_reconciliation_update")
    with _LOCK:
        store = _load_store()
        record = _find(store["records"], cycle_id)
        if record is None:
            previous = _authoritative_record("previous_billing_cycle", now_ts)
            if previous["cycle_id"] != cycle_id:
                raise KeyError("reconciliation_not_found")
            store["records"].append(previous)
            record = previous
        if "actual_bill_amount" in changes:
            record["actual_bill_amount"] = _finite_nonnegative(changes["actual_bill_amount"], "actual_bill_amount", nullable=True)
        if "due_date" in changes:
            record["due_date"] = _valid_date(changes["due_date"], "due_date")
        if "payment_status" in changes:
            status = str(changes["payment_status"])
            if status not in {"paid", "unpaid"}:
                raise ValueError("invalid_payment_status")
            record["payment_status"] = status
            record["paid_at"] = _iso_now(now_ts) if status == "paid" else None
        difference, percent = _differences(record.get("actual_bill_amount"), record["calculated_cost"])
        record["difference_amount"] = difference
        record["difference_percent"] = percent
        record["updated_at"] = _iso_now(now_ts)
        validated = _validate_record(record)
        store["records"] = [validated if item["cycle_id"] == cycle_id else item for item in store["records"]]
        _save_store(store)
        return copy.deepcopy(validated)


def _notification_id(cycle_id: str, kind: str) -> str:
    return f"electricity.{cycle_id}.{kind}"


def _add_notification(state: Dict[str, Any], record: Mapping[str, Any], kind: str, now_ts: int) -> None:
    identifier = _notification_id(str(record["cycle_id"]), kind)
    existing = [item for item in state.get("notifications", []) if isinstance(item, dict) and item.get("id") != identifier]
    if kind == "cycle_closed":
        title = "Electricity billing cycle closed"
        detail = f"{record['cycle_start']} to {record['cycle_end']} (end exclusive): {record['calculated_kwh']:.4f} kWh, {record['calculated_cost']:.2f} THB. Due {record['due_date']}."
        severity = "info"
    elif kind == "due_soon_day_10":
        title, detail, severity = "Electricity payment due soon", f"Cycle {record['cycle_id']} is unpaid and due {record['due_date']}.", "warning"
    else:
        title, detail, severity = "Electricity payment due today", f"Cycle {record['cycle_id']} is unpaid and due today.", "warning"
    existing.append({"id": identifier, "kind": kind, "title": title, "detail": detail, "severity": severity, "created_ts": now_ts, "dismissed": False, "read": False})
    state["notifications"] = existing[-100:]


def process_daily_reminders(state: Mapping[str, Any], now_ts: Optional[int] = None) -> Dict[str, Any]:
    now_value = int(now_ts or time.time())
    today = _local_datetime(now_value).date()
    snapshot = copy.deepcopy(dict(state))
    with _LOCK:
        store = _load_store()
        changed = False
        current_start, _ = billing.billing_period_bounds("current_billing_cycle", now_value)
        if _local_datetime(current_start).date() == today:
            candidate = _authoritative_record("previous_billing_cycle", now_value)
            record = _find(store["records"], candidate["cycle_id"])
            if record is None:
                store["records"].append(candidate)
                record = candidate
                changed = True
            if "cycle_closed" not in record["reminder_events"]:
                _add_notification(snapshot, record, "cycle_closed", now_value)
                record["reminder_events"].append("cycle_closed")
                record["updated_at"] = _iso_now(now_value)
                changed = True
        for record in store["records"]:
            if record["payment_status"] == "paid":
                continue
            due = date.fromisoformat(record["due_date"])
            kind = "due_soon_day_10" if today.day == 10 and due.year == today.year and due.month == today.month else "due_today_day_13" if today == due else None
            if kind and kind not in record["reminder_events"]:
                _add_notification(snapshot, record, kind, now_value)
                record["reminder_events"].append(kind)
                record["updated_at"] = _iso_now(now_value)
                changed = True
        if changed:
            _save_store(store)
    return snapshot


_ORIGINAL_MAINTENANCE_ONCE = settings._maintenance_once


def _maintenance_with_reconciliation() -> Dict[str, Any]:
    snapshot = process_daily_reminders(_ORIGINAL_MAINTENANCE_ONCE())
    settings._save_maintenance(snapshot)
    return snapshot


settings._maintenance_once = _maintenance_with_reconciliation


@app.get("/api/electricity/reconciliation/current")
def current_reconciliation() -> Dict[str, Any]:
    active = billing.billing_cycle_payload("current_billing_cycle")
    previous = billing.billing_cycle_payload("previous_billing_cycle")
    previous_id = cycle_identity(int(previous["billing_period_start"]), int(previous["billing_period_end"]))
    with _LOCK:
        record = _find(_load_store()["records"], previous_id)
    return {"active_cycle": _cycle_metadata(active), "latest_closed_cycle": copy.deepcopy(record), "latest_closed_cycle_id": previous_id}


@app.get("/api/electricity/reconciliation")
def reconciliation_history() -> Dict[str, Any]:
    with _LOCK:
        records = sorted(_load_store()["records"], key=lambda item: item["cycle_end"], reverse=True)
    return {"schema_version": SCHEMA_VERSION, "records": copy.deepcopy(records), "count": len(records)}


@app.put("/api/electricity/reconciliation/{cycle_id}")
def save_actual_bill(cycle_id: str, request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _rate_limit(request)
    if set(payload) - {"actual_bill_amount", "due_date"}:
        _audit(request, "update", cycle_id, "rejected")
        raise HTTPException(status_code=422, detail="invalid_reconciliation_update")
    try:
        record = _update_record(cycle_id, payload)
    except KeyError:
        _audit(request, "update", cycle_id, "not_found")
        raise HTTPException(status_code=404, detail="reconciliation_not_found") from None
    except ValueError as exc:
        _audit(request, "update", cycle_id, "rejected")
        raise HTTPException(status_code=422, detail=str(exc)) from None
    _audit(request, "update", cycle_id, "ok")
    return {"ok": True, "record": record}


@app.post("/api/electricity/reconciliation/{cycle_id}/paid")
def mark_paid(cycle_id: str, request: Request) -> Dict[str, Any]:
    _rate_limit(request)
    try:
        record = _update_record(cycle_id, {"payment_status": "paid"})
    except KeyError:
        _audit(request, "mark_paid", cycle_id, "not_found")
        raise HTTPException(status_code=404, detail="reconciliation_not_found") from None
    _audit(request, "mark_paid", cycle_id, "ok")
    return {"ok": True, "record": record}


@app.post("/api/electricity/reconciliation/{cycle_id}/unpaid")
def mark_unpaid(cycle_id: str, request: Request) -> Dict[str, Any]:
    _rate_limit(request)
    try:
        record = _update_record(cycle_id, {"payment_status": "unpaid"})
    except KeyError:
        _audit(request, "mark_unpaid", cycle_id, "not_found")
        raise HTTPException(status_code=404, detail="reconciliation_not_found") from None
    _audit(request, "mark_unpaid", cycle_id, "ok")
    return {"ok": True, "record": record}
