"""Ingest retained PJ-1103 state through the existing MQTT client."""
from __future__ import annotations

import json
from typing import Any

from backend import app as app_module
from backend import pj1103_electricity_bridge as bridge

STATE_TOPIC = bridge.STATE_TOPIC


def _decode(payload: Any) -> Any:
    try:
        raw = payload.decode(errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
        return json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def install_electricity_mqtt_ingestion() -> None:
    if getattr(app_module, "_electricity_mqtt_ingestion_installed", False):
        return
    previous_connect = app_module.mqttc.on_connect
    previous_message = app_module.mqttc.on_message

    def on_connect(client, userdata, flags, reason_code, properties=None):
        if callable(previous_connect):
            previous_connect(client, userdata, flags, reason_code, properties)
        client.subscribe(STATE_TOPIC)
        topics = list(app_module.state.get("mqtt_subscribed_topics", []))
        if STATE_TOPIC not in topics:
            topics.append(STATE_TOPIC)
        app_module.state["mqtt_subscribed_topics"] = topics

    def on_message(client, userdata, msg):
        topic = str(getattr(msg, "topic", ""))
        if topic == STATE_TOPIC:
            bridge.ingest_retained_state(_decode(getattr(msg, "payload", b"")))
            return None
        if callable(previous_message):
            return previous_message(client, userdata, msg)
        return None

    app_module.mqttc.on_connect = on_connect
    app_module.mqttc.on_message = on_message
    app_module.on_connect = on_connect
    app_module.on_message = on_message
    app_module._electricity_mqtt_ingestion_installed = True


@app_module.app.on_event("startup")
def install_runtime_electricity_mqtt() -> None:
    install_electricity_mqtt_ingestion()
