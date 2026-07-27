"""Bounded, authenticated LG webOS controls with explicit capabilities."""
from __future__ import annotations

import copy
import hashlib
import logging
import os
import re
import socket
import threading
import time
from typing import Any, Callable, Dict

from fastapi import BackgroundTasks, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import app as app_module
from backend import lg_tv_pairing as pairing
from backend import lg_tv_status as status

app = app_module.app
log = logging.getLogger(__name__)
COMMAND_TIMEOUT_SEC = min(10.0, max(1.0, float(os.getenv("LG_TV_COMMAND_TIMEOUT_SEC", "5"))))
TV_MAC = (os.getenv("LG_TV_MAC") or os.getenv("TV_MAC") or "").strip()
COMMAND_LOCK = threading.Lock()
CLIENT_STATE_LOCK = threading.Lock()
CLIENT_CONNECT_LOCK = threading.Lock()
CLIENT_IO_LOCK = threading.Lock()
WOL_STATE_LOCK = threading.Lock()
WOL_RECONNECT_ATTEMPTS = min(6, max(1, int(os.getenv("LG_TV_WOL_RECONNECT_ATTEMPTS", "4"))))
WOL_RECONNECT_TIMEOUT_SEC = min(5.0, max(0.5, float(os.getenv("LG_TV_WOL_RECONNECT_TIMEOUT_SEC", "2"))))
WOL_BACKOFF_SEC = (0.5, 1.0, 2.0, 4.0, 4.0, 4.0)
WOL_BROADCAST_ADDRESS = "192.168.1.255"
WOL_INTERFACES = ("wlx6c4cbcdb7033", "wlan0")
_WOL_RUNTIME: Dict[str, Any] = {
    "last_wol_sent_at": None,
    "reconnect_attempts": 0,
    "last_wol_result": "not_sent",
}

DIRECT_COMMANDS = {
    "volume_up", "volume_down", "mute", "unmute", "set_volume",
    "set_input", "launch_app", "up", "down", "left", "right", "ok",
    "back", "home", "play", "pause", "stop", "rewind", "fast_forward",
    "power_off",
}
ENUMERATION_LOCK = threading.Lock()
ENUMERATION_RAW: Dict[str, Dict[str, Any]] = {"input": {}, "app": {}}
INVENTORY_REFRESH_LOCK = threading.Lock()
INVENTORY_TTL_SEC = min(1800, max(30, int(os.getenv("LG_TV_INVENTORY_TTL_SEC", "300"))))
INVENTORY_RETRY_SEC = min(60, max(5, int(os.getenv("LG_TV_INVENTORY_RETRY_SEC", "15"))))
_CLIENT: Any = None
_CLIENT_KEY: str | None = None
_POINTER_CONTROL: Any = None
_INVENTORY: Dict[str, Any] = {
    "inputs": [],
    "applications": [],
    "inputs_available": False,
    "applications_available": False,
    "last_success_at": None,
    "last_attempt_at": None,
    "refreshing": False,
    "last_error": None,
}


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
    result.update(_inventory_snapshot())
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
    errors: list[OSError] = []
    sent = 0
    bind_to_device = getattr(socket, "SO_BINDTODEVICE", 25)
    for interface in WOL_INTERFACES:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(COMMAND_TIMEOUT_SEC)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, bind_to_device, interface.encode("ascii") + b"\0")
            sock.sendto(packet, (WOL_BROADCAST_ADDRESS, 9))
            sent += 1
        except OSError as exc:
            errors.append(exc)
        finally:
            sock.close()
    if not sent:
        raise errors[-1] if errors else OSError("wol_send_failed")


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


