"""HOTFIX PACK 13: bounded, event-driven automation trigger scheduling.

This module replaces the STORY 6.2 fixed one-second worker after that module is
loaded. It does not execute actions, publish MQTT, poll devices, or call Home
Assistant. Runtime trigger context is assembled only from existing in-memory
state and the existing PJ-1103 bridge cache.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, Optional

from backend import app as app_module
from backend import automation_core as core
from backend import automation_trigger_engine as engine

log = logging.getLogger("automation-trigger-worker")

IDLE_MIN_SEC = 5.0
IDLE_MAX_SEC = 60.0
STATE_CHECK_SEC = 5.0
SLOW_CYCLE_MS = 500.0
SLOW_BACKOFF_SEC = 5.0
WARNING_RATE_LIMIT_SEC = 300.0

_CACHE_LOCK = threading.RLock()
_AUTOMATION_CACHE: Dict[str, Any] = {"mtime_ns": None, "size": None, "items": []}
_LAST_SNAPSHOT_SIGNATURES: Dict[str, Any] = {}
_TIMING: Dict[str, Any] = {
    "cycle_count": 0,
    "last_cycle_duration_ms": 0.0,
    "max_cycle_duration_ms": 0.0,
    "total_cycle_duration_ms": 0.0,
    "context_build_count": 0,
    "automation_reload_count": 0,
    "idle_wake_count": 0,
    "due_rule_count": 0,
    "next_wake_ts": None,
    "last_slow_warning_monotonic": 0.0,
}


def _safe_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "", "unknown", "unavailable") else float(value)
    except (TypeError, ValueError):
        return None


def _app_state_mapping(*keys: str) -> Mapping[str, Any]:
    current: Any = getattr(app_module, "state", {})
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return _safe_mapping(current)


def _cached_electricity() -> Mapping[str, Any]:
    """Read the existing in-process PJ-1103 cache only; never refresh a provider."""
    try:
        from backend import pj1103_electricity_bridge as bridge
        value = bridge.local_state()
        if isinstance(value, Mapping) and value:
            return value
        value = bridge.retained_state()
        return _safe_mapping(value)
    except Exception:
        return _app_state_mapping("electricity")


def _read_runtime_snapshot(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Cheap snapshot: in-memory reads only, with no network or filesystem scan."""
    electricity = _cached_electricity()
    presence = _app_state_mapping("presence") or _app_state_mapping("presence_state")
    pm = _app_state_mapping("pm25") or _app_state_mapping("air_quality")
    temperature = _app_state_mapping("temperature") or _app_state_mapping("system_temperature")
    system = _app_state_mapping("system") or _safe_mapping(getattr(app_module, "state", {}))
    beer = presence.get("beer")
    seem = presence.get("seem")
    normalized = [str(value).lower() for value in (beer, seem) if value is not None]
    living = _number(pm.get("living_room"))
    bedroom = _number(pm.get("bedroom"))
    current = now or datetime.now().astimezone()
    mqtt_value = system.get("mqtt_connected")
    return {
        "electricity": {
            "power": _number(electricity.get("power")),
            "voltage": _number(electricity.get("voltage")),
            "current": _number(electricity.get("current")),
            "health": electricity.get("health"),
        },
        "presence": {
            "beer": beer,
            "seem": seem,
            "any_home": True if "home" in normalized else (False if len(normalized) == 2 else None),
            "all_away": True if normalized == ["away", "away"] else (False if "home" in normalized else None),
        },
        "pm25": {"living_room": living, "bedroom": bedroom, "maximum": max([v for v in (living, bedroom) if v is not None], default=None)},
        "temperature": {"cpu": _number(temperature.get("cpu"))},
        "system": {
            "mqtt_connected": mqtt_value if isinstance(mqtt_value, bool) else None,
            "dashboard_health": system.get("dashboard_health"),
        },
        "time": {"hour": current.hour, "minute": current.minute, "weekday": current.weekday()},
    }


def _context_signature(context: Mapping[str, Any], namespace: str) -> Any:
    values = _safe_mapping(context.get(namespace))
    return tuple(sorted((str(key), repr(value)) for key, value in values.items()))


def _load_automations_cached() -> list[Dict[str, Any]]:
    path = core.AUTOMATIONS_PATH
    try:
        stat = path.stat()
        marker = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        marker = (None, None)
    with _CACHE_LOCK:
        if marker == (_AUTOMATION_CACHE["mtime_ns"], _AUTOMATION_CACHE["size"]):
            return copy.deepcopy(_AUTOMATION_CACHE["items"])
        items = core._load_store().get("automations", [])
        _AUTOMATION_CACHE.update({"mtime_ns": marker[0], "size": marker[1], "items": copy.deepcopy(items)})
        _TIMING["automation_reload_count"] += 1
        return copy.deepcopy(items)


def invalidate_automation_cache() -> None:
    with _CACHE_LOCK:
        _AUTOMATION_CACHE["mtime_ns"] = object()
        _AUTOMATION_CACHE["size"] = object()
    engine._STOP.set()  # wake Event.wait immediately; the worker clears it after replacement startup only


