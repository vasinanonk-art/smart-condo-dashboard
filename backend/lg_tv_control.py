"""Bounded, authenticated LG webOS controls with explicit capabilities."""
from __future__ import annotations

import copy
import os
import re
import socket
import threading
import time
from typing import Any, Dict, Optional

from fastapi import Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import app as app_module
from backend import lg_tv_pairing as pairing
from backend import lg_tv_status as status

app = app_module.app
COMMAND_TIMEOUT_SEC = min(10.0, max(1.0, float(os.getenv("LG_TV_COMMAND_TIMEOUT_SEC", "5"))))
TV_MAC = (os.getenv("LG_TV_MAC") or os.getenv("TV_MAC") or "").strip()
COMMAND_LOCK = threading.Lock()

DIRECT_COMMANDS = {
    "volume_up", "volume_down", "mute", "unmute", "set_volume",
    "set_input", "launch_app", "up", "down", "left", "right", "ok",
    "back", "home", "play", "pause", "stop", "power_off",
}
LEGACY_INPUTS = {f"hdmi{number}": f"hdmi{number}" for number in range(1, 5)}
LEGACY_APPS = {
    "netflix": "netflix", "youtube": "youtube", "disney": "disney",
    "prime": "amazon", "appletv": "apple", "livetv": "livetv",
    "browser": "browser", "viu": "viu", "hbo": "hbo",
}


class TvCommand(BaseModel):
    command: str
    value: Any = None


def capabilities() -> Dict[str, Any]:
    supported = sorted(DIRECT_COMMANDS | {"power_status"})
    if _valid_mac(TV_MAC):
        supported.append("power_on")
    return {
        "supported": sorted(supported),
        "unsupported": [] if "power_on" in supported else ["power_on"],
        "power_on": {"supported": "power_on" in supported, "reason": None if "power_on" in supported else "mac_not_configured"},
    }


