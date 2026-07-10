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
HA_AUTOMATION_CACHE_SEC = 30

_automation_lock = threading.Lock()
_automation_cache: Dict[str, Any] = {
    "items": [],
    "fetched_ts": 0,
    "last_error": None,
}


class ZoneCommand(BaseModel):
    zone: str
    action: str
    value: int | None = None
    h: int | None = None
    s: int | None = None
    v: int | None = None
    preset: str | None = None


class AutomationCommand(BaseModel):
    entity_id: str
    action: str


def _safe_error(exc: Any) -> str:
    text = str(exc or "operation failed")
    for secret in (HA_TOKEN,):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text[:240]


def _load_json_env(name: str) -> Dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _zones() -> Dict[str, List[str]]:
    result = {}
    for name, members in _load_json_env(ZONE_CONFIG_ENV).items():
        if not isinstance(members, list):
            continue
        clean = [str(item).strip() for item in members if str(item).strip()]
        if clean:
            result[str(name).strip()] = clean
    return result


def _presets() -> Dict[str, Dict[str, Any]]:
    result = {}
    for name, config in _load_json_env(PRESET_CONFIG_ENV).items():
        if not isinstance(config, dict):
            continue
        action = str(config.get("action") or "").lower()
        if action == "brightness" and config.get("value") is not None:
            result[str(name)] = {"action": action, "value": max(10, min(1000, int(config["value"])))}
        elif action in ("temperature", "temp", "cct") and config.get("value") is not None:
            result[str(name)] = {"action": "temperature", "value": max(0, min(1000, int(config["value"])))}
        elif action == "rgb" and all(config.get(k) is not None for k in ("h", "s", "v")):
            result[str(name)] = {
                "action": "rgb",
                "h": max(0, min(360, int(config["h"]))),
                "s": max(0, min(1000, int(config["s"]))),
                "v": max(0, min(1000, int(config["v"]))),
            }
    return result


def _device_key(device: Dict[str, Any]) -> str:
    return str(device.get("id") or app_module.device_target(device))


def _zone_devices(zone: str) -> List[Dict[str, Any]]:
    members = set(_zones().get(zone, []))
    if not members:
        return []
    result = []
    for device in app_module.load_lights():
        keys = {_device_key(device), str(device.get("id") or ""), app_module.device_target(device)}
        if members.intersection(keys):
            result.append(device)
    return result


def _status_for(device: Dict[str, Any]) -> Dict[str, Any]:
    status = app_module.cache_first_status(device)
    dps = ((status.get("result") or {}).get("dps") or {}) if isinstance(status, dict) else {}
    return {"status": status, "dps": dps}


def _has_dp(dps: Dict[str, Any], dp: int) -> bool:
    return str(dp) in dps or dp in dps


def _capabilities(device: Dict[str, Any]) -> Dict[str, bool]:
    dps = _status_for(device)["dps"]
    brightness = _has_dp(dps, 22)
    temperature = _has_dp(dps, 23)
    rgb = _has_dp(dps, 24)
    return {
        "brightness": brightness,
        "temperature": temperature,
        "rgb": rgb,
        "scenes": brightness or temperature or rgb,
    }


def _supports(device: Dict[str, Any], action: str) -> bool:
    caps = _capabilities(device)
    if action == "brightness":
        return caps["brightness"]
    if action == "temperature":
        return caps["temperature"]
    if action == "rgb":
        return caps["rgb"]
    return False


def _public_device(device: Dict[str, Any]) -> Dict[str, Any]:
    info = _status_for(device)
    return {
        "deviceid": _device_key(device),
        "target": app_module.device_target(device),
        "name": str(device.get("name") or app_module.device_target(device)),
        "capabilities": _capabilities(device),
        "online": bool(info["status"].get("online")),
        "status": info["status"].get("status"),
        "dps": {key: value for key, value in info["dps"].items() if str(key) in ("21", "22", "23", "24")},
    }


def _zone_payload(name: str) -> Dict[str, Any]:
    configured = _zones().get(name, [])
    devices = _zone_devices(name)
    public = [_public_device(device) for device in devices]
    support = {
        action: sum(1 for item in public if item["capabilities"].get(action))
        for action in ("brightness", "temperature", "rgb", "scenes")
    }
    total = len(public)
    return {
        "zone": name,
        "configured_members": configured,
        "devices": public,
        "support": support,
        "partial_support": any(0 < count < total for count in support.values()) if total else False,
    }


def _command_from_payload(action: str, body: ZoneCommand, preset: Dict[str, Any] | None = None):
    cfg = preset or {}
    if action == "brightness":
        value = cfg.get("value", body.value)
        return app_module.LightCommand(target="", action="brightness", value=max(10, min(1000, int(value))))
    if action == "temperature":
        value = cfg.get("value", body.value)
        return app_module.LightCommand(target="", action="temperature", value=max(0, min(1000, int(value))))
    if action == "rgb":
        h = cfg.get("h", body.h)
        s = cfg.get("s", body.s)
        v = cfg.get("v", body.v)
        return app_module.LightCommand(
            target="",
            action="rgb",
            h=max(0, min(360, int(h))),
            s=max(0, min(1000, int(s))),
            v=max(0, min(1000, int(v))),
        )
    raise HTTPException(status_code=400, detail="unsupported zone action")


