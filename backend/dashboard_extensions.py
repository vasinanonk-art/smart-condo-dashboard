import json
import os
import threading
import time
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
    "last_poll_ts": 0,
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
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:160]
    return type(exc).__name__


def _load_json_env(name: str) -> Dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        print(f"dashboard config invalid: {name}", flush=True)
        return {}


def _zones() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for name, members in _load_json_env(ZONE_CONFIG_ENV).items():
        if not isinstance(members, list):
            continue
        clean = [str(item).strip() for item in members if str(item).strip()]
        if clean:
            result[str(name).strip()] = clean
    return result


def _normalize_preset(name: str, config: Dict[str, Any]) -> Dict[str, Any] | None:
    mode = str(config.get("mode") or "").lower()
    action = str(config.get("action") or "").lower()
    label = str(config.get("label") or name)
    if mode == "white" and config.get("brightness") is not None and config.get("temperature") is not None:
        return {
            "label": label,
            "mode": "white",
            "brightness": max(10, min(1000, int(config["brightness"]))),
            "temperature": max(0, min(1000, int(config["temperature"]))),
        }
    if mode in ("colour", "color", "rgb") and all(config.get(k) is not None for k in ("h", "s", "v")):
        return {
            "label": label,
            "mode": "colour",
            "h": max(0, min(360, int(config["h"]))),
            "s": max(0, min(1000, int(config["s"]))),
            "v": max(0, min(1000, int(config["v"]))),
        }
    if action == "brightness" and config.get("value") is not None:
        return {"label": label, "mode": "brightness", "value": max(10, min(1000, int(config["value"])))}
    if action in ("temperature", "temp", "cct") and config.get("value") is not None:
        return {"label": label, "mode": "temperature", "value": max(0, min(1000, int(config["value"])))}
    if action == "rgb" and all(config.get(k) is not None for k in ("h", "s", "v")):
        return {
            "label": label,
            "mode": "colour",
            "h": max(0, min(360, int(config["h"]))),
            "s": max(0, min(1000, int(config["s"]))),
            "v": max(0, min(1000, int(config["v"]))),
        }
    return None


def _presets() -> tuple[Dict[str, Dict[str, Any]], str]:
    configured = _load_json_env(PRESET_CONFIG_ENV)
    source = configured if configured else app_module.load_scenes()
    source_name = PRESET_CONFIG_ENV if configured else "config/scenes.json"
    result: Dict[str, Dict[str, Any]] = {}
    for name, config in source.items() if isinstance(source, dict) else []:
        if isinstance(config, dict):
            normalized = _normalize_preset(str(name), config)
            if normalized:
                result[str(name)] = normalized
    return result, source_name


def _device_key(device: Dict[str, Any]) -> str:
    return str(device.get("id") or app_module.device_target(device))


def _device_match_keys(device: Dict[str, Any]) -> set[str]:
    return {
        value.lower()
        for value in (
            _device_key(device),
            str(device.get("id") or ""),
            str(device.get("name") or ""),
            str(device.get("ip") or ""),
            app_module.device_target(device),
        )
        if value
    }


def _zone_devices(zone: str) -> tuple[List[Dict[str, Any]], List[str]]:
    configured = _zones().get(zone, [])
    requested = {member.lower() for member in configured}
    result: List[Dict[str, Any]] = []
    matched: set[str] = set()
    for device in app_module.load_lights():
        hits = requested.intersection(_device_match_keys(device))
        if hits:
            result.append(device)
            matched.update(hits)
    return result, sorted(requested - matched)


def _status_for(device: Dict[str, Any]) -> Dict[str, Any]:
    status = app_module.cache_first_status(device)
    dps = ((status.get("result") or {}).get("dps") or {}) if isinstance(status, dict) else {}
    return {"status": status, "dps": dps}


def _has_dp(dps: Dict[str, Any], dp: int) -> bool:
    return str(dp) in dps or dp in dps


def _capabilities(device: Dict[str, Any]) -> Dict[str, bool]:
    dps = _status_for(device)["dps"]
    return {
        "brightness": _has_dp(dps, 22),
        "temperature": _has_dp(dps, 23),
        "rgb": _has_dp(dps, 24),
    }


def _preset_supported(caps: Dict[str, bool], preset: Dict[str, Any]) -> bool:
    mode = preset.get("mode")
    if mode == "white":
        return caps["brightness"] and caps["temperature"]
    if mode == "brightness":
        return caps["brightness"]
    if mode == "temperature":
        return caps["temperature"]
    if mode == "colour":
        return caps["rgb"]
    return False


def _public_device(device: Dict[str, Any]) -> Dict[str, Any]:
    info = _status_for(device)
    dps = info["dps"]
    return {
        "deviceid": _device_key(device),
        "target": app_module.device_target(device),
        "name": str(device.get("name") or app_module.device_target(device)),
        "capabilities": _capabilities(device),
        "online": bool(info["status"].get("online")),
        "status": info["status"].get("status"),
        "values": {
            "brightness": dps.get("22", dps.get(22)),
            "temperature": dps.get("23", dps.get(23)),
            "rgb": dps.get("24", dps.get(24)),
        },
    }