def _next_minute_epoch(now_ts: float) -> float:
    current = datetime.fromtimestamp(now_ts).astimezone()
    boundary = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return boundary.timestamp()


def _interval_due_ts(automation: Mapping[str, Any], now_ts: float) -> float:
    trigger = _safe_mapping(automation.get("trigger"))
    every = max(1, int(trigger.get("every_sec") or 1))
    automation_id = str(automation.get("id") or "")
    last = float(engine._STATE["last_triggered"].get(automation_id) or automation.get("last_triggered_ts") or automation.get("created_ts") or now_ts)
    due = last + every
    if due <= now_ts - every:
        # Falling far behind must not create a catch-up busy loop.
        return now_ts + max(1.0, float(every))
    return due


def _classify_rules(items: list[Dict[str, Any]]) -> Dict[str, list[Dict[str, Any]]]:
    groups: Dict[str, list[Dict[str, Any]]] = {kind: [] for kind in engine.SUPPORTED_TRIGGER_TYPES}
    for automation in items:
        if not automation.get("enabled"):
            continue
        kind = str(_safe_mapping(automation.get("trigger")).get("type") or "")
        if kind in groups:
            groups[kind].append(automation)
    return groups


def _due_rules(groups: Mapping[str, list[Dict[str, Any]]], now_ts: float, snapshot: Optional[Mapping[str, Any]]) -> tuple[list[Dict[str, Any]], float]:
    due: list[Dict[str, Any]] = []
    wake_candidates = [now_ts + IDLE_MAX_SEC]

    interval_rules = groups.get("interval", [])
    for automation in interval_rules:
        due_ts = _interval_due_ts(automation, now_ts)
        if due_ts <= now_ts + 0.05:
            due.append(automation)
        else:
            wake_candidates.append(due_ts)

    time_rules = groups.get("time", [])
    if time_rules:
        minute_key = datetime.fromtimestamp(now_ts).astimezone().strftime("%Y%m%d%H%M")
        if engine._STATE.get("scheduler_minute_key") != minute_key:
            engine._STATE["scheduler_minute_key"] = minute_key
            due.extend(time_rules)
        wake_candidates.append(_next_minute_epoch(now_ts))

    state_kinds = ("electricity", "presence", "pm25", "temperature", "system")
    if any(groups.get(kind) for kind in state_kinds):
        wake_candidates.append(now_ts + STATE_CHECK_SEC)
        if snapshot is not None:
            for kind in state_kinds:
                rules = groups.get(kind, [])
                if not rules:
                    continue
                signature = _context_signature(snapshot, kind)
                if _LAST_SNAPSHOT_SIGNATURES.get(kind) != signature:
                    _LAST_SNAPSHOT_SIGNATURES[kind] = signature
                    due.extend(rules)

    return due, min(wake_candidates)


def evaluate_worker_cycle(now_ts: Optional[int] = None, context: Optional[Mapping[str, Any]] = None) -> int:
    """Compatibility entry point used by tests; performs one bounded due evaluation."""
    wall_now = float(now_ts or time.time())
    items = _load_automations_cached()
    groups = _classify_rules(items)
    snapshot = dict(context) if context is not None else _read_runtime_snapshot()
    due, _ = _due_rules(groups, wall_now, snapshot)
    if not due:
        return 0
    _TIMING["context_build_count"] += 1
    triggered = 0
    for automation in due:
        try:
            detected, reason = engine._detect_trigger(automation, snapshot, int(wall_now))
            if detected:
                result = engine.process_detected_trigger(automation, reason, snapshot, int(wall_now))
                triggered += 1 if result.get("pending_actions") else 0
        except Exception as exc:
            core._audit("validation_failed", str(automation.get("id") or "") or None, "trigger_error", type(exc).__name__)
    return triggered


def _record_cycle(duration_ms: float, due_count: int, next_wake_ts: float) -> None:
    with engine._RUNTIME_LOCK:
        _TIMING["cycle_count"] += 1
        _TIMING["last_cycle_duration_ms"] = round(duration_ms, 3)
        _TIMING["max_cycle_duration_ms"] = round(max(float(_TIMING["max_cycle_duration_ms"]), duration_ms), 3)
        _TIMING["total_cycle_duration_ms"] += duration_ms
        _TIMING["due_rule_count"] += due_count
        _TIMING["next_wake_ts"] = int(next_wake_ts)


