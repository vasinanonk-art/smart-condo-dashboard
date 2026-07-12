"""Stable Home Assistant zone-light state and white-mode handling.

Keeps the existing lighting API contracts. Brightness and color-temperature
commands explicitly use white mode, while recent successful commands are kept
as short-lived optimistic values until Home Assistant reports the same state.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from backend import app as app_module
from backend import dashboard_extensions
from backend import runtime_ha_lighting as base

PENDING_TTL_SEC = 8.0
DEFAULT_WHITE_KELVIN = 4000
_pending_lock = threading.Lock()
_pending: Dict[str, Dict[str, Any]] = {}


def _device_id(device: Dict[str, Any]) -> str:
    return str(device.get("id") or "")


def _pending_values(body: dashboard_extensions.ZoneCommand, preset: Dict[str, Any] | None) -> Dict[str, Any]:
    action = body.action.strip().lower()
    values: Dict[str, Any] = {}
    if action == "brightness":
        values["brightness"] = base._clamp(int(body.value), 10, 1000)
    elif action in ("temperature", "temp", "cct"):
        values["temperature"] = base._clamp(int(body.value), 0, 1000)
    elif action == "rgb":
        values["rgb"] = base._hsv_hex(int(body.h), int(body.s), int(body.v))
        values["brightness"] = base._clamp(int(body.v), 10, 1000)
    elif action == "preset" and preset:
        mode = str(preset.get("mode") or "")
        if mode == "white":
            values["brightness"] = base._clamp(int(preset["brightness"]), 10, 1000)
            values["temperature"] = base._clamp(int(preset["temperature"]), 0, 1000)
        elif mode == "brightness":
            values["brightness"] = base._clamp(int(preset["value"]), 10, 1000)
        elif mode == "temperature":
            values["temperature"] = base._clamp(int(preset["value"]), 0, 1000)
        elif mode == "colour":
            values["rgb"] = base._hsv_hex(int(preset["h"]), int(preset["s"]), int(preset["v"]))
            values["brightness"] = base._clamp(int(preset["v"]), 10, 1000)
    return values


def _current_white_kelvin(entity_id: str) -> int:
    try:
        state = base._ha_request(f"/api/states/{entity_id}")
        attrs = state.get("attributes") if isinstance(state, dict) else {}
        value = attrs.get("color_temp_kelvin") if isinstance(attrs, dict) else None
        if value is not None:
            return max(2000, min(6500, int(float(value))))
    except Exception:
        pass
    return DEFAULT_WHITE_KELVIN


def _service_payload(entity_id: str, body: dashboard_extensions.ZoneCommand, preset: Dict[str, Any] | None) -> Dict[str, Any]:
    action = body.action.strip().lower()
    payload: Dict[str, Any] = {"entity_id": entity_id}

    if action == "brightness":
        # Brightness is a white-light control in this dashboard. Supplying a
        # white color temperature prevents HA from retaining a previous HS/RGB
        # mode (the cause of occasional purple output).
        payload["brightness_pct"] = base._brightness_pct(int(body.value))
        payload["color_temp_kelvin"] = _current_white_kelvin(entity_id)
    elif action in ("temperature", "temp", "cct"):
        payload["color_temp_kelvin"] = base._temperature_kelvin(int(body.value))
    elif action == "rgb":
        payload["hs_color"] = [base._clamp(int(body.h), 0, 360), base._clamp(int(body.s), 0, 1000) / 10.0]
        payload["brightness_pct"] = base._brightness_pct(int(body.v))
    elif action == "preset" and preset:
        mode = str(preset.get("mode") or "")
        if mode == "white":
            payload["brightness_pct"] = base._brightness_pct(int(preset["brightness"]))
            payload["color_temp_kelvin"] = base._temperature_kelvin(int(preset["temperature"]))
        elif mode == "brightness":
            payload["brightness_pct"] = base._brightness_pct(int(preset["value"]))
            payload["color_temp_kelvin"] = _current_white_kelvin(entity_id)
        elif mode == "temperature":
            payload["color_temp_kelvin"] = base._temperature_kelvin(int(preset["value"]))
        elif mode == "colour":
            payload["hs_color"] = [base._clamp(int(preset["h"]), 0, 360), base._clamp(int(preset["s"]), 0, 1000) / 10.0]
            payload["brightness_pct"] = base._brightness_pct(int(preset["v"]))
        else:
            raise ValueError("unsupported preset")
    else:
        raise ValueError("unsupported zone action")
    return payload


def _command_one(device: Dict[str, Any], body: dashboard_extensions.ZoneCommand, preset: Dict[str, Any] | None) -> Dict[str, Any]:
    device_id = _device_id(device)
    entity_id = base._entity_for_device(device)
    if not entity_id:
        return {"deviceid": device_id, "ok": False, "error": "entity unavailable"}
    try:
        current = base._ha_request(f"/api/states/{entity_id}")
        if current.get("state") in ("unavailable", "unknown", None):
            return {"deviceid": device_id, "ok": False, "error": "entity unavailable"}
        payload = _service_payload(entity_id, body, preset)
        base._ha_request("/api/services/light/turn_on", "POST", payload)
        values = _pending_values(body, preset)
        if values:
            with _pending_lock:
                _pending[device_id] = {"expires": time.monotonic() + PENDING_TTL_SEC, "values": values}
        return {"deviceid": device_id, "ok": True}
    except Exception as exc:
        return {"deviceid": device_id, "ok": False, "error": base._safe_error(exc)}


def _confirmed(actual: Any, expected: Any, key: str) -> bool:
    if actual is None:
        return False
    if key in ("brightness", "temperature"):
        try:
            return abs(float(actual) - float(expected)) <= 35
        except (TypeError, ValueError):
            return False
    return str(actual).lower() == str(expected).lower()


def _overlay_pending(payload: Dict[str, Any]) -> Dict[str, Any]:
    now = time.monotonic()
    with _pending_lock:
        for zone in payload.get("zones", []):
            for device in zone.get("devices", []):
                device_id = str(device.get("deviceid") or "")
                pending = _pending.get(device_id)
                if not pending:
                    continue
                if pending["expires"] <= now:
                    _pending.pop(device_id, None)
                    continue
                values = device.setdefault("values", {})
                expected = pending.get("values", {})
                if expected and all(_confirmed(values.get(key), value, key) for key, value in expected.items()):
                    _pending.pop(device_id, None)
                    continue
                values.update(expected)
        return payload


def _zones_payload() -> Dict[str, Any]:
    return _overlay_pending(base._zones_payload())


def _zone_command(body: dashboard_extensions.ZoneCommand):
    zone = body.zone.strip()
    zones = dashboard_extensions._zones()
    if zone not in zones:
        raise dashboard_extensions.HTTPException(status_code=404, detail="zone not configured")
    action = body.action.strip().lower()
    normalized = "temperature" if action in ("temperature", "temp", "cct") else action
    if normalized not in ("brightness", "temperature", "rgb", "preset"):
        raise dashboard_extensions.HTTPException(status_code=400, detail="unsupported zone action")

    presets, _ = dashboard_extensions._presets()
    preset = presets.get(str(body.preset or "")) if normalized == "preset" else None
    if normalized == "preset" and not preset:
        raise dashboard_extensions.HTTPException(status_code=404, detail="preset not configured")

    devices, missing = dashboard_extensions._zone_devices(zone)
    results = []
    with ThreadPoolExecutor(max_workers=base.HA_MAX_PARALLEL) as pool:
        jobs = {pool.submit(_command_one, device, body, preset): device for device in devices}
        for job in as_completed(jobs):
            results.append(job.result())
    order = {_device_id(device): index for index, device in enumerate(devices)}
    results.sort(key=lambda item: order.get(item["deviceid"], 999))
    success = sum(1 for item in results if item.get("ok"))
    return {
        "ok": success > 0,
        "zone": zone,
        "action": normalized,
        "preset": body.preset,
        "partial": success != len(results),
        "missing_members": missing,
        "results": results,
        "control_source": "home_assistant",
    }


def _replace_route(path: str, method: str, endpoint: Any) -> None:
    app = app_module.app
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and method in set(getattr(route, "methods", set()) or set()))
    ]
    app.add_api_route(path, endpoint, methods=[method], response_model=None)


def _install_routes() -> None:
    _replace_route("/api/lighting/zones", "GET", _zones_payload)
    _replace_route("/api/lighting/zone", "POST", _zone_command)


_install_routes()


@app_module.app.on_event("startup")
def ensure_stable_ha_lighting_routes() -> None:
    _install_routes()
    print("lighting zone state mode: optimistic-white", flush=True)
