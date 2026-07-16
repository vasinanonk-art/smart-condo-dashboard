"""Persistent electricity history, usage aggregation, and configurable billing.

Samples are appended only from the existing PJ-1103 successful poll cycle. This
module creates no polling worker and stores no credentials, local keys, or raw DPS.
"""
from __future__ import annotations

import calendar
import csv
import io
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import Query

from backend import app as app_module

app = app_module.app
BANGKOK = ZoneInfo("Asia/Bangkok")
RETENTION_DAYS = max(1, int(os.getenv("ELECTRICITY_HISTORY_RETENTION_DAYS", "400")))
DEFAULT_PATH = Path.home() / ".smart-condo-dashboard" / "electricity_history.jsonl"
HISTORY_PATH = Path(os.getenv("ELECTRICITY_HISTORY_PATH", str(DEFAULT_PATH))).expanduser()
MAX_INTEGRATION_GAP_SEC = max(60, int(os.getenv("ELECTRICITY_HISTORY_MAX_GAP_SEC", "900")))
_lock = threading.RLock()
_last_prune_day: Optional[str] = None

SAFE_FIELDS = ("ts", "voltage", "current", "power", "total_energy", "source", "health")


def _number(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> Optional[int]:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BANGKOK)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _safe_sample(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    ts = _epoch(payload.get("last_success") or payload.get("ts"))
    power = _number(payload.get("power"))
    voltage = _number(payload.get("voltage"))
    current = _number(payload.get("current"))
    total = _number(payload.get("total_energy"))
    if not ts or payload.get("online") is not True:
        return None
    if all(value is None for value in (power, voltage, current, total)):
        return None
    return {
        "ts": ts,
        "voltage": voltage,
        "current": current,
        "power": power,
        "total_energy": total,
        "source": str(payload.get("source") or "tuya_local")[:40],
        "health": "healthy" if payload.get("last_error") in (None, "") else "warning",
    }


def _ensure_parent() -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)


def append_success(payload: Mapping[str, Any]) -> bool:
    """Append one successful existing-poller result; never starts its own loop."""
    sample = _safe_sample(payload)
    if sample is None:
        return False
    encoded = json.dumps(sample, separators=(",", ":"), sort_keys=True)
    with _lock:
        _ensure_parent()
        with HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        _prune_if_due(sample["ts"])
    return True


def _iter_rows() -> Iterable[Dict[str, Any]]:
    if not HISTORY_PATH.exists():
        return
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(raw, Mapping):
                    continue
                sample = _safe_sample({**raw, "online": True, "last_success": raw.get("ts")})
                if sample:
                    yield sample
    except OSError:
        return


def read_samples(start_ts: Optional[int] = None, end_ts: Optional[int] = None) -> list[Dict[str, Any]]:
    start = int(start_ts or 0)
    end = int(end_ts or time.time())
    with _lock:
        rows = [row for row in _iter_rows() if start <= int(row["ts"]) <= end]
    rows.sort(key=lambda row: int(row["ts"]))
    deduped: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        deduped[int(row["ts"])] = row
    return list(deduped.values())


def _prune_if_due(now_ts: int) -> None:
    global _last_prune_day
    day = datetime.fromtimestamp(now_ts, BANGKOK).strftime("%Y-%m-%d")
    if day == _last_prune_day:
        return
    _last_prune_day = day
    cutoff = now_ts - RETENTION_DAYS * 86400
    rows = [row for row in _iter_rows() if int(row["ts"]) >= cutoff]
    temporary = HISTORY_PATH.with_suffix(HISTORY_PATH.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, HISTORY_PATH)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _integrated_energy(rows: list[Mapping[str, Any]]) -> float:
    total = 0.0
    for previous, current in zip(rows, rows[1:]):
        dt = max(0, int(current["ts"]) - int(previous["ts"]))
        if dt <= 0 or dt > MAX_INTEGRATION_GAP_SEC:
            continue
        p1 = _number(previous.get("power"))
        p2 = _number(current.get("power"))
        if p1 is None or p2 is None:
            continue
        total += ((max(0, p1) + max(0, p2)) / 2.0) * dt / 3_600_000.0
    return max(0.0, total)


