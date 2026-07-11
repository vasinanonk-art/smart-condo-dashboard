"""Bounded Tuya zone execution and periodic arrival evaluation.

Public routes and Presence payloads remain unchanged.
"""

from __future__ import annotations

import contextlib
import copy
import io
import multiprocessing
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict

PRESENCE_EVALUATION_SEC = 10
ZONE_DEVICE_TIMEOUT_SEC = 7.0
ZONE_MAX_PARALLEL = 2
PRESENCE_TOPICS = {"condo/presence/beer", "condo/presence/seem"}

_lock = threading.Lock()
_started = False
_presence_started = False
_zone_installed = False
_presence_source: Dict[str, Any] = {"last_non_retained": None}


def _safe_error(value: Any) -> str:
    text = str(value or "").lower()
    if "timeout" in text:
        return "timeout"
    if "905" in text or "unreachable" in text or "no reply" in text:
        return "device unreachable"
    if "unsupported" in text or "capability" in text:
        return "capability not supported"
    return "command failed"


def _router_only_presence(raw: Any) -> Dict[str, Any]:
    """Keep identity/IP but remove retained Home/Away assertions."""
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, Any] = {}
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


def _install_presence_message_guard(app_mod: Any) -> None:
    if getattr(app_mod, "_runtime_presence_guard_installed", False):
        return
    original = app_mod.mqttc.on_message

    def wrapped(client: Any, userdata: Any, msg: Any):
        result = original(client, userdata, msg)
        if getattr(msg, "topic", "") in PRESENCE_TOPICS and not bool(getattr(msg, "retain", False)):
            _presence_source["last_non_retained"] = copy.deepcopy(app_mod.state.get("condo_presence", {}))
        return result

    app_mod.mqttc.on_message = wrapped
    app_mod.on_message = wrapped
    app_mod._runtime_presence_guard_installed = True


def _presence_loop(app_mod: Any, automation_mod: Any) -> None:
    while True:
        try:
            if not bool(getattr(automation_mod, "_presence_event_context", {}).get("retained")):
                raw = _presence_source.get("last_non_retained")
                if not isinstance(raw, dict):
                    raw = _router_only_presence(app_mod.state.get("condo_presence", {}))
                resolver = getattr(automation_mod, "resolve_presence", None)
                if resolver is None:
                    resolved = raw
                else:
                    # Suppress the resolver's per-poll diagnostic only. Arrival
                    # transition logs are emitted outside this context.
                    with contextlib.redirect_stdout(io.StringIO()):
                        resolved = resolver(raw)
                app_mod.state["presence"] = resolved
                automation_mod._run_arrival_automation(resolved)
        except Exception as exc:
            print(f"automation evaluator error: error={type(exc).__name__}", flush=True)
        time.sleep(PRESENCE_EVALUATION_SEC)


def _start_presence(app_mod: Any, automation_mod: Any) -> None:
    global _presence_started
    with _lock:
        if _presence_started:
            return
        _presence_started = True
    _install_presence_message_guard(app_mod)
    threading.Thread(
        target=_presence_loop,
        args=(app_mod, automation_mod),
        name="presence-arrival-evaluator",
        daemon=True,
    ).start()


def _device_worker(device: Dict[str, Any], body: Dict[str, Any], preset: Dict[str, Any] | None, output: Any) -> None:
    try:
        from backend import app as app_module

        target = app_module.device_target(device)
        action = str(body.get("action") or "").lower()
        steps = []

        def step(name: str, command: Any) -> bool:
            try:
                app_module.apply_light(device, command)
                steps.append({"step": name, "ok": True})
                return True
            except Exception as exc:
                steps.append({"step": name, "ok": False, "error": _safe_error(exc)})
                return False

        if action == "brightness":
            ok = step("brightness", app_module.LightCommand(target=target, action="brightness", value=int(body["value"])))
        elif action in ("temperature", "temp", "cct"):
            ok = step("temperature", app_module.LightCommand(target=target, action="temperature", value=int(body["value"])))
        elif action == "rgb":
            ok = step("rgb", app_module.LightCommand(target=target, action="rgb", h=int(body["h"]), s=int(body["s"]), v=int(body["v"])))
        elif action == "preset" and preset:
            mode = str(preset.get("mode") or "")
            if mode == "white":
                first = step("brightness", app_module.LightCommand(target=target, action="brightness", value=int(preset["brightness"])))
                second = step("temperature", app_module.LightCommand(target=target, action="temperature", value=int(preset["temperature"])))
                ok = first and second
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


def _run_bounded(device: Dict[str, Any], body: Dict[str, Any], preset: Dict[str, Any] | None) -> Dict[str, Any]:
    ctx = multiprocessing.get_context("fork")
    output = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_device_worker, args=(device, body, preset, output))
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


