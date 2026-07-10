import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from fastapi import HTTPException
from pydantic import BaseModel

from backend import app as app_module

app = app_module.app

ZONE_CONFIG_ENV = "TUYA_LIGHT_ZONES_JSON"
PRESET_CONFIG_ENV = "TUYA_LIGHT_PRESETS_JSON"
HA_BASE_URL = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "").strip()
HA_AUTOMATION_POLL_SEC = 30

_automation_lock = threading.Lock()
_automation_cache: Dict[str, Any] = {
    "configured": bool(HA_BASE_URL and HA_TOKEN),
    "items": [],
    "last_success_ts": None,
    "last_poll_ts": None,
    "last_error": None,
}


class ZoneCommand(BaseModel):
    action: str
    value: int | None = None
    h: int | None = None
    s: int | None = None
    v: int | None = None
    preset: str | None = None


class AutomationAction(BaseModel):
    action: str


def _safe_error(exc: Exception) -> str:
    return type(exc).__name__


def _json_env(name: str) -> Dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _zone_config() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for zone, refs in _json_env(ZONE_CONFIG_ENV).items():
        if isinstance(zone, str) and isinstance(refs, list):
            cleaned = [str(ref).strip() for ref in refs if str(ref).strip()]
            if cleaned:
                result[zone.strip()] = cleaned
    return result


def _preset_config() -> Dict[str, Dict[str, Any]]:
    configured = _json_env(PRESET_CONFIG_ENV)
    if configured:
        return {str(k): v for k, v in configured.items() if isinstance(v, dict)}
    try:
        return app_module.load_scenes()
    except Exception:
        return {}


def _device_ref_values(device: Dict[str, Any]) -> set[str]:
    values = {
        str(device.get("id") or "").strip(),
        str(device.get("name") or "").strip(),
        str(device.get("ip") or "").strip(),
    }
    try:
        values.add(str(app_module.device_target(device)).strip())
    except Exception:
        pass
    return {value for value in values if value}


def _resolve_zone_devices(zone: str) -> List[Dict[str, Any]]:
    refs = set(_zone_config().get(zone, []))
    if not refs:
        return []
    devices = []
    for device in app_module.load_lights():
        if refs.intersection(_device_ref_values(device)):
            devices.append(device)
    return devices


def _cached_public_device(device: Dict[str, Any]) -> Dict[str, Any]:
    target = app_module.device_target(device)
    status = app_module.cache_first_status(device)
    dps = ((status.get("result") or {}).get("dps") or {}) if isinstance(status, dict) else {}
    capability = {
        "brightness": "22" in dps or 22 in dps,
        "temperature": "23" in dps or 23 in dps,
        "rgb": "24" in dps or 24 in dps,
    }
    capability["scenes"] = capability["rgb"] or (capability["brightness"] and capability["temperature"])
    return {
        "deviceid": str(device.get("id") or target),
        "target": target,
        "name": str(device.get("name") or target),
        "online": bool(status.get("online")),
        "status": status.get("status"),
        "last_seen_ts": status.get("last_seen_ts"),
        "capabilities": capability,
        "values": {
            "brightness": dps.get("22", dps.get(22)),
            "temperature": dps.get("23", dps.get(23)),
            "rgb": dps.get("24", dps.get(24)),
        },
    }


def _preset_supported(preset: Dict[str, Any], capabilities: Dict[str, bool]) -> bool:
    mode = str(preset.get("mode") or "").lower()
    if mode == "white":
        return capabilities.get("brightness", False) and capabilities.get("temperature", False)
    if mode in ("colour", "color", "rgb"):
        return capabilities.get("rgb", False)
    return False


def _zone_payload(zone: str, refs: List[str]) -> Dict[str, Any]:
    devices = [_cached_public_device(device) for device in _resolve_zone_devices(zone)]
    if devices:
        all_caps = {
            key: all(device["capabilities"].get(key, False) for device in devices)
            for key in ("brightness", "temperature", "rgb", "scenes")
        }
        any_caps = {
            key: any(device["capabilities"].get(key, False) for device in devices)
            for key in ("brightness", "temperature", "rgb", "scenes")
        }
    else:
        all_caps = {key: False for key in ("brightness", "temperature", "rgb", "scenes")}
        any_caps = dict(all_caps)
    presets = []
    for key, preset in _preset_config().items():
        if any(_preset_supported(preset, device["capabilities"]) for device in devices):
            presets.append({"key": key, "label": preset.get("label") or key, "mode": preset.get("mode")})
    return {
        "zone": zone,
        "configured_refs": refs,
        "devices": devices,
        "capabilities": any_caps,
        "partial_support": any(any_caps[key] and not all_caps[key] for key in any_caps),
        "presets": presets,
    }


