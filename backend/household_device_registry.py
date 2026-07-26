"""Safe user-facing household device registry.

Provider identifiers and transport configuration intentionally stay outside this
projection. Category-specific routes remain responsible for command dispatch.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from backend import app as app_module
from backend import camera_control, lg_tv_control, lg_tv_status, tapo_ir_local_bridge

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
    tapo_reason = (
        "Tapo H110 detected; command mapping is not configured."
        if tapo else "Tapo H110 configuration is unavailable."
    )
    return [
        _device(
            "living-room-samsung-soundbar", "living_room", "Samsung Soundbar", "soundbar",
            online=None, health="degraded" if tapo else "unavailable",
            reason=tapo_reason,
        ),
        _device(
            "living-room-air-conditioner", "living_room", "Living Room Air Conditioner", "climate",
            online=None, health="degraded" if tapo else "unavailable",
            reason=tapo_reason,
        ),
        _device(
            "living-room-fan", "living_room", "Fan", "fan",
            online=None, health="degraded" if tapo else "unavailable",
            reason=tapo_reason,
        ),
        _device(
            "bed-room-air-conditioner", "bed_room", "Bed Room Air Conditioner", "climate",
            online=None, health="unavailable",
            reason="T3 IR device detected by household inventory; control path is not verified.",
        ),
    ]


def _camera_placeholders() -> list[Dict[str, Any]]:
    configured = camera_control.inventory()
    names = ("Tapo C220", "Xiaomi Camera 1", "Xiaomi Camera 2")
    result = []
    for index, name in enumerate(names):
        record = configured[index] if index < len(configured) else None
        capabilities = copy.deepcopy(record.capabilities) if record is not None else {}
        result.append(_device(
            f"camera-{index + 1}", "living_room", name, "camera",
            online=None,
            health="unknown" if record is not None else "unavailable",
            capabilities=capabilities,
            reason=record.reason if record is not None else "Configuration unavailable",
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
