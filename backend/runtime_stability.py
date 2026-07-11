"""Runtime safety fixes for presence evaluation and Tuya zone commands.

This module is loaded from backend.__init__ and defers all work until the
application modules are fully imported. It does not alter public API paths or
payload shapes.
"""

from __future__ import annotations

import contextlib
import io
import multiprocessing
import queue
import sys
import threading
import time
from typing import Any, Dict

PRESENCE_EVALUATION_SEC = 10
ZONE_DEVICE_TIMEOUT_SEC = 7.0
ZONE_MAX_PARALLEL = 2

_bootstrap_started = False
_presence_thread_started = False
_route_installed = False
_state_lock = threading.Lock()


def _safe_zone_error(value: Any) -> str:
    text = str(value or "").lower()
    if "905" in text or "unreachable" in text or "no reply" in text:
        return "device unreachable"
    if "timeout" in text:
        return "timeout"
    if "capability" in text or "unsupported" in text:
        return "capability not supported"
    return "command failed"


def _presence_loop(app_mod: Any, automation_mod: Any) -> None:
    # The existing state machine owns warmup, departure/arrival stability and
    # cooldown. This loop only supplies real Router/Ping observations on time.
    while True:
        try:
            if not bool(getattr(automation_mod, "_presence_event_context", {}).get("retained")):
                raw = app_mod.state.get("condo_presence", {})
                resolver = getattr(automation_mod, "resolve_presence", None)
                if resolver is None:
                    resolved = raw if isinstance(raw, dict) else {}
                else:
                    # presence_stabilizer prints a diagnostic on each resolve.
                    # Suppress that polling noise while keeping automation
                    # transition logs emitted below.
                    with contextlib.redirect_stdout(io.StringIO()):
                        resolved = resolver(raw)
                app_mod.state["presence"] = resolved
                automation_mod._run_arrival_automation(resolved)
        except Exception as exc:
            # Log only evaluator failures, never raw presence payloads.
            print(f"automation evaluator error: error={type(exc).__name__}", flush=True)
        time.sleep(PRESENCE_EVALUATION_SEC)


def _start_presence_evaluator(app_mod: Any, automation_mod: Any) -> None:
    global _presence_thread_started
    with _state_lock:
        if _presence_thread_started:
            return
        _presence_thread_started = True
    thread = threading.Thread(
        target=_presence_loop,
        args=(app_mod, automation_mod),
        name="presence-arrival-evaluator",
        daemon=True,
    )
    thread.start()


def _zone_worker(device: Dict[str, Any], body_data: Dict[str, Any], preset: Dict[str, Any] | None, output: Any) -> None:
    """Execute one device in an isolated process so a Tuya hang is killable."""
    try:
        from backend import app as app_module

        target = app_module.device_target(device)
        action = str(body_data.get("action") or "").strip().lower()
        steps = []

        def run_step(name: str, command: Any) -> bool:
            try:
                app_module.apply_light(device, command)
                steps.append({"step": name, "ok": True})
                return True
            except Exception as exc:  # process boundary: return safe class only
                steps.append({"step": name, "ok": False, "error": _safe_zone_error(exc)})
                return False

        if action == "brightness":
            ok = run_step(
                "brightness",
                app_module.LightCommand(target=target, action="brightness", value=int(body_data.get("value"))),
            )
        elif action in ("temperature", "temp", "cct"):
            ok = run_step(
                "temperature",
                app_module.LightCommand(target=target, action="temperature", value=int(body_data.get("value"))),
            )
        elif action == "rgb":
            ok = run_step(
                "rgb",
                app_module.LightCommand(
                    target=target,
                    action="rgb",
                    h=int(body_data.get("h")),
                    s=int(body_data.get("s")),
                    v=int(body_data.get("v")),
                ),
            )
        elif action == "preset" and preset:
            mode = str(preset.get("mode") or "")
            if mode == "white":
                # Both steps are attempted independently.
                brightness_ok = run_step(
                    "brightness",
                    app_module.LightCommand(target=target, action="brightness", value=int(preset["brightness"])),
                )
                temperature_ok = run_step(
                    "temperature",
                    app_module.LightCommand(target=target, action="temperature", value=int(preset["temperature"])),
                )
                ok = brightness_ok and temperature_ok
            elif mode == "brightness":
                ok = run_step(
                    "brightness",
                    app_module.LightCommand(target=target, action="brightness", value=int(preset["value"])),
                )
            elif mode == "temperature":
                ok = run_step(
                    "temperature",
                    app_module.LightCommand(target=target, action="temperature", value=int(preset["value"])),
                )
            elif mode == "colour":
                ok = run_step(
                    "rgb",
                    app_module.LightCommand(
                        target=target,
                        action="rgb",
                        h=int(preset["h"]),
                        s=int(preset["s"]),
                        v=int(preset["v"]),
                    ),
                )
            else:
                ok = False
                steps.append({"step": "preset", "ok": False, "error": "capability not supported"})
        else:
            ok = False
            steps.append({"step": action or "command", "ok": False, "error": "command failed"})

        output.put({"ok": bool(ok), "steps": steps})
    except Exception as exc:
        output.put({"ok": False, "error": _safe_zone_error(exc)})


