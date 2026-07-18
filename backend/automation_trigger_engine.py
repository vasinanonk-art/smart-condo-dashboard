"""Single-threaded, non-executing automation trigger engine.

STORY 6.2 detects triggers, evaluates existing declarative conditions, and queues
pending actions in memory. It never publishes MQTT, calls device integrations,
or executes an action.
"""
from __future__ import annotations

import copy
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import automation_core as core

app = app_module.app
SUPPORTED_TRIGGER_TYPES = {"time", "interval", "electricity", "presence", "pm25", "temperature", "system", "manual"}
FIELD_TRIGGER_MAP = {
    "electricity": {"power": "electricity.power", "voltage": "electricity.voltage", "current": "electricity.current", "health": "electricity.health"},
    "pm25": {"living_room": "pm25.living_room", "bedroom": "pm25.bedroom", "maximum": "pm25.maximum"},
    "temperature": {"cpu": "temperature.cpu"},
    "system": {"mqtt_connected": "system.mqtt_connected", "dashboard_health": "system.dashboard_health"},
}
PRESENCE_EVENTS = {
    "beer_arrives": ("presence.beer", "away", "home"),
    "beer_leaves": ("presence.beer", "home", "away"),
    "seem_arrives": ("presence.seem", "away", "home"),
    "seem_leaves": ("presence.seem", "home", "away"),
    "anyone_home": ("presence.any_home", False, True),
    "everyone_away": ("presence.all_away", False, True),
}
QUEUE_LIMIT = 100
WORKER_INTERVAL_SEC = 1.0
_RUNTIME_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None
_PENDING: deque[Dict[str, Any]] = deque(maxlen=QUEUE_LIMIT)
_STATE: Dict[str, Any] = {
    "scheduler_running": False,
    "worker_alive": False,
    "trigger_count": 0,
    "cooldown_count": 0,
    "last_trigger": None,
    "last_values": {},
    "last_fire_keys": {},
    "last_triggered": {},
    "worker_errors": 0,
}


def _trigger_error(message: str) -> None:
    raise core.ValidationFailure([message])


