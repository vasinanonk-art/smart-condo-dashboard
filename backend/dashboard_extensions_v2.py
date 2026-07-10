import threading
import time
from typing import Any, Dict, List

from backend import dashboard_extensions as ext

app = ext.app
app_module = ext.app_module


def _safe_error(exc: Any) -> str:
    return type(exc).__name__ if exc is not None else "operation failed"


def _fallback_zones() -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    try:
        devices = app_module.load_lights()
    except Exception:
        devices = []
    for device in devices:
        target = str(app_module.device_target(device)).lower()
        zone = None
        if target.startswith("living_") or target == "living":
            zone = "living_room"
        elif target.startswith("bedroom_") or target == "bedroom":
            zone = "bedroom"
        elif target.startswith("kitchen_") or target == "kitchen":
            zone = "kitchen"
        if zone:
            result.setdefault(zone, []).append(str(device.get("id") or app_module.device_target(device)))
    return result


def _zones() -> Dict[str, List[str]]:
    configured = {}
    for name, members in ext._load_json_env(ext.ZONE_CONFIG_ENV).items():
        if isinstance(members, list):
            clean = [str(item).strip() for item in members if str(item).strip()]
            if clean:
                configured[str(name).strip()] = clean
    return configured or _fallback_zones()


def _presets() -> Dict[str, Dict[str, Any]]:
    configured = ext._load_json_env(ext.PRESET_CONFIG_ENV)
    if configured:
        result = {}
        for name, cfg in configured.items():
            if not isinstance(cfg, dict):
                continue
            action = str(cfg.get("action") or "").lower()
            if action == "brightness" and cfg.get("value") is not None:
                result[str(name)] = {"action": "brightness", "value": max(10, min(1000, int(cfg["value"])))}
            elif action in ("temperature", "temp", "cct") and cfg.get("value") is not None:
                result[str(name)] = {"action": "temperature", "value": max(0, min(1000, int(cfg["value"])))}
            elif action == "rgb" and all(cfg.get(k) is not None for k in ("h", "s", "v")):
                result[str(name)] = {
                    "action": "rgb",
                    "h": max(0, min(360, int(cfg["h"]))),
                    "s": max(0, min(1000, int(cfg["s"]))),
                    "v": max(0, min(1000, int(cfg["v"]))),
                }
        return result
    try:
        scenes = app_module.load_scenes()
    except Exception:
        scenes = {}
    result = {}
    for name, cfg in scenes.items() if isinstance(scenes, dict) else []:
        if not isinstance(cfg, dict):
            continue
        mode = str(cfg.get("mode") or "").lower()
        if mode == "white" and cfg.get("brightness") is not None and cfg.get("temperature") is not None:
            result[str(name)] = {
                "action": "scene",
                "scene": str(name),
                "mode": "white",
                "brightness": int(cfg["brightness"]),
                "temperature": int(cfg["temperature"]),
                "label": str(cfg.get("label") or name),
            }
        elif mode in ("colour", "color") and all(cfg.get(k) is not None for k in ("h", "s", "v")):
            result[str(name)] = {
                "action": "scene",
                "scene": str(name),
                "mode": "rgb",
                "h": int(cfg["h"]),
                "s": int(cfg["s"]),
                "v": int(cfg["v"]),
                "label": str(cfg.get("label") or name),
            }
    return result


ext._safe_error = _safe_error
ext._zones = _zones
ext._presets = _presets


@app.post("/api/lighting/zones/{zone}/command")
def lighting_zone_command_alias(zone: str, body: ext.ZoneCommand):
    body.zone = zone
    if body.action == "preset":
        preset = _presets().get(str(body.preset or ""))
        if preset and preset.get("action") == "scene":
            results = []
            for device in ext._zone_devices(zone):
                device_id = ext._device_key(device)
                caps = ext._capabilities(device)
                supported = caps.get("rgb") if preset.get("mode") == "rgb" else caps.get("brightness") and caps.get("temperature")
                if not supported:
                    results.append({"deviceid": device_id, "ok": False, "unsupported": True, "error": "capability not supported"})
                    continue
                try:
                    app_module.apply_scene(device, str(preset["scene"]))
                    results.append({"deviceid": device_id, "ok": True})
                except Exception as exc:
                    results.append({"deviceid": device_id, "ok": False, "error": _safe_error(exc)})
            return {
                "ok": any(item.get("ok") for item in results),
                "zone": zone,
                "action": "preset",
                "preset": body.preset,
                "partial": any(not item.get("ok") for item in results) and any(item.get("ok") for item in results),
                "results": results,
            }
    return ext.lighting_zone_command(body)


@app.post("/api/ha/automations/action")
def ha_automation_action_alias(body: ext.AutomationCommand):
    return ext.ha_automation_action(body)


def _automation_poll_loop() -> None:
    while True:
        if ext.HA_BASE_URL and ext.HA_TOKEN:
            ext._fetch_automations(force=True)
        time.sleep(30)


if ext.HA_BASE_URL and ext.HA_TOKEN:
    thread = threading.Thread(target=_automation_poll_loop, name="ha-automation-poller", daemon=True)
    thread.start()