def _zone_command_device(device: Dict[str, Any], body: ZoneCommand) -> Dict[str, Any]:
    target = app_module.device_target(device)
    public = _cached_public_device(device)
    caps = public["capabilities"]
    action = body.action.strip().lower()
    if action == "brightness":
        if not caps["brightness"]:
            return {"deviceid": public["deviceid"], "ok": False, "unsupported": True}
        command = app_module.LightCommand(target=target, action="brightness", value=body.value)
        app_module.apply_light(device, command)
    elif action in ("temperature", "temp", "cct"):
        if not caps["temperature"]:
            return {"deviceid": public["deviceid"], "ok": False, "unsupported": True}
        command = app_module.LightCommand(target=target, action="temperature", value=body.value)
        app_module.apply_light(device, command)
    elif action == "rgb":
        if not caps["rgb"]:
            return {"deviceid": public["deviceid"], "ok": False, "unsupported": True}
        command = app_module.LightCommand(target=target, action="rgb", h=body.h, s=body.s, v=body.v)
        app_module.apply_light(device, command)
    elif action == "preset":
        preset_key = str(body.preset or "")
        preset = _preset_config().get(preset_key)
        if not preset or not _preset_supported(preset, caps):
            return {"deviceid": public["deviceid"], "ok": False, "unsupported": True}
        app_module.apply_scene_config(device, preset)
    else:
        raise HTTPException(status_code=400, detail="unsupported zone action")
    return {"deviceid": public["deviceid"], "ok": True}


@app.get("/api/lighting/zones")
def lighting_zones():
    config = _zone_config()
    return {
        "ok": True,
        "configured": bool(config),
        "expected_config": ZONE_CONFIG_ENV,
        "zones": [_zone_payload(zone, refs) for zone, refs in config.items()],
    }


@app.post("/api/lighting/zones/{zone}/command")
def lighting_zone_command(zone: str, body: ZoneCommand):
    if zone not in _zone_config():
        raise HTTPException(status_code=404, detail="zone not configured")
    devices = _resolve_zone_devices(zone)
    results = []
    for device in devices:
        try:
            results.append(_zone_command_device(device, body))
        except HTTPException:
            raise
        except Exception as exc:
            results.append({"deviceid": str(device.get("id") or app_module.device_target(device)), "ok": False, "error": _safe_error(exc)})
    return {"ok": any(item.get("ok") for item in results), "zone": zone, "action": body.action, "results": results}


def _ha_request(path: str, method: str = "GET", body: Dict[str, Any] | None = None) -> Any:
    if not HA_BASE_URL or not HA_TOKEN:
        raise RuntimeError("Home Assistant not configured")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{HA_BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def _normalize_automation(item: Dict[str, Any]) -> Dict[str, Any] | None:
    entity_id = str(item.get("entity_id") or "")
    if not entity_id.startswith("automation."):
        return None
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    state = str(item.get("state") or "unavailable")
    return {
        "entity_id": entity_id,
        "name": str(attributes.get("friendly_name") or entity_id.removeprefix("automation.").replace("_", " ").title()),
        "enabled": state == "on",
        "state": state,
        "last_triggered": attributes.get("last_triggered"),
        "mode": attributes.get("mode"),
        "current": attributes.get("current", 0),
        "available": state not in ("unavailable", "unknown"),
    }


def _refresh_automations() -> bool:
    with _automation_lock:
        _automation_cache["last_poll_ts"] = int(time.time())
    try:
        payload = _ha_request("/api/states")
        items = []
        for raw in payload if isinstance(payload, list) else []:
            normalized = _normalize_automation(raw) if isinstance(raw, dict) else None
            if normalized:
                items.append(normalized)
        items.sort(key=lambda item: item["name"].lower())
        with _automation_lock:
            _automation_cache.update({"items": items, "last_success_ts": int(time.time()), "last_error": None})
        return True
    except Exception as exc:
        with _automation_lock:
            _automation_cache["last_error"] = _safe_error(exc)
        return False


def _automation_loop() -> None:
    while True:
        _refresh_automations()
        time.sleep(HA_AUTOMATION_POLL_SEC)


def _automation_snapshot() -> Dict[str, Any]:
    with _automation_lock:
        return {
            "configured": _automation_cache["configured"],
            "automations": list(_automation_cache["items"]),
            "last_success_ts": _automation_cache["last_success_ts"],
            "last_poll_ts": _automation_cache["last_poll_ts"],
            "last_error": _automation_cache["last_error"],
        }


@app.get("/api/ha/automations")
def ha_automations():
    snapshot = _automation_snapshot()
    return {"ok": True, **snapshot}


@app.post("/api/ha/automations/{entity_id}/action")
def ha_automation_action(entity_id: str, body: AutomationAction):
    if not entity_id.startswith("automation."):
        raise HTTPException(status_code=400, detail="invalid automation entity")
    snapshot = _automation_snapshot()
    allowed = {item["entity_id"] for item in snapshot["automations"]}
    if entity_id not in allowed:
        raise HTTPException(status_code=404, detail="automation not found in latest state")
    action = body.action.strip().lower()
    service = {"enable": "turn_on", "disable": "turn_off", "trigger": "trigger"}.get(action)
    if not service:
        raise HTTPException(status_code=400, detail="unsupported automation action")
    try:
        _ha_request(f"/api/services/automation/{service}", method="POST", body={"entity_id": entity_id})
        _refresh_automations()
        return {"ok": True, "entity_id": entity_id, "action": action}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_safe_error(exc))


if _automation_cache["configured"]:
    threading.Thread(target=_automation_loop, name="ha-automation-poller", daemon=True).start()
