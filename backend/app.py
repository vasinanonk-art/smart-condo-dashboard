import json
import os
import threading
import time
from typing import Any, Dict, List

import paho.mqtt.client as mqtt
import tinytuya
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CMD_TOPIC = os.getenv("MQTT_CMD_TOPIC", "home/lgtv/cmd")
MQTT_STATE_TOPIC = os.getenv("MQTT_STATE_TOPIC", "home/lgtv/state")
TUYA_DEVICES_FILE = os.getenv("TUYA_DEVICES_FILE", "/root/tuya/devices.json")
TUYA_SNAPSHOT_FILE = os.getenv("TUYA_SNAPSHOT_FILE", "/root/tuya/snapshot.json")
LAMPTAN_PRODUCT = "LAMPTAN Jarton Bulb CCT+RGB 11w"
LAST_SEEN_TTL_SEC = 180
POLL_INTERVAL_SEC = 4
HISTORY_MAX_POINTS = 2000
HISTORY_TTL_SEC = 86400
APPLY_ALL_DEVICE_DELAY_SEC = 0.8
APPLY_ALL_STATUS_RETRY_DELAY_SEC = 1.0
APPLY_ALL_RETRIES = 4

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
FRONTEND_DIR = os.path.join(APP_DIR, "frontend")
SCENES_FILE = os.path.join(APP_DIR, "config", "scenes.json")
FAVORITES_FILE = os.path.join(APP_DIR, "config", "favorites.json")

