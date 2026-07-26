"""Bounded, authenticated LG webOS controls with explicit capabilities."""
from __future__ import annotations

import copy
import hashlib
import os
import re
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional

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
WOL_STATE_LOCK = threading.Lock()
WOL_RECONNECT_ATTEMPTS = min(6, max(1, int(os.getenv("LG_TV_WOL_RECONNECT_ATTEMPTS", "4"))))
WOL_RECONNECT_TIMEOUT_SEC = min(5.0, max(0.5, float(os.getenv("LG_TV_WOL_RECONNECT_TIMEOUT_SEC", "2"))))
WOL_BACKOFF_SEC = (0.5, 1.0, 2.0, 4.0, 4.0, 4.0)
_WOL_RUNTIME: Dict[str, Any] = {
    "last_wol_sent_at": None,
    "reconnect_attempts": 0,
    "last_wol_result": "not_sent",
}

DIRECT_COMMANDS = {
    "volume_up", "volume_down", "mute", "unmute", "set_volume",
    "set_input", "launch_app", "up", "down", "left", "right", "ok",
    "back", "home", "play", "pause", "stop", "power_off",
}
ENUMERATION_LOCK = threading.Lock()
ENUMERATION_CACHE: Dict[str, Dict[str, str]] = {"input": {}, "app": {}}


class TvCommand(BaseModel):
    command: str
    value: Any = None


def capabilities(*, enumerate_live: bool = True) -> Dict[str, Any]:
    supported = sorted(DIRECT_COMMANDS | {"power_status"})
    if _valid_mac(_configured_mac()):
        supported.append("power_on")
    result = {
        "supported": sorted(supported),
        "unsupported": [] if "power_on" in supported else ["power_on"],
        "power_on": {"supported": "power_on" in supported, "reason": None if "power_on" in supported else "mac_not_configured"},
    }
    result.update(_enumerated_options() if enumerate_live else {
        "inputs": [], "applications": [], "enumeration_available": False,
        "enumeration_reason": "not_requested",
    })
    return result


def _valid_mac(value: str) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", value):
        return False
    raw = bytes.fromhex(re.sub(r"[:-]", "", value))
    return raw not in {b"\x00" * 6, b"\xff" * 6} and not bool(raw[0] & 1)


def _configured_mac() -> str:
    # LG_TV_MAC belongs in the protected service environment. TV_MAC remains a
    # backwards-compatible process value for existing installations and tests.
    return (os.getenv("LG_TV_MAC") or os.getenv("TV_MAC") or TV_MAC or "").strip()


def _magic_packet(mac: str) -> bytes:
    if not _valid_mac(mac):
        raise ValueError("mac_not_configured")
    raw = bytes.fromhex(re.sub(r"[:-]", "", mac))
    return b"\xff" * 6 + raw * 16


def _set_wol_runtime(**updates: Any) -> None:
    with WOL_STATE_LOCK:
        _WOL_RUNTIME.update(updates)


def wol_diagnostics() -> Dict[str, Any]:
    with WOL_STATE_LOCK:
        runtime = copy.deepcopy(_WOL_RUNTIME)
    return {
        "wol_configured": _valid_mac(_configured_mac()),
        "last_wol_sent_at": runtime["last_wol_sent_at"],
        "reconnect_attempts": runtime["reconnect_attempts"],
        "last_wol_result": runtime["last_wol_result"],
    }


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


def _open_client(key: str, *, timeout: float = COMMAND_TIMEOUT_SEC) -> Any:
    from pywebostv.connection import WebOSClient

    client = WebOSClient(status.TV_IP, secure=True)
    try:
        client.sock.settimeout(timeout)
        client.connect()
        registered = False
        for value in client.register({"client_key": key}, timeout=timeout):
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
    packet = _magic_packet(_configured_mac())
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(COMMAND_TIMEOUT_SEC)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, ("255.255.255.255", 9))
    finally:
        sock.close()


def _reconnect_after_wol(key: str | None) -> tuple[Dict[str, Any], bool, int]:
    if not key:
        return status._public_status(), False, 0
    for attempt in range(1, WOL_RECONNECT_ATTEMPTS + 1):
        delay = WOL_BACKOFF_SEC[min(attempt - 1, len(WOL_BACKOFF_SEC) - 1)]
        if delay:
            time.sleep(delay)
        _set_wol_runtime(reconnect_attempts=attempt)
        client = None
        try:
            client = _open_client(key, timeout=WOL_RECONNECT_TIMEOUT_SEC)
            now = int(time.time())
            with status.STATE_LOCK:
                status._CACHE.update({
                    "online": True,
                    "power_state": "on",
                    "connection_state": "connected",
                    "paired": True,
                    "pairing_required": False,
                    "last_update_ts": now,
                    "last_success_ts": now,
                    "last_error": None,
                    "consecutive_failures": 0,
                    "stale": False,
                })
            status._persist()
            return status._public_status(), True, attempt
        except Exception:
            continue
        finally:
            if client is not None:
                _close_client(client)
    return status._public_status(), False, WOL_RECONNECT_ATTEMPTS


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


