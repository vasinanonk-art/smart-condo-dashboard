"""Local PJ-1103 electricity meter bridge.

Uses the existing TinyTuya dependency and MQTT client. Secrets are read only from
environment variables and are never logged or exposed through diagnostics.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Dict, Mapping, Optional

import tinytuya

from backend import app as app_module

STATE_TOPIC = "condo/electricity/state"
AVAILABILITY_TOPIC = "condo/electricity/availability"
DISCOVERY_PREFIX = "homeassistant"
POLL_INTERVAL_SEC = 30
POLL_TIMEOUT_SEC = 5
MAX_ATTEMPTS = 2
MAPPING_VERIFIED = False

_DPS_MAPPING = {
    "total_energy": (17, 0.01),
    "current": (18, 0.001),
    "power": (19, 0.1),
    "voltage": (20, 0.1),
}

_stop_event = threading.Event()
_state_lock = threading.RLock()


def _safe_error(exc: BaseException) -> str:
    """Return only a non-sensitive exception class name."""
    return type(exc).__name__


def _configuration() -> Optional[Dict[str, str]]:
    device_id = os.getenv("TUYA_METER_DEVICE_ID", "").strip()
    ip = os.getenv("TUYA_METER_IP", "").strip()
    local_key = os.getenv("TUYA_METER_LOCAL_KEY", "").strip()
    version = os.getenv("TUYA_METER_VERSION", "3.5").strip() or "3.5"
    if not device_id or not ip or not local_key:
        return None
    return {"device_id": device_id, "ip": ip, "local_key": local_key, "version": version}


def _number(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "unknown", "unavailable"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dps_value(dps: Mapping[Any, Any], dp: int) -> Any:
    return dps.get(str(dp), dps.get(dp))


def scale_dps(dps: Mapping[Any, Any]) -> Dict[str, Optional[float]]:
    """Apply the provisional production mapping exactly as specified."""
    result: Dict[str, Optional[float]] = {}
    for metric, (dp, scale) in _DPS_MAPPING.items():
        raw = _number(_dps_value(dps, dp))
        result[metric] = round(raw * scale, 6) if raw is not None else None
    return result


def _read_once(config: Mapping[str, str]) -> Dict[str, Any]:
    started = time.monotonic()
    device = tinytuya.OutletDevice(
        config["device_id"],
        config["ip"],
        config["local_key"],
        version=float(config["version"]),
    )
    device.set_version(float(config["version"]))
    device.set_socketTimeout(POLL_TIMEOUT_SEC)
    response = device.status()
    latency = round((time.monotonic() - started) * 1000, 1)
    if not isinstance(response, Mapping):
        raise RuntimeError("invalid_response")
    dps = response.get("dps")
    if not isinstance(dps, Mapping):
        raise RuntimeError("missing_dps")
    values = scale_dps(dps)
    if not any(value is not None for value in values.values()):
        raise RuntimeError("empty_dps")
    return {**values, "poll_latency_ms": latency}


def _publish(topic: str, payload: str, *, retain: bool) -> bool:
    if not bool(app_module.state.get("mqtt_connected")):
        return False
    try:
        info = app_module.mqttc.publish(topic, payload, qos=0, retain=retain)
        return int(getattr(info, "rc", 0)) == 0
    except Exception:
        return False


def _device_block() -> Dict[str, Any]:
    return {
        "identifiers": ["pj1103_electricity_meter"],
        "name": "PJ-1103 Electricity Meter",
        "manufacturer": "Tuya",
        "model": "PJ-1103",
    }


def _discovery_payloads() -> Dict[str, Dict[str, Any]]:
    base = {
        "state_topic": STATE_TOPIC,
        "availability_topic": AVAILABILITY_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
        "device": _device_block(),
    }
    definitions = {
        "voltage": ("Voltage", "voltage", "measurement", "V"),
        "current": ("Current", "current", "measurement", "A"),
        "power": ("Power", "power", "measurement", "W"),
        "total_energy": ("Total Energy", "energy", "total_increasing", "kWh"),
    }
    result: Dict[str, Dict[str, Any]] = {}
    for key, (name, device_class, state_class, unit) in definitions.items():
        result[f"{DISCOVERY_PREFIX}/sensor/pj1103_{key}/config"] = {
            **base,
            "name": name,
            "unique_id": f"pj1103_{key}",
            "device_class": device_class,
            "state_class": state_class,
            "unit_of_measurement": unit,
            "value_template": f"{{{{ value_json.{key} }}}}",
        }
    return result


def publish_discovery() -> None:
    for topic, payload in _discovery_payloads().items():
        _publish(topic, json.dumps(payload, separators=(",", ":")), retain=True)


def _store_state(payload: Dict[str, Any]) -> None:
    with _state_lock:
        app_module.state["electricity_local_state"] = dict(payload)
    try:
        from backend import electricity_provider

        electricity_provider.invalidate_cache()
    except Exception:
        pass


def local_state() -> Dict[str, Any]:
    with _state_lock:
        value = app_module.state.get("electricity_local_state")
        return dict(value) if isinstance(value, Mapping) else {}


def _success_payload(values: Mapping[str, Any]) -> Dict[str, Any]:
    now = int(time.time())
    return {
        "online": True,
        "voltage": values.get("voltage"),
        "current": values.get("current"),
        "power": values.get("power"),
        "total_energy": values.get("total_energy"),
        "energy_today": None,
        "energy_month": None,
        "frequency": None,
        "power_factor": None,
        "mapping_verified": MAPPING_VERIFIED,
        "source": "tuya_local",
        "ts": now,
        "last_success": now,
        "poll_latency_ms": values.get("poll_latency_ms"),
        "last_error": None,
    }


def _failure_payload(error: str, previous: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        **dict(previous),
        "online": False,
        "mapping_verified": MAPPING_VERIFIED,
        "source": "tuya_local",
        "ts": int(time.time()),
        "last_error": error,
    }


def poll_once() -> Dict[str, Any]:
    config = _configuration()
    previous = local_state()
    if config is None:
        payload = _failure_payload("not_configured", previous)
        _store_state(payload)
        _publish(AVAILABILITY_TOPIC, "offline", retain=True)
        return payload

    last_error = "poll_failed"
    for attempt in range(MAX_ATTEMPTS):
        try:
            values = _read_once(config)
            payload = _success_payload(values)
            _store_state(payload)
            publish_discovery()
            _publish(STATE_TOPIC, json.dumps(payload, separators=(",", ":")), retain=True)
            _publish(AVAILABILITY_TOPIC, "online", retain=True)
            return payload
        except Exception as exc:
            last_error = _safe_error(exc)
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(0.25)

    payload = _failure_payload(last_error, previous)
    _store_state(payload)
    _publish(AVAILABILITY_TOPIC, "offline", retain=True)
    return payload


def _poll_worker() -> None:
    while not _stop_event.is_set():
        poll_once()
        _stop_event.wait(POLL_INTERVAL_SEC)


@app_module.app.on_event("startup")
def start_pj1103_bridge() -> None:
    if getattr(app_module, "_pj1103_bridge_started", False):
        return
    app_module._pj1103_bridge_started = True
    _stop_event.clear()
    thread = threading.Thread(target=_poll_worker, name="pj1103-electricity-meter", daemon=True)
    app_module._pj1103_bridge_thread = thread
    thread.start()


@app_module.app.on_event("shutdown")
def stop_pj1103_bridge() -> None:
    _stop_event.set()
