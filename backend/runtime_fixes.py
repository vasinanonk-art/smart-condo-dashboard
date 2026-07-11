import contextlib
import copy
import io
import multiprocessing
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import sonoff_client as presence_automation
from backend import app as app_module
from backend import dashboard_extensions

PRESENCE_EVALUATION_SEC = 10
ZONE_DEVICE_TIMEOUT_SEC = 7.0
ZONE_MAX_PARALLEL = 2
PRESENCE_TOPICS = {"condo/presence/beer", "condo/presence/seem"}

_seen_home_since_start = {person: False for person in presence_automation.ARRIVAL_PEOPLE}
_last_non_retained_presence = None


def _safe_error(value):
    text = str(value or "").lower()
    if "timeout" in text:
        return "timeout"
    if "905" in text or "unreachable" in text or "no reply" in text:
        return "device unreachable"
    if "unsupported" in text or "capability" in text:
        return "capability not supported"
    return "command failed"


def _guarded_run_arrival_automation(presence):
    """Ignore an Away startup baseline until that person is observed Home once."""
    presence = presence if isinstance(presence, dict) else {}
    for person in presence_automation.ARRIVAL_PEOPLE:
        item = presence.get(person)
        if presence_automation._is_arrived_home(item):
            _seen_home_since_start[person] = True
        if not _seen_home_since_start[person]:
            continue
        presence_automation._run_person_arrival_automation(person, presence)


presence_automation._run_arrival_automation = _guarded_run_arrival_automation


def _router_only_presence(raw):
    if not isinstance(raw, dict):
        return {}
    result = {}
    for person, item in raw.items():
        if not isinstance(item, dict):
            continue
        clean = {
            key: item[key]
            for key in ("name", "ip", "address", "host")
            if item.get(key) not in (None, "")
        }
        clean["ts"] = 0
        result[str(person)] = clean
    return result


def _install_retained_guard():
    global _last_non_retained_presence
    if getattr(app_module, "_presence_retained_guard_installed", False):
        return
    original = app_module.mqttc.on_message

    def wrapped(client, userdata, msg):
        global _last_non_retained_presence
        result = original(client, userdata, msg)
        if getattr(msg, "topic", "") in PRESENCE_TOPICS and not bool(getattr(msg, "retain", False)):
            _last_non_retained_presence = copy.deepcopy(app_module.state.get("condo_presence", {}))
        return result

    app_module.mqttc.on_message = wrapped
    app_module.on_message = wrapped
    app_module._presence_retained_guard_installed = True


def _presence_worker():
    # Router/Ping evaluation is independent from browser refresh and MQTT cadence.
    while True:
        try:
            if not bool(getattr(presence_automation, "_presence_event_context", {}).get("retained")):
                raw = _last_non_retained_presence
                if not isinstance(raw, dict):
                    raw = _router_only_presence(app_module.state.get("condo_presence", {}))
                resolver = getattr(presence_automation, "resolve_presence", None)
                if resolver is None:
                    resolved = raw
                else:
                    # Suppress only the resolver's per-poll diagnostic. Meaningful
                    # automation transition logs remain visible.
                    with contextlib.redirect_stdout(io.StringIO()):
                        resolved = resolver(raw)
                app_module.state["presence"] = resolved
                _guarded_run_arrival_automation(resolved)
        except Exception as exc:
            print(f"automation presence worker error: {type(exc).__name__}", flush=True)
        time.sleep(PRESENCE_EVALUATION_SEC)


def _zone_worker(device, body_data, preset, output):
    try:
        target = app_module.device_target(device)
        action = str(body_data.get("action") or "").strip().lower()
        steps = []

        def step(name, command):
            try:
                app_module.apply_light(device, command)
                steps.append({"step": name, "ok": True})
                return True
            except Exception as exc:
                steps.append({"step": name, "ok": False, "error": _safe_error(exc)})
                return False

        if action == "brightness":
            ok = step("brightness", app_module.LightCommand(target=target, action="brightness", value=int(body_data["value"])))
        elif action in ("temperature", "temp", "cct"):
            ok = step("temperature", app_module.LightCommand(target=target, action="temperature", value=int(body_data["value"])))
        elif action == "rgb":
            ok = step("rgb", app_module.LightCommand(target=target, action="rgb", h=int(body_data["h"]), s=int(body_data["s"]), v=int(body_data["v"])))
        elif action == "preset" and preset:
            mode = str(preset.get("mode") or "")
            if mode == "white":
                brightness_ok = step("brightness", app_module.LightCommand(target=target, action="brightness", value=int(preset["brightness"])))
                temperature_ok = step("temperature", app_module.LightCommand(target=target, action="temperature", value=int(preset["temperature"])))
                ok = brightness_ok and temperature_ok
            elif mode == "brightness":
                ok = step("brightness", app_module.LightCommand(target=target, action="brightness", value=int(preset["value"])))
            elif mode == "temperature":
                ok = step("temperature", app_module.LightCommand(target=target, action="temperature", value=int(preset["value"])))
            elif mode == "colour":
                ok = step("rgb", app_module.LightCommand(target=target, action="rgb", h=int(preset["h"]), s=int(preset["s"]), v=int(preset["v"])))
            else:
                ok = False
                steps.append({"step": "preset", "ok": False, "error": "capability not supported"})
        else:
            ok = False
            steps.append({"step": action or "command", "ok": False, "error": "command failed"})
        output.put({"ok": bool(ok), "steps": steps})
    except Exception as exc:
        output.put({"ok": False, "error": _safe_error(exc)})


