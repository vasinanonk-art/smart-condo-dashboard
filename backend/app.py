import json
import os
import time
from typing import Any, Dict

import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

MQTT_HOST = os.getenv("MQTT_HOST", "127.0.0.1")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_CMD_TOPIC = os.getenv("MQTT_CMD_TOPIC", "home/lgtv/cmd")
MQTT_STATE_TOPIC = os.getenv("MQTT_STATE_TOPIC", "home/lgtv/state")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))

app = FastAPI(title="Smart Condo Dashboard", version="1.0.0")
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
    "available_commands": [
        "power_on", "power_off", "youtube", "netflix", "disney", "prime", "appletv",
        "browser", "livetv", "home", "viu", "hbo", "hdmi1", "hdmi2", "hdmi3", "hdmi4",
        "volume_up", "volume_down", "mute", "unmute", "up", "down", "left", "right", "ok", "back", "home_key"
    ],
}

mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)


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


class Command(BaseModel):
    cmd: str


@app.get("/api/health")
def health():
    return {"ok": True, "mqtt_connected": state["mqtt_connected"], "mqtt_host": MQTT_HOST, "mqtt_port": MQTT_PORT}


@app.get("/api/state")
def get_state():
    return state


@app.post("/api/command")
def send_command(body: Command):
    cmd = body.cmd.strip()
    if not cmd:
        raise HTTPException(status_code=400, detail="empty command")
    if not state["mqtt_connected"]:
        # Still try once; Mosquitto may reconnect between polls.
        pass
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
