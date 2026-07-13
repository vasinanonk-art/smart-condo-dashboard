"""Own LG TV MQTT ingestion at the final runtime callback layer.

This module is loaded last by backend.app_entry. Its startup hook runs after the
other runtime hooks, making this the single authoritative callback for LG TV
state and heartbeat topics without creating another MQTT client or polling loop.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Mapping, Optional

from backend import app as app_module

MQTT_STATE_TOPIC = os.getenv("MQTT_STATE_TOPIC", "home/lgtv/state")
MQTT_HEARTBEAT_TOPIC = os.getenv("MQTT_HEARTBEAT_TOPIC", "home/lgtv/heartbeat")


def _is_heartbeat_only(payload: Mapping[str, Any]) -> bool:
    if payload.get("heartbeat") is True:
        return True
    state_keys = {
        "power",
        "app",
        "current_app",
        "input",
        "source",
        "volume",
        "vol",
        "mute",
        "muted",
    }
    return (
        str(payload.get("status") or "").strip().lower() == "online"
        and not any(key in payload for key in state_keys)
    )


def _normalize_full_state(payload: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = [payload]
    for key in ("tv", "state", "data"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)

    for item in candidates:
        values = {
            "power": item.get("power"),
            "app": item.get("app", item.get("current_app")),
            "input": item.get("input", item.get("source")),
            "volume": item.get("volume", item.get("vol")),
            "mute": item.get("mute", item.get("muted")),
        }
        if any(value is not None for value in values.values()):
            return values
    return None


def ingest_lg_tv_payload(payload: Any, topic: str, now: Optional[int] = None) -> bool:
    """Store a heartbeat or full TV snapshot without erasing prior full values."""
    if topic not in (MQTT_STATE_TOPIC, MQTT_HEARTBEAT_TOPIC):
        return False
    if not isinstance(payload, Mapping):
        return True

    timestamp = int(now or time.time())
    if topic == MQTT_HEARTBEAT_TOPIC or _is_heartbeat_only(payload):
        app_module.state["lg_tv_last_heartbeat_ts"] = timestamp
        app_module.state["lg_tv_bridge_online"] = True
        return True

    full = _normalize_full_state(payload)
    if full is None:
        return True

    previous = app_module.state.get("lg_tv_last_state")
    merged = dict(previous) if isinstance(previous, Mapping) else {}
    for key, value in full.items():
        if value is not None:
            merged[key] = value

    app_module.state["lg_tv_last_state"] = merged
    app_module.state["lg_tv_last_state_ts"] = timestamp
    app_module.state["lg_tv_last_full_state_ts"] = timestamp
    app_module.state["lg_tv_last_heartbeat_ts"] = timestamp
    app_module.state["lg_tv_bridge_online"] = True
    return True


def _decode(payload: Any) -> Any:
    try:
        raw = payload.decode(errors="ignore") if isinstance(payload, (bytes, bytearray)) else str(payload)
        return json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None


def install_primary_lg_tv_callbacks() -> None:
    if getattr(app_module, "_primary_lg_tv_mqtt_callback_installed", False):
        return

    previous_connect = app_module.mqttc.on_connect
    previous_message = app_module.mqttc.on_message

    def authoritative_connect(client, userdata, flags, reason_code, properties=None):
        if callable(previous_connect):
            previous_connect(client, userdata, flags, reason_code, properties)
        client.subscribe(MQTT_HEARTBEAT_TOPIC)
        topics = list(app_module.state.get("mqtt_subscribed_topics", []))
        for topic in (MQTT_STATE_TOPIC, MQTT_HEARTBEAT_TOPIC):
            if topic not in topics:
                topics.append(topic)
        app_module.state["mqtt_subscribed_topics"] = topics

    def authoritative_message(client, userdata, msg):
        topic = str(getattr(msg, "topic", ""))
        if topic in (MQTT_STATE_TOPIC, MQTT_HEARTBEAT_TOPIC):
            ingest_lg_tv_payload(_decode(getattr(msg, "payload", b"")), topic)
            return None
        if callable(previous_message):
            return previous_message(client, userdata, msg)
        return None

    app_module.mqttc.on_connect = authoritative_connect
    app_module.mqttc.on_message = authoritative_message
    app_module.on_connect = authoritative_connect
    app_module.on_message = authoritative_message
    app_module._primary_lg_tv_mqtt_callback_installed = True
    # The old topology monkey-patch is now bypassed for both LG TV topics.
    app_module._lg_tv_state_ownership_installed = False


@app_module.app.on_event("startup")
def install_primary_lg_tv_mqtt_callback() -> None:
    install_primary_lg_tv_callbacks()