def _option_token(kind: str, identifier: str) -> str:
    digest = hashlib.sha256(f"{kind}:{identifier}".encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


def _safe_options(kind: str, items: list[Any], keys: tuple[str, ...]) -> list[Dict[str, str]]:
    options: list[Dict[str, str]] = []
    raw_mapping: Dict[str, Any] = {}
    for item in items:
        data = _data(item)
        identifier = str(data.get(keys[0]) or "").strip()
        label = str(data.get("label") or data.get("title") or data.get("name") or "").strip()
        if not identifier or not label:
            continue
        token = _option_token(kind, identifier)
        raw_mapping[token] = item
        options.append({"id": token, "label": label[:100]})
    with ENUMERATION_LOCK:
        ENUMERATION_RAW[kind] = raw_mapping
    return options


def _client_available(client: Any) -> bool:
    if client is None or bool(getattr(client, "terminated", False)):
        return False
    thread = getattr(client, "_th", None)
    if thread is not None and hasattr(thread, "is_alive") and not thread.is_alive():
        return False
    sock = getattr(client, "sock", None)
    return sock is None or getattr(sock, "connected", True) is not False


def _discard_persistent_client() -> None:
    global _CLIENT, _CLIENT_KEY, _POINTER_CONTROL
    with CLIENT_STATE_LOCK:
        client, pointer = _CLIENT, _POINTER_CONTROL
        _CLIENT = None
        _CLIENT_KEY = None
        _POINTER_CONTROL = None
    if pointer is not None:
        _close_client(getattr(pointer, "mouse_ws", pointer))
    if client is not None:
        _close_client(client)


def _acquire_persistent_client(key: str) -> tuple[Any, bool]:
    global _CLIENT, _CLIENT_KEY
    started = time.monotonic()
    with CLIENT_STATE_LOCK:
        if _CLIENT_KEY == key and _client_available(_CLIENT):
            client = _CLIENT
        else:
            client = None
    if client is not None:
        log.info("LG timing connection_acquisition_ms=%d reused=true", int((time.monotonic() - started) * 1000))
        return client, True
    with CLIENT_CONNECT_LOCK:
        with CLIENT_STATE_LOCK:
            if _CLIENT_KEY == key and _client_available(_CLIENT):
                client = _CLIENT
            else:
                client = None
        if client is None:
            _discard_persistent_client()
            client = _open_client(key)
            with CLIENT_STATE_LOCK:
                _CLIENT, _CLIENT_KEY = client, key
    log.info("LG timing connection_acquisition_ms=%d reused=false", int((time.monotonic() - started) * 1000))
    return client, False


def _adopt_inventory_client(client: Any, key: str) -> bool:
    """Keep a successfully enumerated client if no command client won the race."""
    global _CLIENT, _CLIENT_KEY, _POINTER_CONTROL
    with CLIENT_CONNECT_LOCK:
        with CLIENT_STATE_LOCK:
            if _CLIENT_KEY == key and _client_available(_CLIENT):
                return False
            previous, pointer = _CLIENT, _POINTER_CONTROL
            _CLIENT, _CLIENT_KEY = client, key
            _POINTER_CONTROL = None
    if pointer is not None:
        _close_client(getattr(pointer, "mouse_ws", pointer))
    if previous is not None:
        _close_client(previous)
    return True


def _inventory_snapshot() -> Dict[str, Any]:
    with ENUMERATION_LOCK:
        snapshot = copy.deepcopy(_INVENTORY)
    available = bool(snapshot["inputs_available"] or snapshot["applications_available"])
    reason = None if available else ("pairing_required" if not pairing._current_key()[0] else "live_enumeration_unavailable")
    age = None
    if snapshot["last_success_at"] is not None:
        age = max(0, int(time.time() - snapshot["last_success_at"]))
    return {
        "inputs": snapshot["inputs"],
        "applications": snapshot["applications"],
        "inputs_available": snapshot["inputs_available"],
        "applications_available": snapshot["applications_available"],
        "enumeration_available": available,
        "enumeration_reason": reason,
        "inventory_refreshing": snapshot["refreshing"],
        "inventory_age_sec": age,
    }


def _mark_inventory_refresh() -> bool:
    with ENUMERATION_LOCK:
        if _INVENTORY["refreshing"]:
            return False
        since_attempt = time.time() - (_INVENTORY["last_attempt_at"] or 0)
        if _INVENTORY["last_attempt_at"] and since_attempt < INVENTORY_RETRY_SEC:
            return False
        age = time.time() - (_INVENTORY["last_success_at"] or 0)
        if (
            _INVENTORY["inputs_available"]
            and _INVENTORY["applications_available"]
            and _INVENTORY["last_success_at"]
            and age < INVENTORY_TTL_SEC
        ):
            return False
        _INVENTORY["refreshing"] = True
        _INVENTORY["last_attempt_at"] = time.time()
        return True


def _refresh_inventory() -> None:
    if not INVENTORY_REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        key, _ = pairing._current_key()
        if not key:
            with ENUMERATION_LOCK:
                _INVENTORY.update(refreshing=False, last_error="pairing_required")
            return
        from pywebostv.controls import ApplicationControl, SourceControl

        client = None
        adopted = False
        try:
            client = _open_client(key)
            input_started = time.monotonic()
            try:
                raw_inputs = SourceControl(client).list_sources(timeout=COMMAND_TIMEOUT_SEC)
                with ENUMERATION_LOCK:
                    preserve_inputs = not raw_inputs and _INVENTORY["inputs_available"]
                inputs = None if preserve_inputs else _safe_options("input", raw_inputs, ("id",))
                with ENUMERATION_LOCK:
                    if inputs is not None:
                        _INVENTORY.update(inputs=inputs, inputs_available=True)
            except Exception:
                inputs = None
            log.info("LG timing inputs_enumeration_ms=%d", int((time.monotonic() - input_started) * 1000))
            app_started = time.monotonic()
            try:
                raw_apps = ApplicationControl(client).list_apps(timeout=COMMAND_TIMEOUT_SEC)
                with ENUMERATION_LOCK:
                    preserve_apps = not raw_apps and _INVENTORY["applications_available"]
                applications = None if preserve_apps else _safe_options("app", raw_apps, ("id",))
                with ENUMERATION_LOCK:
                    if applications is not None:
                        _INVENTORY.update(applications=applications, applications_available=True)
            except Exception:
                applications = None
            log.info("LG timing applications_enumeration_ms=%d", int((time.monotonic() - app_started) * 1000))
            if inputs is not None or applications is not None:
                adopted = _adopt_inventory_client(client, key)
        finally:
            if client is not None and not adopted:
                _close_client(client)
        with ENUMERATION_LOCK:
            if inputs is not None or applications is not None:
                _INVENTORY["last_success_at"] = time.time()
                _INVENTORY["last_error"] = None
            else:
                _INVENTORY["last_error"] = "live_enumeration_unavailable"
    except Exception:
        with ENUMERATION_LOCK:
            _INVENTORY["last_error"] = "live_enumeration_unavailable"
    finally:
        with ENUMERATION_LOCK:
            _INVENTORY["refreshing"] = False
        INVENTORY_REFRESH_LOCK.release()


def _pointer_command(client: Any, command: str) -> None:
    global _POINTER_CONTROL
    from pywebostv.controls import InputControl

    with CLIENT_STATE_LOCK:
        control = _POINTER_CONTROL
    if control is None:
        control = InputControl(client)
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
        with CLIENT_STATE_LOCK:
            _POINTER_CONTROL = control
    getattr(control, command)()


def _execute(client: Any, command: str, value: Any) -> None:
    from pywebostv.controls import ApplicationControl, MediaControl, SourceControl, SystemControl

    if command in {"up", "down", "left", "right", "ok", "back", "home"}:
        _pointer_command(client, command)
        return
    if command in {"rewind", "fast_forward"}:
        _pointer_command(client, "fastforward" if command == "fast_forward" else command)
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
        token = str(value or "").strip()
        with ENUMERATION_LOCK:
            selected = ENUMERATION_RAW["input"].get(token)
        if selected is None:
            raise LookupError("input_not_supported")
        SourceControl(client).set_source(selected, timeout=COMMAND_TIMEOUT_SEC)
        return
    if command == "launch_app":
        token = str(value or "").strip()
        with ENUMERATION_LOCK:
            selected = ENUMERATION_RAW["app"].get(token)
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
    started = time.monotonic()
    try:
        live = status._collect_live(key, timeout=COMMAND_TIMEOUT_SEC)
        with status.STATE_LOCK:
            status._CACHE.update(live)
        status._persist()
        return status._public_status(), True
    except Exception:
        return status._public_status(), False
    finally:
        log.info("LG timing status_refresh_ms=%d", int((time.monotonic() - started) * 1000))


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


def _install_client_cleanup() -> None:
    if getattr(app.state, "lg_control_cleanup_installed", False):
        return
    app.state.lg_control_cleanup_installed = True
    app.add_event_handler("shutdown", _discard_persistent_client)


@app.get("/api/lg-tv/capabilities")
def lg_tv_capabilities(background_tasks: BackgroundTasks) -> Dict[str, Any]:
    if _mark_inventory_refresh():
        background_tasks.add_task(_refresh_inventory)
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
            current = status._public_status()
            if (
                current.get("online") is True
                and current.get("connection_state") == "connected"
                and not current.get("stale")
            ):
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
    started = time.monotonic()
    try:
        with CLIENT_IO_LOCK:
            client, reused = _acquire_persistent_client(key)
            dispatch_started = time.monotonic()
            try:
                _execute(client, command, value)
            except Exception:
                _discard_persistent_client()
                raise
            dispatch_ms = int((time.monotonic() - dispatch_started) * 1000)
            log.info("LG timing command_dispatch_ms=%d command=%s", dispatch_ms, command)
        with status.STATE_LOCK:
            status._CACHE.update({
                "last_command": command, "last_command_success": True,
                "last_command_latency_ms": int((time.monotonic() - started) * 1000),
                "last_command_completed_ts": int(time.time()),
            })
            if command == "power_off":
                status._CACHE.update({"power_state": "standby", "connection_state": "standby", "online": False})
            state = copy.deepcopy(status._public_status())
        return {
            "ok": True,
            "command": command,
            "state_refreshed": False,
            "connection_reused": reused,
            "dispatch_ms": dispatch_ms,
            "state": state,
        }
    except (ValueError, LookupError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except (TimeoutError, socket.timeout):
        return JSONResponse({"detail": "command_timeout"}, status_code=504)
    except Exception as exc:
        elapsed = time.monotonic() - started
        if status._safe_code(exc) == "status_timeout" or elapsed >= COMMAND_TIMEOUT_SEC * 0.9:
            return JSONResponse({"detail": "command_timeout"}, status_code=504)
        return JSONResponse({"detail": "command_failed"}, status_code=502)


_install_wol_diagnostics()
_install_client_cleanup()
