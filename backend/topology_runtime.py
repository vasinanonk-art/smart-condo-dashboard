"""Topology/NOC read model built on the unified device registry.

This module is read-only: it does not alter command paths, MQTT topics, device
pollers, or existing API responses. It exposes one topology endpoint consumed by
the dashboard and keeps a last-valid LG TV snapshot sourced from the same MQTT
state used by the command bridge.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Dict, Iterable, List, Mapping, Optional

from backend import app as app_module
from backend.device_registry import registry

app = app_module.app

_EVENT_LIMIT = 80
_EVENT_DEDUP_SEC = 30
_TV_OFFLINE_SEC = int(os.getenv("LG_TV_STATUS_OFFLINE_SEC", "120"))
_lock = threading.Lock()
_events: deque[Dict[str, Any]] = deque(maxlen=_EVENT_LIMIT)
_last_node_health: Dict[str, str] = {}
_last_event_signature: Dict[str, int] = {}
_tv_last_valid: Dict[str, Any] = {}

DEPENDENCIES: Dict[str, List[str]] = {
    "cloudflare_wan": ["internet"],
    "condo_router": ["cloudflare_wan"],
    "tinkerboard": ["condo_router"],
    "dashboard": ["tinkerboard"],
    "mqtt": ["dashboard"],
    "presence": ["mqtt"],
    "lg_tv": ["mqtt"],
    "sonoff": ["dashboard"],
    "zerotier_condo": ["tinkerboard"],
    "zerotier_tunnel": ["zerotier_condo"],
    "zerotier_home": ["zerotier_tunnel"],
    "truenas": ["zerotier_home"],
    "home_assistant": ["truenas"],
    "tuya": ["home_assistant"],
    "electricity": ["home_assistant"],
    "camera": ["dashboard"],
    "pm25": ["home_assistant"],
}

NODE_ORDER = [
    "internet", "cloudflare_wan", "condo_router", "tinkerboard", "dashboard",
    "mqtt", "presence", "lg_tv", "sonoff", "zerotier_condo",
    "zerotier_tunnel", "zerotier_home", "truenas", "home_assistant",
    "tuya", "electricity", "camera", "pm25",
]

NODE_LABELS = {
    "internet": "Internet",
    "cloudflare_wan": "Cloudflare / WAN",
    "condo_router": "Condo Router",
    "tinkerboard": "TinkerBoard",
    "dashboard": "Dashboard",
    "mqtt": "MQTT",
    "presence": "Presence",
    "lg_tv": "LG TV",
    "sonoff": "Sonoff",
    "zerotier_condo": "ZeroTier Condo",
    "zerotier_tunnel": "ZeroTier Tunnel",
    "zerotier_home": "ZeroTier Home",
    "truenas": "TrueNAS",
    "home_assistant": "Home Assistant",
    "tuya": "Tuya",
    "electricity": "Electricity",
    "camera": "Camera",
    "pm25": "PM2.5",
}


def _int(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_tv_state(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return None
    candidates: Iterable[Mapping[str, Any]] = [raw]
    for key in ("tv", "state", "data"):
        nested = raw.get(key)
        if isinstance(nested, Mapping):
            candidates = [*candidates, nested]
    for item in candidates:
        power = item.get("power", item.get("status", item.get("online")))
        app = item.get("app", item.get("current_app"))
        input_name = item.get("input", item.get("source"))
        volume = item.get("volume", item.get("vol"))
        muted = item.get("muted", item.get("mute"))
        if any(value is not None for value in (power, app, input_name, volume, muted)):
            return {
                "power": _safe_scalar(power),
                "app": _safe_scalar(app),
                "input": _safe_scalar(input_name),
                "volume": _safe_scalar(volume),
                "mute": bool(muted) if isinstance(muted, bool) else _safe_scalar(muted),
            }
    return None


def _tv_payload(now: int) -> Dict[str, Any]:
    global _tv_last_valid
    raw = app_module.state.get("last_state")
    updated = _int(app_module.state.get("last_state_ts"))
    normalized = _normalize_tv_state(raw)
    if normalized:
        _tv_last_valid = {**normalized, "last_update_ts": updated or now}
    snapshot = dict(_tv_last_valid)
    last_update = _int(snapshot.get("last_update_ts"))
    age = max(0, now - last_update) if last_update else None
    power_raw = str(snapshot.get("power") or "").strip().lower()
    explicit_off = power_raw in {"off", "false", "0", "offline"}
    online = bool(snapshot) and not explicit_off and age is not None and age <= _TV_OFFLINE_SEC
    return {
        "online": online,
        "health": "healthy" if online else "offline",
        "last_update_ts": last_update,
        "age_sec": age,
        "power": snapshot.get("power"),
        "app": snapshot.get("app"),
        "input": snapshot.get("input"),
        "volume": snapshot.get("volume"),
        "mute": snapshot.get("mute"),
        "source": "mqtt_state",
    }


def _devices_by_type() -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in registry.snapshot():
        grouped.setdefault(item.type, []).append(item.to_dict())
    return grouped


def _aggregate(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"health": "unknown", "online": None, "last_update_ts": None, "latency_ms": None}
    health_values = [str(item.get("health") or "unknown") for item in items]
    if all(value == "offline" for value in health_values):
        health = "offline"
    elif any(value == "offline" for value in health_values) or any(value == "warning" for value in health_values):
        health = "warning"
    elif any(value == "healthy" for value in health_values):
        health = "healthy"
    else:
        health = "unknown"
    updates = [_int(item.get("last_update_ts")) for item in items]
    updates = [value for value in updates if value is not None]
    latencies = [item.get("latency_ms") for item in items if isinstance(item.get("latency_ms"), (int, float))]
    return {
        "health": health,
        "online": health in {"healthy", "warning"},
        "last_update_ts": max(updates) if updates else None,
        "latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "device_count": len(items),
        "devices": items,
    }


def _base_nodes(now: int) -> Dict[str, Dict[str, Any]]:
    grouped = _devices_by_type()
    mqtt_online = bool(app_module.state.get("mqtt_connected"))
    ha_configured = bool(os.getenv("HA_BASE_URL", "").strip() and os.getenv("HA_TOKEN", "").strip())
    pm25 = _aggregate(grouped.get("pm25", []))
    pm25_fresh = bool(pm25.get("last_update_ts") and now - int(pm25["last_update_ts"]) <= 90)
    ha_health = "healthy" if ha_configured and pm25_fresh else ("warning" if ha_configured else "unknown")
    tv = _tv_payload(now)
    return {
        "internet": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "cloudflare_wan": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "condo_router": {"health": "healthy" if grouped.get("presence") else "unknown", "online": True if grouped.get("presence") else None, "diagnostics": {"source": "presence_resolution"}},
        "tinkerboard": {"health": "healthy", "online": True, "last_update_ts": now, "latency_ms": 0.0, "diagnostics": {"source": "local_runtime"}},
        "dashboard": {"health": "healthy", "online": True, "last_update_ts": now, "latency_ms": 0.0, "diagnostics": {"source": "local_runtime"}},
        "mqtt": {"health": "healthy" if mqtt_online else "offline", "online": mqtt_online, "last_update_ts": _int(app_module.state.get("last_state_ts")), "diagnostics": {"source": "mqtt_client"}},
        "presence": _aggregate(grouped.get("presence", [])),
        "lg_tv": {**tv, "diagnostics": {"source": "mqtt_state", "offline_after_sec": _TV_OFFLINE_SEC}},
        "sonoff": _aggregate(grouped.get("sonoff", [])),
        "zerotier_condo": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "zerotier_tunnel": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "zerotier_home": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "truenas": {"health": "unknown", "online": None, "diagnostics": {"source": "not_measured"}},
        "home_assistant": {"health": ha_health, "online": True if ha_health == "healthy" else None, "last_update_ts": pm25.get("last_update_ts"), "diagnostics": {"configured": ha_configured, "source": "configured_pm25_state"}},
        "tuya": _aggregate(grouped.get("tuya_light", [])),
        "electricity": _aggregate(grouped.get("electricity", [])),
        "camera": _aggregate(grouped.get("camera", [])),
        "pm25": pm25,
    }


def _dependents() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {key: [] for key in NODE_ORDER}
    for node, dependencies in DEPENDENCIES.items():
        for dependency in dependencies:
            result.setdefault(dependency, []).append(node)
    return result


def _descendants(root: str, dependents: Dict[str, List[str]]) -> List[str]:
    found: List[str] = []
    pending = list(dependents.get(root, []))
    while pending:
        node = pending.pop(0)
        if node in found:
            continue
        found.append(node)
        pending.extend(dependents.get(node, []))
    return found


def _apply_dependency_health(nodes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    dependents = _dependents()
    roots: List[Dict[str, Any]] = []
    for node_id in NODE_ORDER:
        node = nodes[node_id]
        failed = [dep for dep in DEPENDENCIES.get(node_id, []) if nodes.get(dep, {}).get("health") == "offline"]
        if failed and node.get("health") != "offline":
            node["health"] = "warning"
            node["dependency_warning"] = failed
    for node_id in NODE_ORDER:
        node = nodes[node_id]
        if node.get("health") != "offline":
            continue
        failed_dependencies = [dep for dep in DEPENDENCIES.get(node_id, []) if nodes.get(dep, {}).get("health") == "offline"]
        if failed_dependencies:
            continue
        affected = [item for item in _descendants(node_id, dependents) if nodes.get(item, {}).get("health") in {"offline", "warning"}]
        roots.append({"node": node_id, "label": NODE_LABELS[node_id], "message": f"{NODE_LABELS[node_id]} unavailable", "affected": affected})
    return roots


def _event(kind: str, message: str, node: Optional[str] = None) -> None:
    now = int(time.time())
    signature = f"{kind}:{node or ''}:{message}"
    with _lock:
        last = _last_event_signature.get(signature, 0)
        if now - last < _EVENT_DEDUP_SEC:
            return
        _last_event_signature[signature] = now
        _events.appendleft({"ts": now, "kind": kind, "node": node, "message": message})


def _capture_events(nodes: Dict[str, Dict[str, Any]]) -> None:
    for node_id, node in nodes.items():
        health = str(node.get("health") or "unknown")
        previous = _last_node_health.get(node_id)
        if previous is not None and previous != health:
            if node_id == "mqtt" and health == "healthy":
                _event("reconnect", "MQTT reconnected", node_id)
            elif node_id == "lg_tv" and health == "offline":
                _event("offline", "TV Offline", node_id)
            elif node_id == "tuya" and health in {"warning", "offline"}:
                _event("warning", "Tuya timeout or unavailable device", node_id)
            elif node_id == "home_assistant" and health == "healthy":
                _event("recovery", "Home Assistant available", node_id)
            else:
                _event("health", f"{NODE_LABELS[node_id]} changed to {health}", node_id)
        _last_node_health[node_id] = health


def _overall_health(nodes: Dict[str, Dict[str, Any]]) -> int:
    weights = {"dashboard": 3, "mqtt": 3, "home_assistant": 3, "zerotier_tunnel": 2, "sonoff": 1, "tuya": 1, "lg_tv": 1, "camera": 1, "pm25": 1}
    score = 0.0
    total = 0.0
    values = {"healthy": 1.0, "warning": 0.55, "offline": 0.0, "unknown": 0.7}
    for node_id, weight in weights.items():
        total += weight
        score += values.get(str(nodes.get(node_id, {}).get("health") or "unknown"), 0.7) * weight
    return round(score / total * 100) if total else 0


@app.get("/api/topology")
def topology() -> Dict[str, Any]:
    now = int(time.time())
    nodes = _base_nodes(now)
    roots = _apply_dependency_health(nodes)
    _capture_events(nodes)
    dependents = _dependents()
    public_nodes = []
    for node_id in NODE_ORDER:
        node = dict(nodes[node_id])
        node.update({
            "id": node_id,
            "name": NODE_LABELS[node_id],
            "dependencies": DEPENDENCIES.get(node_id, []),
            "dependents": dependents.get(node_id, []),
            "capabilities": ["status", "diagnostics"],
        })
        public_nodes.append(node)
    with _lock:
        recent_events = list(_events)
    return {
        "ok": True,
        "ts": now,
        "overall_health": _overall_health(nodes),
        "nodes": public_nodes,
        "root_causes": roots,
        "events": recent_events,
        "tv": _tv_payload(now),
    }
