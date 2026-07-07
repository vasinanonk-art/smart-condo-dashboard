import json
import multiprocessing
import os
import socket
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
CONDO_SENSOR_TOPIC = "condo/t3/state"
CONDO_PRESENCE_BEER_TOPIC = "condo/presence/beer"
CONDO_PRESENCE_SEEM_TOPIC = "condo/presence/seem"
CONDO_MQTT_TOPICS = [CONDO_SENSOR_TOPIC, CONDO_PRESENCE_BEER_TOPIC, CONDO_PRESENCE_SEEM_TOPIC]
TUYA_DEVICES_FILE = os.getenv("TUYA_DEVICES_FILE", "/root/tuya/devices.json")
TUYA_SNAPSHOT_FILE = os.getenv("TUYA_SNAPSHOT_FILE", "/root/tuya/snapshot.json")
CAMERA_CONFIG_PATHS = [
    os.getenv("CAMERA_CONFIG_FILE", "/opt/smart-condo-dashboard-run/config/cameras.local.json"),
    "/root/.smart-condo-dashboard/cameras.local.json",
    os.path.abspath(os.path.join(os.getcwd(), "config", "cameras.local.json")),
]
LAMPTAN_PRODUCT = "LAMPTAN Jarton Bulb CCT+RGB 11w"
LAST_SEEN_TTL_SEC = 300
CACHE_ONLINE_TTL_SEC = 300
ERR_905_LAST_SEEN_TTL_SEC = 60
POLL_INTERVAL_SEC = 4
HISTORY_MAX_POINTS = 2000
HISTORY_TTL_SEC = 86400
APPLY_ALL_DEVICE_DELAY_SEC = 0.8
APPLY_ALL_STATUS_RETRY_DELAY_SEC = 1.0
APPLY_VERIFY_FIRST_DELAY_SEC = 2.0
LIVE_STATUS_DEVICE_DELAY_SEC = 0.3
STATUS_READ_TIMEOUT_SEC = 5.0
APPLY_ALL_RETRIES = 4
CAMERA_TCP_TIMEOUT_SEC = 1.0
CAMERA_FALLBACK_PORTS = [80, 443, 554, 8554, 8080]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
FRONTEND_DIR = os.path.join(APP_DIR, "frontend")
SCENES_FILE = os.path.join(APP_DIR, "config", "scenes.json")
FAVORITES_FILE = os.path.join(APP_DIR, "config", "favorites.json")

