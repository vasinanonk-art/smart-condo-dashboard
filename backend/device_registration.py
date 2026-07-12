"""Adapters that expose existing modules through the unified registry.

The adapters are deliberately read-only. They consume current in-memory state
and existing loader functions without changing routes, commands, polling,
MQTT topics, or frontend payloads.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Mapping, Optional

from backend.device_framework import UnifiedDevice
from backend.device_registry import DeviceRegistry, registry


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _room_from_name(name: str) -> Optional[str]:
    lowered = str(name or "").lower()
    for room in ("living_room", "bedroom", "kitchen", "bathroom", "office", "balcony"):
        if room.replace("_", " ") in lowered or room in lowered:
            return room
    if "living" in lowered:
        return "living_room"
    if "bed" in lowered:
        return "bedroom"
    return None


def _health(online: Optional[bool], stale: bool = False) -> str:
    if online is False:
        return "offline"
    if stale:
        return "warning"
    if online is True:
        return "healthy"
    return "unknown"


def _sonoff_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    state = app_module.state
    devices = state.get("sonoff_devices") or []
    last_update = _int(state.get("sonoff_last_sync_ts"))
    result: List[UnifiedDevice] = []
    for raw in devices if isinstance(devices, list) else []:
        if not isinstance(raw, Mapping):
            continue
        device_id = str(raw.get("deviceid") or raw.get("id") or "").strip()
        if not device_id:
            continue
        name = str(raw.get("name") or raw.get("deviceName") or device_id)
        online_value = raw.get("online")
        online = bool(online_value) if online_value is not None else None
        result.append(
            UnifiedDevice(
                id=device_id,
                type="sonoff",
                name=name,
                room=_room_from_name(name),
                online=online,
                health=_health(online),
                last_update_ts=last_update,
                status={"switch": raw.get("switch"), "state": raw.get("state")},
                diagnostics={"source": "ewelink", "configured": bool(state.get("ewelink_config_loaded"))},
                capabilities=("power", "automation"),
                actions=("on", "off"),
                metadata={"brand": "Sonoff"},
            )
        )
    return result


def _tuya_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    try:
        lights = app_module.load_lights()
    except Exception:
        lights = []
    result: List[UnifiedDevice] = []
    for raw in lights if isinstance(lights, list) else []:
        if not isinstance(raw, Mapping):
            continue
        device_id = str(raw.get("id") or "").strip()
        if not device_id:
            continue
        name = str(raw.get("name") or device_id)
        try:
            current = app_module.cache_first_status(dict(raw))
        except Exception:
            current = {}
        current = current if isinstance(current, Mapping) else {}
        dps = ((current.get("result") or {}).get("dps") or {}) if isinstance(current.get("result"), Mapping) else {}
        dps = dps if isinstance(dps, Mapping) else {}
        online = current.get("online") if isinstance(current.get("online"), bool) else None
        stale = str(current.get("status") or "").lower() == "stale"
        caps = ["power"]
        if "22" in dps or 22 in dps:
            caps.append("brightness")
        if "23" in dps or 23 in dps:
            caps.append("temperature")
        if "24" in dps or 24 in dps:
            caps.extend(("rgb", "preset"))
        result.append(
            UnifiedDevice(
                id=device_id,
                type="tuya_light",
                name=name,
                room=_room_from_name(name),
                online=online,
                health=_health(online, stale),
                last_update_ts=_int(current.get("last_seen_ts")),
                status={
                    "state": current.get("status"),
                    "brightness": dps.get("22", dps.get(22)),
                    "temperature": dps.get("23", dps.get(23)),
                    "rgb": dps.get("24", dps.get(24)),
                },
                diagnostics={"source": current.get("source"), "stale": stale},
                capabilities=tuple(caps),
                actions=("brightness", "temperature", "rgb", "preset"),
                metadata={"target": app_module.device_target(dict(raw)), "product_name": raw.get("product_name")},
            )
        )
    return result


def _lg_tv_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    state = app_module.state
    last_state = state.get("last_state") if isinstance(state.get("last_state"), Mapping) else {}
    online = bool(state.get("mqtt_connected"))
    return (
        UnifiedDevice(
            id="lg_tv",
            type="lg_tv",
            name="LG TV",
            room="living_room",
            online=online,
            health=_health(online),
            last_update_ts=_int(state.get("last_state_ts")),
            status={
                "power": last_state.get("power"),
                "volume": last_state.get("volume"),
                "muted": last_state.get("muted", last_state.get("mute")),
                "input": last_state.get("input"),
                "app": last_state.get("app"),
            },
            diagnostics={"source": "mqtt", "connected": online},
            capabilities=("power", "volume", "mute", "remote"),
            actions=tuple(state.get("available_commands") or ()),
            metadata={"command_topic": "configured", "state_topic": "configured"},
        ),
    )


def _presence_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    state = app_module.state
    raw_presence = state.get("condo_presence") or state.get("presence") or {}
    result: List[UnifiedDevice] = []
    if not isinstance(raw_presence, Mapping):
        return result
    for person, raw in raw_presence.items():
        if not isinstance(raw, Mapping):
            continue
        person_id = str(person).lower()
        home = raw.get("home")
        source = raw.get("source")
        updated = _int(raw.get("ts") or raw.get("last_seen_ts"))
        online = updated is not None
        result.append(
            UnifiedDevice(
                id=f"presence:{person_id}",
                type="presence",
                name=str(raw.get("name") or person).title(),
                room=str(raw.get("room") or "home"),
                online=online,
                health=_health(online),
                last_update_ts=updated,
                status={"home": home, "state": raw.get("state"), "ip": raw.get("ip")},
                diagnostics={"source": source, "topic": raw.get("topic")},
                capabilities=("presence", "sensor", "automation"),
                actions=(),
                metadata={"person": person_id},
            )
        )
    return result


def _pm25_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    state = app_module.state
    sensor = state.get("condo_sensor") if isinstance(state.get("condo_sensor"), Mapping) else {}
    updated = _int(sensor.get("ts"))
    source = sensor.get("pm25_source")
    entries = (
        ("pm25:living_room", "Living Room PM2.5", "living_room", sensor.get("pm25_living_room", sensor.get("pm25"))),
        ("pm25:bedroom", "Bedroom PM2.5", "bedroom", sensor.get("pm25_bedroom")),
    )
    result: List[UnifiedDevice] = []
    for device_id, name, room, value in entries:
        numeric = _float(value)
        online = numeric is not None and updated is not None
        stale = bool(updated and int(time.time()) - updated > 90)
        result.append(
            UnifiedDevice(
                id=device_id,
                type="pm25",
                name=name,
                room=room,
                online=online,
                health=_health(online, stale),
                last_update_ts=updated,
                status={"pm25": numeric, "unit": "µg/m³"},
                diagnostics={"source": source, "stale": stale},
                capabilities=("sensor",),
                actions=(),
                metadata={"metric": "pm25"},
            )
        )
    return result


def _camera_provider(app_module: Any) -> Iterable[UnifiedDevice]:
    try:
        payload = app_module.camera_config_payload()
    except Exception:
        payload = {"loaded": False, "cameras": []}
    cameras = payload.get("cameras") if isinstance(payload, Mapping) else []
    result: List[UnifiedDevice] = []
    for raw in cameras if isinstance(cameras, list) else []:
        if not isinstance(raw, Mapping):
            continue
        device_id = str(raw.get("id") or raw.get("name") or raw.get("ip") or "camera")
        name = str(raw.get("name") or "Camera")
        result.append(
            UnifiedDevice(
                id=f"camera:{device_id}",
                type="camera",
                name=name,
                room=str(raw.get("room") or _room_from_name(name) or "unknown"),
                online=None,
                health="unknown",
                last_update_ts=None,
                status={"has_rtsp": bool(raw.get("rtsp") or raw.get("rtsp_url") or raw.get("rtsp_path") or raw.get("rtsp_port"))},
                diagnostics={"source": "camera_config", "configured": bool(payload.get("loaded"))},
                capabilities=("video",),
                actions=(),
                metadata={"brand": raw.get("brand"), "model": raw.get("model")},
            )
        )
    return result


def install_default_device_registry(app_module: Any = None, target_registry: DeviceRegistry = registry) -> DeviceRegistry:
    if app_module is None:
        from backend import app as app_module  # imported lazily to avoid circular startup imports

    providers = {
        "sonoff": lambda: _sonoff_provider(app_module),
        "tuya": lambda: _tuya_provider(app_module),
        "lg_tv": lambda: _lg_tv_provider(app_module),
        "presence": lambda: _presence_provider(app_module),
        "pm25": lambda: _pm25_provider(app_module),
        "camera": lambda: _camera_provider(app_module),
    }
    for name, provider in providers.items():
        target_registry.register_provider(name, provider)

    app_module.device_registry = target_registry
    app_module.state["device_registry_registered_modules"] = target_registry.provider_names()
    return target_registry
