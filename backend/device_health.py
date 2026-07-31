"""Read-only live health projection for household devices.

The health tracker observes the existing safe household registry. It does not
own provider polling, create background threads, or change device control
routes.
"""
from __future__ import annotations

import copy
import ipaddress
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, Mapping

from backend import app as app_module
from backend import (
    camera_read_providers,
    household_device_registry,
    lg_tv_control,
    lg_tv_status,
    tapo_ir_local_bridge,
)

app = app_module.app
DEFAULT_STALE_AFTER_SECONDS = 90
_TAPO_BRIDGE_DEVICE_IDS = {
    "living-room-samsung-soundbar",
    "living-room-air-conditioner",
    "living-room-fan",
    "living-room-configured-tv-ir",
}
_CAMERA_CONFIG_IDS = {
    "camera-1": "tapo-c220",
    "camera-2": "xiaomi-camera-1",
    "camera-3": "xiaomi-camera-2",
}


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


def _text(value: Any, limit: int = 80) -> str | None:
    if value in (None, "") or isinstance(value, (dict, list, tuple, set)):
        return None
    text = str(value).strip()
    if not text or any(ord(char) < 32 for char in text):
        return None
    return text[:limit]


def _ip_address(value: Any) -> str | None:
    text = _text(value, 64)
    if text is None:
        return None
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _mac_address(value: Any) -> str | None:
    text = _text(value, 32)
    if text is None:
        return None
    if re.fullmatch(r"[0-9A-Fa-f]{12}", text):
        compact = text
    elif re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", text):
        compact = text.replace(":", "")
    elif re.fullmatch(r"(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}", text):
        compact = text.replace("-", "")
    else:
        return None
    raw = bytes.fromhex(compact)
    if raw in {b"\x00" * 6, b"\xff" * 6} or raw[0] & 1:
        return None
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2)).upper()


def _number(value: Any, *, minimum: float, maximum: float) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum:
        return None
    return number


def _uptime(value: Any) -> int | None:
    number = _number(value, minimum=0, maximum=315_576_000)
    return int(number) if number is not None else None


def _signal_strength(value: Any) -> float | None:
    return _number(value, minimum=-200, maximum=100)


def _connection_type(value: Any) -> str | None:
    text = _text(value, 32)
    if text is None:
        return None
    normalized = re.sub(r"[^a-z0-9]", "", text.casefold())
    if normalized in {"wifi", "wlan", "wireless"} or normalized.startswith("80211"):
        return "Wi-Fi"
    if normalized in {"ethernet", "wired", "lan"}:
        return "Ethernet"
    if normalized in {"unknown"}:
        return "Unknown"
    return None


def _first(mapping: Mapping[str, Any] | None, *keys: str) -> Any:
    if not isinstance(mapping, Mapping):
        return None
    return next((mapping[key] for key in keys if mapping.get(key) not in (None, "")), None)


def _nested_mappings(device: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    state = device.get("state")
    if not isinstance(state, Mapping):
        return []
    result: list[Mapping[str, Any]] = [state]
    for key in ("device", "device_metadata", "ir_diagnostics", "diagnostics"):
        value = state.get(key)
        if isinstance(value, Mapping):
            result.append(value)
    return result


@dataclass(frozen=True)
class ProviderMetrics:
    firmware_version: str | None = None
    uptime: int | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    signal_strength: float | None = None
    connection_type: str | None = None
    model: str | None = None
    manufacturer: str | None = None


class MetricSources:
    """Lazily read existing cached/configured provider metadata."""

    def __init__(self) -> None:
        self._lg: Mapping[str, Any] | None = None
        self._tapo: Mapping[str, Any] | None = None
        self._cameras: Dict[str, Any] | None = None

    def lg(self) -> Mapping[str, Any]:
        if self._lg is None:
            try:
                self._lg = lg_tv_status._public_status()
            except Exception:
                self._lg = {}
        return self._lg

    def tapo(self) -> Mapping[str, Any]:
        if self._tapo is None:
            try:
                self._tapo = tapo_ir_local_bridge.local_tapo_ir_status()
            except Exception:
                self._tapo = {}
        return self._tapo

    def cameras(self) -> Dict[str, Any]:
        if self._cameras is None:
            try:
                status, specs = camera_read_providers.load_inventory()
                self._cameras = (
                    {spec.id: spec for spec in specs}
                    if status in {"configured", "configuration_partial"} else {}
                )
            except Exception:
                self._cameras = {}
        return self._cameras


def _metric_candidates(
    device: Mapping[str, Any],
    sources: MetricSources,
) -> list[Mapping[str, Any]]:
    candidates = _nested_mappings(device)
    identifier = str(device.get("id") or "")
    if identifier == "living-room-lg-tv":
        status = sources.lg()
        details = status.get("device")
        if isinstance(details, Mapping):
            candidates.insert(0, details)
        candidates.append({
            "ip_address": lg_tv_status.TV_IP,
            "mac_address": lg_tv_control._configured_mac(),
        })
    elif identifier in _TAPO_BRIDGE_DEVICE_IDS:
        tapo = sources.tapo()
        candidates.insert(0, tapo)
        debug = tapo.get("debug")
        if isinstance(debug, Mapping):
            for key in ("sys_info", "hw_info"):
                value = debug.get(key)
                if isinstance(value, Mapping):
                    candidates.append(value)
    elif identifier in _CAMERA_CONFIG_IDS:
        spec = sources.cameras().get(_CAMERA_CONFIG_IDS[identifier])
        if spec is not None:
            candidates.append({
                "ip_address": getattr(spec, "host", None),
                "manufacturer": getattr(spec, "vendor", None),
                "model": getattr(spec, "model", None),
            })
    return candidates


def normalize_provider_metrics(
    device: Mapping[str, Any],
    sources: MetricSources | None = None,
) -> ProviderMetrics:
    candidates = _metric_candidates(device, sources or MetricSources())

    def value(*keys: str) -> Any:
        return next((
            found
            for mapping in candidates
            if (found := _first(mapping, *keys)) not in (None, "")
        ), None)

    return ProviderMetrics(
        firmware_version=_text(value(
            "firmware_version", "firmware", "sw_ver", "software_version",
        )),
        uptime=_uptime(value("uptime", "uptime_seconds", "device_on_time")),
        ip_address=_ip_address(value("ip_address", "ip", "host")),
        mac_address=_mac_address(value("mac_address", "mac")),
        signal_strength=_signal_strength(value(
            "signal_strength", "rssi", "wifi_rssi",
        )),
        connection_type=_connection_type(value(
            "connection_type", "network_type", "interface_type",
        )),
        model=_text(value("model", "model_name", "modelName", "device_model")),
        manufacturer=_text(value(
            "manufacturer", "vendor", "brand", "manufacturer_name",
        )),
    )


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
    firmware_version: str | None = None
    uptime: int | None = None
    ip_address: str | None = None
    mac_address: str | None = None
    signal_strength: float | None = None
    connection_type: str | None = None
    model: str | None = None
    manufacturer: str | None = None


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
        provider_metrics: ProviderMetrics | None = None,
    ) -> DeviceHealth:
        now = float(observed_at if observed_at is not None else time.time())
        identifier = str(device.get("id") or "")
        provider_online = device.get("online") if isinstance(device.get("online"), bool) else None
        source_seen = _source_timestamp(device)
        metrics = provider_metrics or ProviderMetrics()

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
                **asdict(metrics),
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
    sources = MetricSources()
    devices = [
        asdict(tracker.observe(
            device,
            response_time_ms=elapsed,
            observed_at=now,
            provider_metrics=normalize_provider_metrics(device, sources),
        ))
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
