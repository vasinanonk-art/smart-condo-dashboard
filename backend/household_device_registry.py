"""Safe user-facing household device registry.

Provider identifiers and transport configuration intentionally stay outside this
projection. Category-specific routes remain responsible for command dispatch.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from backend import app as app_module
from backend import camera_read_providers, ir_framework, lg_tv_control, lg_tv_status, tapo_ir_local_bridge

app = app_module.app
_SAFE_FIELDS = {
    "id", "room", "display_name", "category", "online", "health",
    "capabilities", "state", "state_quality", "unavailable_reason",
}


def _device(
    identifier: str,
    room: str,
    name: str,
    category: str,
    *,
    online: bool | None,
    health: str,
    capabilities: Dict[str, Any] | None = None,
    state: Dict[str, Any] | None = None,
    state_quality: str = "unknown",
    reason: str | None = None,
) -> Dict[str, Any]:
    return {
        "id": identifier,
        "room": room,
        "display_name": name,
        "category": category,
        "online": online,
        "health": health,
        "capabilities": copy.deepcopy(capabilities or {}),
        "state": copy.deepcopy(state or {}),
        "state_quality": state_quality,
        "unavailable_reason": reason,
    }


def _lg_tv() -> Dict[str, Any]:
    status = lg_tv_status._public_status()
    controls = lg_tv_control.capabilities(enumerate_live=False)
    online = status.get("online")
    if online is None:
        online = status.get("connection_state") == "connected" if status.get("connection_state") else None
    audio = status.get("audio") if isinstance(status.get("audio"), dict) else {}
    app = status.get("current_app") if isinstance(status.get("current_app"), dict) else {}
    source = status.get("current_input") if isinstance(status.get("current_input"), dict) else {}
    state = {
        "power": status.get("power_state"),
        "input_or_app": source.get("name") or app.get("name"),
        "volume": audio.get("volume"),
        "muted": audio.get("muted"),
        "updated_at": status.get("last_update_ts") or status.get("last_success_ts"),
    }
    return _device(
        "living-room-lg-tv", "living_room", "LG TV", "tv",
        online=online,
        health="healthy" if online is True else "unavailable" if online is False else "unknown",
        capabilities={
            "commands": list(controls.get("supported") or []),
            "power_on": bool((controls.get("power_on") or {}).get("supported")),
            "inputs_available": bool(controls.get("enumeration_available") and controls.get("inputs")),
            "applications_available": bool(controls.get("enumeration_available") and controls.get("applications")),
        },
        state=state,
        state_quality="confirmed" if status.get("last_success_ts") else "unknown",
        reason=None if online is True else "LG TV status is unavailable.",
    )


def _tapo_detected() -> bool:
    try:
        return tapo_ir_local_bridge._configured(tapo_ir_local_bridge._configuration())
    except Exception:
        return False


def _ir_devices() -> list[Dict[str, Any]]:
    tapo = _tapo_detected()
    try:
        inventory = tapo_ir_local_bridge.existing_ir_remote_inventory()
    except Exception:
        inventory = {"bridge_online": None, "remotes": []}
    discovered = inventory.get("remotes") if isinstance(inventory.get("remotes"), list) else []

    def remote_kind(remote: Dict[str, Any]) -> str | None:
        name = str(remote.get("display_name") or "").casefold()
        if "sound" in name:
            return "soundbar"
        if "air" in name and "condition" in name:
            return "air_conditioner"
        if "fan" in name:
            return "fan"
        if "tv" in name or "television" in name:
            return "television"
        return None

    remote_by_kind = {
        kind: remote
        for remote in discovered
        if isinstance(remote, dict) and (kind := remote_kind(remote))
    }
    categories = {
        "television": "tv",
        "soundbar": "soundbar",
        "air_conditioner": "climate",
        "fan": "fan",
    }
    devices = []
    for item in ir_framework.public_devices():
        identity = item.get("device") or {}
        metadata = copy.deepcopy(item.get("capabilities") or [])
        commands = [
            command
            for capability in metadata
            for command in capability.get("commands") or []
        ]
        capabilities = {"ir": metadata} if commands else {}
        is_t3 = identity.get("id") == "bed-room-air-conditioner"
        runtime_status = item.get("runtime_status") or {}
        remote = remote_by_kind.get(identity["type"])
        reason = item.get("unavailable_reason")
        if not commands:
            reason = (
                "T3 IR device detected by household inventory; control path is not verified."
                if is_t3 else
                "Configured Tapo IR remote discovered; transmit interface is not verified."
                if remote else
                "Tapo H110 detected; configured remote was not matched."
                if tapo else "Tapo H110 configuration is unavailable."
            )
        ir_diagnostics = {
            key: copy.deepcopy(runtime_status.get(key))
            for key in (
                "online", "authenticated", "healthy", "firmware_version",
                "model", "latency_ms", "last_command", "last_response",
                "last_error", "pending_queue", "retry_count",
            )
        }
        if remote:
            ir_diagnostics.update({
                "remote_discovered": True,
                "configured_remote_name": remote.get("display_name"),
                "reported_state": copy.deepcopy(remote.get("reported_state") or {}),
                "stored_commands_present": remote.get("stored_commands_present") is True,
                "verified_controls": [],
            })
        devices.append(_device(
            identity["id"],
            identity["room"],
            identity["friendly_name"],
            categories.get(identity["type"], identity["type"]),
            online=runtime_status.get("online"),
            health=(
                "healthy" if runtime_status.get("healthy") is True else
                "degraded" if commands or (tapo and not is_t3) else "unavailable"
            ),
            capabilities=capabilities,
            state={"ir_diagnostics": ir_diagnostics},
            state_quality=(item.get("runtime_status") or {}).get("state_quality") or "unknown",
            reason=reason,
        ))
    tv_remote = remote_by_kind.get("television")
    if tv_remote:
        devices.append(_device(
            "living-room-configured-tv-ir",
            "living_room",
            str(tv_remote.get("display_name") or "Configured TV Remote"),
            "tv",
            online=inventory.get("bridge_online"),
            health="degraded",
            capabilities={},
            state={
                "ir_diagnostics": {
                    "online": inventory.get("bridge_online"),
                    "authenticated": inventory.get("authenticated") is True,
                    "healthy": False,
                    "remote_discovered": True,
                    "configured_remote_name": tv_remote.get("display_name"),
                    "reported_state": copy.deepcopy(tv_remote.get("reported_state") or {}),
                    "stored_commands_present": tv_remote.get("stored_commands_present") is True,
                    "verified_controls": [],
                    "pending_queue": 0,
                    "retry_count": 0,
                }
            },
            state_quality="unknown",
            reason="Configured Tapo IR remote discovered; transmit interface is not verified.",
        ))
    return devices


def _camera_placeholders() -> list[Dict[str, Any]]:
    payload = camera_read_providers._inventory_payload(discover_live=True)
    configured = {
        item["id"]: item
        for item in payload.get("cameras", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    inventory = (
        ("camera-1", "tapo-c220", "Tapo C220"),
        ("camera-2", "xiaomi-camera-1", "Xiaomi Camera 1"),
        ("camera-3", "xiaomi-camera-2", "Xiaomi Camera 2"),
    )
    result = []
    for public_id, config_id, name in inventory:
        record = configured.get(config_id)
        capabilities = copy.deepcopy(record.get("capabilities") or {}) if record else {}
        online = record.get("online") if record else None
        health = record.get("health") if record else "unknown"
        state_quality = "confirmed" if online is True else "unknown"
        state = {}
        if record:
            state = {
                "vendor": record.get("vendor"),
                "model": record.get("model"),
                "last_update": record.get("last_update"),
                "provider_verified": record.get("verification_status") == "verified",
                "discovered_capabilities": copy.deepcopy(record.get("discovered_capabilities") or []),
            }
        result.append(_device(
            public_id,
            record.get("room", "unknown") if record else "unknown",
            record.get("display_name", name) if record else name,
            "camera",
            online=online,
            health=health,
            capabilities=capabilities,
            state=state,
            state_quality=state_quality,
            reason=record.get("unavailable_reason") if record else "Configuration unavailable",
        ))
    return result


def registry() -> list[Dict[str, Any]]:
    devices = [_lg_tv(), *_ir_devices(), *_camera_placeholders()]
    # Defensive projection: future internal fields cannot cross this boundary.
    return [{key: copy.deepcopy(device[key]) for key in _SAFE_FIELDS} for device in devices]


@app.get("/api/devices")
def household_devices() -> Dict[str, Any]:
    devices = registry()
    return {"devices": devices, "count": len(devices)}
