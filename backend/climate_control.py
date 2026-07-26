"""Provider-based IR climate contract with honest state confidence."""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import app as app_module
from backend import tapo_ir_local_bridge

app = app_module.app
STATE_PATH = Path(os.getenv("CLIMATE_STATE_FILE", "/root/.smart-condo-dashboard/state/climate_last_commands.json"))
_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.Lock] = {}
_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Dict[str, Any]] = {}
STATE_FIELDS = ("power", "mode", "temperature", "fan", "swing")


class ClimateCommand(BaseModel):
    power: bool | None = None
    mode: str | None = None
    temperature: int | None = None
    fan: str | None = None
    swing: bool | None = None


@dataclass
class ClimateDevice:
    id: str
    name: str
    provider: str
    controllable: bool
    modes: tuple[str, ...] = ()
    fans: tuple[str, ...] = ()
    temperature_min: int | None = None
    temperature_max: int | None = None
    swing_supported: bool = False
    feedback: bool = False
    reason: str | None = None

    def send(self, command: Dict[str, Any]) -> None:
        del command
        raise LookupError("unsupported_provider")

    def read_feedback(self) -> Optional[Dict[str, Any]]:
        return None


def _load_state() -> None:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(item, dict):
                    continue
                state = item.get("state")
                if isinstance(state, dict):
                    _STATE[str(key)] = {
                        "state": _sanitize_state(state),
                        "updated_at": item.get("updated_at") if isinstance(item.get("updated_at"), int) else None,
                    }
    except Exception:
        pass


def _persist_state() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_PATH.parent, 0o700)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(_STATE, separators=(",", ":")), encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, STATE_PATH)


def detect_devices() -> list[ClimateDevice]:
    provider_status = tapo_ir_local_bridge.local_tapo_ir_status()
    if not provider_status.get("configured"):
        return []
    diagnostics = provider_status.get("diagnostics") if isinstance(provider_status.get("diagnostics"), dict) else {}
    supported = bool(diagnostics.get("local_control_supported") and provider_status.get("supported_actions"))
    reason = "climate_command_mapping_not_configured" if supported else "ir_send_not_supported_by_installed_provider"
    return [ClimateDevice(
        id="climate-1", name="Tapo IR bridge", provider="tapo_local",
        controllable=False, reason=reason,
    )]


def _device(device_id: str) -> ClimateDevice | None:
    return next((device for device in detect_devices() if device.id == device_id), None)


def _lock(device_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(device_id, threading.Lock())


def _public_device(device: ClimateDevice) -> Dict[str, Any]:
    return {
        "id": device.id, "name": device.name, "provider": device.provider,
        "controllable": device.controllable,
        "capabilities": {
            "modes": list(device.modes), "fans": list(device.fans),
            "temperature_min": device.temperature_min,
            "temperature_max": device.temperature_max,
            "swing": device.swing_supported,
            "feedback": device.feedback,
        },
        "unsupported_reason": device.reason,
    }


def _validate(device: ClimateDevice, command: Dict[str, Any]) -> str | None:
    if not command:
        return "empty_command"
    if command.get("mode") is not None and command["mode"] not in device.modes:
        return "unsupported_mode"
    if command.get("fan") is not None and command["fan"] not in device.fans:
        return "unsupported_fan"
    if command.get("swing") is not None and not device.swing_supported:
        return "unsupported_swing"
    temperature = command.get("temperature")
    if temperature is not None and (
        device.temperature_min is None or device.temperature_max is None
        or temperature < device.temperature_min or temperature > device.temperature_max
    ):
        return "temperature_out_of_range"
    return None


def _sanitize_state(value: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value[key]) for key in STATE_FIELDS if key in value}


def _status(device: ClimateDevice) -> Dict[str, Any]:
    feedback = device.read_feedback() if device.feedback else None
    if isinstance(feedback, dict):
        return {"state": _sanitize_state(feedback), "state_confidence": "confirmed", "updated_at": int(time.time())}
    with _STATE_LOCK:
        assumed = copy.deepcopy(_STATE.get(device.id))
    if assumed:
        return {"state": assumed.get("state"), "state_confidence": "assumed", "updated_at": assumed.get("updated_at")}
    return {"state": None, "state_confidence": "unknown", "updated_at": None}


@app.get("/api/climate/devices")
def climate_devices() -> Dict[str, Any]:
    return {"devices": [{**_public_device(device), "status": _status(device)} for device in detect_devices()]}


@app.get("/api/climate/{device_id}/status")
def climate_status(device_id: str):
    device = _device(device_id)
    if device is None:
        return JSONResponse({"detail": "climate_device_not_found"}, status_code=404)
    return {"device": _public_device(device), **_status(device)}


@app.post("/api/climate/{device_id}/command")
def climate_command(device_id: str, payload: ClimateCommand = Body(...)):
    device = _device(device_id)
    if device is None:
        return JSONResponse({"detail": "climate_device_not_found"}, status_code=404)
    if not device.controllable:
        return JSONResponse({"detail": "unsupported_provider", "reason": device.reason}, status_code=422)
    command = payload.model_dump(exclude_none=True)
    error = _validate(device, command)
    if error:
        return JSONResponse({"detail": error}, status_code=422)
    lock = _lock(device_id)
    if not lock.acquire(blocking=False):
        return JSONResponse({"detail": "climate_device_busy"}, status_code=409)
    try:
        device.send(command)
        feedback = device.read_feedback() if device.feedback else None
        now = int(time.time())
        if isinstance(feedback, dict):
            return {"ok": True, "state": _sanitize_state(feedback), "state_confidence": "confirmed", "updated_at": now}
        with _STATE_LOCK:
            previous = _STATE.get(device.id, {}).get("state")
            merged = dict(previous) if isinstance(previous, dict) else {}
            merged.update(command)
            _STATE[device.id] = {"state": merged, "updated_at": now}
            _persist_state()
        return {"ok": True, "state": merged, "state_confidence": "assumed", "updated_at": now}
    except TimeoutError:
        return JSONResponse({"detail": "climate_command_timeout", "state_confidence": "unknown"}, status_code=504)
    except Exception:
        return JSONResponse({"detail": "climate_command_failed", "state_confidence": "unknown"}, status_code=502)
    finally:
        lock.release()


_load_state()