def _int(value: Any, name: str, minimum: int, maximum: Optional[int] = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise core.ValidationFailure([f"{name} must be an integer."]) from exc
    if result < minimum or (maximum is not None and result > maximum):
        raise core.ValidationFailure([f"{name} is outside the supported range."])
    return result


def _parse_time(value: Any) -> Tuple[int, int]:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        _trigger_error("Exact time must use HH:MM.")
    return _int(parts[0], "Hour", 0, 23), _int(parts[1], "Minute", 0, 59)


def _parse_weekdays(value: Any) -> list[int]:
    if value in (None, "", []):
        return list(range(7))
    if value == "weekday":
        return [0, 1, 2, 3, 4]
    if value == "weekend":
        return [5, 6]
    if not isinstance(value, list) or not value:
        _trigger_error("Weekday must be weekday, weekend, or a non-empty list.")
    return sorted({_int(item, "Weekday", 0, 6) for item in value})


def _cron_part(value: str, minimum: int, maximum: int, name: str) -> Optional[set[int]]:
    value = value.strip()
    if value == "*":
        return None
    result: set[int] = set()
    for piece in value.split(","):
        if "-" in piece:
            start_text, end_text = piece.split("-", 1)
            start = _int(start_text, name, minimum, maximum)
            end = _int(end_text, name, minimum, maximum)
            if end < start:
                _trigger_error(f"{name} range must be ascending.")
            result.update(range(start, end + 1))
        else:
            result.add(_int(piece, name, minimum, maximum))
    return result


def _parse_cron_lite(value: Any) -> Dict[str, Optional[set[int]]]:
    parts = str(value or "").strip().split()
    if len(parts) != 5:
        _trigger_error("cron-lite must contain minute hour day month weekday.")
    minute, hour, day, month, weekday = parts
    if day != "*" or month != "*":
        _trigger_error("cron-lite supports wildcard day and month only.")
    return {
        "minute": _cron_part(minute, 0, 59, "Minute"),
        "hour": _cron_part(hour, 0, 23, "Hour"),
        "weekday": _cron_part(weekday, 0, 6, "Weekday"),
    }


def validate_trigger(trigger: Any, errors: list[str]) -> Dict[str, Any]:
    if not isinstance(trigger, Mapping):
        errors.append("Trigger must be an object.")
        return {}
    if core._contains_forbidden(trigger) or core._json_size(trigger) > 8192:
        errors.append("Trigger contains unsupported or unsafe data.")
        return {}
    kind = str(trigger.get("type") or "").strip().lower()
    if kind not in SUPPORTED_TRIGGER_TYPES:
        errors.append("Trigger type must be time, interval, electricity, presence, pm25, temperature, system, or manual.")
        return {}
    try:
        if kind == "manual":
            return {"type": "manual"}
        if kind == "time":
            result: Dict[str, Any] = {"type": "time"}
            if trigger.get("cron") is not None:
                _parse_cron_lite(trigger.get("cron"))
                result["cron"] = str(trigger.get("cron")).strip()
            elif trigger.get("at") is not None:
                hour, minute = _parse_time(trigger.get("at"))
                result.update({"hour": hour, "minute": minute})
            else:
                result["hour"] = None if trigger.get("hour") in (None, "", "*") else _int(trigger.get("hour"), "Hour", 0, 23)
                result["minute"] = None if trigger.get("minute") in (None, "", "*") else _int(trigger.get("minute"), "Minute", 0, 59)
            result["weekdays"] = _parse_weekdays(trigger.get("weekdays") or trigger.get("days"))
            return result
        if kind == "interval":
            if trigger.get("every_sec") is not None:
                seconds = _int(trigger.get("every_sec"), "Interval", 1, 31_536_000)
            elif trigger.get("seconds") is not None:
                seconds = _int(trigger.get("seconds"), "Interval", 1, 31_536_000)
            elif trigger.get("minutes") is not None:
                seconds = _int(trigger.get("minutes"), "Interval minutes", 1, 525_600) * 60
            elif trigger.get("hours") is not None:
                seconds = _int(trigger.get("hours"), "Interval hours", 1, 8_760) * 3600
            else:
                _trigger_error("Interval trigger requires every_sec, seconds, minutes, or hours.")
            return {"type": "interval", "every_sec": seconds}
        if kind == "presence":
            event = str(trigger.get("event") or "").strip().lower()
            if event not in PRESENCE_EVENTS:
                _trigger_error("Presence event is unsupported.")
            return {"type": "presence", "event": event}
        field = str(trigger.get("field") or "").strip()
        mapped = FIELD_TRIGGER_MAP[kind].get(field)
        if not mapped:
            _trigger_error(f"Unsupported {kind} trigger field.")
        event = str(trigger.get("event") or "threshold").strip().lower()
        if event == "change":
            return {"type": kind, "field": field, "event": "change"}
        operator = str(trigger.get("operator") or "").strip().lower()
        allowed = core.TYPE_OPERATORS[core.FIELD_TYPES[mapped]] - {"exists", "not_exists", "in", "not_in"}
        if operator not in allowed:
            _trigger_error(f"Operator {operator or 'missing'} is not valid for {mapped} trigger.")
        value = trigger.get("value")
        if not core._safe_scalar(value):
            _trigger_error("Trigger threshold value is unsupported.")
        edge = str(trigger.get("edge") or "rising").strip().lower()
        if edge not in {"rising", "falling", "both"}:
            _trigger_error("Trigger edge must be rising, falling, or both.")
        return {"type": kind, "field": field, "operator": operator, "value": copy.deepcopy(value), "edge": edge}
    except core.ValidationFailure as exc:
        errors.extend(exc.errors)
        return {}


# STORY 6.1 validation resolves this global dynamically, so install the stricter
# trigger validator without changing storage, condition evaluation, or auth.
core._validate_trigger = validate_trigger


def _time_matches(trigger: Mapping[str, Any], now: datetime) -> Tuple[bool, str]:
    if trigger.get("cron"):
        parsed = _parse_cron_lite(trigger["cron"])
        matched = all(values is None or actual in values for values, actual in ((parsed["minute"], now.minute), (parsed["hour"], now.hour), (parsed["weekday"], now.weekday())))
    else:
        weekdays = trigger.get("weekdays") or list(range(7))
        matched = now.weekday() in weekdays and (trigger.get("hour") is None or now.hour == trigger.get("hour")) and (trigger.get("minute") is None or now.minute == trigger.get("minute"))
    return matched, now.strftime("%Y%m%d%H%M")


def _threshold_state(trigger: Mapping[str, Any], actual: Any) -> bool:
    return core._compare(actual, str(trigger.get("operator") or ""), trigger.get("value"))


def _detect_trigger(automation: Mapping[str, Any], context: Mapping[str, Any], now_ts: int) -> Tuple[bool, str]:
    trigger = automation.get("trigger") or {}
    kind = trigger.get("type")
    automation_id = str(automation.get("id") or "")
    if kind == "manual":
        return False, "manual_only"
    if kind == "time":
        matched, key = _time_matches(trigger, datetime.fromtimestamp(now_ts).astimezone())
        previous_key = _STATE["last_fire_keys"].get(automation_id)
        if matched and previous_key != key:
            _STATE["last_fire_keys"][automation_id] = key
            return True, "time_match"
        return False, "time_not_due"
    if kind == "interval":
        last = int(_STATE["last_triggered"].get(automation_id) or automation.get("last_triggered_ts") or automation.get("created_ts") or now_ts)
        return (now_ts - last >= int(trigger.get("every_sec") or 1), "interval_due" if now_ts - last >= int(trigger.get("every_sec") or 1) else "interval_not_due")
    if kind == "presence":
        field, before, after = PRESENCE_EVENTS[str(trigger.get("event"))]
        actual = core.resolve_field(context, field)
        state_key = f"{automation_id}:{field}"
        previous = _STATE["last_values"].get(state_key)
        _STATE["last_values"][state_key] = actual
        return (previous == before and actual == after, "presence_transition" if previous == before and actual == after else "no_transition")
    field = FIELD_TRIGGER_MAP[str(kind)][str(trigger.get("field"))]
    actual = core.resolve_field(context, field)
    state_key = f"{automation_id}:{field}"
    previous = _STATE["last_values"].get(state_key)
    _STATE["last_values"][state_key] = actual
    if previous is None:
        return False, "baseline_recorded"
    if trigger.get("event") == "change":
        return (actual != previous, "value_changed" if actual != previous else "no_change")
    previous_state = _threshold_state(trigger, previous)
    current_state = _threshold_state(trigger, actual)
    edge = trigger.get("edge", "rising")
    fired = (edge in {"rising", "both"} and not previous_state and current_state) or (edge in {"falling", "both"} and previous_state and not current_state)
    return fired, "threshold_crossed" if fired else "no_threshold_crossing"


def _cooldown_active(automation: Mapping[str, Any], now_ts: int) -> bool:
    cooldown = int(automation.get("cooldown_sec") or 0)
    last = int(_STATE["last_triggered"].get(str(automation.get("id"))) or automation.get("last_triggered_ts") or 0)
    return cooldown > 0 and last > 0 and now_ts - last < cooldown


def _update_automation_result(automation_id: str, now_ts: int, result: str) -> None:
    store, index = core._find(automation_id)
    if index is None:
        return
    store["automations"][index]["last_triggered_ts"] = now_ts
    store["automations"][index]["last_result"] = result
    store["automations"][index]["updated_ts"] = now_ts
    core._atomic_save(store)


def _queue_pending(automation: Mapping[str, Any], now_ts: int, reason: str) -> Dict[str, Any]:
    entry = {"automation_id": automation["id"], "queued_ts": now_ts, "reason": reason, "expires_ts": now_ts + max(300, int(automation.get("cooldown_sec") or 0), 3600)}
    _PENDING.append(entry)
    return entry


def process_detected_trigger(automation: Mapping[str, Any], reason: str, context: Mapping[str, Any], now_ts: Optional[int] = None, audit_event: str = "trigger_detected") -> Dict[str, Any]:
    now_ts = int(now_ts or time.time())
    automation_id = str(automation.get("id") or "")
    core._audit(audit_event, automation_id, "detected", reason)
    if _cooldown_active(automation, now_ts):
        with _RUNTIME_LOCK:
            _STATE["cooldown_count"] += 1
        core._audit("cooldown_active", automation_id, "ignored", reason)
        return {"matched": False, "conditions_passed": False, "pending_actions": False, "reason": "cooldown_active", "execution_enabled": False}
    evaluation = core.evaluate_automation(automation, context)
    if not evaluation.get("conditions_passed") or not automation.get("enabled"):
        core._audit("condition_failed", automation_id, "not_matched", evaluation.get("reason") or "condition_failed")
        return {**evaluation, "pending_actions": False, "execution_enabled": False}
    queue_entry = _queue_pending(automation, now_ts, reason)
    with _RUNTIME_LOCK:
        _STATE["trigger_count"] += 1
        _STATE["last_trigger"] = {"automation_id": automation_id, "ts": now_ts, "reason": reason}
        _STATE["last_triggered"][automation_id] = now_ts
    try:
        _update_automation_result(automation_id, now_ts, "pending_actions")
    except Exception:
        pass
    core._audit("condition_matched", automation_id, "pending", reason)
    return {**evaluation, "matched": True, "pending_actions": True, "queue_entry": queue_entry, "actions_executed": False, "execution_enabled": False, "reason": "pending_actions"}


def evaluate_worker_cycle(now_ts: Optional[int] = None, context: Optional[Mapping[str, Any]] = None) -> int:
    now_ts = int(now_ts or time.time())
    active_context = context or core.build_automation_context()
    triggered = 0
    for automation in core._load_store()["automations"]:
        if not automation.get("enabled"):
            continue
        try:
            detected, reason = _detect_trigger(automation, active_context, now_ts)
            if detected:
                result = process_detected_trigger(automation, reason, active_context, now_ts)
                triggered += 1 if result.get("pending_actions") else 0
        except Exception as exc:
            core._audit("validation_failed", str(automation.get("id") or "") or None, "trigger_error", type(exc).__name__)
    return triggered


def _worker() -> None:
    with _RUNTIME_LOCK:
        _STATE["scheduler_running"] = True
        _STATE["worker_alive"] = True
    next_tick = time.monotonic()
    while not _STOP.is_set():
        try:
            evaluate_worker_cycle()
        except Exception:
            with _RUNTIME_LOCK:
                _STATE["worker_errors"] += 1
        next_tick += WORKER_INTERVAL_SEC
        wait = max(0.0, next_tick - time.monotonic())
        if _STOP.wait(wait):
            break
        if wait == 0.0 and time.monotonic() - next_tick > WORKER_INTERVAL_SEC:
            next_tick = time.monotonic()
    with _RUNTIME_LOCK:
        _STATE["scheduler_running"] = False
        _STATE["worker_alive"] = False


def start_worker() -> threading.Thread:
    global _THREAD
    with _RUNTIME_LOCK:
        if _THREAD and _THREAD.is_alive():
            return _THREAD
        _STOP.clear()
        _THREAD = threading.Thread(target=_worker, name="automation-trigger-worker", daemon=True)
        _THREAD.start()
        return _THREAD


def runtime_status() -> Dict[str, Any]:
    with _RUNTIME_LOCK:
        alive = bool(_THREAD and _THREAD.is_alive())
        return {
            "scheduler_running": bool(_STATE["scheduler_running"]),
            "worker_alive": alive,
            "trigger_count": int(_STATE["trigger_count"]),
            "pending_queue": list(_PENDING),
            "pending_queue_count": len(_PENDING),
            "cooldown_count": int(_STATE["cooldown_count"]),
            "last_trigger": copy.deepcopy(_STATE["last_trigger"]),
            "worker_errors": int(_STATE["worker_errors"]),
            "execution_enabled": False,
        }


@app.get("/api/automations/runtime")
def get_runtime_status() -> Dict[str, Any]:
    return runtime_status()


@app.post("/api/automations/{automation_id}/trigger")
def manual_trigger(automation_id: str, payload: Dict[str, Any] = Body(default={})):
    store, index = core._find(automation_id)
    if index is None:
        return JSONResponse({"detail": "automation_not_found"}, status_code=404)
    automation = store["automations"][index]
    if (automation.get("trigger") or {}).get("type") != "manual":
        return JSONResponse({"detail": "manual_trigger_not_configured"}, status_code=409)
    context = core.build_automation_context()
    try:
        if payload.get("context_override"):
            context = core._override_context(context, payload.get("context_override"))
    except core.ValidationFailure as exc:
        return core._safe_error_payload(exc.errors)
    core._audit("manual_trigger", automation_id, "requested", "manual")
    return process_detected_trigger(automation, "manual_trigger", context, audit_event="manual_trigger")


# Keep simulation non-executing while recording its trigger-specific audit event.
_original_simulate = core.simulate_automation


def simulate_with_trigger_audit(payload: Dict[str, Any] = Body(...)):
    response = _original_simulate(payload)
    automation = payload.get("automation") if isinstance(payload, Mapping) else None
    automation_id = str((automation or {}).get("id") or "") or None
    core._audit("simulation_trigger", automation_id, "evaluated", "execution_disabled")
    return response


for route in app.routes:
    if getattr(route, "path", None) == "/api/automations/simulate" and "POST" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = simulate_with_trigger_audit
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = simulate_with_trigger_audit


start_worker()
