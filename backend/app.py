import json
import os
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
LAMPTAN_PRODUCT = "LAMPTAN Jarton Bulb CCT+RGB 11w"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

app = FastAPI(title="Smart Condo Dashboard", version="1.1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

state: Dict[str, Any] = {
    "mqtt_connected": False,
    "last_state": {},
    "last_cmd": None,
    "last_cmd_ts": None,
    "last_light_cmd": None,
    "last_light_cmd_ts": None,
    "available_commands": [
        "power_on", "power_off", "youtube", "netflix", "disney", "prime", "appletv",
        "browser", "livetv", "home", "viu", "hbo", "hdmi1", "hdmi2", "hdmi3", "hdmi4",
        "volume_up", "volume_down", "mute", "unmute", "up", "down", "left", "right", "ok", "back", "home_key"
    ],
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


def on_connect(client, userdata, flags, reason_code, properties=None):
    state["mqtt_connected"] = True
    client.subscribe(MQTT_STATE_TOPIC)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties=None):
    state["mqtt_connected"] = False


def on_message(client, userdata, msg):
    payload = msg.payload.decode(errors="ignore")
    try:
        state["last_state"] = json.loads(payload)
    except Exception:
        state["last_state"] = {"raw": payload}
    state["last_state_topic"] = msg.topic
    state["last_state_ts"] = int(time.time())


mqttc.on_connect = on_connect
mqttc.on_disconnect = on_disconnect
mqttc.on_message = on_message


@app.on_event("startup")
def startup():
    try:
        mqttc.connect(MQTT_HOST, MQTT_PORT, 60)
        mqttc.loop_start()
    except Exception as e:
        state["mqtt_connected"] = False
        state["mqtt_error"] = repr(e)


@app.on_event("shutdown")
def shutdown():
    try:
        mqttc.loop_stop()
        mqttc.disconnect()
    except Exception:
        pass


def clamp(n: int, low: int, high: int) -> int:
    return max(low, min(high, n))


def slug(name: str) -> str:
    return name.lower().replace("light ", "").replace(" room ", " ").replace(" ", "_").replace("-", "_")


def load_lights() -> List[Dict[str, Any]]:
    try:
        with open(TUYA_DEVICES_FILE, encoding="utf-8") as f:
            devices = json.load(f)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"cannot load {TUYA_DEVICES_FILE}: {exc}")

    lights = []
    for dev in devices:
        if dev.get("product_name") != LAMPTAN_PRODUCT:
            continue
        if not dev.get("ip") or not dev.get("id") or not dev.get("key"):
            continue
        lights.append(dev)
    return lights


def select_lights(target: str) -> List[Dict[str, Any]]:
    target = target.strip().lower().replace(" ", "_")
    lights = load_lights()
    if target in ("all", "lamptan"):
        return lights
    selected = [d for d in lights if slug(d.get("name", "")) == target]
    if not selected:
        raise HTTPException(status_code=404, detail=f"light target not found: {target}")
    return selected


def tuya_device(dev: Dict[str, Any]) -> tinytuya.Device:
    d = tinytuya.Device(dev["id"], dev["ip"], dev["key"])
    d.set_version(float(dev.get("version") or 3.3))
    return d


def tuya_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("Error") or result.get("Err"):
        return False
    if "dps" in result or "data" in result:
        return True
    return False


def set_dp(dev: Dict[str, Any], dp: int, value: Any) -> Dict[str, Any]:
    d = tuya_device(dev)
    result = d.set_status(value, dp)
    if not tuya_ok(result):
        raise RuntimeError(result)
    return result


def hsv_hex(h: int, s: int, v: int) -> str:
    return f"{clamp(h, 0, 360):04x}{clamp(s, 0, 1000):04x}{clamp(v, 0, 1000):04x}"


def apply_light(dev: Dict[str, Any], body: LightCommand) -> Dict[str, Any]:
    action = body.action.strip().lower()
    if action == "brightness":
        value = clamp(int(body.value or 500), 10, 1000)
        return set_dp(dev, 22, value)
    if action in ("temperature", "temp", "cct"):
        value = clamp(int(body.value or 500), 0, 1000)
        set_dp(dev, 21, "white")
        return set_dp(dev, 23, value)
    if action == "rgb":
        h = clamp(int(body.h or 0), 0, 360)
        s = clamp(int(body.s if body.s is not None else 1000), 0, 1000)
        v = clamp(int(body.v if body.v is not None else 1000), 0, 1000)
        set_dp(dev, 21, "colour")
        return set_dp(dev, 24, hsv_hex(h, s, v))
    if action == "scene":
        return apply_scene(dev, body.scene or "relax")
    raise HTTPException(status_code=400, detail=f"unsupported light action: {action}")


def apply_scene(dev: Dict[str, Any], scene: str) -> Dict[str, Any]:
    scene = scene.strip().lower()
    presets = {
        "relax": ("white", 350, 850, None),
        "reading": ("white", 900, 350, None),
        "night": ("white", 80, 1000, None),
        "movie": ("colour", None, None, (240, 700, 250)),
        "party": ("colour", None, None, (300, 1000, 1000)),
    }
    if scene not in presets:
        raise HTTPException(status_code=400, detail=f"unsupported scene: {scene}")
    mode, bright, temp, hsv = presets[scene]
    set_dp(dev, 21, mode)
    if mode == "white":
        set_dp(dev, 22, bright)
        return set_dp(dev, 23, temp)
    h, s, v = hsv
    return set_dp(dev, 24, hsv_hex(h, s, v))


@app.get("/api/health")
def health():
    return {"ok": True, "mqtt_connected": state["mqtt_connected"], "mqtt_host": MQTT_HOST, "mqtt_port": MQTT_PORT}


@app.get("/api/state")
def get_state():
    return state


@app.get("/api/lights")
def lights():
    return {
        "ok": True,
        "devices": [{"name": d.get("name"), "target": slug(d.get("name", "")), "ip": d.get("ip")} for d in load_lights()],
    }


@app.post("/api/light")
def light_control(body: LightCommand):
    devices = select_lights(body.target)
    results = []
    for dev in devices:
        try:
            result = apply_light(dev, body)
            results.append({"name": dev.get("name"), "ok": True, "result": result})
        except Exception as exc:
            results.append({"name": dev.get("name"), "ok": False, "error": repr(exc)})
    state["last_light_cmd"] = body.model_dump()
    state["last_light_cmd_ts"] = int(time.time())
    ok = all(r["ok"] for r in results)
    return {"ok": ok, "target": body.target, "action": body.action, "results": results}


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