def _run_bounded(device, body_data, preset):
    ctx = multiprocessing.get_context("fork")
    output = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_zone_worker, args=(device, body_data, preset, output))
    process.start()
    process.join(ZONE_DEVICE_TIMEOUT_SEC)
    if process.is_alive():
        process.terminate()
        process.join(0.5)
        result = {"ok": False, "error": "timeout"}
    else:
        try:
            result = output.get(timeout=0.5)
        except queue.Empty:
            result = {"ok": False, "error": "command failed"}
    try:
        output.close()
        output.join_thread()
    except Exception:
        pass
    return result


def _cache_success(device, body_data, preset):
    action = str(body_data.get("action") or "").strip().lower()
    dps = {}
    if action == "brightness":
        dps["22"] = int(body_data["value"])
    elif action in ("temperature", "temp", "cct"):
        dps.update({"21": "white", "23": int(body_data["value"])})
    elif action == "rgb":
        dps.update({"21": "colour", "24": app_module.hsv_hex(int(body_data["h"]), int(body_data["s"]), int(body_data["v"]))})
    elif action == "preset" and preset:
        mode = preset.get("mode")
        if mode == "white":
            dps.update({"21": "white", "22": int(preset["brightness"]), "23": int(preset["temperature"])})
        elif mode == "brightness":
            dps["22"] = int(preset["value"])
        elif mode == "temperature":
            dps.update({"21": "white", "23": int(preset["value"])})
        elif mode == "colour":
            dps.update({"21": "colour", "24": app_module.hsv_hex(int(preset["h"]), int(preset["s"]), int(preset["v"]))})
    if dps:
        app_module.cache_dps(device, dps, "command")


def _refresh_success_async(device):
    def refresh():
        try:
            result = app_module.read_status_once(device, "zone-refresh", timeout_sec=3.0)
            if app_module.tuya_ok(result):
                dps = result.get("dps") or result.get("data", {}).get("dps") or {}
                if dps:
                    app_module.cache_dps(device, dps, "zone-refresh")
        except Exception:
            pass

    threading.Thread(target=refresh, name="tuya-zone-refresh", daemon=True).start()


def _bounded_zone_command(body: dashboard_extensions.ZoneCommand):
    zone = body.zone.strip()
    if zone not in dashboard_extensions._zones():
        raise dashboard_extensions.HTTPException(status_code=404, detail="zone not configured")
    requested = body.action.strip().lower()
    action = "temperature" if requested in ("temperature", "temp", "cct") else requested
    presets, _ = dashboard_extensions._presets()
    preset = presets.get(str(body.preset or "")) if action == "preset" else None
    if action == "preset" and not preset:
        raise dashboard_extensions.HTTPException(status_code=404, detail="preset not configured")
    if action not in ("brightness", "temperature", "rgb", "preset"):
        raise dashboard_extensions.HTTPException(status_code=400, detail="unsupported zone action")

    devices, missing = dashboard_extensions._zone_devices(zone)
    body_data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    by_id = {}
    runnable = []
    for device in devices:
        device_id = dashboard_extensions._device_key(device)
        if not dashboard_extensions._supports(device, action, preset):
            by_id[device_id] = {"deviceid": device_id, "ok": False, "unsupported": True, "error": "capability not supported"}
        else:
            runnable.append(device)

    with ThreadPoolExecutor(max_workers=ZONE_MAX_PARALLEL) as pool:
        jobs = {pool.submit(_run_bounded, device, body_data, preset): device for device in runnable}
        for job in as_completed(jobs):
            device = jobs[job]
            device_id = dashboard_extensions._device_key(device)
            try:
                outcome = job.result()
            except Exception as exc:
                outcome = {"ok": False, "error": _safe_error(exc)}
            item = {"deviceid": device_id, **outcome}
            if item.get("ok"):
                _cache_success(device, body_data, preset)
                _refresh_success_async(device)
            else:
                item["error"] = _safe_error(item.get("error") or item.get("steps"))
            by_id[device_id] = item

    results = [by_id[dashboard_extensions._device_key(device)] for device in devices]
    success = sum(1 for item in results if item.get("ok"))
    return {
        "ok": success > 0,
        "zone": zone,
        "action": action,
        "preset": body.preset,
        "partial": success != len(results),
        "missing_members": missing,
        "results": results,
    }


def _install_zone_route():
    if getattr(app_module, "_bounded_zone_route_installed", False):
        return
    app = app_module.app
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/api/lighting/zone"
            and "POST" in set(getattr(route, "methods", set()) or set())
        )
    ]
    app.add_api_route(
        "/api/lighting/zone",
        _bounded_zone_command,
        methods=["POST"],
        response_model=None,
    )
    app_module._bounded_zone_route_installed = True


@app_module.app.on_event("startup")
def start_runtime_fixes():
    if getattr(app_module, "_presence_automation_worker_started", False):
        return
    _install_retained_guard()
    _install_zone_route()
    app_module._presence_automation_worker_started = True
    threading.Thread(
        target=_presence_worker,
        name="presence-automation-worker",
        daemon=True,
    ).start()