def _valid_mac(value: str) -> bool:
    return bool(re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", value))


def _close_client(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass
    thread = getattr(client, "_th", None)
    if thread is not None and thread.is_alive():
        try:
            thread.join(timeout=0.5)
        except Exception:
            pass


def _open_client(key: str) -> Any:
    from pywebostv.connection import WebOSClient

    client = WebOSClient(status.TV_IP, secure=True)
    try:
        client.sock.settimeout(COMMAND_TIMEOUT_SEC)
        client.connect()
        registered = False
        for value in client.register({"client_key": key}, timeout=COMMAND_TIMEOUT_SEC):
            if value == WebOSClient.REGISTERED:
                registered = True
                break
            if value == WebOSClient.PROMPTED:
                raise PermissionError("pairing_required")
        if not registered:
            raise PermissionError("pairing_required")
        return client
    except Exception:
        _close_client(client)
        raise


def _wake() -> None:
    if not _valid_mac(TV_MAC):
        raise ValueError("mac_not_configured")
    raw = bytes.fromhex(re.sub(r"[:-]", "", TV_MAC))
    packet = b"\xff" * 6 + raw * 16
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(COMMAND_TIMEOUT_SEC)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, ("255.255.255.255", 9))
    finally:
        sock.close()


def _data(item: Any) -> Dict[str, Any]:
    value = getattr(item, "data", item)
    return value if isinstance(value, dict) else {}


def _find_item(items: list[Any], requested: str, keys: tuple[str, ...], token: Optional[str] = None) -> Any:
    needle = requested.strip().lower()
    normalized = re.sub(r"[^a-z0-9]", "", needle)
    for item in items:
        values = [str(_data(item).get(key) or "").strip().lower() for key in keys]
        normalized_values = [re.sub(r"[^a-z0-9]", "", value) for value in values]
        if needle in values or normalized in normalized_values or (token and any(token in value for value in values)):
            return item
    return None


def _pointer_command(client: Any, command: str) -> None:
    from pywebostv.controls import InputControl

    control = InputControl(client)
    pointer = None
    try:
        response = control.request(
            "ssap://com.webos.service.networkinput/getPointerInputSocket",
            None,
            block=True,
            timeout=COMMAND_TIMEOUT_SEC,
        )
        path = (response.get("payload") or {}).get("socketPath")
        if not path:
            raise RuntimeError("pointer_unavailable")
        pointer = control.ws_class(path)
        pointer.sock.settimeout(COMMAND_TIMEOUT_SEC)
        pointer.connect()
        control.mouse_ws = pointer
        getattr(control, command)()
    finally:
        if pointer is not None:
            _close_client(pointer)


def _execute(client: Any, command: str, value: Any) -> None:
    from pywebostv.controls import ApplicationControl, MediaControl, SourceControl, SystemControl

    if command in {"up", "down", "left", "right", "ok", "back", "home"}:
        _pointer_command(client, command)
        return
    if command in {"volume_up", "volume_down", "play", "pause", "stop"}:
        getattr(MediaControl(client), command)(timeout=COMMAND_TIMEOUT_SEC)
        return
    if command in {"mute", "unmute"}:
        MediaControl(client).mute(command == "mute", timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "set_volume":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("volume_out_of_range")
        MediaControl(client).set_volume(value, timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "power_off":
        SystemControl(client).power_off(timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "set_input":
        requested = str(value or "").strip()
        sources = SourceControl(client).list_sources(timeout=COMMAND_TIMEOUT_SEC)
        selected = _find_item(sources, requested, ("id", "inputId", "label", "name"))
        if selected is None:
            raise LookupError("input_not_supported")
        SourceControl(client).set_source(selected, timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "launch_app":
        requested = str(value or "").strip()
        apps = ApplicationControl(client).list_apps(timeout=COMMAND_TIMEOUT_SEC)
        selected = _find_item(apps, requested, ("id", "title", "name"), requested.lower())
        if selected is None:
            raise LookupError("application_not_supported")
        ApplicationControl(client).launch(selected, timeout=COMMAND_TIMEOUT_SEC)
        return
    raise LookupError("unsupported_command")


def _normalize(command: str, value: Any) -> tuple[str, Any]:
    command = command.strip().lower()
    if command == "home_key":
        return "home", value
    if command in LEGACY_INPUTS:
        return "set_input", LEGACY_INPUTS[command]
    if command in LEGACY_APPS:
        return "launch_app", LEGACY_APPS[command]
    return command, value


def _refresh(key: str) -> tuple[Dict[str, Any], bool]:
    try:
        live = status._collect_live(key, timeout=COMMAND_TIMEOUT_SEC)
        with status.STATE_LOCK:
            status._CACHE.update(live)
        status._persist()
        return status._public_status(), True
    except Exception:
        return status._public_status(), False


@app.get("/api/lg-tv/capabilities")
def lg_tv_capabilities() -> Dict[str, Any]:
    return capabilities()


@app.post("/api/lg-tv/command")
def lg_tv_command(payload: TvCommand = Body(...)):
    command, value = _normalize(payload.command, payload.value)
    if command == "power_on":
        if not _valid_mac(TV_MAC):
            return JSONResponse({"detail": "unsupported_capability", "capability": "power_on", "reason": "mac_not_configured"}, status_code=422)
        if not COMMAND_LOCK.acquire(blocking=False):
            return JSONResponse({"detail": "command_busy"}, status_code=409)
        try:
            _wake()
            with status.STATE_LOCK:
                status._CACHE.update({"power_state": "starting", "connection_state": "connecting", "last_command": command, "last_command_success": True})
            return {"ok": True, "command": command, "state_refreshed": False, "state": status._public_status()}
        finally:
            COMMAND_LOCK.release()
    if command not in DIRECT_COMMANDS:
        return JSONResponse({"detail": "unsupported_command", "command": command}, status_code=422)
    key, _ = pairing._current_key()
    if not key:
        return JSONResponse({"detail": "pairing_required"}, status_code=409)
    if not (status._reachable(3001, timeout=COMMAND_TIMEOUT_SEC) or status._reachable(3000, timeout=COMMAND_TIMEOUT_SEC)):
        return JSONResponse({"detail": "tv_unreachable"}, status_code=503)
    if not COMMAND_LOCK.acquire(blocking=False):
        return JSONResponse({"detail": "command_busy"}, status_code=409)
    started = time.monotonic()
    client = None
    try:
        client = _open_client(key)
        _execute(client, command, value)
        _close_client(client)
        client = None
        state, refreshed = _refresh(key)
        with status.STATE_LOCK:
            status._CACHE.update({
                "last_command": command, "last_command_success": True,
                "last_command_latency_ms": int((time.monotonic() - started) * 1000),
                "last_command_completed_ts": int(time.time()),
            })
            if command == "power_off" and not refreshed:
                status._CACHE.update({"power_state": "standby", "connection_state": "standby", "online": False})
            state = copy.deepcopy(status._public_status())
        return {"ok": True, "command": command, "state_refreshed": refreshed, "state": state}
    except (ValueError, LookupError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except (TimeoutError, socket.timeout):
        return JSONResponse({"detail": "command_timeout"}, status_code=504)
    except Exception as exc:
        elapsed = time.monotonic() - started
        if status._safe_code(exc) == "status_timeout" or elapsed >= COMMAND_TIMEOUT_SEC * 0.9:
            return JSONResponse({"detail": "command_timeout"}, status_code=504)
        return JSONResponse({"detail": "command_failed"}, status_code=502)
    finally:
        if client is not None:
            _close_client(client)
        COMMAND_LOCK.release()
