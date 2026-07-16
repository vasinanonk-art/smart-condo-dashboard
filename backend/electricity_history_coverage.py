"""Coverage metadata, tariff status, and safe backfill investigation for electricity history."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from backend import app as app_module
from backend import electricity_history as history

app = app_module.app
_original_history_payload = history.history_payload


def _coverage(payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    start = int(payload.get("from") or 0)
    end = int(payload.get("to") or 0)
    requested = max(0, end - start)
    timestamps = sorted(int(row.get("ts")) for row in rows if isinstance(row, dict) and row.get("ts"))
    first = timestamps[0] if timestamps else None
    last = timestamps[-1] if timestamps else None
    available = max(0, last - first) if first is not None and last is not None else 0
    if requested <= 0:
        percent = 100.0 if rows else 0.0
    else:
        covered_start = max(start, first) if first is not None else end
        covered_end = min(end, last) if last is not None else start
        percent = max(0.0, min(100.0, (max(0, covered_end - covered_start) / requested) * 100.0))
    complete = bool(rows and first is not None and last is not None and first <= start and last >= end - history.MAX_INTEGRATION_GAP_SEC)
    return {
        "first_sample_ts": first,
        "last_sample_ts": last,
        "available_duration_sec": available,
        "requested_duration_sec": requested,
        "complete": complete,
        "coverage_percent": round(percent, 2),
    }


def history_payload_with_coverage(range_name: str, from_ts: Optional[int] = None, to_ts: Optional[int] = None) -> Dict[str, Any]:
    payload = _original_history_payload(range_name, from_ts, to_ts)
    coverage = _coverage(payload)
    summary = dict(payload.get("summary") or {})
    summary.update({
        "first_sample_ts": coverage["first_sample_ts"],
        "last_sample_ts": coverage["last_sample_ts"],
        "sample_count": len(payload.get("samples") or []),
        "coverage_complete": coverage["complete"],
        "coverage_percent": coverage["coverage_percent"],
    })
    return {**payload, "coverage": coverage, "summary": summary, "max_gap_sec": history.MAX_INTEGRATION_GAP_SEC}


# Existing route functions resolve this module global at call time, so replacing the
# function enriches the existing API without adding a duplicate route.
history.history_payload = history_payload_with_coverage


@app.get("/api/electricity/tariff/status")
def get_tariff_status() -> Dict[str, Any]:
    config, error = history._tariff_config()
    if config is None:
        return {
            "configured": bool(os.getenv("ELECTRICITY_TARIFF_CONFIG_JSON", "").strip()),
            "valid": False,
            "tariff_name": None,
            "effective_date": None,
            "diagnostics": {"reason": error or "invalid_tariff_config"},
        }
    return {
        "configured": True,
        "valid": True,
        "tariff_name": config["tariff_name"],
        "effective_date": config["effective_date"],
        "ft_rate": config["ft_rate"],
        "service_charge": config["service_charge"],
        "vat_percent": config["vat_percent"],
        "minimum_charge": config["minimum_charge"],
        "tier_count": len(config["tiers"]),
        "diagnostics": {"reason": None, "source": "environment_json"},
    }


def _safe_backfill_status() -> Dict[str, Any]:
    sensor_path = Path(os.getenv("SENSOR_HISTORY_PATH", str(Path.home() / ".smart-condo-dashboard" / "sensor_history.jsonl"))).expanduser()
    ha_configured = bool(os.getenv("HA_BASE_URL", "").strip() and os.getenv("HA_TOKEN", "").strip())
    mapping_configured = bool(os.getenv("ELECTRICITY_HA_ENTITIES_JSON", "").strip())
    candidates = []
    if sensor_path.exists() and sensor_path.is_file():
        candidates.append("sensor_history_jsonl")
    if ha_configured and mapping_configured:
        candidates.append("home_assistant_recorder")
    return {
        "available": bool(candidates),
        "sources": candidates,
        "result": "backfill_source_available" if candidates else "no_backfill_source_available",
        "automatic_import": False,
        "diagnostics": {
            "sensor_history_present": "sensor_history_jsonl" in candidates,
            "home_assistant_configured": ha_configured,
            "entity_mapping_configured": mapping_configured,
        },
    }


@app.get("/api/electricity/backfill/status")
def get_backfill_status() -> Dict[str, Any]:
    return _safe_backfill_status()
