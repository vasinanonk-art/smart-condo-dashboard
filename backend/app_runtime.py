import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import app as app_module
from backend.sensor_history_store import (
    HISTORY_RETENTION_SEC,
    append_row,
    diagnostics,
    load_history,
    normalize_row,
    prune_history,
)

app = app_module.app
APP_VERSION = "3.1.0"
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
FRONTEND_ASSETS_DIR = os.path.join(FRONTEND_DIR, "assets")
DASHBOARD_V3_ASSETS = (
    "dashboard_v3.css",
    "dashboard_v3_layout.css",
    "dashboard_v3.js",
)
MI_AIR_PURIFIER_MQTT_TOPIC = os.getenv("MI_AIR_PURIFIER_MQTT_TOPIC", "").strip()
HA_BASE_URL = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "").strip()
HA_PM25_LIVING_ENTITY = os.getenv(
    "HA_PM25_LIVING_ENTITY", "sensor.xiaomi_cpa5_6c9e_pm25_density"
).strip()
HA_PM25_BEDROOM_ENTITY = os.getenv(
    "HA_PM25_BEDROOM_ENTITY", "sensor.xiaomi_cpa5_46f0_pm25_density"
).strip()
HA_POLL_SEC = 30
HA_STALE_SEC = 90
PM25_EXPECTED_SOURCE = "HA_BASE_URL and HA_TOKEN with the configured Home Assistant PM2.5 entities"

_ha_state: Dict[str, Any] = {
    "configured": bool(HA_BASE_URL and HA_TOKEN),
    "last_poll_ts": None,
    "last_success_ts": None,
    "last_error": None,
    "living_room": {"entity_id": HA_PM25_LIVING_ENTITY, "value": None, "updated_ts": None, "attributes": {}},
    "bedroom": {"entity_id": HA_PM25_BEDROOM_ENTITY, "value": None, "updated_ts": None, "attributes": {}},
}


def _mount_dashboard_assets() -> None:
    app.router.routes = [
        route
        for route in app.router.routes
        if not (getattr(route, "path", None) == "/assets" and route.__class__.__name__ == "Mount")
    ]
    app.mount(
        "/assets",
        StaticFiles(directory=FRONTEND_ASSETS_DIR, check_dir=False),
        name="assets",
    )


def _validate_dashboard_assets() -> None:
    missing = [
        filename
        for filename in DASHBOARD_V3_ASSETS
        if not os.path.isfile(os.path.join(FRONTEND_ASSETS_DIR, filename))
    ]
    if missing:
        print("dashboard assets missing: " + ",".join(missing), flush=True)
    else:
        print("dashboard assets: ready", flush=True)


def _sync_history_state(rows):
    app_module.state["condo_sensor_history"] = rows
    app_module.state["sensor_history"] = rows
    app_module.state["condo_history"] = rows


def _history_diag() -> Dict[str, Any]:
    result = diagnostics()
    result.update(
        {
            "source_missing": not _ha_state["configured"] and not bool(MI_AIR_PURIFIER_MQTT_TOPIC),
            "expected_source": PM25_EXPECTED_SOURCE if not _ha_state["configured"] and not MI_AIR_PURIFIER_MQTT_TOPIC else None,
            "pm25_source": "home_assistant" if _ha_state["configured"] else (MI_AIR_PURIFIER_MQTT_TOPIC or None),
        }
    )
    return result


def _publish_diagnostics() -> None:
    info = _history_diag()
    app_module.state["sensor_history_diagnostics"] = info
    print(
        "sensor history: "
        f"history_store_path={info['history_store_path']} "
        f"loaded_count={info['loaded_count']} "
        f"appended_count={info['appended_count']} "
        f"pruned_count={info['pruned_count']}",
        flush=True,
    )
    if info["source_missing"]:
        print(
            "pm25 source: source_missing=true "
            f"expected_source={info['expected_source']}",
            flush=True,
        )


def _persist_latest_sensor() -> None:
    sensor = app_module.state.get("condo_sensor", {})
    row = normalize_row(sensor)
    if row["ts"] <= 0 or not append_row(row):
        return
    history = list(app_module.state.get("condo_sensor_history", []))
    normalized = normalize_row(history[-1]) if history else None
    if normalized != row:
        history.append(row)
    if diagnostics().get("appended_count", 0) % 100 == 0:
        history = prune_history(history)
    _sync_history_state(history)
    app_module.state["sensor_history_diagnostics"] = _history_diag()