def energy_used(rows: list[Mapping[str, Any]]) -> Optional[float]:
    if len(rows) < 2:
        return None
    deltas = []
    previous_total: Optional[float] = None
    for row in rows:
        current = _number(row.get("total_energy"))
        if current is None:
            continue
        if previous_total is not None:
            delta = current - previous_total
            if 0 <= delta <= 1000:
                deltas.append(delta)
        previous_total = current
    cumulative = sum(deltas)
    if deltas and cumulative > 0:
        return round(max(0.0, cumulative), 6)
    integrated = _integrated_energy(rows)
    return round(integrated, 6) if integrated > 0 else 0.0


def summarize(rows: list[Mapping[str, Any]]) -> Dict[str, Any]:
    powers = [value for value in (_number(row.get("power")) for row in rows) if value is not None]
    return {
        "sample_count": len(rows),
        "min_power": round(min(powers), 3) if powers else None,
        "max_power": round(max(powers), 3) if powers else None,
        "avg_power": round(sum(powers) / len(powers), 3) if powers else None,
        "energy_used_kwh": energy_used(rows),
    }


def _month_start(value: datetime) -> datetime:
    return value.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_bounds(name: str, now_ts: Optional[int] = None) -> tuple[int, int]:
    now = datetime.fromtimestamp(now_ts or time.time(), BANGKOK)
    end = int(now.timestamp())
    key = name.lower()
    if key == "24h":
        start = now - timedelta(hours=24)
    elif key == "7d":
        start = now - timedelta(days=7)
    elif key == "30d":
        start = now - timedelta(days=30)
    elif key in {"month", "this_month"}:
        start = _month_start(now)
    elif key == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif key == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif key == "yesterday":
        end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = end_dt - timedelta(days=1)
        end = int(end_dt.timestamp())
    elif key == "last_month":
        end_dt = _month_start(now)
        previous_day = end_dt - timedelta(days=1)
        start = _month_start(previous_day)
        end = int(end_dt.timestamp())
    else:
        start = now - timedelta(hours=24)
    return int(start.timestamp()), end