def _zone_payload(name: str, presets: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    configured = _zones().get(name, [])
    devices, missing = _zone_devices(name)
    public = [_public_device(device) for device in devices]
    support = {
        action: sum(1 for item in public if item["capabilities"].get(action))
        for action in ("brightness", "temperature", "rgb")
    }
    total = len(public)
    available_presets = []
    for preset_name, preset in presets.items():
        count = sum(1 for item in public if _preset_supported(item["capabilities"], preset))
        if count:
            available_presets.append({
                "key": preset_name,
                **preset,
                "supported_devices": count,
                "total_devices": total,
                "partial": count < total,
            })
    return {
        "zone": name,
        "configured_members": configured,
        "missing_members": missing,
        "devices": public,
        "support": support,
        "partial_support": any(0 < count < total for count in support.values()) if total else False,
        "presets": available_presets,
    }


def _supports(device: Dict[str, Any], action: str, preset: Dict[str, Any] | None = None) -> bool:
    caps = _capabilities(device)
    if action in caps:
        return caps[action]
    return action == "preset" and preset is not None and _preset_supported(caps, preset)


def _apply_to_device(device: Dict[str, Any], body: ZoneCommand, preset: Dict[str, Any] | None) -> None:
    target = app_module.device_target(device)
    action = body.action.strip().lower()
    if action == "brightness":
        command = app_module.LightCommand(target=target, action="brightness", value=max(10, min(1000, int(body.value))))
        app_module.apply_light(device, command)
        return
    if action in ("temperature", "temp", "cct"):
        command = app_module.LightCommand(target=target, action="temperature", value=max(0, min(1000, int(body.value))))
        app_module.apply_light(device, command)
        return
    if action == "rgb":
        command = app_module.LightCommand(
            target=target,
            action="rgb",
            h=max(0, min(360, int(body.h))),
            s=max(0, min(1000, int(body.s))),
            v=max(0, min(1000, int(body.v))),
        )
        app_module.apply_light(device, command)
        return
    if action == "preset" and preset:
        mode = preset["mode"]
        if mode == "white":
            app_module.apply_light(device, app_module.LightCommand(target=target, action="brightness", value=preset["brightness"]))
            app_module.apply_light(device, app_module.LightCommand(target=target, action="temperature", value=preset["temperature"]))
        elif mode == "brightness":
            app_module.apply_light(device, app_module.LightCommand(target=target, action="brightness", value=preset["value"]))
        elif mode == "temperature":
            app_module.apply_light(device, app_module.LightCommand(target=target, action="temperature", value=preset["value"]))
        elif mode == "colour":
            app_module.apply_light(device, app_module.LightCommand(target=target, action="rgb", h=preset["h"], s=preset["s"], v=preset["v"]))
        return
    raise HTTPException(status_code=400, detail="unsupported zone action")


@app.get("/api/lighting/zones")
def lighting_zones():
    zones = _zones()
    presets, preset_source = _presets()
    return {
        "ok": True,
        "configured": bool(zones),
        "zones": [_zone_payload(name, presets) for name in zones],
        "preset_source": preset_source,
    }


@app.post("/api/lighting/zone")
def lighting_zone_command(body: ZoneCommand):
    zone = body.zone.strip()
    if zone not in _zones():
        raise HTTPException(status_code=404, detail="zone not configured")
    requested_action = body.action.strip().lower()
    normalized_action = "temperature" if requested_action in ("temperature", "temp", "cct") else requested_action
    preset = None
    presets, _ = _presets()
    if normalized_action == "preset":
        preset = presets.get(str(body.preset or ""))
        if not preset:
            raise HTTPException(status_code=404, detail="preset not configured")
    if normalized_action not in ("brightness", "temperature", "rgb", "preset"):
        raise HTTPException(status_code=400, detail="unsupported zone action")
    devices, missing = _zone_devices(zone)
    results = []
    for device in devices:
        device_id = _device_key(device)
        if not _supports(device, normalized_action, preset):
            results.append({"deviceid": device_id, "ok": False, "unsupported": True, "error": "capability not supported"})
            continue
        try:
            _apply_to_device(device, body, preset)
            results.append({"deviceid": device_id, "ok": True})
        except Exception as exc:
            results.append({"deviceid": device_id, "ok": False, "error": _safe_error(exc)})
    success = sum(1 for item in results if item.get("ok"))
    return {
        "ok": success > 0,
        "zone": zone,
        "action": normalized_action,
        "preset": body.preset,
        "partial": success != len(results),
        "missing_members": missing,
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
    return json.loads(raw.decode("utf-8")) if raw else {}


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
        _automation_cache["last_poll_ts"] = now
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


def _automation_poll_loop() -> None:
    while True:
        _fetch_automations(force=True)
        time.sleep(HA_AUTOMATION_CACHE_SEC)


@app.get("/api/ha/automations")
def ha_automations():
    items = _fetch_automations()
    with _automation_lock:
        error = _automation_cache["last_error"]
        fetched_ts = _automation_cache["fetched_ts"]
        last_poll_ts = _automation_cache["last_poll_ts"]
    return {
        "ok": error is None or bool(items),
        "configured": bool(HA_BASE_URL and HA_TOKEN),
        "automations": items,
        "last_success_ts": fetched_ts or None,
        "last_poll_ts": last_poll_ts or None,
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
    services = {"enable": "turn_on", "disable": "turn_off", "run": "trigger", "trigger": "trigger"}
    service = services.get(action)
    if not service:
        raise HTTPException(status_code=400, detail="unsupported automation action")
    try:
        _ha_request(f"/api/services/automation/{service}", method="POST", payload={"entity_id": entity_id})
        _fetch_automations(force=True)
        return {"ok": True, "entity_id": entity_id, "action": action}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_safe_error(exc))


if HA_BASE_URL and HA_TOKEN:
    _automation_thread = threading.Thread(target=_automation_poll_loop, name="ha-automation-poller", daemon=True)
    _automation_thread.start()