def _option_token(kind: str, identifier: str) -> str:
    digest = hashlib.sha256(f"{kind}:{identifier}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


def _safe_options(kind: str, items: list[Any], keys: tuple[str, ...]) -> list[Dict[str, str]]:
    options: list[Dict[str, str]] = []
    mapping: Dict[str, str] = {}
    for item in items:
        data = _data(item)
        identifier = str(data.get(keys[0]) or "").strip()
        label = str(data.get("label") or data.get("title") or data.get("name") or "").strip()
        if not identifier or not label:
            continue
        token = _option_token(kind, identifier)
        mapping[token] = identifier
        options.append({"id": token, "label": label[:100]})
    with ENUMERATION_LOCK:
        ENUMERATION_CACHE[kind] = mapping
    return options


def _enumerated_options() -> Dict[str, Any]:
    key, _ = pairing._current_key()
    if not key:
        return {"inputs": [], "applications": [], "enumeration_available": False, "enumeration_reason": "pairing_required"}
    client = None
    try:
        from pywebostv.controls import ApplicationControl, SourceControl

        client = _open_client(key)
        inputs = _safe_options("input", SourceControl(client).list_sources(timeout=COMMAND_TIMEOUT_SEC), ("id",))
        applications = _safe_options("app", ApplicationControl(client).list_apps(timeout=COMMAND_TIMEOUT_SEC), ("id",))
        return {"inputs": inputs, "applications": applications, "enumeration_available": True, "enumeration_reason": None}
    except Exception:
        return {"inputs": [], "applications": [], "enumeration_available": False, "enumeration_reason": "live_enumeration_unavailable"}
    finally:
        if client is not None:
            _close_client(client)


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
        with ENUMERATION_LOCK:
            requested = ENUMERATION_CACHE["input"].get(requested, "")
        if not requested:
            raise LookupError("input_not_supported")
        sources = SourceControl(client).list_sources(timeout=COMMAND_TIMEOUT_SEC)
        selected = _find_item(sources, requested, ("id", "inputId", "label", "name"))
        if selected is None:
            raise LookupError("input_not_supported")
        SourceControl(client).set_source(selected, timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "launch_app":
        requested = str(value or "").strip()
        with ENUMERATION_LOCK:
            requested = ENUMERATION_CACHE["app"].get(requested, "")
        if not requested:
            raise LookupError("application_not_supported")
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


def _install_wol_diagnostics() -> None:
    diagnostics_route = next(
        (
            route for route in app.routes
            if getattr(route, "path", None) == "/api/lg-tv/status/diagnostics"
            and "GET" in set(getattr(route, "methods", set()) or set())
        ),
        None,
    )
    if diagnostics_route is None:
        return
    original: Callable[[], Dict[str, Any]] = diagnostics_route.endpoint

    def diagnostics_with_wol() -> Dict[str, Any]:
        return {**original(), **wol_diagnostics()}

    diagnostics_route.endpoint = diagnostics_with_wol
    if getattr(diagnostics_route, "dependant", None) is not None:
        diagnostics_route.dependant.call = diagnostics_with_wol


@app.get("/api/lg-tv/capabilities")
def lg_tv_capabilities() -> Dict[str, Any]:
    return capabilities()


@app.post("/api/lg-tv/command")
def lg_tv_command(payload: TvCommand = Body(...)):
    command, value = _normalize(payload.command, payload.value)
    if command == "power_on":
        if not _valid_mac(_configured_mac()):
            return JSONResponse({"detail": "unsupported_capability", "capability": "power_on", "reason": "mac_not_configured"}, status_code=422)
        if not COMMAND_LOCK.acquire(blocking=False):
            return JSONResponse({"detail": "command_busy"}, status_code=409)
        try:
            key, _ = pairing._current_key()
            if status._reachable(3001, timeout=COMMAND_TIMEOUT_SEC) or status._reachable(3000, timeout=COMMAND_TIMEOUT_SEC):
                state, refreshed = _refresh(key) if key else (status._public_status(), False)
                _set_wol_runtime(reconnect_attempts=0, last_wol_result="already_online")
                return {
                    "ok": True,
                    "command": command,
                    "wol_sent": False,
                    "reconnect_attempts": 0,
                    "state_refreshed": refreshed,
                    "state": state,
                }
            try:
                _wake()
            except (TimeoutError, socket.timeout):
                _set_wol_runtime(reconnect_attempts=0, last_wol_result="send_timeout")
                return JSONResponse({"detail": "wol_timeout"}, status_code=504)
            except OSError:
                _set_wol_runtime(reconnect_attempts=0, last_wol_result="send_failed")
                return JSONResponse({"detail": "wol_failed"}, status_code=502)
            sent_at = int(time.time())
            _set_wol_runtime(
                last_wol_sent_at=sent_at,
                reconnect_attempts=0,
                last_wol_result="reconnect_pending",
            )
            with status.STATE_LOCK:
                status._CACHE.update({
                    "power_state": "starting",
                    "connection_state": "connecting",
                    "last_command": command,
                    "last_command_success": True,
                    "last_command_completed_ts": sent_at,
                })
            status._RUNTIME["wake_grace_until"] = time.time() + status.WAKE_GRACE_SEC
            state, reconnected, attempts = _reconnect_after_wol(key)
            result = "reconnected" if reconnected else "sent_reconnect_pending"
            _set_wol_runtime(reconnect_attempts=attempts, last_wol_result=result)
            return {
                "ok": True,
                "command": command,
                "wol_sent": True,
                "reconnect_attempts": attempts,
                "state_refreshed": reconnected,
                "state": state,
            }
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


_install_wol_diagnostics()