def history_payload(range_name: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> Dict[str, Any]:
    start, end = _period_bounds(range_name)
    if from_ts is not None:
        start = int(from_ts)
    if to_ts is not None:
        end = int(to_ts)
    if end < start:
        start, end = end, start
    rows = read_samples(start, end)
    return {"range": range_name, "from": start, "to": end, "samples": rows, "summary": summarize(rows)}


def period_usage(name: str) -> Optional[float]:
    payload = history_payload(name)
    return payload["summary"]["energy_used_kwh"]


def usage_summary(current_power: Optional[float] = None) -> Dict[str, Any]:
    today = period_usage("today")
    yesterday = period_usage("yesterday")
    month = period_usage("month")
    last_month = period_usage("last_month")
    now = datetime.now(BANGKOK)
    elapsed_days = max(1.0 / 24.0, (now - _month_start(now)).total_seconds() / 86400.0)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    projected = round(month / elapsed_days * days_in_month, 3) if month is not None else None
    return {
        "today_kwh": today,
        "yesterday_kwh": yesterday,
        "month_kwh": month,
        "last_month_kwh": last_month,
        "current_power_w": _number(current_power),
        "estimated_month_end_kwh": projected,
        "diagnostics": {
            "source": "electricity_history",
            "timezone": "Asia/Bangkok",
            "history_path": str(HISTORY_PATH.name),
            "retention_days": RETENTION_DAYS,
            "insufficient_history": any(value is None for value in (today, yesterday, month, last_month)),
        },
    }


def _tariff_config() -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    raw = os.getenv("ELECTRICITY_TARIFF_CONFIG_JSON", "").strip()
    if not raw:
        return None, "tariff_not_configured"
    try:
        config = json.loads(raw)
    except json.JSONDecodeError:
        return None, "invalid_tariff_json"
    if not isinstance(config, Mapping):
        return None, "invalid_tariff_config"
    try:
        name = str(config.get("tariff_name") or "Configured tariff")[:120]
        effective = str(config.get("effective_date") or "")
        datetime.strptime(effective, "%Y-%m-%d")
        vat = float(config.get("vat_percent", 0))
        ft = float(config.get("ft_rate", 0))
        service = float(config.get("service_charge", 0))
        minimum = float(config.get("minimum_charge", 0) or 0)
        if not 0 <= vat <= 100 or min(ft, service, minimum) < 0:
            raise ValueError
        tiers_raw = config.get("tiers")
        if not isinstance(tiers_raw, list) or not tiers_raw:
            raise ValueError
        tiers = []
        previous = 0.0
        unlimited_seen = False
        for item in tiers_raw:
            if not isinstance(item, Mapping):
                raise ValueError
            rate = float(item.get("rate"))
            limit_raw = item.get("up_to_kwh")
            limit = None if limit_raw is None else float(limit_raw)
            if rate < 0 or (limit is not None and (limit <= previous or unlimited_seen)):
                raise ValueError
            if limit is None:
                unlimited_seen = True
            else:
                previous = limit
            tiers.append({"up_to_kwh": limit, "rate": rate})
        return {"tariff_name": name, "effective_date": effective, "tiers": tiers, "ft_rate": ft, "service_charge": service, "vat_percent": vat, "minimum_charge": minimum}, None
    except (TypeError, ValueError):
        return None, "invalid_tariff_config"


def calculate_bill(usage_kwh: Optional[float], estimated: bool = True) -> Dict[str, Any]:
    config, error = _tariff_config()
    if config is None:
        return {"configured": False, "total": None, "currency": "THB", "diagnostics": {"reason": error, "source": "environment_json"}}
    if usage_kwh is None:
        return {"configured": True, "tariff_name": config["tariff_name"], "effective_date": config["effective_date"], "usage_kwh": None, "total": None, "currency": "THB", "estimated": estimated, "diagnostics": {"reason": "insufficient_history", "source": "environment_json"}}
    usage = max(0.0, float(usage_kwh))
    remaining = usage
    lower = 0.0
    base = 0.0
    for tier in config["tiers"]:
        upper = tier["up_to_kwh"]
        quantity = remaining if upper is None else min(remaining, max(0.0, upper - lower))
        base += quantity * tier["rate"]
        remaining -= quantity
        if upper is not None:
            lower = upper
        if remaining <= 0:
            break
    ft_charge = usage * config["ft_rate"]
    service = config["service_charge"]
    subtotal = max(config["minimum_charge"], base + ft_charge + service)
    vat = subtotal * config["vat_percent"] / 100.0
    total = subtotal + vat
    money = lambda value: round(value + 1e-9, 2)
    return {
        "configured": True,
        "tariff_name": config["tariff_name"],
        "effective_date": config["effective_date"],
        "usage_kwh": round(usage, 4),
        "base_energy_charge": money(base),
        "ft_charge": money(ft_charge),
        "service_charge": money(service),
        "subtotal": money(subtotal),
        "vat": money(vat),
        "total": money(total),
        "currency": "THB",
        "estimated": estimated,
        "diagnostics": {"source": "environment_json", "official_invoice": False},
    }


@app.get("/api/electricity/history")
def get_history(range: str = Query("24h"), from_ts: Optional[int] = Query(None, alias="from"), to_ts: Optional[int] = Query(None, alias="to")) -> Dict[str, Any]:
    selected = range if range in {"24h", "7d", "30d", "month", "year"} else "24h"
    return history_payload(selected, from_ts, to_ts)


@app.get("/api/electricity/summary")
def get_summary() -> Dict[str, Any]:
    try:
        from backend import electricity_provider
        current = electricity_provider.electricity_status().get("power")
    except Exception:
        current = None
    return usage_summary(current)


@app.get("/api/electricity/billing")
def get_billing(range: str = Query("month"), from_ts: Optional[int] = Query(None, alias="from"), to_ts: Optional[int] = Query(None, alias="to")) -> Dict[str, Any]:
    allowed = {"today", "yesterday", "month", "last_month"}
    if from_ts is not None or to_ts is not None:
        usage = history_payload("custom", from_ts, to_ts)["summary"]["energy_used_kwh"]
    else:
        usage = period_usage(range if range in allowed else "month")
    return calculate_bill(usage, estimated=range != "last_month")
