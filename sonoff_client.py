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
ARRIVAL_WARMUP_SEC = 60
ARRIVAL_STABLE_HOME_SEC = 60
DEPARTURE_STABLE_AWAY_SEC = 1800
ARRIVAL_PEOPLE = ("beer", "seem")
ARRIVAL_HOME_SOURCES = ("Router", "MQTT", "Ping")
HISTORY_RANGE_SEC = {"24h": 86400, "3d": 259200, "7d": 604800}
HISTORY_MAX_RETURN = {"24h": 720, "3d": 720, "7d": 840}
_automation_state = {
    "startup_ts": int(time.time()) if time is not None else 0,
    "initialized": {"beer": False, "seem": False},
    "home": {"beer": None, "seem": None},
    "pending_since": {"beer": 0, "seem": 0},
    "away_since": {"beer": 0, "seem": 0},
    "arrival_armed": {"beer": False, "seem": False},
    "last_ts": {"beer": 0, "seem": 0},
}
_presence_event_context = {"retained": False}


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


def _first_sensor_value(row, keys):
    if not isinstance(row, dict):
        return None
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _normalize_sensor_row(row):
    row = row if isinstance(row, dict) else {}
    return {
        "ts": int(row.get("ts") or 0),
        "temperature": _first_sensor_value(row, ("temperature", "temp", "t")),
        "humidity": _first_sensor_value(row, ("humidity", "hum", "h")),
        "pm25": _first_sensor_value(row, ("pm25", "pm2_5", "pm2.5", "PM25", "pm_25")),
    }


def _downsample_history(rows, limit):
    if len(rows) <= limit:
        return rows
    step = len(rows) / float(limit)
    indexes = sorted({min(len(rows) - 1, int(i * step)) for i in range(limit)} | {len(rows) - 1})
    return [rows[i] for i in indexes]


async def _sensor_history_handler(request: Request):
    import backend.app as app_mod
    range_key = str(request.query_params.get("range", "24h")).lower()
    if range_key not in HISTORY_RANGE_SEC:
        range_key = "24h"
    now = int(time.time())
    cutoff = now - HISTORY_RANGE_SEC[range_key]
    raw = app_mod.state.get("condo_sensor_history", [])
    normalized = [_normalize_sensor_row(row) for row in raw if isinstance(row, dict)]
    normalized = [row for row in normalized if row["ts"] >= cutoff]
    raw_count = len(normalized)
    points = _downsample_history(normalized, HISTORY_MAX_RETURN[range_key])
    current = _normalize_sensor_row(app_mod.state.get("condo_sensor", {}))
    return {
        "ok": True,
        "range": range_key,
        "history": points,
        "points": points,
        "raw_count": raw_count,
        "current": current,
    }


def _presence_source(item):
    if not isinstance(item, dict):
        return "-"
    return str(item.get("source") or "-")


def _presence_state(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("state") or item.get("status") or "").lower()


def _is_arrived_home(item):
    if not isinstance(item, dict):
        return False
    source = _presence_source(item)
    # Router:REACHABLE and Router:DELAY are valid Home observations. A single
    # neighbor-state flap still cannot trigger arrival because Home must remain
    # continuous for ARRIVAL_STABLE_HOME_SEC and arrival must already be armed.
    return bool(item.get("home")) and _presence_state(item) == "home" and source.startswith(ARRIVAL_HOME_SOURCES)


def _is_confirmed_away(item):
    if not isinstance(item, dict):
        return False
    return bool(item.get("home")) is False and _presence_state(item) == "away" and _presence_source(item) == "Expired"


def _cancel_departure(person):
    if int(_automation_state["away_since"].get(person) or 0):
        print(f"automation departure cancelled: person={person}", flush=True)
    _automation_state["away_since"][person] = 0


def _cancel_arrival(person):
    if int(_automation_state["pending_since"].get(person) or 0):
        print(f"automation arrival cancelled: person={person}", flush=True)
    _automation_state["pending_since"][person] = 0