def _cache_command(app_mod: Any, device: Dict[str, Any], body: Dict[str, Any], preset: Dict[str, Any] | None) -> None:
    action = str(body.get("action") or "").lower()
    dps: Dict[str, Any] = {}
    if action == "brightness":
        dps["22"] = int(body["value"])
    elif action in ("temperature", "temp", "cct"):
        dps.update({"21": "white", "23": int(body["value"])})
    elif action == "rgb":
        dps.update({"21": "colour", "24": app_mod.hsv_hex(int(body["h"]), int(body["s"]), int(body["v"]))})
    elif action == "preset" and preset:
        mode = preset.get("mode")
        if mode == "white":
            dps.update({"21": "white", "22": int(preset["brightness"]), "23": int(preset["temperature"])})
        elif mode == "brightness":
            dps["22"] = int(preset["value"])
        elif mode == "temperature":
            dps.update({"21": "white", "23": int(preset["value"])})
        elif mode == "colour":
            dps.update({"21": "colour", "24": app_mod.hsv_hex(int(preset["h"]), int(preset["s"]), int(preset["v"]))})
    if dps:
        app_mod.cache_dps(device, dps, "command")


def _refresh_async(app_mod: Any, device: Dict[str, Any]) -> None:
    def refresh() -> None:
        try:
            reply = app_mod.read_status_once(device, "zone-refresh", timeout_sec=3.0)
            if app_mod.tuya_ok(reply):
                dps = reply.get("dps") or reply.get("data", {}).get("dps") or {}
                if dps:
                    app_mod.cache_dps(device, dps, "zone-refresh")
        except Exception:
            pass

    threading.Thread(target=refresh, name="tuya-zone-refresh", daemon=True).start()


def _install_zone_route(app_mod: Any, ext: Any) -> None:
    global _zone_installed
    with _lock:
        if _zone_installed:
            return
        _zone_installed = True

    ZoneCommand = ext.ZoneCommand

    def handler(body: ZoneCommand):
        zone = body.zone.strip()
        if zone not in ext._zones():
            raise ext.HTTPException(status_code=404, detail="zone not configured")
        requested = body.action.strip().lower()
        action = "temperature" if requested in ("temperature", "temp", "cct") else requested
        presets, _ = ext._presets()
        preset = presets.get(str(body.preset or "")) if action == "preset" else None
        if action == "preset" and not preset:
            raise ext.HTTPException(status_code=404, detail="preset not configured")
        if action not in ("brightness", "temperature", "rgb", "preset"):
            raise ext.HTTPException(status_code=400, detail="unsupported zone action")

        devices, missing = ext._zone_devices(zone)
        body_data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        by_id: Dict[str, Dict[str, Any]] = {}
        runnable = []
        for device in devices:
            device_id = ext._device_key(device)
            if not ext._supports(device, action, preset):
                by_id[device_id] = {"deviceid": device_id, "ok": False, "unsupported": True, "error": "capability not supported"}
            else:
                runnable.append(device)

        with ThreadPoolExecutor(max_workers=ZONE_MAX_PARALLEL) as pool:
            jobs = {pool.submit(_run_bounded, device, body_data, preset): device for device in runnable}
            for job in as_completed(jobs):
                device = jobs[job]
                device_id = ext._device_key(device)
                try:
                    outcome = job.result()
                except Exception as exc:
                    outcome = {"ok": False, "error": _safe_error(exc)}
                item = {"deviceid": device_id, **outcome}
                if item.get("ok"):
                    _cache_command(app_mod, device, body_data, preset)
                    _refresh_async(app_mod, device)
                else:
                    item["error"] = _safe_error(item.get("error") or item.get("steps"))
                by_id[device_id] = item

        results = [by_id[ext._device_key(device)] for device in devices]
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

    app = app_mod.app
    app.router.routes = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == "/api/lighting/zone" and "POST" in set(getattr(route, "methods", set()) or set()))
    ]
    app.add_api_route("/api/lighting/zone", handler, methods=["POST"])


def _bootstrap() -> None:
    deadline = time.time() + 60
    while time.time() < deadline:
        app_mod = sys.modules.get("backend.app")
        ext = sys.modules.get("backend.dashboard_extensions")
        automation_mod = sys.modules.get("sonoff_client")
        if app_mod is not None and ext is not None and automation_mod is not None:
            try:
                _install_zone_route(app_mod, ext)
                _start_presence(app_mod, automation_mod)
                return
            except Exception as exc:
                print(f"runtime stability setup error: error={type(exc).__name__}", flush=True)
        time.sleep(0.25)


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_bootstrap, name="runtime-stability-bootstrap", daemon=True).start()


start()
