import backend.sonoff_client as _backend_sonoff
from backend.sonoff_client import *

# Route shim for legacy backend.app import style.
# backend.app imports this top-level module before registering /api/sonoff.
# Patch only Sonoff handlers so the API exposes safe diagnostics and channel control.
try:
    import inspect
    import json
    import os
    import threading
    import time
    from backend.presence_stabilizer import resolve_presence
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse
    from fastapi.routing import APIRouter
except Exception:  # pragma: no cover
    inspect = None
    json = None
    os = None
    threading = None
    time = None
    resolve_presence = None
    FastAPI = None
    HTTPException = None
    Request = None
    HTMLResponse = None
    APIRouter = None

ARRIVAL_DEVICEID = "1002354e11"
ARRIVAL_CHANNEL = 1
ARRIVAL_COOLDOWN_SEC = 600
ARRIVAL_PEOPLE = ("beer", "seem")
_automation_state = {
    "home": {"beer": None, "seem": None},
    "last_ts": {"beer": 0, "seem": 0},
}


def _stable_command_diag(detail):
    safe = _backend_sonoff.redact_payload(detail)
    status = safe.get("result_status") if isinstance(safe, dict) else None
    if status and status not in ("ok", 0, None):
        print("sonoff command error: " + json.dumps(safe, ensure_ascii=False), flush=True)


def _stable_refresh_diag(detail):
    safe = _backend_sonoff.redact_payload(detail)
    _backend_sonoff._cache["refresh_diag"] = safe
    if isinstance(safe, dict) and not safe.get("refresh_success", True):
        print("sonoff refresh error: " + json.dumps(safe, ensure_ascii=False), flush=True)


_backend_sonoff.log_command_diag = _stable_command_diag
_backend_sonoff.log_refresh_diag = _stable_refresh_diag


def _sonoff_payload(data):
    payload = {
        "ok": bool(data.get("ok", True)),
        "config_loaded": bool(data.get("config_loaded")),
        "config_path": data.get("config_path"),
        "auth_status": data.get("auth_status"),
        "last_error": data.get("last_error"),
        "devices": data.get("devices", []),
    }
    if "results" in data:
        payload["results"] = data.get("results", [])
    return payload


def _sonoff_get_handler():
    return _sonoff_payload(devices())


async def _sonoff_post_handler(request: Request):
    body = await request.json()
    result = set_state(body.get("deviceid", ""), body.get("action", ""), int(body.get("channel") or 1))
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff command failed"))
    data = {
        "config_loaded": True,
        "config_path": config_payload().get("path"),
        "auth_status": result.get("auth_status"),
        "last_error": result.get("last_error"),
        "devices": result.get("devices", []),
    }
    payload = _sonoff_payload(data)
    payload.update({"deviceid": result.get("deviceid"), "channel": result.get("channel", 1), "action": result.get("action")})
    return payload


async def _sonoff_device_handler(request: Request):
    body = await request.json()
    result = bulk_device_state(body.get("deviceid", ""), body.get("action", ""))
    if not result.get("devices") and not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff device bulk command failed"))
    return _sonoff_payload(result)


async def _sonoff_all_handler(request: Request):
    body = await request.json()
    result = bulk_all_state(body.get("action", ""))
    if not result.get("devices") and not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff bulk command failed"))
    return _sonoff_payload(result)


def _is_arrived_home(item):
    if not isinstance(item, dict):
        return False
    state = str(item.get("state") or item.get("status") or "").lower()
    return bool(item.get("home")) and bool(item.get("online")) and state == "home"


def _run_person_arrival_automation(person, presence):
    item = presence.get(person) if isinstance(presence, dict) else None
    current_home = _is_arrived_home(item)
    previous_home = _automation_state["home"].get(person)
    if previous_home != current_home:
        print(f"automation transition: {person} old_home={previous_home} new_home={current_home}", flush=True)
    now = int(time.time()) if time is not None else 0
    should_trigger = previous_home == False and current_home == True
    cooldown_ok = now - int(_automation_state["last_ts"].get(person) or 0) >= ARRIVAL_COOLDOWN_SEC
    _automation_state["home"][person] = current_home
    if not should_trigger or not cooldown_ok:
        return
    _automation_state["last_ts"][person] = now
    try:
        print(f"automation: {person}_arrived -> living_room_on", flush=True)
        result = bulk_device_state(ARRIVAL_DEVICEID, "on")
        ok = bool(result.get("ok"))
        error = _backend_sonoff.safe_error(result.get("error") or result.get("last_error"))
        print(f"automation result: ok={str(ok).lower()} error={error}", flush=True)
    except Exception as exc:
        error = _backend_sonoff.safe_error(repr(exc))
        print(f"automation result: ok=false error={error}", flush=True)


def _run_arrival_automation(presence):
    for person in ARRIVAL_PEOPLE:
        _run_person_arrival_automation(person, presence)


def _resolve_store_and_evaluate_presence(app_mod):
    raw_presence = app_mod.state.get("condo_presence", {})
    if resolve_presence is None:
        presence = raw_presence if isinstance(raw_presence, dict) else {}
    else:
        presence = resolve_presence(raw_presence)
    app_mod.state["condo_presence"] = presence
    app_mod.state["presence"] = presence
    _run_arrival_automation(presence)
    return presence


