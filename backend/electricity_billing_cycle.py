"""Billing-cycle aware electricity usage and safe tariff sync diagnostics.

The configured cycle cuts over at 00:00 Asia/Bangkok on the second day of each
month by default. This module creates no meter polling loop and never fetches an
unverified tariff source.
"""
from __future__ import annotations

import calendar
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import Query

from backend import app as app_module
from backend import electricity_history as history

app = app_module.app
TIMEZONE_NAME = os.getenv("ELECTRICITY_BILLING_TIMEZONE", "Asia/Bangkok").strip() or "Asia/Bangkok"
try:
    BILLING_TZ = ZoneInfo(TIMEZONE_NAME)
except Exception:
    TIMEZONE_NAME = "Asia/Bangkok"
    BILLING_TZ = ZoneInfo(TIMEZONE_NAME)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


BILLING_CYCLE_DAY = _bounded_int("ELECTRICITY_BILLING_CYCLE_DAY", 2, 1, 31)
PROJECTION_MIN_HOURS = _bounded_int("ELECTRICITY_PROJECTION_MIN_HOURS", 24, 1, 24 * 31)
TARIFF_SYNC_ENABLED = os.getenv("ELECTRICITY_TARIFF_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
TARIFF_SYNC_HOUR = _bounded_int("ELECTRICITY_TARIFF_SYNC_HOUR", 3, 0, 23)
TARIFF_SYNC_INTERVAL_DAYS = _bounded_int("ELECTRICITY_TARIFF_SYNC_INTERVAL_DAYS", 1, 1, 365)
TARIFF_SOURCE = os.getenv("ELECTRICITY_TARIFF_SOURCE", "manual").strip() or "manual"


def _cycle_boundary(year: int, month: int) -> datetime:
    day = min(BILLING_CYCLE_DAY, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, 0, 0, 0, tzinfo=BILLING_TZ)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def billing_period_bounds(period: str = "current_billing_cycle", now_ts: Optional[int] = None) -> tuple[int, int]:
    now = datetime.fromtimestamp(now_ts or time.time(), BILLING_TZ)
    this_boundary = _cycle_boundary(now.year, now.month)
    if now >= this_boundary:
        current_start = this_boundary
        next_year, next_month = _next_month(now.year, now.month)
        current_end = _cycle_boundary(next_year, next_month)
    else:
        prev_year, prev_month = _previous_month(now.year, now.month)
        current_start = _cycle_boundary(prev_year, prev_month)
        current_end = this_boundary

    key = str(period or "current_billing_cycle").lower()
    if key == "previous_billing_cycle":
        prev_year, prev_month = _previous_month(current_start.year, current_start.month)
        return int(_cycle_boundary(prev_year, prev_month).timestamp()), int(current_start.timestamp())
    if key in {"current_billing_cycle", "billing_cycle", "cycle"}:
        return int(current_start.timestamp()), int(current_end.timestamp())
    if key == "calendar_month":
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        next_year, next_month = _next_month(now.year, now.month)
        return int(month_start.timestamp()), int(datetime(next_year, next_month, 1, tzinfo=BILLING_TZ).timestamp())
    return history._period_bounds(key, now_ts)


def _period_label(start_ts: int, end_ts: int) -> str:
    start = datetime.fromtimestamp(start_ts, BILLING_TZ)
    inclusive_end = datetime.fromtimestamp(max(start_ts, end_ts - 1), BILLING_TZ)
    return f"{start.day} {start.strftime('%b %Y')} – {inclusive_end.day} {inclusive_end.strftime('%b %Y')}"


def _coverage(start_ts: int, end_ts: int, rows: list[Dict[str, Any]]) -> Dict[str, Any]:
    requested = max(1, end_ts - start_ts)
    first = int(rows[0]["ts"]) if rows else None
    last = int(rows[-1]["ts"]) if rows else None
    actual_start = max(start_ts, first) if first is not None else None
    actual_end = min(end_ts, last) if last is not None else None
    available = max(0, actual_end - actual_start) if actual_start is not None and actual_end is not None else 0
    missing_start = first is None or first > start_ts + history.MAX_INTEGRATION_GAP_SEC
    missing_end = last is None or last < min(end_ts, int(time.time())) - history.MAX_INTEGRATION_GAP_SEC
    complete = bool(rows) and not missing_start and not missing_end
    return {
        "requested_from_ts": start_ts,
        "requested_to_ts": end_ts,
        "actual_from_ts": first,
        "actual_to_ts": last,
        "calculation_from_ts": actual_start,
        "calculation_to_ts": actual_end,
        "complete": complete,
        "coverage_complete": complete,
        "coverage_percent": round(min(100.0, available / requested * 100.0), 2),
        "missing_start": missing_start,
        "missing_end": missing_end,
        "sample_count": len(rows),
    }


def billing_cycle_payload(period: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> Dict[str, Any]:
    if from_ts is not None or to_ts is not None:
        start = int(from_ts or 0)
        end = int(to_ts or time.time())
        if end < start:
            start, end = end, start
        selected = "custom"
    else:
        selected = period if period in {"current_billing_cycle", "previous_billing_cycle", "calendar_month", "today", "yesterday"} else "current_billing_cycle"
        start, end = billing_period_bounds(selected)
    rows = history.read_samples(start, end)
    coverage = _coverage(start, end, rows)
    usage = history.energy_used(rows) if len(rows) >= 2 else None
    bill = history.calculate_bill(usage, estimated=selected != "previous_billing_cycle")
    duration_hours = 0.0
    if coverage["actual_from_ts"] is not None and coverage["actual_to_ts"] is not None:
        duration_hours = max(0.0, (coverage["actual_to_ts"] - coverage["actual_from_ts"]) / 3600.0)
    projected_usage = None
    projected_bill = None
    if usage is not None and duration_hours >= PROJECTION_MIN_HOURS and selected == "current_billing_cycle":
        elapsed = max(1, int(time.time()) - start)
        full = max(1, end - start)
        projected_usage = round(max(0.0, usage * full / elapsed), 4)
        projected_bill = history.calculate_bill(projected_usage, estimated=True).get("total")
    return {
        **bill,
        "range": selected,
        "billing_cycle_day": BILLING_CYCLE_DAY,
        "billing_timezone": TIMEZONE_NAME,
        "billing_period_start": start,
        "billing_period_end": end,
        "billing_period_label": _period_label(start, end),
        "actual_partial_usage_kwh": usage,
        "actual_partial_cost": bill.get("total"),
        "projected_cycle_usage_kwh": projected_usage,
        "projected_cycle_bill": projected_bill,
        "projection_status": "available" if projected_usage is not None else "insufficient_projection_history",
        "coverage": coverage,
    }


@app.get("/api/electricity/billing-cycle/status")
def billing_cycle_status() -> Dict[str, Any]:
    current_start, current_end = billing_period_bounds("current_billing_cycle")
    previous_start, previous_end = billing_period_bounds("previous_billing_cycle")
    return {
        "configured": True,
        "billing_cycle_day": BILLING_CYCLE_DAY,
        "timezone": TIMEZONE_NAME,
        "current_period": {"from_ts": current_start, "to_ts": current_end, "label": _period_label(current_start, current_end)},
        "previous_period": {"from_ts": previous_start, "to_ts": previous_end, "label": _period_label(previous_start, previous_end)},
    }


@app.get("/api/electricity/billing-cycle")
def get_billing_cycle(
    range: str = Query("current_billing_cycle"),
    from_ts: Optional[int] = Query(None, alias="from"),
    to_ts: Optional[int] = Query(None, alias="to"),
) -> Dict[str, Any]:
    return billing_cycle_payload(range, from_ts, to_ts)


@app.get("/api/electricity/tariff/sync-status")
def tariff_sync_status() -> Dict[str, Any]:
    config, error = history._tariff_config()
    return {
        "enabled": TARIFF_SYNC_ENABLED,
        "source": TARIFF_SOURCE,
        "sync_interval_days": TARIFF_SYNC_INTERVAL_DAYS,
        "sync_hour": TARIFF_SYNC_HOUR,
        "timezone": TIMEZONE_NAME,
        "last_checked_ts": None,
        "last_updated_ts": None,
        "effective_date": config.get("effective_date") if config else None,
        "version": None,
        "status": "disabled" if not TARIFF_SYNC_ENABLED and TARIFF_SOURCE != "manual" else "manual_update_required" if TARIFF_SOURCE == "manual" else "not_configured" if error else "ready",
        "diagnostics": {"reason": error} if error else {},
    }