@app.get("/api/lighting/zones")
def lighting_zones():
    zones = _zones()
    return {
        "ok": True,
        "configured": bool(zones),
        "zones": [_zone_payload(name) for name in zones],
        "presets": _presets(),
    }


@app.post("/api/lighting/zone")
def lighting_zone_command(body: ZoneCommand):
    zone = body.zone.strip()
    if zone not in _zones():
        raise HTTPException(status_code=404, detail="zone not configured")
    action = body.action.strip().lower()
    preset = None
    preset_name = None
    if action == "preset":
        preset_name = str(body.preset or "")
        preset = _presets().get(preset_name)
        if not preset:
            raise HTTPException(status_code=404, detail="preset not configured")
        action = preset["action"]
    command = _command_from_payload(action, body, preset)
    results = []
    for device in _zone_devices(zone):
        device_id = _device_key(device)
        if not _supports(device, action):
            results.append({"deviceid": device_id, "ok": False, "unsupported": True, "error": "capability not supported"})
            continue
        try:
            app_module.apply_light(device, command)
            results.append({"deviceid": device_id, "ok": True})
        except Exception as exc:
            results.append({"deviceid": device_id, "ok": False, "error": _safe_error(exc)})
    return {
        "ok": any(item.get("ok") for item in results),
        "zone": zone,
        "action": body.action,
        "preset": preset_name,
        "partial": any(not item.get("ok") for item in results) and any(item.get("ok") for item in results),
        "results": results,
    }


def _ha_request(path: str, method: str = "GET", payload: Dict[str, Any] | None = None) -> Any:
    if not HA_BASE_URL or not HA_TOKEN:
        raise RuntimeError("Home Assistant is not configured")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{HA_BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        raw = response.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _normalize_automation(item: Dict[str, Any]) -> Dict[str, Any] | None:
    entity_id = str(item.get("entity_id") or "")
    if not entity_id.startswith("automation."):
        return None
    attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    state = str(item.get("state") or "unavailable")
    return {
        "entity_id": entity_id,
        "name": str(attributes.get("friendly_name") or entity_id.replace("automation.", "").replace("_", " ").title()),
        "enabled": state == "on",
        "state": state,
        "last_triggered": attributes.get("last_triggered"),
        "mode": attributes.get("mode"),
        "current": int(attributes.get("current") or 0),
        "available": state not in ("unavailable", "unknown"),
    }


def _fetch_automations(force: bool = False) -> List[Dict[str, Any]]:
    now = int(time.time())
    with _automation_lock:
        if not force and _automation_cache["items"] and now - int(_automation_cache["fetched_ts"] or 0) < HA_AUTOMATION_CACHE_SEC:
            return list(_automation_cache["items"])
    try:
        payload = _ha_request("/api/states")
        items = []
        for raw in payload if isinstance(payload, list) else []:
            normalized = _normalize_automation(raw if isinstance(raw, dict) else {})
            if normalized:
                items.append(normalized)
        items.sort(key=lambda item: item["name"].lower())
        with _automation_lock:
            _automation_cache.update({"items": items, "fetched_ts": now, "last_error": None})
        return items
    except Exception as exc:
        with _automation_lock:
            _automation_cache["last_error"] = _safe_error(exc)
            return list(_automation_cache["items"])


@app.get("/api/ha/automations")
def ha_automations():
    items = _fetch_automations()
    with _automation_lock:
        error = _automation_cache["last_error"]
        fetched_ts = _automation_cache["fetched_ts"]
    return {
        "ok": error is None or bool(items),
        "configured": bool(HA_BASE_URL and HA_TOKEN),
        "automations": items,
        "last_success_ts": fetched_ts or None,
        "stale": bool(error and items),
        "error": error if error and not items else None,
    }


@app.post("/api/ha/automation")
def ha_automation_action(body: AutomationCommand):
    entity_id = body.entity_id.strip()
    action = body.action.strip().lower()
    if not entity_id.startswith("automation."):
        raise HTTPException(status_code=400, detail="invalid automation entity")
    latest = {item["entity_id"] for item in _fetch_automations(force=True)}
    if entity_id not in latest:
        raise HTTPException(status_code=404, detail="automation not found")
    services = {"enable": "turn_on", "disable": "turn_off", "trigger": "trigger"}
    service = services.get(action)
    if not service:
        raise HTTPException(status_code=400, detail="unsupported automation action")
    try:
        _ha_request(f"/api/services/automation/{service}", method="POST", payload={"entity_id": entity_id})
        _fetch_automations(force=True)
        return {"ok": True, "entity_id": entity_id, "action": action}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_safe_error(exc))
