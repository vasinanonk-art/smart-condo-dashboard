import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List

HISTORY_RETENTION_SEC = 7 * 24 * 60 * 60
HISTORY_STORE_PATH = os.getenv(
    "SENSOR_HISTORY_STORE_PATH",
    "/root/.smart-condo-dashboard/sensor_history.jsonl",
)

_lock = threading.Lock()
_diagnostics: Dict[str, Any] = {
    "history_store_path": HISTORY_STORE_PATH,
    "loaded_count": 0,
    "appended_count": 0,
    "pruned_count": 0,
}
_last_signature = None


def _number(value: Any):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_row(row: Any) -> Dict[str, Any]:
    row = row if isinstance(row, dict) else {}
    living = _number(row.get("pm25_living_room"))
    bedroom = _number(row.get("pm25_bedroom"))
    legacy = None
    for key in ("pm25", "pm2_5", "pm2.5", "PM25", "pm_25"):
        if key in row and row.get(key) is not None:
            legacy = _number(row.get(key))
            break
    if living is None:
        living = legacy
    return {
        "ts": int(row.get("ts") or 0),
        "temperature": _number(row.get("temperature", row.get("temp"))),
        "humidity": _number(row.get("humidity", row.get("hum"))),
        "pm25": living,
        "pm25_living_room": living,
        "pm25_bedroom": bedroom,
    }


def _signature(row: Dict[str, Any]):
    return (
        row.get("ts"),
        row.get("temperature"),
        row.get("humidity"),
        row.get("pm25"),
        row.get("pm25_living_room"),
        row.get("pm25_bedroom"),
    )


def _read_rows_unlocked() -> List[Dict[str, Any]]:
    if not os.path.isfile(HISTORY_STORE_PATH):
        return []
    rows: List[Dict[str, Any]] = []
    try:
        with open(HISTORY_STORE_PATH, encoding="utf-8") as handle:
            first = handle.read(1)
            handle.seek(0)
            if first == "[":
                payload = json.load(handle)
                source = payload if isinstance(payload, list) else []
            else:
                source = []
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        source.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        for item in source:
            row = normalize_row(item)
            if row["ts"] > 0:
                rows.append(row)
    except (OSError, ValueError, TypeError):
        return []
    return rows


def _rewrite_unlocked(rows: Iterable[Dict[str, Any]]) -> None:
    directory = os.path.dirname(HISTORY_STORE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = HISTORY_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(normalize_row(row), ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, HISTORY_STORE_PATH)


def load_history(existing: Iterable[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    global _last_signature
    now = int(time.time())
    cutoff = now - HISTORY_RETENTION_SEC
    with _lock:
        stored = _read_rows_unlocked()
        merged = {}
        for item in [*stored, *list(existing or ())]:
            row = normalize_row(item)
            if row["ts"] >= cutoff:
                merged[_signature(row)] = row
        rows = sorted(merged.values(), key=lambda item: item["ts"])
        pruned = max(0, len(stored) - sum(1 for row in stored if row["ts"] >= cutoff))
        _rewrite_unlocked(rows)
        _diagnostics["loaded_count"] = len(rows)
        _diagnostics["pruned_count"] += pruned
        _last_signature = _signature(rows[-1]) if rows else None
        return rows


def append_row(row: Dict[str, Any]) -> bool:
    global _last_signature
    normalized = normalize_row(row)
    if normalized["ts"] <= 0:
        return False
    signature = _signature(normalized)
    with _lock:
        if signature == _last_signature:
            return False
        directory = os.path.dirname(HISTORY_STORE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(HISTORY_STORE_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
        _last_signature = signature
        _diagnostics["appended_count"] += 1
        return True


def prune_history(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = int(time.time())
    cutoff = now - HISTORY_RETENTION_SEC
    normalized = [normalize_row(row) for row in rows]
    kept = [row for row in normalized if row["ts"] >= cutoff]
    pruned = len(normalized) - len(kept)
    if pruned:
        with _lock:
            _rewrite_unlocked(kept)
            _diagnostics["pruned_count"] += pruned
    return kept


def diagnostics() -> Dict[str, Any]:
    with _lock:
        return dict(_diagnostics)


# app_runtime imports this module after backend.app has completed loading.
# Install passive, read-only shared services without changing command paths.
from backend.device_registration import install_default_device_registry  # noqa: E402

install_default_device_registry()

# Register the read-only topology route after the unified registry exists.
import backend.topology_runtime  # noqa: E402,F401