def _initialize_person_state(person, item):
    if _is_arrived_home(item):
        _automation_state["home"][person] = True
    elif _is_confirmed_away(item):
        _automation_state["home"][person] = False
    else:
        _automation_state["home"][person] = None
    _automation_state["away_since"][person] = 0
    _automation_state["pending_since"][person] = 0
    _automation_state["arrival_armed"][person] = False
    _automation_state["initialized"][person] = True


def _run_arrival_action(person, now):
    print(f"automation: {person}_arrived -> living_room_on", flush=True)
    try:
        result = set_state(ARRIVAL_DEVICEID, "on", ARRIVAL_CHANNEL)
        ok = bool(result.get("ok"))
        error = _backend_sonoff.safe_error(result.get("error") or result.get("last_error"))
    except Exception as exc:
        ok = False
        error = _backend_sonoff.safe_error(repr(exc))
    print(f"automation result: ok={str(ok).lower()} error={error}", flush=True)
    if not ok:
        return False
    _automation_state["last_ts"][person] = now
    _automation_state["home"][person] = True
    _automation_state["pending_since"][person] = 0
    _automation_state["away_since"][person] = 0
    _automation_state["arrival_armed"][person] = False
    return True


def _run_person_arrival_automation(person, presence):
    item = presence.get(person) if isinstance(presence, dict) else None
    now = int(time.time()) if time is not None else 0

    if not _automation_state["initialized"].get(person):
        _initialize_person_state(person, item)
        return

    current_home = _is_arrived_home(item)
    confirmed_away = _is_confirmed_away(item)
    automation_home = _automation_state["home"].get(person)
    arrival_armed = bool(_automation_state["arrival_armed"].get(person))

    # During startup warmup, current presence may initialize Home but can never
    # start departure/arrival timers, arm arrival, or trigger a command.
    startup_ts = int(_automation_state.get("startup_ts") or now)
    if now - startup_ts < ARRIVAL_WARMUP_SEC:
        _cancel_departure(person)
        _cancel_arrival(person)
        if current_home:
            _automation_state["home"][person] = True
            _automation_state["arrival_armed"][person] = False
        return

    if confirmed_away:
        _cancel_arrival(person)
        if arrival_armed and automation_home is False:
            return
        away_since = int(_automation_state["away_since"].get(person) or 0)
        if not away_since:
            _automation_state["away_since"][person] = now
            print(f"automation departure pending: person={person}", flush=True)
            return
        away_sec = now - away_since
        if away_sec < DEPARTURE_STABLE_AWAY_SEC:
            return
        _automation_state["home"][person] = False
        _automation_state["arrival_armed"][person] = True
        _automation_state["away_since"][person] = 0
        print(f"automation departure confirmed: person={person} away_sec={away_sec}", flush=True)
        return

    if current_home:
        _cancel_departure(person)

        if automation_home is True:
            _cancel_arrival(person)
            _automation_state["arrival_armed"][person] = False
            return

        if automation_home is not False or not arrival_armed:
            _cancel_arrival(person)
            _automation_state["home"][person] = True
            _automation_state["arrival_armed"][person] = False
            return

        pending_since = int(_automation_state["pending_since"].get(person) or 0)
        if not pending_since:
            _automation_state["pending_since"][person] = now
            print(f"automation arrival pending: person={person}", flush=True)
            return

        if now - pending_since < ARRIVAL_STABLE_HOME_SEC:
            return
        if now - int(_automation_state["last_ts"].get(person) or 0) < ARRIVAL_COOLDOWN_SEC:
            return

        if not _run_arrival_action(person, now):
            _cancel_arrival(person)
        return

    # Cached, Recently Seen, unknown and every state other than exact Expired
    # Away or a positive Router/MQTT/Ping Home observation cancel both timers.
    # They never change automation_home and never arm arrival.
    _cancel_departure(person)
    _cancel_arrival(person)


def _run_arrival_automation(presence):
    for person in ARRIVAL_PEOPLE:
        _run_person_arrival_automation(person, presence)


def _resolve_store_presence(app_mod, evaluate=False):
    raw_presence = app_mod.state.get("condo_presence", {})
    if resolve_presence is None:
        presence = raw_presence if isinstance(raw_presence, dict) else {}
    else:
        presence = resolve_presence(raw_presence)
    app_mod.state["presence"] = presence
    if evaluate:
        _run_arrival_automation(presence)
    return presence


