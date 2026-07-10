import json
import os
import time
from typing import Any, Dict

from fastapi import Request
from fastapi.responses import JSONResponse

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

MI_AIR_PURIFIER_MQTT_TOPIC = os.getenv("MI_AIR_PURIFIER_MQTT_TOPIC", "").strip()
PM25_EXPECTED_SOURCE = (
    "MI_AIR_PURIFIER_MQTT_TOPIC carrying JSON with pm25, pm2_5, "
    "pm2.5, PM25, or pm_25"
)


def _sync_history_state(rows):
    app_module.state["condo_sensor_history"] = rows
    app_module.state["sensor_history"] = rows
    app_module.state["condo_history"] = rows


def _history_diag() -> Dict[str, Any]:
    result = diagnostics()
    result.update(
        {
            "source_missing": not bool(MI_AIR_PURIFIER_MQTT_TOPIC),
            "expected_source": PM25_EXPECTED_SOURCE if not MI_AIR_PURIFIER_MQTT_TOPIC else None,
            "pm25_source": MI_AIR_PURIFIER_MQTT_TOPIC or None,
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
    if row["ts"] <= 0:
        return
    if not append_row(row):
        return

    history = list(app_module.state.get("condo_sensor_history", []))
    normalized = normalize_row(history[-1]) if history else None
    if normalized != row:
        history.append(row)
    if diagnostics().get("appended_count", 0) % 100 == 0:
        history = prune_history(history)
    _sync_history_state(history)
    app_module.state["sensor_history_diagnostics"] = _history_diag()


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
            value = item.get(key)
            if value is None or value == "":
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _ingest_pm25(payload: Any) -> None:
    value = _first_pm25(payload)
    if value is None:
        return
    now = int(time.time())
    sensor = dict(app_module.state.get("condo_sensor", {}))
    sensor.update(
        {
            "pm25": value,
            "pm25_source": MI_AIR_PURIFIER_MQTT_TOPIC,
            "ts": now,
        }
    )
    app_module.state["condo_sensor"] = sensor
    app_module.state["sensor"] = sensor
    history = list(app_module.state.get("condo_sensor_history", []))
    history.append(normalize_row(sensor))
    history = app_module.prune_history(history)
    _sync_history_state(history)
    _persist_latest_sensor()


def _install_sensor_update_hooks() -> None:
    if getattr(app_module, "_persistent_sensor_history_installed", False):
        return

    original_sensor_update = app_module.update_condo_sensor_from_topic
    original_state_update = app_module.update_condo_state

    def wrapped_sensor_update(data):
        original_sensor_update(data)
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


def _initialize_persistent_history() -> None:
    app_module.HISTORY_TTL_SEC = HISTORY_RETENTION_SEC
    app_module.HISTORY_MAX_POINTS = max(int(getattr(app_module, "HISTORY_MAX_POINTS", 0)), 200000)
    existing = app_module.state.get("condo_sensor_history", [])
    rows = load_history(existing)
    _sync_history_state(rows)
    _install_sensor_update_hooks()
    _install_pm25_mqtt_source()
    _publish_diagnostics()


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
        payload.update(_history_diag())
    return JSONResponse(content=payload, status_code=response.status_code)


_initialize_persistent_history()