def _run_device_bounded(device: Dict[str, Any], body_data: Dict[str, Any], preset: Dict[str, Any] | None) -> Dict[str, Any]:
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
            result = output.get_nowait()
        except queue.Empty:
            result = {"ok": False, "error": "command failed"}
    try:
        output.close()
        output.join_thread()
    except Exception:
        pass
    return result


def _optimistic_cache(app_mod: Any, device: Dict[str, Any], body_data: Dict[str, Any], preset: Dict[str, Any] | None) -> None:
    action = str(body_data.get("action") or "").strip().lower()
    dps: Dict[str, Any] = {}
    if action == "brightness":
        dps["22"] = int(body_data["value"])
    elif action in ("temperature", "temp", "cct"):
        dps.update({"21": "white", "23": int(body_data["value"])})
    elif action == "rgb":
        dps.update({"21": "colour", "24": app_mod.hsv_hex(int(body_data["h"]), int(body_data["s"]), int(body_data["v"]))})
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


def _refresh_success_async(app_mod: Any, device: Dict[str, Any]) -> None:
    def refresh() -> None:
        try:
            result = app_mod.read_status_once(device, "zone-refresh", timeout_sec=3.0)
            if app_mod.tuya_ok(result):
                dps = result.get("dps") or result.get("data", {}).get("dps") or {}
                if dps:
                    app_mod.cache_dps(device, dps, "zone-refresh")
        except Exception:
            # Last command cache remains valid if refresh fails.
            return

    threading.Thread(target=refresh, name="tuya-zone-refresh", daemon=True).start()


def _install_zone_route(app_mod: Any, extensions: Any) -> None:
    global _route_installed
    with _state_lock:
        if _route_installed:
            return
        _route_installed = True

    ZoneCommand = extensions.ZoneCommand

    def lighting_zone_command(body: ZoneCommand):
        zone = body.zone.strip()
        zones = extensions._zones()
        if zone not in zones:
            raise extensions.HTTPException(status_code=404, detail="zone not configured")

        requested_action = body.action.strip().lower()
        normalized_action = "temperature" if requested_action in ("temperature", "temp", "cct") else requested_action
        presets, _ = extensions._presets()
        preset = presets.get(str(body.preset or "")) if normalized_action == "preset" else None
        if normalized_action == "preset" and not preset:
            raise extensions.HTTPException(status_code=404, detail="preset not configured")
        if normalized_action not in ("brightness", "temperature", "rgb", "preset"):
            raise extensions.HTTPException(status_code=400, detail="unsupported zone action")

        devices, missing = extensions._zone_devices(zone)
        body_data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        results_by_id: Dict[str, Dict[str, Any]] = {}
        runnable = []
        for device in devices:
            device_id = extensions._device_key(device)
            if not extensions._supports(device, normalized_action, preset):
                results_by_id[device_id] = {
                    "deviceid": device_id,
                    "ok": False,
                    "unsupported": True,
                    "error": "capability not supported",
                }
            else:
                runnable.append(device)

        # At most two distinct bulbs are active at once. Each operation is a
        # killable process with its own hard deadline.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=ZONE_MAX_PARALLEL) as executor:
            futures = {executor.submit(_run_device_bounded, device, body_data, preset): device for device in runnable}
            for future in as_completed(futures):
                device = futures[future]
                device_id = extensions._device_key(device)
                try:
                    outcome = future.result()
                except Exception as exc:
                    outcome = {"ok": False, "error": _safe_zone_error(exc)}
                item = {"deviceid": device_id, **outcome}
                if not item.get("ok"):
                    item["error"] = _safe_zone_error(item.get("error") or item.get("steps"))
                else:
                    _optimistic_cache(app_mod, device, body_data, preset)
                    _refresh_success_async(app_mod, device)
                results_by_id[device_id] = item

        results = [results_by_id[extensions._device_key(device)] for device in devices]
        success = sum(1 for item in results if item.get("ok"))
        return {
            "ok": success > 0,
            "zone": zone,
            "action": normalized_action,
            "preset": body.preset,
            "partial": success != len(results),
            "missing_members": missing,
            "results": results,
        }

    app = app_mod.app
    app.router.routes = [
        route
        for route in app.router.routes
        if not (
            getattr(route, "path", None) == "/api/lighting/zone"
            and "POST" in set(getattr(route, "methods", set()) or set())
        )
    ]
    app.add_api_route("/api/lighting/zone", lighting_zone_command, methods=["POST"])


def _bootstrap() -> None:
    # Wait for app_runtime to finish importing dashboard_extensions so its
    # original routes exist before replacing only the zone command endpoint.
    deadline = time.time() + 60
    while time.time() < deadline:
        app_mod = sys.modules.get("backend.app")
        extensions = sys.modules.get("backend.dashboard_extensions")
        automation_mod = sys.modules.get("sonoff_client")
        if app_mod is not None and extensions is not None and automation_mod is not None:
            try:
                _install_zone_route(app_mod, extensions)
                _start_presence_evaluator(app_mod, automation_mod)
                return
            except Exception as exc:
                print(f"runtime stability setup error: error={type(exc).__name__}", flush=True)
        time.sleep(0.25)


def start() -> None:
    global _bootstrap_started
    with _state_lock:
        if _bootstrap_started:
            return
        _bootstrap_started = True
    threading.Thread(target=_bootstrap, name="runtime-stability-bootstrap", daemon=True).start()


start()