def _optimized_worker() -> None:
    with engine._RUNTIME_LOCK:
        engine._STATE["scheduler_running"] = True
        engine._STATE["worker_alive"] = True
    engine._STOP.clear()
    next_state_check = 0.0
    while not engine._STOP.is_set():
        cycle_started = time.monotonic()
        wall_now = time.time()
        due_count = 0
        next_wake = wall_now + IDLE_MAX_SEC
        try:
            items = _load_automations_cached()
            groups = _classify_rules(items)
            has_state_rules = any(groups.get(kind) for kind in ("electricity", "presence", "pm25", "temperature", "system"))
            snapshot = None
            if has_state_rules and wall_now >= next_state_check:
                snapshot = _read_runtime_snapshot()
                next_state_check = wall_now + STATE_CHECK_SEC
            due, next_wake = _due_rules(groups, wall_now, snapshot)
            due_count = len(due)
            if due:
                if snapshot is None:
                    snapshot = _read_runtime_snapshot()
                _TIMING["context_build_count"] += 1
                for automation in due:
                    try:
                        detected, reason = engine._detect_trigger(automation, snapshot, int(wall_now))
                        if detected:
                            engine.process_detected_trigger(automation, reason, snapshot, int(wall_now))
                    except Exception as exc:
                        core._audit("validation_failed", str(automation.get("id") or "") or None, "trigger_error", type(exc).__name__)
            else:
                _TIMING["idle_wake_count"] += 1
        except Exception:
            with engine._RUNTIME_LOCK:
                engine._STATE["worker_errors"] += 1
            next_wake = wall_now + IDLE_MIN_SEC

        duration_ms = (time.monotonic() - cycle_started) * 1000.0
        if duration_ms > SLOW_CYCLE_MS:
            now_mono = time.monotonic()
            if now_mono - float(_TIMING["last_slow_warning_monotonic"]) >= WARNING_RATE_LIMIT_SEC:
                _TIMING["last_slow_warning_monotonic"] = now_mono
                log.warning("AUTOMATION_TRIGGER_SLOW_CYCLE duration_ms=%.1f due_rules=%s", duration_ms, due_count)
            next_wake = max(next_wake, time.time() + SLOW_BACKOFF_SEC)

        # Never catch up in a zero-wait loop. A late scheduler resets from now.
        current_wall = time.time()
        if next_wake <= current_wall:
            next_wake = current_wall + 1.0
        _record_cycle(duration_ms, due_count, next_wake)
        timeout = max(1.0, min(IDLE_MAX_SEC, next_wake - current_wall))
        if due_count == 0 and not any(_classify_rules(_load_automations_cached()).get(k) for k in ("interval", "time", "electricity", "presence", "pm25", "temperature", "system")):
            timeout = max(IDLE_MIN_SEC, timeout)
        if engine._STOP.wait(timeout):
            break

    with engine._RUNTIME_LOCK:
        engine._STATE["scheduler_running"] = False
        engine._STATE["worker_alive"] = False


def _start_optimized_worker() -> threading.Thread:
    with engine._RUNTIME_LOCK:
        if engine._THREAD and engine._THREAD.is_alive():
            return engine._THREAD
        engine._STOP.clear()
        engine._THREAD = threading.Thread(target=_optimized_worker, name="automation-trigger-worker", daemon=True)
        engine._THREAD.start()
        return engine._THREAD


def _runtime_status() -> Dict[str, Any]:
    base = {
        "scheduler_running": bool(engine._STATE["scheduler_running"]),
        "worker_alive": bool(engine._THREAD and engine._THREAD.is_alive()),
        "trigger_count": int(engine._STATE["trigger_count"]),
        "pending_queue": list(engine._PENDING),
        "pending_queue_count": len(engine._PENDING),
        "cooldown_count": int(engine._STATE["cooldown_count"]),
        "last_trigger": copy.deepcopy(engine._STATE["last_trigger"]),
        "worker_errors": int(engine._STATE["worker_errors"]),
        "execution_enabled": False,
    }
    cycles = int(_TIMING["cycle_count"])
    base.update({
        "cycle_count": cycles,
        "last_cycle_duration_ms": _TIMING["last_cycle_duration_ms"],
        "max_cycle_duration_ms": _TIMING["max_cycle_duration_ms"],
        "context_build_count": int(_TIMING["context_build_count"]),
        "automation_reload_count": int(_TIMING["automation_reload_count"]),
        "idle_wake_count": int(_TIMING["idle_wake_count"]),
        "due_rule_count": int(_TIMING["due_rule_count"]),
        "average_cycle_duration_ms": round(float(_TIMING["total_cycle_duration_ms"]) / cycles, 3) if cycles else 0.0,
        "next_wake_ts": _TIMING["next_wake_ts"],
    })
    return base


def _replace_runtime_route() -> None:
    for route in app_module.app.routes:
        if getattr(route, "path", None) == "/api/automations/runtime" and "GET" in set(getattr(route, "methods", set()) or set()):
            route.endpoint = _runtime_status
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = _runtime_status


# Stop the fixed one-second worker before installing the optimized replacement.
engine._STOP.set()
old_thread = engine._THREAD
if old_thread and old_thread.is_alive():
    old_thread.join(timeout=2.5)
engine.evaluate_worker_cycle = evaluate_worker_cycle
engine._worker = _optimized_worker
engine.start_worker = _start_optimized_worker
engine.runtime_status = _runtime_status
_replace_runtime_route()
_start_optimized_worker()
