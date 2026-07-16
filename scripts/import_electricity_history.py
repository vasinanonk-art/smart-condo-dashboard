#!/usr/bin/env python3
"""Investigate and optionally import legitimate existing electricity history.

Default mode is read-only. Use --apply only after reviewing the detected source.
No credentials, tokens, URLs, raw payloads, or DPS data are printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import electricity_history as history  # noqa: E402


def number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
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


def read_sensor_history(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, Mapping):
                continue
            metric = str(item.get("metric") or item.get("sensor") or item.get("entity_id") or "").lower()
            if not any(term in metric for term in ("electric", "pj1103", "power", "voltage", "current", "energy")):
                continue
            ts = epoch(item.get("ts") or item.get("timestamp") or item.get("last_updated"))
            if not ts:
                continue
            rows.append({
                "ts": ts,
                "voltage": number(item.get("voltage")),
                "current": number(item.get("current")),
                "power": number(item.get("power")),
                "total_energy": number(item.get("total_energy") or item.get("energy")),
                "source": "sensor_history_import",
                "health": "unknown",
            })
    return rows


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
    return {key: str(value) for key, value in parsed.items() if key in {"voltage", "current", "power", "total_energy"} and value}


def read_ha_history(days: int) -> list[dict[str, Any]]:
    base = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("HA_TOKEN", "").strip()
    mapping = ha_mapping()
    if not base or not token or not mapping:
        return []
    start = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    entities = ",".join(mapping.values())
    query = urllib.parse.urlencode({"filter_entity_id": entities, "minimal_response": "0", "no_attributes": "1"})
    url = f"{base}/api/history/period/{start.isoformat()}?{query}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
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
            entity = str(item.get("entity_id") or "")
            metric = reverse.get(entity)
            ts = epoch(item.get("last_updated") or item.get("last_changed"))
            value = number(item.get("state"))
            if not metric or not ts or value is None:
                continue
            row = by_ts.setdefault(ts, {"ts": ts, "voltage": None, "current": None, "power": None, "total_energy": None, "source": "home_assistant_import", "health": "unknown"})
            row[metric] = value
    return sorted(by_ts.values(), key=lambda row: row["ts"])


def merge_rows(rows: Iterable[Mapping[str, Any]], apply: bool) -> tuple[int, int]:
    existing = {int(row["ts"]) for row in history.read_samples()}
    candidates = []
    for raw in rows:
        ts = epoch(raw.get("ts"))
        if not ts or ts in existing:
            continue
        row = {field: raw.get(field) for field in history.SAFE_FIELDS}
        row["ts"] = ts
        if all(number(row.get(key)) is None for key in ("voltage", "current", "power", "total_energy")):
            continue
        candidates.append(row)
    if apply and candidates:
        history.HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with history.HISTORY_PATH.open("a", encoding="utf-8") as handle:
            for row in sorted(candidates, key=lambda item: int(item["ts"])):
                handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    return len(candidates), len(existing)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="append new, non-duplicate rows")
    parser.add_argument("--days", type=int, default=30, help="HA recorder lookback days")
    args = parser.parse_args()

    sensor_path = Path(os.getenv("SENSOR_HISTORY_PATH", str(Path.home() / ".smart-condo-dashboard" / "sensor_history.jsonl"))).expanduser()
    sensor_rows = list(read_sensor_history(sensor_path))
    ha_rows = read_ha_history(args.days)
    source = "home_assistant_recorder" if ha_rows else "sensor_history_jsonl" if sensor_rows else None
    rows = ha_rows or sensor_rows
    if not rows:
        print("no_backfill_source_available")
        return 0
    candidates, existing = merge_rows(rows, args.apply)
    print(json.dumps({
        "source": source,
        "mode": "apply" if args.apply else "dry_run",
        "candidate_rows": candidates,
        "existing_rows": existing,
        "duplicates_skipped": max(0, len(rows) - candidates),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