def _payload_contains_presence(payload):
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("presence"), dict):
        return True
    return any(key in payload for key in ("occupancy", "motion", "present", "home", "living", "bedroom", "door", "person", "persons"))


def _install_presence_refresh_hook():
    try:
        import backend.app as app_mod
        if getattr(app_mod, "_presence_automation_hook_installed", False):
            return
        original_presence_topic = app_mod.update_condo_presence_from_topic
        original_condo_state = app_mod.update_condo_state
        original_on_message = getattr(app_mod, "on_message", None)

        def _wrapped_update_condo_presence_from_topic(person, data, topic):
            original_presence_topic(person, data, topic)
            if not _presence_event_context.get("retained"):
                _resolve_store_presence(app_mod, evaluate=True)
            else:
                _resolve_store_presence(app_mod, evaluate=False)

        def _wrapped_update_condo_state(payload):
            original_condo_state(payload)
            if _payload_contains_presence(payload):
                _resolve_store_presence(app_mod, evaluate=not _presence_event_context.get("retained"))

        def _wrapped_on_message(client, userdata, msg):
            previous = bool(_presence_event_context.get("retained"))
            _presence_event_context["retained"] = bool(getattr(msg, "retain", False))
            try:
                return original_on_message(client, userdata, msg)
            finally:
                _presence_event_context["retained"] = previous

        app_mod.update_condo_presence_from_topic = _wrapped_update_condo_presence_from_topic
        app_mod.update_condo_state = _wrapped_update_condo_state
        if original_on_message is not None:
            app_mod.on_message = _wrapped_on_message
            app_mod.mqttc.on_message = _wrapped_on_message
        app_mod._presence_automation_hook_installed = True
    except Exception as exc:
        print(f"automation hook error: {repr(exc)}", flush=True)


def _install_sensor_history_support():
    try:
        import backend.app as app_mod
        app_mod.HISTORY_TTL_SEC = max(int(getattr(app_mod, "HISTORY_TTL_SEC", 0)), HISTORY_RANGE_SEC["7d"])
        app_mod.HISTORY_MAX_POINTS = max(int(getattr(app_mod, "HISTORY_MAX_POINTS", 0)), 20000)
    except Exception as exc:
        print(f"sensor history setup error: {repr(exc)}", flush=True)


def _presence_status_handler():
    import backend.app as app_mod
    sensor = app_mod.state.get("condo_sensor", {})
    presence = _resolve_store_presence(app_mod, evaluate=False)
    return {"ok": True, "sensor": sensor, "presence": presence}


def _presence_api_handler():
    data = _presence_status_handler()
    return {"ok": True, "presence": data.get("presence", {})}


def _initialize_presence_state(label="startup"):
    try:
        import backend.app as app_mod
        _install_sensor_history_support()
        _install_presence_refresh_hook()
        presence = _resolve_store_presence(app_mod, evaluate=False)
        for person in ARRIVAL_PEOPLE:
            _initialize_person_state(person, presence.get(person) if isinstance(presence, dict) else None)
        print(f"presence initialized: source={label} count={len(presence)}", flush=True)
    except Exception as exc:
        print(f"presence initialize error: source={label} error={repr(exc)}", flush=True)


def _schedule_presence_initialization():
    _initialize_presence_state("startup")


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
        '<script src="/assets/sensor_dashboard.js"></script>',
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
        elif path == "/api/condo/history" and "GET" in methods:
            endpoint = _sensor_history_handler
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
        elif path == "/api/condo/history" and "GET" in methods:
            endpoint = _sensor_history_handler
        elif path == "/" and "GET" in methods and HTMLResponse is not None:
            endpoint = _dashboard_index_handler
        return _orig_fastapi_add_api_route(self, path, endpoint, **kwargs)

    FastAPI.on_event = _patched_fastapi_on_event
    FastAPI.add_api_route = _patched_fastapi_add_api_route
    FastAPI._sonoff_route_patch = True