app = FastAPI(title="Smart Condo Dashboard", version="2.1.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

state: Dict[str, Any] = {
    "mqtt_connected": False,
    "mqtt_subscribed_topics": [],
    "last_state": {},
    "last_cmd": None,
    "last_cmd_ts": None,
    "last_light_cmd": None,
    "last_light_cmd_ts": None,
    "light_status_cache": {},
    "tuya_log_tail": [],
    "tuya_snapshot_sync_log": [],
    "camera_config_loaded": False,
    "camera_config_path": None,
    "camera_count": 0,
    "poller_started": False,
    "poller_running": False,
    "condo_sensor": {},
    "condo_presence": {},
    "condo_sensor_history": [],
    "condo_history": [],
    "sensor": {},
    "presence": {},
    "sensor_history": [],
    "available_commands": ["power_on", "power_off", "youtube", "netflix", "disney", "prime", "appletv", "browser", "livetv", "home", "viu", "hbo", "hdmi1", "hdmi2", "hdmi3", "hdmi4", "volume_up", "volume_down", "mute", "unmute", "up", "down", "left", "right", "ok", "back", "home_key"],
}

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
tuya_semaphore = threading.Semaphore(1)


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


def parse_json_payload(payload: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": payload}


def prune_history(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = int(time.time()) - HISTORY_TTL_SEC
    return [x for x in items if int(x.get("ts", 0)) >= cutoff][-HISTORY_MAX_POINTS:]


def update_condo_sensor_from_topic(data: Dict[str, Any]) -> None:
    now = int(time.time())
    sensor = {**data, "ts": now, "topic": CONDO_SENSOR_TOPIC}
    state["condo_sensor"] = sensor
    state["sensor"] = sensor
    history = state.setdefault("condo_sensor_history", [])
    history.append({**sensor})
    history = prune_history(history)
    state["condo_sensor_history"] = history
    state["sensor_history"] = history
    state["condo_history"] = history


def update_condo_presence_from_topic(person: str, data: Dict[str, Any], topic: str) -> None:
    now = int(time.time())
    presence = {**data, "ts": now, "topic": topic}
    state.setdefault("condo_presence", {})[person] = presence
    state.setdefault("presence", {})[person] = presence


def on_connect(client, userdata, flags, reason_code, properties=None):
    state["mqtt_connected"] = True
    client.subscribe(MQTT_STATE_TOPIC)
    for topic in CONDO_MQTT_TOPICS:
        client.subscribe(topic)
    state["mqtt_subscribed_topics"] = [MQTT_STATE_TOPIC, *CONDO_MQTT_TOPICS]
    log = "subscribed MQTT topics: " + ", ".join(CONDO_MQTT_TOPICS)
    state["mqtt_subscription_log"] = log
    print(log, flush=True)


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
        state["sensor"] = sensor
        history = state.setdefault("condo_sensor_history", [])
        history.append({"ts": now, "temperature": sensor.get("temperature"), "humidity": sensor.get("humidity")})
        history = prune_history(history)
        state["condo_sensor_history"] = history
        state["sensor_history"] = history
        state["condo_history"] = history
    presence = payload.get("presence")
    if isinstance(presence, dict):
        state["condo_presence"] = {**presence, "ts": now}
        state["presence"] = state["condo_presence"]
    else:
        presence_keys = ["occupancy", "motion", "present", "home", "living", "bedroom", "door", "person", "persons"]
        extracted = {k: payload[k] for k in presence_keys if k in payload}
        if extracted:
            state["condo_presence"] = {**extracted, "ts": now}
            state["presence"] = state["condo_presence"]


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    topic = msg.topic
    parsed = parse_json_payload(payload)
    state["last_state_topic"] = topic
    state["last_state_ts"] = int(time.time())
    if topic == CONDO_SENSOR_TOPIC:
        state["last_state"] = parsed
        update_condo_sensor_from_topic(parsed)
        return
    if topic == CONDO_PRESENCE_BEER_TOPIC:
        update_condo_presence_from_topic("beer", parsed, topic)
        return
    if topic == CONDO_PRESENCE_SEEM_TOPIC:
        update_condo_presence_from_topic("seem", parsed, topic)
        return
    if topic == MQTT_STATE_TOPIC:
        state["last_state"] = parsed
        update_condo_state(parsed)
        return
    state["last_state"] = parsed


mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message


@app.on_event("startup")
def startup():
    preload_snapshot_cache()
    log_camera_config_startup()
    start_light_poller()
    state["mqtt_subscription_log"] = "subscribed MQTT topics: " + ", ".join(CONDO_MQTT_TOPICS)
    print(state["mqtt_subscription_log"], flush=True)
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


def device_target(dev: Dict[str, Any]) -> str:
    return slug(dev.get("name", "")) or str(dev.get("id") or dev.get("ip") or "unknown")


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


def item_device_id(item: Dict[str, Any]) -> str | None:
    for key in ("id", "gwId", "devId", "device_id", "deviceId"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def item_mac(item: Dict[str, Any]) -> str | None:
    for key in ("mac", "macAddress", "mac_address", "node_id"):
        value = item.get(key)
        if value:
            return str(value).lower().replace(":", "").replace("-", "")
    return None


def item_dps(item: Dict[str, Any]) -> Dict[str, Any]:
    raw_dps = item.get("dps") or item.get("data", {}).get("dps") or {}
    dps = raw_dps.get("dps") if isinstance(raw_dps, dict) and isinstance(raw_dps.get("dps"), dict) else raw_dps
    return dps if isinstance(dps, dict) else {}


def item_ip(item: Dict[str, Any]) -> str | None:
    ip = item.get("ip") or item.get("ip_address") or item.get("address")
    return str(ip) if ip else None


def item_version(item: Dict[str, Any]) -> Any:
    return item.get("ver") or item.get("version")


def snapshot_dps_by_id() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for item in snapshot_items():
        dev_id = item_device_id(item)
        dps = item_dps(item)
        if dev_id and dps:
            found[dev_id] = dps
    return found


def build_snapshot_indexes() -> Dict[str, Dict[str, Dict[str, Any]]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    by_mac: Dict[str, Dict[str, Any]] = {}
    for item in snapshot_items():
        dev_id = item_device_id(item)
        mac = item_mac(item)
        if dev_id:
            by_id[dev_id] = item
        if mac:
            by_mac[mac] = item
    return {"id": by_id, "mac": by_mac}


def matching_snapshot_item(dev: Dict[str, Any], indexes: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any] | None:
    dev_id = item_device_id(dev)
    if dev_id and dev_id in indexes["id"]:
        return indexes["id"][dev_id]
    mac = item_mac(dev)
    if mac and mac in indexes["mac"]:
        return indexes["mac"][mac]
    return None


def sync_devices_from_snapshot(devices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    indexes = build_snapshot_indexes()
    changed = False
    for dev in devices:
        item = matching_snapshot_item(dev, indexes)
        if not item:
            continue
        old_ip = dev.get("ip")
        new_ip = item_ip(item)
        target = device_target(dev)
        if new_ip and old_ip != new_ip:
            dev["ip"] = new_ip
            changed = True
            log = f"light ip updated: {target} {old_ip or '-'} -> {new_ip}"
            state.setdefault("tuya_snapshot_sync_log", []).append({"ts": int(time.time()), "message": log})
            state["tuya_snapshot_sync_log"] = state["tuya_snapshot_sync_log"][-50:]
            print(log, flush=True)
        ver = item_version(item)
        if ver and dev.get("version") != ver:
            dev["version"] = ver
            changed = True
        dps = item_dps(item)
        if dps:
            cache_dps(dev, dps, "snapshot")
    if changed:
        try:
            with open(TUYA_DEVICES_FILE, "w", encoding="utf-8") as f:
                json.dump(devices, f, indent=4)
            state["tuya_devices_last_sync_ts"] = int(time.time())
        except Exception as exc:
            state["tuya_devices_save_error"] = repr(exc)
    return devices


def sync_snapshot_ip_and_cache() -> None:
    if not os.path.exists(TUYA_SNAPSHOT_FILE):
        state["tuya_snapshot_sync_status"] = "snapshot_missing"
        return
    try:
        devices = load_json(TUYA_DEVICES_FILE)
        sync_devices_from_snapshot(devices)
        state["tuya_snapshot_sync_status"] = "ok"
        state["tuya_snapshot_sync_ts"] = int(time.time())
    except Exception as exc:
        state["tuya_snapshot_sync_status"] = "error"
        state["tuya_snapshot_sync_error"] = repr(exc)


def load_all_devices() -> List[Dict[str, Any]]:
    return load_json(TUYA_DEVICES_FILE)


def load_lights() -> List[Dict[str, Any]]:
    devices = load_all_devices()
    return [d for d in devices if d.get("product_name") == LAMPTAN_PRODUCT and d.get("ip") and d.get("id") and d.get("key")]


def camera_config_payload() -> Dict[str, Any]:
    for path in CAMERA_CONFIG_PATHS:
        if path and os.path.exists(path):
            data = load_json_optional(path)
            if isinstance(data, list):
                cameras = [x for x in data if isinstance(x, dict)]
            elif isinstance(data, dict):
                raw = data.get("cameras", [])
                cameras = [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []
            else:
                cameras = []
            return {"loaded": True, "path": path, "cameras": cameras}
    return {"loaded": False, "path": None, "cameras": []}


def load_camera_config() -> Dict[str, Any]:
    payload = camera_config_payload()
    state["camera_config_loaded"] = bool(payload["loaded"])
    state["camera_config_path"] = payload["path"]
    state["camera_count"] = len(payload["cameras"])
    return payload


def log_camera_config_startup() -> None:
    payload = load_camera_config()
    path = payload["path"] or "not found"
    log = f"Loaded {len(payload['cameras'])} cameras from {path}"
    state["camera_config_log"] = log
    print(log, flush=True)


def tcp_check(ip: str, port: int, timeout: float = CAMERA_TCP_TIMEOUT_SEC) -> bool:
    try:
        with socket.create_connection((ip, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def camera_has_rtsp(cam: Dict[str, Any]) -> bool:
    return bool(cam.get("rtsp") or cam.get("rtsp_url") or cam.get("rtsp_path") or cam.get("rtsp_port") or cam.get("has_rtsp"))


def camera_online(cam: Dict[str, Any], has_rtsp: bool) -> bool:
    ip = str(cam.get("ip") or "").strip()
    if not ip:
        return False
    if has_rtsp and tcp_check(ip, int(cam.get("rtsp_port") or 554)):
        return True
    ports = cam.get("ports") if isinstance(cam.get("ports"), list) else CAMERA_FALLBACK_PORTS
    for port in ports:
        try:
            if tcp_check(ip, int(port)):
                return True
        except Exception:
            continue
    return False


def public_camera(cam: Dict[str, Any]) -> Dict[str, Any]:
    has_rtsp = camera_has_rtsp(cam)
    return {
        "id": str(cam.get("id") or cam.get("name") or cam.get("ip") or "camera"),
        "name": str(cam.get("name") or "Camera"),
        "ip": str(cam.get("ip") or ""),
        "brand": str(cam.get("brand") or ""),
        "online": camera_online(cam, has_rtsp),
        "has_rtsp": has_rtsp,
    }


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


def tuya_err(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    return str(result.get("Err") or result.get("Error") or "")


def is_retryable_tuya_error(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    return tuya_err(result) in ("901", "904", "905", "914") or bool(result.get("Error"))


def status_base(dev: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": dev.get("name"), "target": device_target(dev), "ip": dev.get("ip")}


def cache_online(dev: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    item["last_seen_ts"] = int(time.time())
    item["status"] = "online"
    item["online"] = True
    state["light_status_cache"][dev["id"]] = item
    return item


def normalize_dps(dps: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k): v for k, v in dps.items()} if isinstance(dps, dict) else {}


def cache_dps(dev: Dict[str, Any], dps: Dict[str, Any], source: str = "command") -> Dict[str, Any]:
    incoming = normalize_dps(dps)
    cached = state["light_status_cache"].get(dev["id"], {})
    old_dps = ((cached.get("result") or {}).get("dps") or {}).copy()
    if source == "snapshot" and cached and cached.get("source") != "snapshot" and old_dps:
        merged = incoming.copy()
        merged.update(old_dps)
        item = {**status_base(dev), "source": cached.get("source", "command"), "result": {"dps": merged}, "last_seen_ts": cached.get("last_seen_ts", int(time.time())), "online": True, "status": cached.get("status", "online")}
        state["light_status_cache"][dev["id"]] = item
        return item
    old_dps.update(incoming)
    return cache_online(dev, {**status_base(dev), "source": source, "result": {"dps": old_dps}})


def dpsOf(item: Dict[str, Any]) -> Dict[str, Any]:
    return ((item or {}).get("result") or {}).get("dps") or {}


def any_cached_status_with_dps(dev: Dict[str, Any]) -> Dict[str, Any] | None:
    cached = state["light_status_cache"].get(dev["id"])
    if cached and dpsOf(cached):
        return cached
    return None


def cache_first_status(dev: Dict[str, Any]) -> Dict[str, Any]:
    base = status_base(dev)
    cached = any_cached_status_with_dps(dev)
    now = int(time.time())
    if cached:
        age = max(0, now - int(cached.get("last_seen_ts", 0)))
        status = "online" if age <= CACHE_ONLINE_TTL_SEC else "stale"
        return {**base, "source": cached.get("source", "cache"), "result": {"dps": dpsOf(cached)}, "last_seen_ts": cached.get("last_seen_ts"), "age_sec": age, "online": True, "status": status}
    return {**base, "source": "cache", "result": {"dps": {}}, "online": False, "status": "offline"}


def cache_first_status_devices() -> List[Dict[str, Any]]:
    return [cache_first_status(dev) for dev in load_lights()]


def fallback_status(dev: Dict[str, Any], base: Dict[str, Any], error_item: Dict[str, Any], snapshot: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    cached = any_cached_status_with_dps(dev)
    if cached:
        return cache_first_status(dev)
    return {**base, "source": "direct", "result": error_item, "online": False, "status": "offline"}


def record_tuya_log(entry: Dict[str, Any]) -> None:
    tail = state.setdefault("tuya_log_tail", [])
    tail.append(entry)
    state["tuya_log_tail"] = tail[-80:]
    state["tuya_last_log"] = entry
    try:
        print("[tuya] " + json.dumps(entry, ensure_ascii=False, default=str), flush=True)
    except Exception:
        pass


def tuya_request(dev: Dict[str, Any], send: Dict[str, Any], source: str, fn, retry_count: int = 0) -> Any:
    start = time.time()
    reply: Any = None
    try:
        with tuya_semaphore:
            reply = fn()
        return reply
    except Exception as exc:
        reply = {"exception": repr(exc)}
        raise
    finally:
        record_tuya_log({"target": device_target(dev), "send": send, "reply": reply, "duration_ms": int((time.time() - start) * 1000), "retry_count": retry_count, "source": source, "timeout": False})


def _tuya_status_worker(dev: Dict[str, Any], queue) -> None:
    try:
        queue.put({"ok": True, "result": tuya_device(dev).status()})
    except Exception as exc:
        queue.put({"ok": False, "exception": repr(exc)})


def read_status_once(dev: Dict[str, Any], source: str, retry_count: int = 0, timeout_sec: float = STATUS_READ_TIMEOUT_SEC) -> Dict[str, Any]:
    send = {"op": "status", "timeout_sec": timeout_sec}
    start = time.time()
    reply: Dict[str, Any] = {"Err": "905", "Error": "status_no_reply"}
    timeout = False
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_tuya_status_worker, args=(dev, queue))
    tuya_semaphore.acquire()
    try:
        proc.start()
        proc.join(timeout_sec)
        if proc.is_alive():
            timeout = True
            proc.terminate()
            proc.join(0.5)
            reply = {"Err": "905", "Error": f"status_timeout_{timeout_sec}s", "timeout": True}
        elif not queue.empty():
            msg = queue.get_nowait()
            reply = msg.get("result") if msg.get("ok") else {"Err": "905", "Error": msg.get("exception") or "status_exception"}
            if not reply:
                reply = {"Err": "905", "Error": "status_empty_result"}
        return reply
    finally:
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(0.2)
        except Exception:
            pass
        try:
            queue.close()
            queue.join_thread()
        except Exception:
            pass
        tuya_semaphore.release()
        record_tuya_log({"target": device_target(dev), "send": send, "reply": reply, "duration_ms": int((time.time() - start) * 1000), "retry_count": retry_count, "source": source, "timeout": timeout})


def read_dps(dev: Dict[str, Any], source: str = "status") -> Dict[str, Any] | None:
    result = read_status_once(dev, source)
    if tuya_ok(result):
        return result.get("dps") or result.get("data", {}).get("dps")
    return None


def read_dps_after_command(dev: Dict[str, Any]) -> Dict[str, Any]:
    time.sleep(APPLY_VERIFY_FIRST_DELAY_SEC)
    errors = []
    for attempt in (1, 2):
        result = read_status_once(dev, "verify", retry_count=attempt - 1)
        if tuya_ok(result):
            dps = result.get("dps") or result.get("data", {}).get("dps") or {}
            cache_dps(dev, dps, "verify")
            return dps
        errors.append({"attempt": attempt, "result": result})
        if attempt == 1:
            time.sleep(APPLY_ALL_STATUS_RETRY_DELAY_SEC)
    cached = any_cached_status_with_dps(dev)
    if cached:
        return dpsOf(cached)
    snap_dps = snapshot_dps_by_id().get(dev.get("id"))
    if snap_dps:
        return snap_dps
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


def extract_dps(result: Dict[str, Any]) -> Dict[str, Any]:
    return result.get("dps") or result.get("data", {}).get("dps") or {}


def extract_command_dps(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    dps = extract_dps(result)
    if dps:
        return normalize_dps(dps)
    if result.get("dp") is not None and result.get("value") is not None:
        return {str(result["dp"]): result["value"]}
    for key in ("result", "command_result"):
        nested = result.get(key)
        dps = extract_command_dps(nested)
        if dps:
            return dps
    return {}


def merge_command_cache(dev: Dict[str, Any], result: Any) -> None:
    dps = extract_command_dps(result)
    if dps:
        cache_dps(dev, dps, "command")


def get_light_status_deep(dev: Dict[str, Any], snapshot: Dict[str, Dict[str, Any]] | None = None) -> Dict[str, Any]:
    snapshot = snapshot or {}
    base = status_base(dev)
    result = read_status_once(dev, "status-deep")
    if tuya_ok(result):
        return cache_online(dev, {**base, "source": "direct", "result": result})
    time.sleep(APPLY_ALL_STATUS_RETRY_DELAY_SEC)
    retry = read_status_once(dev, "status-deep-retry", retry_count=1)
    if tuya_ok(retry):
        return cache_online(dev, {**base, "source": "direct", "result": retry})
    return fallback_status(dev, base, retry, snapshot)


def deep_status_devices() -> List[Dict[str, Any]]:
    devices = load_lights()
    results = []
    for idx, dev in enumerate(devices):
        try:
            results.append(get_light_status_deep(dev, {}))
        except Exception as exc:
            results.append(fallback_status(dev, status_base(dev), {"exception": repr(exc)}, {}))
        if idx < len(devices) - 1:
            time.sleep(LIVE_STATUS_DEVICE_DELAY_SEC)
    return results


def preload_snapshot_cache():
    sync_snapshot_ip_and_cache()
    state["snapshot_preload_ts"] = int(time.time())
    state["snapshot_cache_ready"] = True


def light_poller_loop():
    state["poller_running"] = True
    while state.get("poller_running"):
        try:
            state["poller_source"] = "cache-only"
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
    return tuya_request(dev, {"op": "set_status", "dp": dp, "value": value}, "send", lambda: tuya_device(dev).set_status(value, dp))


def set_dp(dev: Dict[str, Any], dp: int, value: Any, attempts: int = 2, retry_delay: float = 0.25) -> Dict[str, Any]:
    cache_dps(dev, {str(dp): value}, "pending")
    errors = []
    for attempt in range(1, attempts + 1):
        result = tuya_request(dev, {"op": "set_status", "dp": dp, "value": value}, "send", lambda: tuya_device(dev).set_status(value, dp), retry_count=attempt - 1)
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
        dps = read_dps(dev, "verify-after-error")
        if dps and str(dps.get(str(dp)) if str(dp) in dps else dps.get(dp)) == str(value):
            cache_dps(dev, dps, "verify")
            return {"ok": True, "verified_after_error": True, "dp": dp, "value": value, "dps": dps, "errors": errors}
    except Exception as exc:
        errors.append({"verify_error": repr(exc)})
    raise RuntimeError({"errors": errors})


def set_dp_for_apply_all(dev: Dict[str, Any], dp: int, value: Any) -> Dict[str, Any]:
    cache_dps(dev, {str(dp): value}, "pending")
    result = tuya_request(dev, {"op": "set_status", "dp": dp, "value": value}, "send", lambda: tuya_device(dev).set_status(value, dp))
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


def execute_for_target(target: str, fn, delay_between_devices: float = 0.0, update_command_cache: bool = False):
    results = []
    devices = select_lights(target)
    for idx, dev in enumerate(devices):
        try:
            result = fn(dev)
            if update_command_cache:
                merge_command_cache(dev, result)
            results.append({"name": dev.get("name"), "ok": True, "result": result})
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
    return {"ok": True, "history": state.get("condo_sensor_history", [])}


@app.get("/api/lights")
def lights():
    return {"ok": True, "devices": [{"name": d.get("name"), "target": device_target(d), "ip": d.get("ip")} for d in load_lights()]}


@app.get("/api/light/status/{target}")
def light_status_one(target: str):
    dev = select_single_light(target)
    return {"ok": True, "source": "single-fast", "device": cache_first_status(dev)}


@app.get("/api/lights/status")
def lights_status():
    return {"ok": True, "source": "live", "devices": cache_first_status_devices()}


@app.get("/api/lights/status-fast")
def lights_status_fast():
    return {"ok": True, "source": "live", "devices": cache_first_status_devices()}


@app.get("/api/lights/status-live")
def lights_status_live():
    return {"ok": True, "source": "live", "devices": cache_first_status_devices()}


@app.get("/api/lights/status-deep")
def lights_status_deep():
    return {"ok": True, "source": "deep", "devices": deep_status_devices()}


@app.post("/api/lights/refresh")
def lights_refresh():
    return {"ok": True, "source": "manual-live", "devices": cache_first_status_devices()}


@app.get("/api/cameras")
def cameras():
    payload = load_camera_config()
    public = [public_camera(cam) for cam in payload["cameras"]]
    return {
        "ok": True,
        "config_loaded": bool(payload["loaded"]),
        "config_path": payload["path"],
        "camera_count": len(public),
        "cameras": public,
    }


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
        results = execute_for_target(body.target, lambda dev: apply_light_all_reliable(dev, body), delay_between_devices=APPLY_ALL_DEVICE_DELAY_SEC, update_command_cache=True)
    else:
        results = execute_for_target(body.target, lambda dev: apply_light(dev, body), update_command_cache=True)
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
