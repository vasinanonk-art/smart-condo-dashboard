"""Central LG webOS status provider for EPIC 09.

One background worker owns live telemetry. Existing lgtv-mqtt remains the command
transport. Client keys, raw websocket payloads and exception text never leave this
module.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import logging
import os
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import lg_tv_pairing as pairing

app = app_module.app
TV_IP = pairing.TV_IP
STATE_PATH = Path("/root/.smart-condo-dashboard/state/lg_tv_status.json")
POLL_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()
WAKE_GRACE_SEC = 60
STALE_SEC = 45
BACKOFF = (30, 60, 120, 300)
log = logging.getLogger("smart-condo.lg")

APP_NAMES = {
    "netflix": "Netflix", "youtube": "YouTube", "disney": "Disney+",
    "amazon": "Prime Video", "prime": "Prime Video", "appletv": "Apple TV",
    "hbo": "HBO Max", "max": "Max", "viu": "Viu", "browser": "Browser",
    "com.webos.app.browser": "Browser", "com.webos.app.livetv": "Live TV",
    "com.webos.app.home": "Home",
}

_CACHE: Dict[str, Any] = {
    "tv_ip": TV_IP, "online": False, "power_state": "unknown",
    "connection_state": "starting", "paired": False, "pairing_required": False,
    "service_active": False, "current_app": {"id": None, "name": None},
    "current_input": {"id": None, "name": None},
    "audio": {"volume": None, "muted": None, "sound_output": None},
    "device": {"name": None, "model": None, "product_name": None,
               "software_version": None, "webos_version": None, "firmware_version": None},
    "last_update_ts": None, "last_success_ts": None, "last_attempt_ts": None,
    "last_command": None, "last_command_success": None, "last_command_latency_ms": None,
    "last_command_received_ts": None, "last_command_completed_ts": None,
    "last_error": None, "reconnect_count": 0, "consecutive_failures": 0,
    "key_source": "none", "stale": True,
}
_RUNTIME: Dict[str, Any] = {
    "worker_active": False, "refresh_running": False, "next_poll_ts": None,
    "last_poll_started": None, "last_poll_completed": None, "last_poll_duration_ms": None,
    "last_poll_result": None, "last_connection_error": None, "active_connection_count": 0,
    "transition": None, "intentional_power_off_until": 0, "wake_grace_until": 0,
}
_WAKE = threading.Event()
_STOP = threading.Event()
_WORKER: Optional[threading.Thread] = None


def _safe_code(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, TimeoutError) or "timeout" in text:
        return "status_timeout"
    if "401" in text or "403" in text or "register" in text or "key" in text:
        return "key_rejected"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError)) or "closed" in text:
        return "websocket_connect_failed"
    if isinstance(exc, OSError):
        return "tv_unreachable"
    if isinstance(exc, ImportError):
        return "dependency_missing"
    return "status_read_failed"


def _validate_ip() -> None:
    address = ipaddress.ip_address(TV_IP)
    if not address.is_private:
        raise ValueError("invalid_tv_ip")


def _service_active() -> bool:
    return pairing._service_active()


def _reachable(port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((TV_IP, port), timeout=timeout):
            return True
    except OSError:
        return False


def _friendly_app(app_id: Optional[str]) -> Optional[str]:
    if not app_id:
        return None
    low = app_id.lower()
    for token, label in APP_NAMES.items():
        if token in low:
            return label
    if "hdmi" in low:
        digits = "".join(ch for ch in low if ch.isdigit())
        return f"HDMI {digits}" if digits else "External Input"
    return app_id


def _input_name(value: Any) -> tuple[Optional[str], Optional[str]]:
    if isinstance(value, Mapping):
        raw = str(value.get("id") or value.get("inputId") or value.get("label") or value.get("name") or "").strip()
    else:
        raw = str(value or "").strip()
    if not raw:
        return None, None
    low = raw.lower()
    if "hdmi" in low:
        digits = "".join(ch for ch in raw if ch.isdigit())
        return raw, f"HDMI {digits}" if digits else "HDMI"
    if "live" in low or "tv" == low:
        return raw, "Live TV"
    if "component" in low:
        return raw, "Component"
    if "av" in low:
        return raw, "AV"
    return raw, raw


def _call(control: Any, names: tuple[str, ...], default: Any = None) -> Any:
    for name in names:
        method = getattr(control, name, None)
        if callable(method):
            try:
                return method()
            except Exception:
                continue
    return default


def _collect_live(key: str) -> Dict[str, Any]:
    from pywebostv.connection import WebOSClient
    from pywebostv.controls import ApplicationControl, MediaControl, SourceControl, SystemControl

    store = {"client_key": key}
    client = WebOSClient(TV_IP, secure=True)
    client.connect()
    registered = False
    for value in client.register(store):
        if value == WebOSClient.REGISTERED:
            registered = True
            break
        if value == WebOSClient.PROMPTED:
            raise PermissionError("pairing_required")
    if not registered:
        raise PermissionError("pairing_required")

    app_control = ApplicationControl(client)
    media = MediaControl(client)
    source = SourceControl(client)
    system = SystemControl(client)

    current = _call(app_control, ("get_current", "get_current_app"), {}) or {}
    app_id = str(current.get("appId") or current.get("id") or current.get("app_id") or "").strip() if isinstance(current, Mapping) else str(current or "").strip()
    source_value = _call(source, ("get_current", "get_source", "get_current_source"), {})
    input_id, input_name = _input_name(source_value)
    app_name = _friendly_app(app_id)
    if app_name and not app_name.startswith("HDMI") and app_name not in {"Live TV", "External Input"}:
        input_id, input_name = "webos", "App / webOS"
    elif app_name and app_name.startswith("HDMI"):
        input_id, input_name = app_id, app_name

    volume_raw = _call(media, ("get_volume",), {}) or {}
    volume = None
    muted = None
    sound_output = None
    if isinstance(volume_raw, Mapping):
        raw_volume = volume_raw.get("volume")
        try: volume = int(raw_volume) if raw_volume is not None else None
        except (TypeError, ValueError): volume = None
        raw_mute = volume_raw.get("muted", volume_raw.get("mute"))
        muted = bool(raw_mute) if raw_mute is not None else None
        sound_output = volume_raw.get("soundOutput") or volume_raw.get("sound_output")

    info = _call(system, ("info", "get_info", "get_system_info"), {}) or {}
    if not isinstance(info, Mapping): info = {}
    now = int(time.time())
    return {
        "online": True, "power_state": "on", "connection_state": "connected",
        "paired": True, "pairing_required": False,
        "current_app": {"id": app_id or None, "name": app_name},
        "current_input": {"id": input_id, "name": input_name},
        "audio": {"volume": volume, "muted": muted, "sound_output": sound_output},
        "device": {
            "name": info.get("deviceName") or info.get("name"),
            "model": info.get("modelName") or info.get("model"),
            "product_name": info.get("productName"),
            "software_version": info.get("softwareVersion") or info.get("swVersion"),
            "webos_version": info.get("webOSVersion") or info.get("webosVersion"),
            "firmware_version": info.get("firmwareVersion") or info.get("firmware"),
        },
        "last_update_ts": now, "last_success_ts": now, "last_error": None,
        "consecutive_failures": 0, "stale": False,
    }


def _persist() -> None:
    safe = {k: v for k, v in _CACHE.items() if k not in {"tv_ip", "key_source"}}
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE_PATH.parent, 0o700)
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, STATE_PATH)


def _load_persisted() -> None:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, Mapping):
            _CACHE.update(copy.deepcopy(data)); _CACHE["stale"] = True
    except Exception:
        pass


def _transition(name: str) -> None:
    if name == _RUNTIME.get("transition"): return
    _RUNTIME["transition"] = name
    log.info(name)


def _poll_once() -> Dict[str, Any]:
    if not POLL_LOCK.acquire(blocking=False):
        return {"started": False, "state": "refresh_already_running"}
    started = time.monotonic(); now = int(time.time())
    _RUNTIME.update({"refresh_running": True, "last_poll_started": now})
    with STATE_LOCK:
        _CACHE["last_attempt_ts"] = now; _CACHE["service_active"] = _service_active()
    try:
        _validate_ip()
        key, source = pairing._current_key()
        with STATE_LOCK: _CACHE["key_source"] = source
        if not key:
            with STATE_LOCK:
                _CACHE.update({"online": False, "paired": False, "pairing_required": True,
                               "connection_state": "key_missing", "power_state": "unknown",
                               "last_error": "key_missing", "stale": True})
            _transition("LG_PAIRING_REQUIRED")
            result = "key_missing"
        elif not (_reachable(3001) or _reachable(3000)):
            intentional = time.time() < _RUNTIME.get("intentional_power_off_until", 0)
            wake = time.time() < _RUNTIME.get("wake_grace_until", 0)
            with STATE_LOCK:
                failures = int(_CACHE.get("consecutive_failures") or 0) + 1
                _CACHE.update({"online": False, "power_state": "starting" if wake else "standby" if intentional else "offline",
                               "connection_state": "connecting" if wake else "standby" if intentional else "unreachable",
                               "last_error": None if intentional or wake else "tv_unreachable",
                               "consecutive_failures": failures, "stale": True})
            _transition("LG_STANDBY" if intentional else "LG_RECONNECTING" if wake else "LG_OFFLINE")
            result = "standby" if intentional else "tv_unreachable"
        else:
            live = _collect_live(key)
            with STATE_LOCK:
                recovered = int(_CACHE.get("consecutive_failures") or 0) > 0
                _CACHE.update(live); _CACHE["key_source"] = source; _CACHE["service_active"] = _service_active()
                if recovered: _CACHE["reconnect_count"] = int(_CACHE.get("reconnect_count") or 0) + 1
            pairing._RUNTIME.update({"last_connection_error": None, "last_error": None,
                                     "last_connection_success": int(time.time())})
            _transition("LG_STATUS_RECOVERED" if recovered else "LG_CONNECTED")
            result = "connected"
            _persist()
    except PermissionError:
        with STATE_LOCK:
            _CACHE.update({"online": False, "paired": False, "pairing_required": True,
                           "connection_state": "pairing_required", "last_error": "key_rejected", "stale": True})
        _transition("LG_PAIRING_REQUIRED"); result = "pairing_required"
    except Exception as exc:
        code = _safe_code(exc)
        with STATE_LOCK:
            _CACHE.update({"online": False, "connection_state": "websocket_error", "last_error": code,
                           "consecutive_failures": int(_CACHE.get("consecutive_failures") or 0) + 1, "stale": True})
        _RUNTIME["last_connection_error"] = code; _transition("LG_RECONNECTING"); result = code
    finally:
        completed = int(time.time())
        _RUNTIME.update({"refresh_running": False, "last_poll_completed": completed,
                         "last_poll_duration_ms": int((time.monotonic()-started)*1000), "last_poll_result": result})
        POLL_LOCK.release()
    return {"started": True, "state": result}


def _interval() -> int:
    state = _CACHE.get("connection_state")
    if state == "connected" and _CACHE.get("power_state") == "on": return 5
    if state in {"standby", "key_missing", "pairing_required"}: return 30
    failures = max(1, int(_CACHE.get("consecutive_failures") or 1))
    return BACKOFF[min(failures - 1, len(BACKOFF)-1)]


def _worker() -> None:
    _RUNTIME["worker_active"] = True; _transition("LG_STATUS_WORKER_STARTED")
    while not _STOP.is_set():
        _poll_once()
        wait = _interval(); _RUNTIME["next_poll_ts"] = int(time.time()) + wait
        _WAKE.wait(wait); _WAKE.clear()
    _RUNTIME["worker_active"] = False


def _public_status() -> Dict[str, Any]:
    with STATE_LOCK:
        payload = copy.deepcopy(_CACHE)
    now = int(time.time()); last = payload.get("last_update_ts") or payload.get("last_success_ts")
    payload["data_age_sec"] = max(0, now-int(last)) if last else None
    payload["stale"] = bool(payload.get("stale") or (payload["data_age_sec"] is not None and payload["data_age_sec"] > STALE_SEC))
    return payload


@app.on_event("startup")
def start_lg_status_worker() -> None:
    global _WORKER
    _load_persisted()
    if _WORKER and _WORKER.is_alive(): return
    _STOP.clear(); _WORKER = threading.Thread(target=_worker, name="lg-tv-status", daemon=True); _WORKER.start()


@app.get("/api/lg-tv/status")
def lg_status() -> Dict[str, Any]:
    return _public_status()


@app.post("/api/lg-tv/status/refresh")
def lg_status_refresh(payload: Dict[str, Any] = Body(default={})):
    del payload
    if POLL_LOCK.locked():
        return JSONResponse({"detail": "refresh_already_running", "running": True}, status_code=409)
    _WAKE.set()
    return {"ok": True, "running": False, "state": "refresh_requested"}


@app.get("/api/lg-tv/status/diagnostics")
def lg_status_diagnostics() -> Dict[str, Any]:
    key, source = pairing._current_key(); now = int(time.time()); last = _CACHE.get("last_update_ts")
    return {
        "tv_reachable": _reachable(3000) or _reachable(3001), "port_3000_reachable": _reachable(3000),
        "port_3001_reachable": _reachable(3001), "secure_websocket": True,
        "paired": bool(_CACHE.get("paired")), "connection_state": _CACHE.get("connection_state"),
        "current_key_present": bool(key), "current_key_source": source, "service_active": _service_active(),
        "status_worker_active": bool(_RUNTIME.get("worker_active")), "last_poll_started": _RUNTIME.get("last_poll_started"),
        "last_poll_completed": _RUNTIME.get("last_poll_completed"), "last_poll_duration_ms": _RUNTIME.get("last_poll_duration_ms"),
        "last_poll_result": _RUNTIME.get("last_poll_result"), "last_connection_error": _RUNTIME.get("last_connection_error"),
        "reconnect_count": _CACHE.get("reconnect_count", 0), "consecutive_failures": _CACHE.get("consecutive_failures", 0),
        "next_poll_ts": _RUNTIME.get("next_poll_ts"), "active_connection_count": 1 if _RUNTIME.get("refresh_running") else 0,
        "status_cache_age_sec": max(0, now-int(last)) if last else None,
    }


@app.post("/api/lg-tv/pairing/test")
def lg_pairing_test(payload: Dict[str, Any] = Body(default={})):
    del payload
    key, _ = pairing._current_key()
    if not key: return {"result": "pairing_required"}
    if not (_reachable(3000) or _reachable(3001)): return {"result": "unreachable"}
    result = _poll_once()
    state = _CACHE.get("connection_state")
    if state == "connected":
        pairing._RUNTIME["last_connection_success"] = int(time.time())
        return {"result": "connected"}
    if state in {"pairing_required", "key_missing"}: return {"result": "pairing_required"}
    return {"result": "failed", "error": _CACHE.get("last_error") or result.get("state")}


@app.post("/api/lg-tv/pairing/forget")
def lg_pairing_forget(payload: Dict[str, Any] = Body(default={})):
    del payload
    key_path = pairing.KEY_PATH
    if not key_path.exists(): return {"ok": True, "state": "key_missing"}
    backup = key_path.with_name(f"{key_path.name}.forgotten-{int(time.time())}.bak")
    shutil.copy2(key_path, backup); os.chmod(backup, 0o600)
    key_path.unlink()
    with STATE_LOCK:
        _CACHE.update({"paired": False, "pairing_required": True, "connection_state": "key_missing",
                       "online": False, "last_error": "key_missing", "key_source": "none", "stale": True})
    _WAKE.set()
    return {"ok": True, "state": "key_missing", "backup_created": True}


def record_command(command: str, success: Optional[bool], latency_ms: Optional[int], error: Optional[str] = None) -> None:
    now = int(time.time())
    with STATE_LOCK:
        _CACHE.update({"last_command": command, "last_command_success": success,
                       "last_command_latency_ms": latency_ms, "last_command_completed_ts": now,
                       "last_error": error or _CACHE.get("last_error")})
    if command == "power_on":
        _RUNTIME["wake_grace_until"] = time.time() + WAKE_GRACE_SEC
    elif command == "power_off":
        _RUNTIME["intentional_power_off_until"] = time.time() + 90
    _WAKE.set()