def _number(value: Any):
    try:
        if value is None or value in ("", "unknown", "unavailable"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_pm25(payload: Any):
    if not isinstance(payload, dict):
        return None
    candidates = [payload]
    for key in ("sensor", "state", "attributes", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    for item in candidates:
        for key in ("pm25", "pm2_5", "pm2.5", "PM25", "pm_25"):
            value = _number(item.get(key))
            if value is not None:
                return value
    return None


def _apply_air_values(now: int) -> None:
    sensor = dict(app_module.state.get("condo_sensor", {}))
    living = _ha_state["living_room"].get("value")
    bedroom = _ha_state["bedroom"].get("value")
    if living is not None:
        sensor["pm25"] = living
        sensor["pm25_living_room"] = living
    if bedroom is not None:
        sensor["pm25_bedroom"] = bedroom
    if living is None and bedroom is None:
        return
    sensor["pm25_source"] = "home_assistant"
    sensor["ts"] = now
    app_module.state["condo_sensor"] = sensor
    app_module.state["sensor"] = sensor
    _persist_latest_sensor()


def _ingest_pm25(payload: Any) -> None:
    value = _first_pm25(payload)
    if value is None:
        return
    now = int(time.time())
    sensor = dict(app_module.state.get("condo_sensor", {}))
    sensor.update({"pm25": value, "pm25_living_room": value, "pm25_source": MI_AIR_PURIFIER_MQTT_TOPIC, "ts": now})
    app_module.state["condo_sensor"] = sensor
    app_module.state["sensor"] = sensor
    _persist_latest_sensor()


def _ha_get(entity_id: str) -> Dict[str, Any]:
    url = f"{HA_BASE_URL}/api/states/{entity_id}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid Home Assistant response")
    return payload


def _update_ha_room(room: str, entity_id: str) -> bool:
    try:
        payload = _ha_get(entity_id)
        value = _number(payload.get("state"))
        if value is None:
            raise ValueError("PM2.5 state unavailable")
        _ha_state[room].update(
            {
                "value": value,
                "updated_ts": int(time.time()),
                "attributes": payload.get("attributes") if isinstance(payload.get("attributes"), dict) else {},
                "error": None,
            }
        )
        return True
    except Exception as exc:
        _ha_state[room]["error"] = type(exc).__name__
        return False


def _ha_poll_loop() -> None:
    while True:
        now = int(time.time())
        _ha_state["last_poll_ts"] = now
        living_ok = _update_ha_room("living_room", HA_PM25_LIVING_ENTITY)
        bedroom_ok = _update_ha_room("bedroom", HA_PM25_BEDROOM_ENTITY)
        if living_ok or bedroom_ok:
            _ha_state["last_success_ts"] = int(time.time())
            _ha_state["last_error"] = None
            _apply_air_values(int(time.time()))
        else:
            _ha_state["last_error"] = "Home Assistant PM2.5 entities unavailable"
        time.sleep(HA_POLL_SEC)


def _install_sensor_update_hooks() -> None:
    if getattr(app_module, "_persistent_sensor_history_installed", False):
        return
    original_sensor_update = app_module.update_condo_sensor_from_topic
    original_state_update = app_module.update_condo_state

    def wrapped_sensor_update(data):
        original_sensor_update(data)
        sensor = dict(app_module.state.get("condo_sensor", {}))
        living = _ha_state["living_room"].get("value")
        bedroom = _ha_state["bedroom"].get("value")
        if living is not None:
            sensor.update({"pm25": living, "pm25_living_room": living})
        if bedroom is not None:
            sensor["pm25_bedroom"] = bedroom
        app_module.state["condo_sensor"] = sensor
        app_module.state["sensor"] = sensor
        _persist_latest_sensor()

    def wrapped_state_update(payload):
        original_state_update(payload)
        _persist_latest_sensor()

    app_module.update_condo_sensor_from_topic = wrapped_sensor_update
    app_module.update_condo_state = wrapped_state_update
    app_module._persistent_sensor_history_installed = True


def _install_pm25_mqtt_source() -> None:
    if not MI_AIR_PURIFIER_MQTT_TOPIC or getattr(app_module, "_pm25_mqtt_source_installed", False):
        return
    original_connect = app_module.mqttc.on_connect
    original_message = app_module.mqttc.on_message

    def wrapped_connect(client, userdata, flags, reason_code, properties=None):
        original_connect(client, userdata, flags, reason_code, properties)
        client.subscribe(MI_AIR_PURIFIER_MQTT_TOPIC)
        topics = list(app_module.state.get("mqtt_subscribed_topics", []))
        if MI_AIR_PURIFIER_MQTT_TOPIC not in topics:
            topics.append(MI_AIR_PURIFIER_MQTT_TOPIC)
        app_module.state["mqtt_subscribed_topics"] = topics

    def wrapped_message(client, userdata, msg):
        if msg.topic == MI_AIR_PURIFIER_MQTT_TOPIC:
            try:
                payload = json.loads(msg.payload.decode(errors="ignore"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return
            _ingest_pm25(payload)
            return
        original_message(client, userdata, msg)

    app_module.mqttc.on_connect = wrapped_connect
    app_module.mqttc.on_message = wrapped_message
    app_module._pm25_mqtt_source_installed = True


def _room_payload(room: str) -> Dict[str, Any]:
    item = dict(_ha_state[room])
    updated = item.get("updated_ts")
    age = max(0, int(time.time()) - int(updated)) if updated else None
    attributes = item.pop("attributes", {}) or {}
    filter_life = None
    for key in ("filter_life_remaining", "filter_life", "filter_hours_remaining", "filter_hours_used"):
        if attributes.get(key) is not None:
            filter_life = {"key": key, "value": attributes.get(key)}
            break
    item.update({"age_sec": age, "stale": age is None or age > HA_STALE_SEC, "filter_life": filter_life})
    return item


def _automation_payload() -> Dict[str, Any]:
    try:
        import sonoff_client as automation_module
        state = getattr(automation_module, "_automation_state", {})
        now = int(time.time())
        people = {}
        for person in ("beer", "seem"):
            last_ts = int((state.get("last_ts") or {}).get(person) or 0)
            people[person] = {
                "automation_home": (state.get("home") or {}).get(person),
                "cooldown_remaining_sec": max(0, 600 - (now - last_ts)) if last_ts else 0,
                "arrival_pending_since": (state.get("pending_since") or {}).get(person) or None,
                "away_pending_since": (state.get("away_since") or {}).get(person) or None,
            }
        return {"enabled": True, "people": people, "recent_events": []}
    except Exception:
        return {"enabled": True, "people": {}, "recent_events": []}


@app.get("/api/air-quality")
def air_quality():
    return {
        "ok": True,
        "configured": _ha_state["configured"],
        "source": "home_assistant" if _ha_state["configured"] else None,
        "last_poll_ts": _ha_state["last_poll_ts"],
        "last_success_ts": _ha_state["last_success_ts"],
        "last_error": _ha_state["last_error"],
        "living_room": _room_payload("living_room"),
        "bedroom": _room_payload("bedroom"),
    }


@app.get("/api/dashboard/status")
def dashboard_status():
    history_diag = _history_diag()
    cameras = app_module.state.get("camera_count", 0)
    return {
        "ok": True,
        "version": APP_VERSION,
        "service": "online",
        "mqtt": {"connected": bool(app_module.state.get("mqtt_connected")), "topics": app_module.state.get("mqtt_subscribed_topics", [])},
        "sonoff_cloud": {
            "configured": bool(app_module.state.get("ewelink_config_loaded")),
            "last_sync_ts": app_module.state.get("sonoff_last_sync_ts"),
        },
        "home_assistant": {
            "configured": _ha_state["configured"],
            "last_success_ts": _ha_state["last_success_ts"],
            "last_error": _ha_state["last_error"],
        },
        "history": history_diag,
        "camera": {"count": cameras, "config_loaded": bool(app_module.state.get("camera_config_loaded"))},
        "automation": _automation_payload(),
    }


def _initialize_persistent_history() -> None:
    app_module.HISTORY_TTL_SEC = HISTORY_RETENTION_SEC
    app_module.HISTORY_MAX_POINTS = max(int(getattr(app_module, "HISTORY_MAX_POINTS", 0)), 200000)
    rows = load_history(app_module.state.get("condo_sensor_history", []))
    _sync_history_state(rows)
    _install_sensor_update_hooks()
    _install_pm25_mqtt_source()
    _publish_diagnostics()
    if _ha_state["configured"]:
        thread = threading.Thread(target=_ha_poll_loop, name="ha-pm25-poller", daemon=True)
        thread.start()


@app.on_event("startup")
def validate_dashboard_assets() -> None:
    _validate_dashboard_assets()


@app.middleware("http")
async def sensor_history_diagnostics_middleware(request: Request, call_next):
    response = await call_next(request)
    if request.url.path != "/api/condo/history":
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(content={"ok": False, "history": []}, status_code=response.status_code)
    if isinstance(payload, dict):
        range_key = str(request.query_params.get("range", "24h")).lower()
        seconds = {"24h": 86400, "3d": 259200, "7d": 604800}.get(range_key, 86400)
        rows = [normalize_row(row) for row in app_module.state.get("condo_sensor_history", [])]
        rows = [row for row in rows if row["ts"] >= int(time.time()) - seconds]
        payload["history"] = rows
        payload["points"] = rows
        payload["raw_count"] = len(rows)
        payload["current"] = normalize_row(app_module.state.get("condo_sensor", {}))
        payload.update(_history_diag())
    return JSONResponse(content=payload, status_code=response.status_code)


_mount_dashboard_assets()
_initialize_persistent_history()
import backend.dashboard_extensions  # noqa: E402,F401
