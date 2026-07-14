"""Electricity provider with Home Assistant priority and PJ-1103 fallbacks."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional

from backend import app as app_module
from backend.device_framework import UnifiedDevice
from backend.device_registry import registry

app = app_module.app
_CACHE_SEC = max(5, int(os.getenv("ELECTRICITY_CACHE_SEC", "15")))
_TIME_OUT = max(0.5, float(os.getenv("ELECTRICITY_HA_TIMEOUT_SEC", "3")))
_HA_STALE_SEC = max(30, int(os.getenv("ELECTRICITY_STALE_SEC", "180")))
_LOCAL_STALE_SEC = max(60, int(os.getenv("ELECTRICITY_LOCAL_STALE_SEC", "90")))
_lock = threading.RLock()
_cache: Dict[str, Any] = {"ts": 0, "payload": None}

METRICS = (
    "voltage", "current", "power", "energy_today", "energy_month",
    "total_energy", "frequency", "power_factor",
)
_DEVICE_CLASS_MAP = {
    "voltage": "voltage", "current": "current", "power": "power",
    "frequency": "frequency", "power_factor": "power_factor", "energy": "total_energy",
}
_KEYWORDS = {
    "voltage": ("voltage",),
    "current": ("current", "ampere", "amps"),
    "power": ("active power", "power", "watt"),
    "frequency": ("frequency", "hz"),
    "power_factor": ("power factor", "power_factor", "pf"),
    "energy_today": ("energy today", "today energy", "daily energy", "energy_daily"),
    "energy_month": ("energy month", "monthly energy", "month energy", "energy_monthly"),
    "total_energy": ("total energy", "energy total", "lifetime energy", "energy"),
}
_UNIT_HINTS = {
    "voltage": ("v",), "current": ("a",), "power": ("w", "kw"),
    "frequency": ("hz",), "power_factor": ("%", ""),
    "energy_today": ("kwh", "wh"), "energy_month": ("kwh", "wh"),
    "total_energy": ("kwh", "wh"),
}


def invalidate_cache() -> None:
    with _lock:
        _cache["ts"] = 0
        _cache["payload"] = None


def _number(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable", "none"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _configured_mapping() -> Dict[str, str]:
    raw = os.getenv("ELECTRICITY_HA_ENTITIES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {str(key): str(value) for key, value in parsed.items() if key in METRICS and value}


def _ha_states() -> tuple[list[Dict[str, Any]], Optional[str], Optional[float]]:
    base_url = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("HA_TOKEN", "").strip()
    if not base_url or not token:
        return [], "not_configured", None
    started = time.monotonic()
    request = urllib.request.Request(
        f"{base_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "smart-condo-dashboard-electricity",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIME_OUT) as response:
            payload = json.load(response)
        latency = round((time.monotonic() - started) * 1000, 1)
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else [], None, latency
    except Exception as exc:
        return [], type(exc).__name__, round((time.monotonic() - started) * 1000, 1)


def _entity_text(entity: Mapping[str, Any]) -> str:
    attributes = entity.get("attributes") if isinstance(entity.get("attributes"), Mapping) else {}
    return f"{entity.get('entity_id') or ''} {attributes.get('friendly_name') or ''}".lower().replace("_", " ")


def _score(metric: str, entity: Mapping[str, Any]) -> int:
    entity_id = str(entity.get("entity_id") or "").lower()
    attributes = entity.get("attributes") if isinstance(entity.get("attributes"), Mapping) else {}
    text = _entity_text(entity)
    unit = str(attributes.get("unit_of_measurement") or "").strip().lower()
    device_class = str(attributes.get("device_class") or "").strip().lower()
    if metric == "energy_today" and not any(term in text for term in ("today", "daily")):
        return -100
    if metric == "energy_month" and not any(term in text for term in ("month", "monthly")):
        return -100
    if metric == "total_energy" and any(term in text for term in ("today", "daily", "month", "monthly")):
        return -100
    score = 2 if entity_id.startswith("sensor.") else 0
    if device_class == metric:
        score += 12
    if _DEVICE_CLASS_MAP.get(device_class) == metric:
        score += 10
    for keyword in _KEYWORDS[metric]:
        if keyword in text:
            score += 5 if keyword != "energy" else 2
    if unit in _UNIT_HINTS[metric]:
        score += 3
    if metric in ("energy_today", "energy_month"):
        score += 8
    if metric == "total_energy" and any(term in text for term in ("total", "lifetime")):
        score += 8
    return score


def _discover(states: Iterable[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    items = list(states)
    by_id = {str(item.get("entity_id")): item for item in items if item.get("entity_id")}
    explicit = _configured_mapping()
    result: Dict[str, Mapping[str, Any]] = {}
    for metric in METRICS:
        configured = explicit.get(metric)
        if configured and configured in by_id:
            result[metric] = by_id[configured]
            continue
        ranked = sorted(((_score(metric, item), item) for item in items), key=lambda pair: pair[0], reverse=True)
        if ranked and ranked[0][0] >= 7:
            result[metric] = ranked[0][1]
    return result


def _ha_snapshot() -> Dict[str, Any]:
    now = int(time.time())
    states, error, latency = _ha_states()
    found = _discover(states) if states else {}
    values: Dict[str, Optional[float]] = {metric: None for metric in METRICS}
    entities: Dict[str, str] = {}
    updates = []
    for metric, entity in found.items():
        values[metric] = _number(entity.get("state"))
        entity_id = str(entity.get("entity_id") or "")
        if entity_id:
            entities[metric] = entity_id
        updated = _epoch(entity.get("last_updated") or entity.get("last_changed"))
        if updated:
            updates.append(updated)
    last_update = max(updates) if updates else None
    configured = bool(os.getenv("HA_BASE_URL", "").strip() and os.getenv("HA_TOKEN", "").strip())
    available_count = sum(value is not None for value in values.values())
    stale = bool(last_update and now - last_update > _HA_STALE_SEC)
    if not configured:
        health = "unknown"
    elif error:
        health = "offline"
    elif available_count == 0:
        health = "unknown"
    elif stale or available_count < len(METRICS):
        health = "warning"
    else:
        health = "healthy"
    return {
        **values,
        "last_update": last_update,
        "health": health,
        "diagnostics": {
            "source": "home_assistant",
            "configured": configured,
            "auto_discovery": True,
            "configured_entity_overrides": sorted(_configured_mapping().keys()),
            "discovered_entities": entities,
            "available_metric_count": available_count,
            "missing_metrics": [metric for metric in METRICS if values[metric] is None],
            "stale": stale,
            "latency_ms": latency,
            "mapping_verified": None,
            "poll_latency_ms": None,
            "last_success": last_update,
            "last_error": error,
        },
    }


def _bridge_state(kind: str) -> Dict[str, Any]:
    try:
        from backend import pj1103_electricity_bridge as bridge
        raw = bridge.local_state() if kind == "runtime" else bridge.retained_state()
        configured = bridge.configured()
    except Exception:
        raw = {}
        configured = bool(
            os.getenv("TUYA_METER_DEVICE_ID", "").strip()
            and os.getenv("TUYA_METER_IP", "").strip()
            and os.getenv("TUYA_METER_LOCAL_KEY", "").strip()
        )
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    now = int(time.time())
    last_success = _epoch(raw.get("last_success") or raw.get("ts"))
    age = max(0, now - last_success) if last_success else None
    stale = age is None or age > _LOCAL_STALE_SEC
    values = {metric: _number(raw.get(metric)) for metric in METRICS}
    available_count = sum(value is not None for value in values.values())
    online = raw.get("online") is True and not stale
    if not configured and not raw:
        health = "unknown"
    elif online and available_count:
        health = "warning" if raw.get("mapping_verified") is not True else "healthy"
    elif last_success and stale:
        health = "offline"
    elif raw.get("last_error") and not last_success:
        health = "warning"
    else:
        health = "unknown"
    return {
        **values,
        "last_update": last_success,
        "health": health,
        "diagnostics": {
            "source": "tuya_local" if configured or raw else "unknown",
            "snapshot_source": kind,
            "configured": configured,
            "mapping_verified": raw.get("mapping_verified") is True,
            "poll_latency_ms": _number(raw.get("poll_latency_ms")),
            "last_success": last_success,
            "last_error": raw.get("last_error"),
            "stale": stale,
            "available_metric_count": available_count,
            "missing_metrics": [metric for metric in METRICS if values[metric] is None],
        },
    }


def _snapshot_uncached() -> Dict[str, Any]:
    ha = _ha_snapshot()
    if int((ha.get("diagnostics") or {}).get("available_metric_count") or 0) > 0:
        return ha
    runtime = _bridge_state("runtime")
    if int((runtime.get("diagnostics") or {}).get("available_metric_count") or 0) > 0:
        return runtime
    retained = _bridge_state("retained")
    if int((retained.get("diagnostics") or {}).get("available_metric_count") or 0) > 0:
        return retained
    if (runtime.get("diagnostics") or {}).get("configured"):
        return runtime
    return {
        **{metric: None for metric in METRICS},
        "last_update": None,
        "health": "unknown",
        "diagnostics": {
            "source": "unknown", "configured": False, "mapping_verified": False,
            "poll_latency_ms": None, "last_success": None, "last_error": None,
            "stale": True, "missing_metrics": list(METRICS), "available_metric_count": 0,
        },
    }


def electricity_status(force: bool = False) -> Dict[str, Any]:
    now = int(time.time())
    with _lock:
        if not force and _cache.get("payload") is not None and now - int(_cache.get("ts") or 0) < _CACHE_SEC:
            return dict(_cache["payload"])
    payload = _snapshot_uncached()
    with _lock:
        _cache["ts"] = now
        _cache["payload"] = dict(payload)
    return payload


def electricity_provider() -> Iterable[UnifiedDevice]:
    payload = electricity_status()
    health = str(payload.get("health") or "unknown")
    online = True if health in ("healthy", "warning") else (False if health == "offline" else None)
    source = (payload.get("diagnostics") or {}).get("source")
    return (
        UnifiedDevice(
            id="electricity:home",
            type="electricity",
            name="Digital Meter PJ-1103" if source == "tuya_local" else "Home Electricity",
            room="condo" if source == "tuya_local" else "home",
            online=online,
            health=health,
            last_update_ts=payload.get("last_update"),
            latency_ms=(payload.get("diagnostics") or {}).get("poll_latency_ms") or (payload.get("diagnostics") or {}).get("latency_ms"),
            status={metric: payload.get(metric) for metric in METRICS},
            diagnostics=payload.get("diagnostics") or {},
            capabilities=("meter", "sensor"),
            actions=(),
            metadata={"source": source, "physical_site": "condo" if source == "tuya_local" else "home", "read_only": True},
        ),
    )


@app.get("/api/electricity/status")
def get_electricity_status() -> Dict[str, Any]:
    return electricity_status()


registry.register_provider("electricity", electricity_provider, replace=True)
app_module.state["device_registry_registered_modules"] = registry.provider_names()