def _install_presence_refresh_hook():
    try:
        import backend.app as app_mod
        if getattr(app_mod, "_presence_automation_hook_installed", False):
            return
        original_presence_topic = app_mod.update_condo_presence_from_topic
        original_condo_state = app_mod.update_condo_state

        def _wrapped_update_condo_presence_from_topic(person, data, topic):
            original_presence_topic(person, data, topic)
            _resolve_store_and_evaluate_presence(app_mod)

        def _wrapped_update_condo_state(payload):
            original_condo_state(payload)
            if isinstance(app_mod.state.get("condo_presence"), dict):
                _resolve_store_and_evaluate_presence(app_mod)

        app_mod.update_condo_presence_from_topic = _wrapped_update_condo_presence_from_topic
        app_mod.update_condo_state = _wrapped_update_condo_state
        app_mod._presence_automation_hook_installed = True
    except Exception as exc:
        print(f"automation hook error: {repr(exc)}", flush=True)


def _presence_status_handler():
    import backend.app as app_mod
    sensor = app_mod.state.get("condo_sensor", {})
    presence = _resolve_store_and_evaluate_presence(app_mod)
    return {"ok": True, "sensor": sensor, "presence": presence}


def _presence_api_handler():
    data = _presence_status_handler()
    return {"ok": True, "presence": data.get("presence", {})}


def _initialize_presence_state(label="startup"):
    try:
        _install_presence_refresh_hook()
        data = _presence_status_handler()
        print(f"presence initialized: source={label} count={len(data.get('presence', {}))}", flush=True)
    except Exception as exc:
        print(f"presence initialize error: source={label} error={repr(exc)}", flush=True)


def _schedule_presence_initialization():
    _initialize_presence_state("startup")
    if threading is not None:
        timer = threading.Timer(2.0, lambda: _initialize_presence_state("startup-delayed"))
        timer.daemon = True
        timer.start()


def _dashboard_index_handler():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "frontend", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("Smart Condo Dashboard V2", "Smart Condo Dashboard v2.2.0 Stable")
    html = html.replace("console.log(device.deviceid,device.state,device.channel_states);", "")
    scripts = [
        '<script src="/assets/sonoff_bulk.js"></script>',
        '<script src="/assets/presence_stabilizer.js"></script>',
    ]
    for script in scripts:
        if script not in html:
            html = html.replace("</body></html>", script + "</body></html>")
    return HTMLResponse(html)


def _install_extra_routes_on_router(router):
    if getattr(router, "_sonoff_bulk_routes_installed", False):
        return
    router._sonoff_bulk_routes_installed = True
    _orig_router_add_api_route(router, "/api/sonoff/device", _sonoff_device_handler, methods=["POST"])
    _orig_router_add_api_route(router, "/api/sonoff/all", _sonoff_all_handler, methods=["POST"])
    _orig_router_add_api_route(router, "/api/presence", _presence_api_handler, methods=["GET"])


def _install_extra_routes(app):
    if getattr(app, "_sonoff_bulk_routes_installed", False):
        return
    app._sonoff_bulk_routes_installed = True
    app.add_api_route("/api/sonoff/device", _sonoff_device_handler, methods=["POST"])
    app.add_api_route("/api/sonoff/all", _sonoff_all_handler, methods=["POST"])
    app.add_api_route("/api/presence", _presence_api_handler, methods=["GET"])


if APIRouter is not None and not getattr(APIRouter, "_sonoff_route_patch", False):
    _orig_router_add_api_route = APIRouter.add_api_route

    def _patched_router_add_api_route(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or [])
        if path == "/api/sonoff":
            _install_extra_routes_on_router(self)
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        elif path == "/api/condo/status" and "GET" in methods:
            endpoint = _presence_status_handler
        elif path == "/" and "GET" in methods and HTMLResponse is not None:
            endpoint = _dashboard_index_handler
        return _orig_router_add_api_route(self, path, endpoint, **kwargs)

    APIRouter.add_api_route = _patched_router_add_api_route
    APIRouter._sonoff_route_patch = True

if FastAPI is not None and not getattr(FastAPI, "_sonoff_route_patch", False):
    _orig_fastapi_add_api_route = FastAPI.add_api_route
    _orig_fastapi_on_event = FastAPI.on_event

    def _patched_fastapi_on_event(self, event_type):
        original_decorator = _orig_fastapi_on_event(self, event_type)
        if event_type != "startup":
            return original_decorator

        def _decorator(func):
            if inspect is not None and inspect.iscoroutinefunction(func):
                async def _wrapped_startup(*args, **kwargs):
                    result = await func(*args, **kwargs)
                    _schedule_presence_initialization()
                    return result
            else:
                def _wrapped_startup(*args, **kwargs):
                    result = func(*args, **kwargs)
                    _schedule_presence_initialization()
                    return result
            return original_decorator(_wrapped_startup)

        return _decorator

    def _patched_fastapi_add_api_route(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or [])
        if path == "/api/sonoff":
            _install_extra_routes(self)
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        elif path == "/api/condo/status" and "GET" in methods:
            endpoint = _presence_status_handler
        elif path == "/" and "GET" in methods and HTMLResponse is not None:
            endpoint = _dashboard_index_handler
        return _orig_fastapi_add_api_route(self, path, endpoint, **kwargs)

    FastAPI.on_event = _patched_fastapi_on_event
    FastAPI.add_api_route = _patched_fastapi_add_api_route
    FastAPI._sonoff_route_patch = True
