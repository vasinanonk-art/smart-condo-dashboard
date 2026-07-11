"""Home Assistant-backed zone lighting using confirmed entity mappings.

Keeps the existing /api/lighting/zones and /api/lighting/zone contracts while
avoiding unreliable local TinyTuya connectivity. No arbitrary entity IDs or HA
services are accepted from the frontend.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

from backend import app as app_module
from backend import dashboard_extensions

HA_BASE_URL = dashboard_extensions.HA_BASE_URL
HA_TOKEN = dashboard_extensions.HA_TOKEN
HA_TIMEOUT_SEC = 8
HA_MAX_PARALLEL = 5

DEVICE_ENTITY_MAP = {
    "a3ac50b2cb931f9ca2dign": "light.lamptan_jarton_bulb_cct_rgb_11w",
    "a3e7aa413d36be834afz2v": "light.lamptan_jarton_bulb_cct_rgb_11w_2",
    "a3096068728bcb42b0lstt": "light.lamptan_jarton_bulb_cct_rgb_11w_3",
    "a3bded1b786f3ccc4ejngb": "light.lamptan_jarton_bulb_cct_rgb_11w_4",
    "a3b42e18202f06f8a1s5x2": "light.lamptan_jarton_bulb_cct_rgb_11w_5",
}

_state_lock = threading.Lock()
_last_valid_states: Dict[str, Dict[str, Any]] = {}


def _safe_error(exc: Any) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "home assistant authentication issue"
        if exc.code == 404:
            return "entity unavailable"
        return "home assistant command failed"
    if isinstance(exc, (urllib.error.URLError, TimeoutError)):
        return "home assistant unavailable"
    text = str(exc or "").lower()
    if "timeout" in text:
        return "timeout"
    return "home assistant command failed"


def _ha_request(path: str, method: str = "GET", payload: Dict[str, Any] | None = None) -> Any:
    if not HA_BASE_URL or not HA_TOKEN:
        raise RuntimeError("Home Assistant is not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{HA_BASE_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {HA_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=HA_TIMEOUT_SEC) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _brightness_pct(value: int) -> int:
    return max(1, min(100, round(_clamp(value, 10, 1000) / 10)))


def _temperature_kelvin(value: int) -> int:
    # Existing dashboard semantics: 0 = cool/daylight, 1000 = warm.
    normalized = _clamp(value, 0, 1000) / 1000.0
    return round(6500 - (6500 - 2700) * normalized)


def _temperature_value(kelvin: Any) -> int | None:
    try:
        number = float(kelvin)
    except (TypeError, ValueError):
        return None
    return _clamp(round((6500 - number) / (6500 - 2700) * 1000), 0, 1000)


def _hsv_hex(h: int, s: int, v: int) -> str:
    return f"{_clamp(h, 0, 360):04x}{_clamp(s, 0, 1000):04x}{_clamp(v, 0, 1000):04x}"


def _entity_for_device(device: Dict[str, Any]) -> str | None:
    return DEVICE_ENTITY_MAP.get(str(device.get("id") or ""))


def _service_payload(entity_id: str, body: dashboard_extensions.ZoneCommand, preset: Dict[str, Any] | None) -> Dict[str, Any]:
    action = body.action.strip().lower()
    payload: Dict[str, Any] = {"entity_id": entity_id}

    if action == "brightness":
        payload["brightness_pct"] = _brightness_pct(int(body.value))
    elif action in ("temperature", "temp", "cct"):
        payload["color_temp_kelvin"] = _temperature_kelvin(int(body.value))
    elif action == "rgb":
        payload["hs_color"] = [_clamp(int(body.h), 0, 360), _clamp(int(body.s), 0, 1000) / 10.0]
        payload["brightness_pct"] = _brightness_pct(int(body.v))
    elif action == "preset" and preset:
        mode = str(preset.get("mode") or "")
        if mode == "white":
            payload["brightness_pct"] = _brightness_pct(int(preset["brightness"]))
            payload["color_temp_kelvin"] = _temperature_kelvin(int(preset["temperature"]))
        elif mode == "brightness":
            payload["brightness_pct"] = _brightness_pct(int(preset["value"]))
        elif mode == "temperature":
            payload["color_temp_kelvin"] = _temperature_kelvin(int(preset["value"]))
        elif mode == "colour":
            payload["hs_color"] = [_clamp(int(preset["h"]), 0, 360), _clamp(int(preset["s"]), 0, 1000) / 10.0]
            payload["brightness_pct"] = _brightness_pct(int(preset["v"]))
        else:
            raise ValueError("unsupported preset")
    else:
        raise ValueError("unsupported zone action")
    return payload


def _command_one(device: Dict[str, Any], body: dashboard_extensions.ZoneCommand, preset: Dict[str, Any] | None) -> Dict[str, Any]:
    device_id = str(device.get("id") or "")
    entity_id = _entity_for_device(device)
    if not entity_id:
        return {"deviceid": device_id, "ok": False, "error": "entity unavailable"}
    try:
        payload = _service_payload(entity_id, body, preset)
        _ha_request("/api/services/light/turn_on", "POST", payload)
        return {"deviceid": device_id, "ok": True}
    except Exception as exc:
        return {"deviceid": device_id, "ok": False, "error": _safe_error(exc)}


def _normalized_ha_state(device: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    attrs = payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {}
    supported = attrs.get("supported_color_modes") if isinstance(attrs.get("supported_color_modes"), list) else []
    brightness = attrs.get("brightness")
    brightness_value = None
    try:
        if brightness is not None:
            brightness_value = _clamp(round(float(brightness) / 255 * 1000), 10, 1000)
    except (TypeError, ValueError):
        pass
    hs = attrs.get("hs_color") if isinstance(attrs.get("hs_color"), (list, tuple)) and len(attrs.get("hs_color")) >= 2 else None
    rgb_value = None
    if hs:
        value = max(10, brightness_value or 1000)
        rgb_value = _hsv_hex(round(float(hs[0])), round(float(hs[1]) * 10), value)
    return {
        "deviceid": str(device.get("id") or ""),
        "target": app_module.device_target(device),
        "name": str(device.get("name") or app_module.device_target(device)),
        "capabilities": {
            "brightness": bool("brightness" in supported or brightness is not None),
            "temperature": bool(any(mode in supported for mode in ("color_temp", "color_temp_kelvin")) or attrs.get("color_temp_kelvin") is not None),
            "rgb": bool(any(mode in supported for mode in ("hs", "rgb", "rgbw", "rgbww")) or hs is not None),
        },
        "online": payload.get("state") not in ("unavailable", "unknown", None),
        "status": "online" if payload.get("state") not in ("unavailable", "unknown", None) else "offline",
        "values": {
            "brightness": brightness_value,
            "temperature": _temperature_value(attrs.get("color_temp_kelvin")),
            "rgb": rgb_value,
        },
    }


def _read_one(device: Dict[str, Any]) -> Dict[str, Any] | None:
    device_id = str(device.get("id") or "")
    entity_id = _entity_for_device(device)
    if not entity_id:
        return None
    try:
        payload = _ha_request(f"/api/states/{entity_id}")
        item = _normalized_ha_state(device, payload)
        with _state_lock:
            _last_valid_states[device_id] = item
        return item
    except Exception:
        with _state_lock:
            cached = _last_valid_states.get(device_id)
        return dict(cached) if cached else None


def _zones_payload() -> Dict[str, Any]:
    presets, preset_source = dashboard_extensions._presets()
    zones = dashboard_extensions._zones()
    result = []
    for zone_name in zones:
        devices, missing = dashboard_extensions._zone_devices(zone_name)
        public = []
        with ThreadPoolExecutor(max_workers=HA_MAX_PARALLEL) as pool:
            jobs = {pool.submit(_read_one, device): device for device in devices}
            for job in as_completed(jobs):
                item = job.result()
                if item:
                    public.append(item)
        order = {str(device.get("id") or ""): index for index, device in enumerate(devices)}
        public.sort(key=lambda item: order.get(item["deviceid"], 999))
        support = {
            action: sum(1 for item in public if item["capabilities"].get(action))
            for action in ("brightness", "temperature", "rgb")
        }
        total = len(public)
        available_presets = []
        for key, preset in presets.items():
            count = sum(1 for item in public if dashboard_extensions._preset_supported(item["capabilities"], preset))
            if count:
                available_presets.append({
                    "key": key,
                    **preset,
                    "supported_devices": count,
                    "total_devices": total,
                    "partial": count < total,
                })
        result.append({
            "zone": zone_name,
            "configured_members": zones.get(zone_name, []),
            "missing_members": missing,
            "devices": public,
            "support": support,
            "partial_support": any(0 < count < total for count in support.values()) if total else False,
            "presets": available_presets,
        })
    return {"ok": True, "configured": bool(zones), "zones": result, "preset_source": preset_source, "control_source": "home_assistant"}


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
    with ThreadPoolExecutor(max_workers=HA_MAX_PARALLEL) as pool:
        jobs = {pool.submit(_command_one, device, body, preset): device for device in devices}
        for job in as_completed(jobs):
            results.append(job.result())
    order = {str(device.get("id") or ""): index for index, device in enumerate(devices)}
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
    }


def _replace_route(path: str, method: str, endpoint: Any) -> None:
    app = app_module.app
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and method in set(getattr(route, "methods", set()) or set()))
    ]
    app.add_api_route(path, endpoint, methods=[method], response_model=None)


_replace_route("/api/lighting/zones", "GET", _zones_payload)
_replace_route("/api/lighting/zone", "POST", _zone_command)
