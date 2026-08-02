"""Tariff-effective billing segmentation for cycles spanning a tariff change."""
from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import electricity_billing_cycle as billing
from backend import electricity_history as history
from backend import mea_tariff_provider as mea

BILLING_CACHE_TTL_SEC = 8.0
BILLING_CACHE_WAIT_SEC = 30.0
_billing_cache_lock = threading.Lock()
_billing_cache_key: Optional[tuple[Any, ...]] = None
_billing_cache_value: Optional[Dict[str, Any]] = None
_billing_cache_at = 0.0
_billing_inflight: Dict[tuple[Any, ...], threading.Event] = {}


def _money(value: float) -> float:
    return round(value + 1e-9, 2)


def calculate_with_tariff(usage_kwh: Optional[float], tariff: Mapping[str, Any], include_service: bool = True) -> Dict[str, Any]:
    if usage_kwh is None:
        return {"usage_kwh": None, "total": None}
    usage = max(0.0, float(usage_kwh))
    remaining, lower, base = usage, 0.0, 0.0
    for tier in tariff.get("tiers") or []:
        upper = tier.get("up_to_kwh")
        quantity = remaining if upper is None else min(remaining, max(0.0, float(upper) - lower))
        base += quantity * float(tier.get("rate") or 0)
        remaining -= quantity
        if upper is not None:
            lower = float(upper)
        if remaining <= 0:
            break
    ft = usage * float(tariff.get("ft_rate") or 0)
    service = float(tariff.get("service_charge") or 0) if include_service else 0.0
    subtotal = max(float(tariff.get("minimum_charge") or 0) if include_service else 0.0, base + ft + service)
    vat = subtotal * float(tariff.get("vat_percent") or 0) / 100.0
    return {
        "usage_kwh": round(usage, 4),
        "base_energy_charge": _money(base),
        "ft_charge": _money(ft),
        "service_charge": _money(service),
        "subtotal": _money(subtotal),
        "vat": _money(vat),
        "total": _money(subtotal + vat),
    }


def _history_records() -> list[Dict[str, Any]]:
    try:
        raw = json.loads(mea.TARIFF_HISTORY_PATH.read_text(encoding="utf-8"))
        rows = raw.get("tariffs", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
        return [row for row in rows if isinstance(row, dict) and isinstance(row.get("tariff"), dict)]
    except Exception:
        return []


def _effective_tariffs(start: int, end: int) -> list[Dict[str, Any]]:
    rows = _history_records()
    try:
        from backend import dashboard_settings as settings
        active = settings.load_settings()["electricity"]["tariff"]
        effective = int(datetime.strptime(str(active.get("effective_date")), "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Bangkok")).timestamp())
        rows.append({"effective_ts": effective, "tariff": active})
    except Exception:
        pass
    unique: Dict[tuple[int, str], Dict[str, Any]] = {}
    for row in rows:
        key = (int(row.get("effective_ts") or 0), str((row.get("tariff") or {}).get("version") or ""))
        unique[key] = row
    ordered = sorted(unique.values(), key=lambda row: int(row.get("effective_ts") or 0))
    if not ordered:
        return []
    prior = [row for row in ordered if int(row.get("effective_ts") or 0) <= start]
    selected = ([prior[-1]] if prior else []) + [row for row in ordered if start < int(row.get("effective_ts") or 0) < end]
    return selected or [ordered[-1]]


def segmented_bill(start: int, end: int, rows: list[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    tariffs = _effective_tariffs(start, end)
    if not tariffs:
        return None
    boundaries = [start] + [int(row["effective_ts"]) for row in tariffs[1:]] + [end]
    segments = []
    for index, record in enumerate(tariffs):
        seg_start, seg_end = boundaries[index], boundaries[index + 1]
        segment_rows = [row for row in rows if seg_start <= int(row.get("ts") or 0) <= seg_end]
        usage = history.energy_used(segment_rows) if len(segment_rows) >= 2 else 0.0
        charge = calculate_with_tariff(usage, record["tariff"], include_service=index == len(tariffs) - 1)
        segments.append({
            "from_ts": seg_start,
            "to_ts": seg_end,
            "tariff_version": record["tariff"].get("version"),
            "effective_date": record["tariff"].get("effective_date"),
            "usage_kwh": charge["usage_kwh"],
            "cost": charge["total"],
            **charge,
        })
    if len(segments) <= 1:
        return {"tariff_segments": segments}
    totals = {field: _money(sum(float(segment.get(field) or 0) for segment in segments)) for field in ("base_energy_charge", "ft_charge", "service_charge", "subtotal", "vat", "total")}
    return {**totals, "usage_kwh": round(sum(float(segment.get("usage_kwh") or 0) for segment in segments), 4), "tariff_segments": segments, "tariff_segmented": True}


def _file_fingerprint(path) -> tuple[int, int]:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return 0, 0


def _request_key(selected: str, start: int, end: int) -> tuple[Any, ...]:
    try:
        from backend import dashboard_settings as settings
        settings_path = settings.SETTINGS_PATH
    except Exception:
        settings_path = history.HISTORY_PATH.with_name("settings.json")
    return (
        selected,
        start,
        end,
        _file_fingerprint(history.HISTORY_PATH),
        _file_fingerprint(mea.TARIFF_HISTORY_PATH),
        _file_fingerprint(settings_path),
    )


def _calculate_segmented_payload(selected: str, start: int, end: int) -> Dict[str, Any]:
    rows = history.read_samples(start, end)
    payload = billing._billing_cycle_payload_from_rows(selected, start, end, rows)
    segmented = segmented_bill(start, end, rows)
    if segmented:
        payload.update(segmented)
        if segmented.get("tariff_segmented"):
            payload["actual_partial_cost"] = segmented.get("total")
    payload.setdefault("tariff_segments", [])
    return payload


def _cached_segmented_payload(selected: str, start: int, end: int) -> Dict[str, Any]:
    global _billing_cache_key, _billing_cache_value, _billing_cache_at
    key = _request_key(selected, start, end)
    while True:
        with _billing_cache_lock:
            if (
                _billing_cache_key == key
                and _billing_cache_value is not None
                and time.monotonic() - _billing_cache_at < BILLING_CACHE_TTL_SEC
            ):
                return copy.deepcopy(_billing_cache_value)
            event = _billing_inflight.get(key)
            if event is None:
                event = threading.Event()
                _billing_inflight[key] = event
                owner = True
            else:
                owner = False
        if owner:
            break
        if not event.wait(BILLING_CACHE_WAIT_SEC):
            raise TimeoutError("billing cycle calculation timed out")

    try:
        payload = _calculate_segmented_payload(selected, start, end)
    except Exception:
        with _billing_cache_lock:
            _billing_inflight.pop(key, None)
            event.set()
        raise

    with _billing_cache_lock:
        _billing_cache_key = key
        _billing_cache_value = copy.deepcopy(payload)
        _billing_cache_at = time.monotonic()
        _billing_inflight.pop(key, None)
        event.set()
    return payload


def billing_cycle_payload_segmented(period: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> Dict[str, Any]:
    selected, start, end = billing._billing_request_bounds(period, from_ts, to_ts)
    return _cached_segmented_payload(selected, start, end)


billing.billing_cycle_payload = billing_cycle_payload_segmented
for route in billing.app.routes:
    if getattr(route, "path", None) == "/api/electricity/billing-cycle" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = billing.get_billing_cycle
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = billing.get_billing_cycle
