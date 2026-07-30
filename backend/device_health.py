"""Read-only live health projection for household devices.

The health tracker observes the existing safe household registry. It does not
own provider polling, create background threads, or change device control
routes.
"""
from __future__ import annotations

import copy
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable

from backend import app as app_module
from backend import household_device_registry

app = app_module.app
DEFAULT_STALE_AFTER_SECONDS = 90


def _stale_after_seconds() -> int:
    try:
        return max(15, int(os.getenv(
            "DEVICE_HEALTH_STALE_AFTER_SECONDS",
            str(DEFAULT_STALE_AFTER_SECONDS),
        )))
    except (TypeError, ValueError):
        return DEFAULT_STALE_AFTER_SECONDS


def _iso_timestamp(value: float | int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace("+00:00", "Z")


def _source_timestamp(device: Dict[str, Any]) -> float | None:
    state = device.get("state")
    if not isinstance(state, dict):
        return None
    for key in ("updated_at", "last_update", "last_seen", "last_seen_ts"):
        value = state.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                continue
            try:
                return datetime.fromisoformat(candidate.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    return None


@dataclass(frozen=True)
class DeviceHealth:
    id: str
    display_name: str
    room: str
    category: str
    health: str
    health_indicator: str
    online: bool | None
    heartbeat_at: str | None
    heartbeat_age_seconds: int | None
    last_seen: str | None
    response_time_ms: float | None
    observed_at: str


class DeviceHealthTracker:
    """Thread-safe in-memory heartbeat history keyed by safe registry ID."""

    def __init__(self, stale_after_seconds: int | None = None) -> None:
        self.stale_after_seconds = stale_after_seconds or _stale_after_seconds()
        self._lock = threading.RLock()
        self._records: Dict[str, Dict[str, float | None]] = {}

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def observe(
        self,
        device: Dict[str, Any],
        *,
        response_time_ms: float | None,
        observed_at: float | None = None,
    ) -> DeviceHealth:
        now = float(observed_at if observed_at is not None else time.time())
        identifier = str(device.get("id") or "")
        provider_online = device.get("online") if isinstance(device.get("online"), bool) else None
        source_seen = _source_timestamp(device)

        with self._lock:
            record = self._records.setdefault(identifier, {
                "heartbeat_at": None,
                "last_seen": None,
            })
            if provider_online is True:
                record["heartbeat_at"] = now
                record["last_seen"] = source_seen or now
            elif source_seen is not None:
                previous = record.get("last_seen")
                record["last_seen"] = max(source_seen, float(previous or 0))

            heartbeat_at = record.get("heartbeat_at")
            heartbeat_age = (
                max(0, int(now - float(heartbeat_at)))
                if heartbeat_at is not None else None
            )
            if provider_online is False:
                online: bool | None = False
            elif provider_online is True:
                online = True
            elif heartbeat_age is not None:
                online = heartbeat_age <= self.stale_after_seconds
            else:
                online = None

            declared_health = str(device.get("health") or "unknown")
            if online is False:
                health = "offline"
                indicator = "red"
            elif online is True and declared_health == "healthy":
                health = "healthy"
                indicator = "green"
            elif online is True or declared_health == "degraded":
                health = "degraded"
                indicator = "yellow"
            else:
                health = "unknown"
                indicator = "yellow"

            return DeviceHealth(
                id=identifier,
                display_name=str(device.get("display_name") or "Unknown device"),
                room=str(device.get("room") or "unknown"),
                category=str(device.get("category") or "unknown"),
                health=health,
                health_indicator=indicator,
                online=online,
                heartbeat_at=_iso_timestamp(heartbeat_at),
                heartbeat_age_seconds=heartbeat_age,
                last_seen=_iso_timestamp(record.get("last_seen")),
                response_time_ms=(
                    round(max(0.0, float(response_time_ms)), 1)
                    if response_time_ms is not None else None
                ),
                observed_at=_iso_timestamp(now) or "",
            )


tracker = DeviceHealthTracker()


def _timed_devices() -> Iterable[tuple[Dict[str, Any], float]]:
    builders: tuple[Callable[[], Any], ...] = (
        household_device_registry._lg_tv,
        household_device_registry._ir_devices,
        household_device_registry._camera_placeholders,
    )
    for builder in builders:
        started = time.perf_counter()
        result = builder()
        elapsed_ms = (time.perf_counter() - started) * 1000
        devices = result if isinstance(result, list) else [result]
        for device in devices:
            if isinstance(device, dict):
                safe = {
                    key: copy.deepcopy(device[key])
                    for key in household_device_registry._SAFE_FIELDS
                    if key in device
                }
                yield safe, elapsed_ms


def health_snapshot(*, observed_at: float | None = None) -> Dict[str, Any]:
    now = float(observed_at if observed_at is not None else time.time())
    devices = [
        asdict(tracker.observe(device, response_time_ms=elapsed, observed_at=now))
        for device, elapsed in _timed_devices()
    ]
    summary = {
        "total": len(devices),
        "online": sum(item["online"] is True for item in devices),
        "offline": sum(item["online"] is False for item in devices),
        "unknown": sum(item["online"] is None for item in devices),
        "healthy": sum(item["health"] == "healthy" for item in devices),
        "degraded": sum(item["health"] == "degraded" for item in devices),
    }
    return {
        "generated_at": _iso_timestamp(now),
        "stale_after_seconds": tracker.stale_after_seconds,
        "summary": summary,
        "devices": devices,
    }


@app.get("/api/device-health")
def device_health() -> Dict[str, Any]:
    return health_snapshot()
