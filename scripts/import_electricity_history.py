#!/usr/bin/env python3
"""Dry-run or apply legitimate electricity history backfill.

The importer is conservative: it never fabricates values, skips unrelated sensor
rows, deduplicates timestamps, and creates a backup before apply.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import electricity_history as history  # noqa: E402


METRIC_KEYS = ("voltage", "current", "power", "total_energy")
IDENTITY_TERMS = ("electric", "pj1103", "pj_1103", "digital meter", "energy meter", "power meter")
EXCLUDED_TERMS = ("pm25", "pm2.5", "temperature", "humidity", "presence", "camera", "lg_tv", "sonoff_action")


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def epoch(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _first_number(item: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = number(item.get(name))
        if value is not None:
            return value
    return None


def _valid_values(row: Mapping[str, Any]) -> bool:
    voltage = number(row.get("voltage"))
    current = number(row.get("current"))
    power = number(row.get("power"))
    total = number(row.get("total_energy"))
    if all(value is None for value in (voltage, current, power, total)):
        return False
    if voltage is not None and not 0 < voltage < 400:
        return False
    if current is not None and not 0 <= current < 500:
        return False
    if power is not None and not 0 <= power < 200_000:
        return False
    if total is not None and total < 0:
        return False
    return True


def _candidate_from_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    ts = epoch(item.get("ts") or item.get("timestamp") or item.get("last_updated") or item.get("updated_ts"))
    if not ts:
        return None
    text = " ".join(str(item.get(key) or "") for key in ("metric", "sensor", "entity_id", "source", "name", "device", "device_id")).lower()
    if any(term in text for term in EXCLUDED_TERMS):
        return None
    nested = item.get("values") if isinstance(item.get("values"), Mapping) else item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    voltage = _first_number(item, "voltage", "voltage_v")
    current = _first_number(item, "current", "current_a")
    power = _first_number(item, "power", "active_power", "power_w")
    total = _first_number(item, "total_energy", "total_energy_kwh", "energy")
    if isinstance(nested, Mapping):
        voltage = voltage if voltage is not None else _first_number(nested, "voltage", "voltage_v")
        current = current if current is not None else _first_number(nested, "current", "current_a")
        power = power if power is not None else _first_number(nested, "power", "active_power", "power_w")
        total = total if total is not None else _first_number(nested, "total_energy", "total_energy_kwh", "energy")
    has_identity = any(term in text for term in IDENTITY_TERMS)
    has_bundle = sum(value is not None for value in (voltage, current, power, total)) >= 2
    if not has_identity and not has_bundle:
        return None
    row = {
        "ts": ts,
        "voltage": voltage,
        "current": current,
        "power": power,
        "total_energy": total,
        "source": "sensor_history_import",
        "health": "unknown",
    }
    return row if _valid_values(row) else None


def read_sensor_history(path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {"records_scanned": 0, "candidate_records": 0, "malformed_records": 0, "invalid_records": 0}
    if not path.exists() or not path.is_file():
        return [], stats
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stats["records_scanned"] += 1
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                stats["malformed_records"] += 1
                continue
            if not isinstance(item, Mapping):
                stats["invalid_records"] += 1
                continue
            row = _candidate_from_item(item)
            if row is None:
                stats["invalid_records"] += 1
                continue
            stats["candidate_records"] += 1
            rows.append(row)
    return rows, stats


def ha_mapping() -> dict[str, str]:
    raw = os.getenv("ELECTRICITY_HA_ENTITIES_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, Mapping):
        return {}
    return {key: str(value) for key, value in parsed.items() if key in METRIC_KEYS and value}


def read_ha_history(days: int) -> list[dict[str, Any]]:
    base = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("HA_TOKEN", "").strip()
    mapping = ha_mapping()
    if not base or not token or not mapping:
        return []
    start = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    query = urllib.parse.urlencode({"filter_entity_id": ",".join(mapping.values()), "minimal_response": "0", "no_attributes": "1"})
    request = urllib.request.Request(f"{base}/api/history/period/{start.isoformat()}?{query}", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except Exception:
        return []
    by_ts: dict[int, dict[str, Any]] = {}
    reverse = {entity: metric for metric, entity in mapping.items()}
    for series in payload if isinstance(payload, list) else []:
        if not isinstance(series, list):
            continue
        for item in series:
            if not isinstance(item, Mapping):
                continue
            metric = reverse.get(str(item.get("entity_id") or ""))
            ts = epoch(item.get("last_updated") or item.get("last_changed"))
            value = number(item.get("state"))
            if not metric or not ts or value is None:
                continue
            row = by_ts.setdefault(ts, {"ts": ts, "voltage": None, "current": None, "power": None, "total_energy": None, "source": "home_assistant_import", "health": "unknown"})
            row[metric] = value
    return [row for row in sorted(by_ts.values(), key=lambda item: item["ts"]) if _valid_values(row)]


def merge_rows(rows: Iterable[Mapping[str, Any]], apply: bool) -> dict[str, Any]:
    existing_rows = history.read_samples()
    existing = {int(row["ts"]) for row in existing_rows}
    unique: dict[int, dict[str, Any]] = {}
    duplicate_records = 0
    for raw in rows:
        ts = epoch(raw.get("ts"))
        if not ts or ts in existing or ts in unique:
            duplicate_records += 1
            continue
        row = {field: raw.get(field) for field in history.SAFE_FIELDS}
        row["ts"] = ts
        if not _valid_values(row):
            continue
        unique[ts] = row
    candidates = [unique[key] for key in sorted(unique)]
    backup_path = None
    if apply and candidates:
        history.HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if history.HISTORY_PATH.exists():
            backup_path = history.HISTORY_PATH.with_name(f"{history.HISTORY_PATH.name}.backup-{int(time.time())}")
            shutil.copy2(history.HISTORY_PATH, backup_path)
        merged = {int(row["ts"]): row for row in existing_rows}
        merged.update({int(row["ts"]): row for row in candidates})
        temporary = history.HISTORY_PATH.with_suffix(history.HISTORY_PATH.suffix + ".import.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            for row in (merged[key] for key in sorted(merged)):
                handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, history.HISTORY_PATH)
    return {
        "records_imported": len(candidates) if apply else 0,
        "records_would_import": len(candidates),
        "duplicate_records": duplicate_records,
        "first_timestamp": candidates[0]["ts"] if candidates else None,
        "last_timestamp": candidates[-1]["ts"] if candidates else None,
        "backup_created": bool(backup_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write non-duplicate rows after creating a backup")
    parser.add_argument("--days", type=int, default=30, help="HA recorder lookback days")
    args = parser.parse_args()

    sensor_path = Path(os.getenv("SENSOR_HISTORY_PATH", str(Path.home() / ".smart-condo-dashboard" / "sensor_history.jsonl"))).expanduser()
    sensor_rows, sensor_stats = read_sensor_history(sensor_path)
    ha_rows = read_ha_history(args.days)
    source = "home_assistant_recorder" if ha_rows else "sensor_history_jsonl" if sensor_rows else None
    rows = ha_rows or sensor_rows
    if not rows:
        print(json.dumps({**sensor_stats, "source": None, "result": "no_backfill_source_available", "dry_run": not args.apply}, separators=(",", ":")))
        return 0
    result = merge_rows(rows, args.apply)
    print(json.dumps({**sensor_stats, **result, "source": source, "records_skipped": max(0, sensor_stats["records_scanned"] - sensor_stats["candidate_records"]), "dry_run": not args.apply, "mode": "apply" if args.apply else "dry_run"}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