app = FastAPI(title="Smart Condo Dashboard", version="2.0.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

state: Dict[str, Any] = {
    "mqtt_connected": False,
    "last_state": {},
    "last_cmd": None,
    "last_cmd_ts": None,
    "last_light_cmd": None,
    "last_light_cmd_ts": None,
    "light_status_cache": {},
    "poller_started": False,
    "poller_running": False,
    "condo_sensor": {},
    "condo_presence": {},
    "condo_history": [],
    "available_commands": ["power_on", "power_off", "youtube", "netflix", "disney", "prime", "appletv", "browser", "livetv", "home", "viu", "hbo", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "volume_up", "volume_down", "mute", "unmute", "up", "down", "left", "right", "ok", "back", "home_key"],
}

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


class Command(BaseModel):
    cmd: str


class LightCommand(BaseModel):
    target: str = "living_1"
    action: str
    value: int | None = None
    h: int | None = None
    s: int | None = None
    v: int | None = None
    scene: str | None = None


class SceneCommand(BaseModel):
    target: str = "living_1"
    scene: str


class FavoriteRunCommand(BaseModel):
    favorite: str


def on_connect(client, userdata, flags, reason_code, properties=None):
    state["mqtt_connected"] = True
    client.subscribe(MQTT_STATE_TOPIC)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    state["mqtt_connected"] = False


def to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def first_value(data: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def update_condo_state(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    now = int(time.time())
    sensor_src = payload.get("sensor") if isinstance(payload.get("sensor"), dict) else payload
    temp = first_value(sensor_src, ["temperature", "temp", "t"])
    hum = first_value(sensor_src, ["humidity", "hum", "h"])
    sensor: Dict[str, Any] = {}
    if temp is not None:
        sensor["temperature"] = to_float(temp)
    if hum is not None:
        sensor["humidity"] = to_float(hum)
    for key in ("ip", "source", "name"):
        if sensor_src.get(key) is not None:
            sensor[key] = sensor_src.get(key)
    if sensor:
        sensor["ts"] = now
        state["condo_sensor"] = sensor
        if sensor.get("temperature") is not None or sensor.get("humidity") is not None:
            history = state.setdefault("condo_history", [])
            history.append({"ts": now, "temperature": sensor.get("temperature"), "humidity": sensor.get("humidity")})
            cutoff = now - HISTORY_TTL_SEC
            state["condo_history"] = [x for x in history if int(x.get("ts", 0)) >= cutoff][-HISTORY_MAX_POINTS:]
    presence = payload.get("presence")
    if isinstance(presence, dict):
        state["condo_presence"] = {**presence, "ts": now}
    else:
        presence_keys = ["occupancy", "motion", "present", "home", "living", "bedroom", "door", "person", "persons"]
        extracted = {k: payload[k] for k in presence_keys if k in payload}
        if extracted:
            state["condo_presence"] = {**extracted, "ts": now}


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    try:
        parsed = json.loads(payload)
        state["last_state"] = parsed
        update_condo_state(parsed)
    except Exception:
        state["last_state"] = {"raw": payload}
    state["last_state_topic"] = msg.topic
    state["last_state_ts"] = int(time.time())


mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message


@app.on_event("startup")
def startup():
    preload_snapshot_cache()
    start_light_poller()
    try:
        mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
        mqttc.loop_start()
    except Exception as e:
        state["mqtt_connected"] = False
        state["mqtt_error"] = repr(e)


@app.on_event("shutdown")
def shutdown():
    state["poller_running"] = False
    try:
        mqttc.loop_stop()
        mqttc.disconnect()
    except Exception:
        pass


def clamp(n: int, low: int, high: int) -> int:
    return max(low, min(high, n))


def slug(name: str) -> str:
    return name.lower().replace("light ", "").replace(" room ", " ").replace(" ", "_").replace("-", "_")


def is_all_target(target: str) -> bool:
    return target.strip().lower().replace(" ", "_") in ("all", "lamptan")


def load_json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"cannot load {path}: {exc}")


def load_json_optional(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_scenes() -> Dict[str, Dict[str, Any]]:
    return load_json(SCENES_FILE)


def load_favorites() -> Dict[str, Dict[str, Any]]:
    return load_json(FAVORITES_FILE)


def snapshot_items() -> List[Dict[str, Any]]:
    data = load_json_optional(TUYA_SNAPSHOT_FILE)
    if not isinstance(data, dict):
        return []
    items = data.get("devices") if isinstance(data.get("devices"), list) else list(data.values())
    return [x for x in items if isinstance(x, dict)]


def snapshot_meta_by_id() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for item in snapshot_items():
        dev_id = item.get("gwId") or item.get("id") or item.get("devId")
        if dev_id:
            found[str(dev_id)] = item
    return found


def snapshot_dps_by_id() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for item in snapshot_items():
        dev_id = item.get("gwId") or item.get("id") or item.get("devId")
        raw_dps = item.get("dps") or item.get("data", {}).get("dps") or {}
        dps = raw_dps.get("dps") if isinstance(raw_dps, dict) and isinstance(raw_dps.get("dps"), dict) else raw_dps
        if dev_id and isinstance(dps, dict):
            found[str(dev_id)] = dps
    return found


def sync_devices_from_snapshot(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snap = snapshot_meta_by_id()
    changed = False
    for dev in devices:
        item = snap.get(str(dev.get("id")))
        if not item:
            continue
        ip = item.get("ip")
        ver = item.get("ver") or item.get("version")
        if ip and dev.get("ip") != ip:
            dev["ip"] = ip
            changed = True
        if ver and dev.get("version") != ver:
            dev["version"] = ver
            changed = True
    if changed:
        try:
            with open(TUYA_DEVICES_FILE, "w", encoding="utf-8") as f:
                json.dump(devices, f, indent=4)
            state["tuya_devices_last_sync_ts"] = int(time.time())
        except Exception as exc:
            state["tuya_devices_save_error"] = repr(exc)
    return devices


def load_all_devices() -> List[Dict[str, Any]]:
    return sync_devices_from_snapshot(load_json(TUYA_DEVICES_FILE))


def load_lights() -> List[Dict[str, Any]]:
    devices = load_all_devices()
    return [d for d in devices if d.get("product_name") == LAMPTAN_PRODUCT and d.get("ip") and d.get("id") and d.get("key")]


def select_lights(target: str) -> List[Dict[str, Any]]:
    target = target.strip().lower().replace(" ", "_")
    lights = load_lights()
    if target in ("all", "lamptan"):
        return lights
    selected = [d for d in lights if slug(d.get("name", "")) == target]
    if not selected:
        raise HTTPException(status_code=404, detail=f"light target not found: {target}")
    return selected


def select_single_light(target: str) -> Dict[str, Any]:
    if is_all_target(target):
        raise HTTPException(status_code=400, detail="single light status does not support all")
    return select_lights(target)[0]


def tuya_device(dev: Dict[str, Any]) -> tinytuya.Device:
    d = tinytuya.Device(dev["id"], dev["ip"], dev["key"])
    d.set_version(float(dev.get("version") or dev.get("ver") or 3.3))
    try:
        d.set_socketTimeout(1.5)
    except Exception:
        pass
    return d


def tuya_ok(result: Any) -> bool:
    return isinstance(result, dict) and not result.get("Error") and not result.get("Err") and ("dps" in result or "data" in result)


def is_retryable_tuya_error(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    return str(result.get("Err") or "") in ("901", "904", "905", "914") or bool(result.get("Error"))


def status_base(dev: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": dev.get("name"), "target": slug(dev.get("name", "")), "ip": dev.get("ip")}


def cache_online(dev: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    item["last_seen_ts"] = int(time.time())
    item["status"] = "online"
    item["online"] = True
    state["light_status_cache"][dev["id"]] = item
    return item


def cache_dps(dev: Dict[str, Any], dps: Dict[str, Any], source: str = "command") -> Dict[str, Any]:
    cached = state["light_status_cache"].get(dev["id"], {})
    old_dps = ((cached.get("result") or {}).get("dps") or {}).copy()
    old_dps.update({str(k): v for k, v in dps.items()})
    return cache_online(dev, {**status_base(dev), "source": source, "result": {"dps": old_dps}})


def fresh_cached_status(dev: Dict[str, Any]) -> Dict[str, Any] | None:
    cached = state["light_status_cache"].get(dev["id"])
    if cached and int(time.time()) - int(cached.get("last_seen_ts", 0)) <= LAST_SEEN_TTL_SEC:
        return cached
    return None


def cached_or_offline(dev: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    cached = fresh_cached_status(dev)
    if cached:
        return {**cached, "online": True, "status": "unstable", "source": "last_seen", "last_error": item}
    return {**item, "online": False, "status": "offline"}


def fast_light_status(dev: Dict[str, Any], snapshot: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    cached = state["light_status_cache"].get(dev["id"])
    if cached:
        return cached
    snap_dps = snapshot.get(dev.get("id"))
    if snap_dps:
        return cache_dps(dev, snap_dps, "snapshot")
    return {**status_base(dev), "online": False, "status": "unknown", "source": "cache", "result": {"dps": {}}}


def read_dps(dev: Dict[str, Any]) -> Dict[str, Any] | None:
    result = tuya_device(dev).status()
    if tuya_ok(result):
        return result.get("dps") or result.get("data", {}).get("dps")
    return None


def read_dps_after_command(dev: Dict[str, Any]) -> Dict[str, Any]:
    errors = []
    for attempt in (1, 2):
        try:
            dps = read_dps(dev)
            if dps:
                cache_dps(dev, dps, "verify")
                return dps
            errors.append({"attempt": attempt, "status": "empty_or_error"})
        except Exception as exc:
            errors.append({"attempt": attempt, "error": repr(exc)})
        if attempt == 1:
            time.sleep(APPLY_ALL_STATUS_RETRY_DELAY_SEC)
    raise RuntimeError({"verify_status_errors": errors})


def dps_matches(dps: Dict[str, Any], dp: int, value: Any) -> bool:
    actual = dps.get(str(dp)) if str(dp) in dps else dps.get(dp)
    return str(actual) == str(value)


def verify_dp_after_command(dev: Dict[str, Any], dp: int, value: Any, command_result: Dict[str, Any]) -> Dict[str, Any]:
    dps = read_dps_after_command(dev)
    if not dps_matches(dps, dp, value):
        actual = dps.get(str(dp)) if str(dp) in dps else dps.get(dp)
        raise RuntimeError({"verify_failed": True, "dp": dp, "expected": value, "actual": actual, "dps": dps, "command_result": command_result})
    return {"ok": True, "verified": True, "dp": dp, "value": value, "dps": dps, "command_result": command_result}


def get_light_status(dev: Dict[str, Any], snapshot: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    snapshot = snapshot or snapshot_dps_by_id()
    base = status_base(dev)
    try:
        result = tuya_device(dev).status()
        if tuya_ok(result):
            return cache_online(dev, {**base, "source": "direct", "result": result})
        cached = fresh_cached_status(dev)
        if cached:
            return {**cached, "online": True, "status": "unstable", "source": "last_seen", "last_error": {**base, "source": "direct", "result": result}}
        snap_dps = snapshot.get(dev.get("id"))
        if snap_dps:
            return cache_dps(dev, snap_dps, "snapshot")
        return cached_or_offline(dev, {**base, "source": "direct", "result": result})
    except Exception as exc:
        cached = fresh_cached_status(dev)
        if cached:
            return {**cached, "online": True, "status": "unstable", "source": "last_seen", "last_error": {**base, "source": "direct", "error": repr(exc)}}
        snap_dps = snapshot.get(dev.get("id"))
        if snap_dps:
            return cache_dps(dev, snap_dps, "snapshot")
        return cached_or_offline(dev, {**base, "source": "direct", "error": repr(exc)})


def preload_snapshot_cache():
    snap = snapshot_dps_by_id()
    for dev in load_lights():
        dps = snap.get(dev.get("id"))
        if dps:
            cache_dps(dev, dps, "snapshot")
    state["snapshot_preload_ts"] = int(time.time())


def light_poller_loop():
    state["poller_running"] = True
    idx = 0
    while state.get("poller_running"):
        try:
            lights = load_lights()
            if lights:
                dev = lights[idx % len(lights)]
                get_light_status(dev)
                idx += 1
                state["poller_last_device"] = dev.get("name")
                state["poller_last_ts"] = int(time.time())
        except Exception as exc:
            state["poller_error"] = repr(exc)
        time.sleep(POLL_INTERVAL_SEC)


def start_light_poller():
    if state.get("poller_started"):
        return
    state["poller_started"] = True
    t = threading.Thread(target=light_poller_loop, daemon=True)
    t.start()


def set_dp_once(dev: Dict[str, Any], dp: int, value: Any) -> Dict[str, Any]:
    return tuya_device(dev).set_status(value, dp)


def extract_dps(result: Dict[str, Any]) -> Dict[str, Any]:
    return result.get("dps") or result.get("data", {}).get("dps") or {}


def set_dp(dev: Dict[str, Any], dp: int, value: Any, attempts: int = 2, retry_delay: float = 0.25) -> Dict[str, Any]:
    cache_dps(dev, {str(dp): value}, "pending")
    errors = []
    for attempt in range(1, attempts + 1):
        result = set_dp_once(dev, dp, value)
        if tuya_ok(result):
            dps = extract_dps(result) or {str(dp): value}
            cache_dps(dev, dps, "command")
            return {**result, "attempt": attempt, "previous_errors": errors}
        errors.append(result)
        if not is_retryable_tuya_error(result):
            break
        if attempt < attempts:
            time.sleep(retry_delay)
    time.sleep(retry_delay)
    try:
        dps = read_dps(dev)
        if dps and str(dps.get(str(dp)) if str(dp) in dps else dps.get(dp)) == str(value):
            cache_dps(dev, dps, "verify")
            return {"ok": True, "verified_after_error": True, "dp": dp, "value": value, "dps": dps, "errors": errors}
    except Exception as exc:
        errors.append({"verify_error": repr(exc)})
    raise RuntimeError({"errors": errors})


def set_dp_for_apply_all(dev: Dict[str, Any], dp: int, value: Any) -> Dict[str, Any]:
    cache_dps(dev, {str(dp): value}, "pending")
    result = set_dp_once(dev, dp, value)
    if not tuya_ok(result):
        raise RuntimeError({"command_failed": True, "dp": dp, "value": value, "result": result})
    dps = extract_dps(result) or {str(dp): value}
    cache_dps(dev, dps, "command")
    return result


def hsv_hex(h: int, s: int, v: int) -> str:
    return f"{clamp(h, 0, 360):04x}{clamp(s, 0, 1000):04x}{clamp(v, 0, 1000):04x}"


def body_value(body: LightCommand, fallback: int) -> int:
    return int(body.value if body.value is not None else fallback)


def apply_scene_config(dev: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    mode = cfg.get("mode")
    if mode == "white":
        set_dp(dev, 21, "white")
        set_dp(dev, 22, clamp(int(cfg.get("brightness", 500)), 10, 1000))
        return set_dp(dev, 23, clamp(int(cfg.get("temperature", 500)), 0, 1000))
    if mode == "colour":
        set_dp(dev, 21, "colour")
        return set_dp(dev, 24, hsv_hex(int(cfg.get("h", 0)), int(cfg.get("s", 1000)), int(cfg.get("v", 1000))))
    raise HTTPException(status_code=400, detail=f"unsupported scene mode: {mode}")


def apply_light(dev: Dict[str, Any], body: LightCommand) -> Dict[str, Any]:
    action = body.action.strip().lower()
    if action == "brightness":
        return set_dp(dev, 22, clamp(body_value(body, 500), 10, 1000))
    if action in ("temperature", "temp", "cct"):
        set_dp(dev, 21, "white")
        return set_dp(dev, 23, clamp(body_value(body, 500), 0, 1000))
    if action == "rgb":
        color = hsv_hex(int(body.h or 0), int(body.s if body.s is not None else 1000), int(body.v if body.v is not None else 1000))
        set_dp(dev, 21, "colour")
        return set_dp(dev, 24, color)
    if action == "scene":
        return apply_scene(dev, body.scene or "relax")
    raise HTTPException(status_code=400, detail=f"unsupported light action: {action}")


def apply_scene_all_once(dev: Dict[str, Any], scene: str) -> Dict[str, Any]:
    scenes = load_scenes()
    key = scene.strip().lower()
    if key not in scenes:
        raise HTTPException(status_code=400, detail=f"unsupported scene: {key}")
    cfg = scenes[key]
    mode = cfg.get("mode")
    if mode == "white":
        set_dp_for_apply_all(dev, 21, "white")
        time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
        brightness = clamp(int(cfg.get("brightness", 500)), 10, 1000)
        set_dp_for_apply_all(dev, 22, brightness)
        time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
        temperature = clamp(int(cfg.get("temperature", 500)), 0, 1000)
        result = set_dp_for_apply_all(dev, 23, temperature)
        return verify_dp_after_command(dev, 23, temperature, result)
    if mode == "colour":
        set_dp_for_apply_all(dev, 21, "colour")
        time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
        color = hsv_hex(int(cfg.get("h", 0)), int(cfg.get("s", 1000)), int(cfg.get("v", 1000)))
        result = set_dp_for_apply_all(dev, 24, color)
        return verify_dp_after_command(dev, 24, color, result)
    raise HTTPException(status_code=400, detail=f"unsupported scene mode: {mode}")


def apply_light_all_once(dev: Dict[str, Any], body: LightCommand) -> Dict[str, Any]:
    action = body.action.strip().lower()
    if action == "brightness":
        value = clamp(body_value(body, 500), 10, 1000)
        result = set_dp_for_apply_all(dev, 22, value)
        return verify_dp_after_command(dev, 22, value, result)
    if action in ("temperature", "temp", "cct"):
        set_dp_for_apply_all(dev, 21, "white")
        time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
        value = clamp(body_value(body, 500), 0, 1000)
        result = set_dp_for_apply_all(dev, 23, value)
        return verify_dp_after_command(dev, 23, value, result)
    if action == "rgb":
        set_dp_for_apply_all(dev, 21, "colour")
        time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
        color = hsv_hex(int(body.h or 0), int(body.s if body.s is not None else 1000), int(body.v if body.v is not None else 1000))
        result = set_dp_for_apply_all(dev, 24, color)
        return verify_dp_after_command(dev, 24, color, result)
    if action == "scene":
        return apply_scene_all_once(dev, body.scene or "relax")
    raise HTTPException(status_code=400, detail=f"unsupported light action: {action}")


def apply_light_all_reliable(dev: Dict[str, Any], body: LightCommand) -> Dict[str, Any]:
    errors = []
    for attempt in range(1, APPLY_ALL_RETRIES + 1):
        try:
            result = apply_light_all_once(dev, body)
            return {**result, "all_attempt": attempt, "all_previous_errors": errors}
        except Exception as exc:
            errors.append(repr(exc))
            if attempt < APPLY_ALL_RETRIES:
                time.sleep(APPLY_ALL_DEVICE_DELAY_SEC)
    raise RuntimeError({"errors": errors})


def apply_scene(dev: Dict[str, Any], scene: str) -> Dict[str, Any]:
    scenes = load_scenes()
    key = scene.strip().lower()
    if key not in scenes:
        raise HTTPException(status_code=400, detail=f"unsupported scene: {key}")
    return apply_scene_config(dev, scenes[key])


def execute_for_target(target: str, fn, delay_between_devices: float = 0.0):
    results = []
    devices = select_lights(target)
    for idx, dev in enumerate(devices):
        try:
            results.append({"name": dev.get("name"), "ok": True, "result": fn(dev)})
        except Exception as exc:
            results.append({"name": dev.get("name"), "ok": False, "error": repr(exc)})
        if delay_between_devices and idx < len(devices) - 1:
            time.sleep(delay_between_devices)
    return results


@app.get("/api/health")
def health():
    return {"ok": True, "mqtt_connected": state["mqtt_connected"], "mqtt_host": MQTT_HOST, "mqtt_port": MQTT_PORT}


@app.get("/api/state")
def get_state():
    return state


@app.get("/api/condo/status")
def condo_status():
    return {"ok": True, "sensor": state.get("condo_sensor", {}), "presence": state.get("condo_presence", {})}


@app.get("/api/condo/history")
def condo_history():
    return {"ok": True, "history": state.get("condo_history", [])}


@app.get("/api/lights")
def lights():
    return {"ok": True, "devices": [{"name": d.get("name"), "target": slug(d.get("name", "")), "ip": d.get("ip")} for d in load_lights()]}


@app.get("/api/light/status/{target}")
def light_status_one(target: str):
    snap = snapshot_dps_by_id()
    dev = select_single_light(target)
    return {"ok": True, "source": "single-fast", "device": fast_light_status(dev, snap)}


@app.get("/api/lights/status")
def lights_status():
    snap = snapshot_dps_by_id()
    return {"ok": True, "source": "fast", "devices": [fast_light_status(dev, snap) for dev in load_lights()]}


@app.get("/api/lights/status-fast")
def lights_status_fast():
    snap = snapshot_dps_by_id()
    return {"ok": True, "source": "fast", "devices": [fast_light_status(dev, snap) for dev in load_lights()]}


@app.get("/api/lights/status-live")
def lights_status_live():
    snap = snapshot_dps_by_id()
    return {"ok": True, "source": "live", "devices": [get_light_status(dev, snap) for dev in load_lights()]}


@app.post("/api/lights/refresh")
def lights_refresh():
    snap = snapshot_dps_by_id()
    return {"ok": True, "source": "manual-live", "devices": [get_light_status(dev, snap) for dev in load_lights()]}


@app.get("/api/scenes")
def scenes():
    return {"ok": True, "scenes": load_scenes()}


@app.get("/api/favorites")
def favorites():
    return {"ok": True, "favorites": load_favorites()}


@app.post("/api/scene")
def scene_control(body: SceneCommand):
    results = execute_for_target(body.target, lambda dev: apply_scene(dev, body.scene))
    state["last_light_cmd"] = body.model_dump()
    state["last_light_cmd_ts"] = int(time.time())
    return {"ok": all(r["ok"] for r in results), "target": body.target, "scene": body.scene, "results": results}


@app.post("/api/favorite/run")
def favorite_run(body: FavoriteRunCommand):
    favorites = load_favorites()
    key = body.favorite.strip().lower()
    if key not in favorites:
        raise HTTPException(status_code=404, detail=f"favorite not found: {key}")
    fav = favorites[key]
    results = execute_for_target(fav.get("target", "living_1"), lambda dev: apply_scene(dev, fav["scene"]))
    state["last_light_cmd"] = {"favorite": key, **fav}
    state["last_light_cmd_ts"] = int(time.time())
    return {"ok": all(r["ok"] for r in results), "favorite": key, "config": fav, "results": results}


@app.post("/api/light")
def light_control(body: LightCommand):
    if is_all_target(body.target):
        results = execute_for_target(
            body.target,
            lambda dev: apply_light_all_reliable(dev, body),
            delay_between_devices=APPLY_ALL_DEVICE_DELAY_SEC,
        )
    else:
        results = execute_for_target(body.target, lambda dev: apply_light(dev, body))
    state["last_light_cmd"] = body.model_dump()
    state["last_light_cmd_ts"] = int(time.time())
    return {"ok": all(r["ok"] for r in results), "target": body.target, "action": body.action, "results": results}


@app.post("/api/command")
def send_command(body: Command):
    cmd = body.cmd.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty command")
    try:
        mqttc.publish(MQTT_CMD_TOPIC, cmd, qos=0, retain=False)
        state["last_cmd"] = cmd
        state["last_cmd_ts"] = int(time.time())
        return {"ok": True, "cmd": cmd, "topic": MQTT_CMD_TOPIC}
    except Exception as e:
        raise HTTPException(status_code=500, detail=repr(e))


@app.post("/api/ai-command")
def ai_command(body: Command):
    text = body.cmd.strip().lower()
    mapping = {
        "เปิดยูทูป": "youtube", "ยูทูป": "youtube", "youtube": "youtube",
        "เปิด netflix": "netflix", "netflix": "netflix", "เน็ตฟลิกซ์": "netflix",
        "disney": "disney", "ดิสนีย์": "disney",
        "prime": "prime", "amazon": "prime",
        "apple tv": "appletv", "appletv": "appletv",
        "browser": "browser", "เว็บ": "browser",
        "hdmi1": "hdmi1", "hdmi 1": "hdmi1",
        "hdmi2": "hdmi2", "hdmi 2": "hdmi2",
        "hdmi3": "hdmi3", "hdmi 3": "hdmi3",
        "hdmi4": "hdmi4", "hdmi 4": "hdmi4",
        "เปิดทีวี": "power_on", "เปิด tv": "power_on", "power on": "power_on",
        "ปิดทีวี": "power_off", "ปิด tv": "power_off", "power off": "power_off",
        "เพิ่มเสียง": "volume_up", "เสียงดังขึ้น": "volume_up", "vol up": "volume_up",
        "ลดเสียง": "volume_down", "เสียงเบาลง": "volume_down", "vol down": "volume_down",
        "ปิดเสียง": "mute", "mute": "mute", "เปิดเสียง": "unmute", "unmute": "unmute",
        "กลับ": "back", "back": "back", "ตกลง": "ok", "ok": "ok", "home": "home_key", "หน้าหลัก": "home_key",
    }
    selected = None
    for k, v in mapping.items():
        if k in text:
            selected = v
            break
    if not selected:
        raise HTTPException(status_code=400, detail="ยังแปลคำสั่งไม่ได้")
    mqttc.publish(MQTT_CMD_TOPIC, selected, qos=0, retain=False)
    state["last_cmd"] = selected
    state["last_cmd_ts"] = int(time.time())
    return {"ok": True, "input": body.cmd, "cmd": selected}


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
