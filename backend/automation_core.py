"""Safe, non-executing automation rule engine core for Smart Condo Dashboard.

STORY 6.1 deliberately evaluates and simulates rules only. It never publishes MQTT,
invokes a device command, starts a poller, or executes an action.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module

app = app_module.app
SCHEMA_VERSION = 1
MAX_JSON_BYTES = max(4096, int(os.getenv("AUTOMATION_MAX_JSON_BYTES", "65536")))
MAX_DEPTH = 8
MAX_CONDITIONS = 50
EVENT_RETENTION_DAYS = max(1, int(os.getenv("AUTOMATION_EVENT_RETENTION_DAYS", "90")))
DATA_DIR = Path(os.getenv("SMART_CONDO_DATA_DIR", str(Path.home() / ".smart-condo-dashboard"))).expanduser()
AUTOMATIONS_PATH = DATA_DIR / "automations.json"
EVENTS_PATH = DATA_DIR / "automation_events.jsonl"
_LOCK = threading.RLock()
_RUNTIME = {"last_validation_error": None, "last_simulation_ts": None}

MODES = {"single", "restart", "queued"}
OPERATORS = {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists", "not_exists"}
FORBIDDEN_TERMS = ("password", "token", "cookie", "secret", "credential", "local_key", "authentication", "csrf", "session")
FIELD_TYPES = {
    "electricity.power": "number", "electricity.voltage": "number", "electricity.current": "number", "electricity.health": "string",
    "presence.beer": "string", "presence.seem": "string", "presence.any_home": "boolean", "presence.all_away": "boolean",
    "pm25.living_room": "number", "pm25.bedroom": "number", "pm25.maximum": "number",
    "temperature.cpu": "number",
    "system.mqtt_connected": "boolean", "system.dashboard_health": "string",
    "time.hour": "number", "time.minute": "number", "time.weekday": "number",
}
TYPE_OPERATORS = {
    "number": {"eq", "ne", "gt", "gte", "lt", "lte", "exists", "not_exists"},
    "boolean": {"eq", "ne", "exists", "not_exists"},
    "string": {"eq", "ne", "in", "not_in", "exists", "not_exists"},
}
ID_RE = re.compile(r"^automation_[a-z0-9][a-z0-9_-]{2,63}$")


class ValidationFailure(ValueError):
    def __init__(self, errors: Iterable[str]):
        self.errors = list(errors)
        super().__init__(self.errors[0] if self.errors else "invalid_automation")


def _safe_error_payload(errors: Iterable[str], status_code: int = 422) -> JSONResponse:
    clean = [str(item)[:180] for item in errors]
    _RUNTIME["last_validation_error"] = clean[0] if clean else "invalid_automation"
    return JSONResponse({"ok": False, "errors": clean}, status_code=status_code)


def _json_size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _contains_forbidden(value: Any, path: str = "") -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            current = f"{path}.{key}".lower()
            if any(term in current for term in FORBIDDEN_TERMS) or _contains_forbidden(item, current):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden(item, path) for item in value)
    return False


def _safe_scalar(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    return isinstance(value, str) and len(value) <= 512


def _validate_trigger(trigger: Any, errors: list[str]) -> Dict[str, Any]:
    if not isinstance(trigger, Mapping):
        errors.append("Trigger must be an object.")
        return {}
    if _contains_forbidden(trigger) or _json_size(trigger) > 8192:
        errors.append("Trigger contains unsupported or unsafe data.")
        return {}
    # Core story stores declarative trigger metadata only; no trigger is activated.
    kind = trigger.get("type")
    if kind is not None and (not isinstance(kind, str) or len(kind) > 80):
        errors.append("Trigger type must be a short string.")
    return copy.deepcopy(dict(trigger))


def _validate_actions(actions: Any, errors: list[str]) -> list[Any]:
    if not isinstance(actions, list):
        errors.append("Actions must be a list.")
        return []
    if len(actions) > 20 or _json_size(actions) > 16384 or _contains_forbidden(actions):
        errors.append("Actions contain unsupported or unsafe data.")
        return []
    for item in actions:
        if not isinstance(item, Mapping):
            errors.append("Each action placeholder must be an object.")
            break
    return copy.deepcopy(actions)


def _condition_stats(node: Any, depth: int = 1) -> Tuple[int, int]:
    if depth > MAX_DEPTH:
        raise ValidationFailure([f"Condition nesting exceeds maximum depth {MAX_DEPTH}."])
    if not isinstance(node, Mapping):
        raise ValidationFailure(["Condition must be an object."])
    groups = [key for key in ("and", "or", "not") if key in node]
    if groups:
        if len(groups) != 1 or len(node) != 1:
            raise ValidationFailure(["Boolean condition groups must contain only and, or, or not."])
        key = groups[0]
        value = node[key]
        if key in {"and", "or"}:
            if not isinstance(value, list) or not value:
                raise ValidationFailure([f"{key.upper()} group must contain at least one condition."])
            total = 0
            deepest = depth
            for child in value:
                child_count, child_depth = _condition_stats(child, depth + 1)
                total += child_count
                deepest = max(deepest, child_depth)
            return total, deepest
        child_count, child_depth = _condition_stats(value, depth + 1)
        return child_count, child_depth
    return 1, depth


def validate_condition(node: Any) -> Dict[str, Any]:
    count, _ = _condition_stats(node)
    if count > MAX_CONDITIONS:
        raise ValidationFailure([f"Condition count exceeds maximum {MAX_CONDITIONS}."])

    def walk(item: Mapping[str, Any]) -> Dict[str, Any]:
        for group in ("and", "or"):
            if group in item:
                return {group: [walk(child) for child in item[group]]}
        if "not" in item:
            return {"not": walk(item["not"])}
        field = item.get("field")
        operator = item.get("operator")
        if field not in FIELD_TYPES:
            raise ValidationFailure([f"Unsupported condition field: {field or 'missing' }."])
        if operator not in OPERATORS or operator not in TYPE_OPERATORS[FIELD_TYPES[field]]:
            raise ValidationFailure([f"Operator {operator or 'missing'} is not valid for {field}."])
        value = item.get("value")
        if operator not in {"exists", "not_exists"}:
            if operator in {"in", "not_in"}:
                if not isinstance(value, list) or not value or len(value) > 50 or not all(_safe_scalar(v) for v in value):
                    raise ValidationFailure([f"Operator {operator} requires a safe, non-empty list."])
            elif not _safe_scalar(value):
                raise ValidationFailure([f"Condition value for {field} is unsupported."])
        return {"field": field, "operator": operator, **({} if operator in {"exists", "not_exists"} else {"value": copy.deepcopy(value)})}

    return walk(node)


def _new_id() -> str:
    return f"automation_{uuid.uuid4().hex[:12]}"


def validate_automation(raw: Any, existing_ids: Optional[set[str]] = None, current_id: Optional[str] = None) -> Dict[str, Any]:
    errors: list[str] = []
    if not isinstance(raw, Mapping):
        raise ValidationFailure(["Automation must be a JSON object."])
    if _json_size(raw) > MAX_JSON_BYTES:
        raise ValidationFailure([f"Automation exceeds maximum JSON size of {MAX_JSON_BYTES} bytes."])
    if _contains_forbidden(raw):
        raise ValidationFailure(["Automation contains a forbidden secret or authentication field."])
    now = int(time.time())
    automation_id = str(raw.get("id") or current_id or _new_id()).strip().lower()
    if not ID_RE.fullmatch(automation_id):
        errors.append("Automation ID must start with automation_ and contain only lowercase letters, numbers, dash, or underscore.")
    if existing_ids and automation_id in existing_ids and automation_id != current_id:
        errors.append("Automation ID already exists.")
    name = str(raw.get("name") or "").strip()
    if not name:
        errors.append("Name is required.")
    if len(name) > 120:
        errors.append("Name must be 120 characters or fewer.")
    description = str(raw.get("description") or "").strip()[:1000]
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        errors.append("Enabled must be true or false.")
        enabled = False
    mode = str(raw.get("mode") or "single").lower()
    if mode not in MODES:
        errors.append("Mode must be single, restart, or queued.")
    try:
        cooldown = int(raw.get("cooldown_sec") or 0)
        if cooldown < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("Cooldown must be zero or a positive integer.")
        cooldown = 0
    trigger = _validate_trigger(raw.get("trigger") or {}, errors)
    actions = _validate_actions(raw.get("actions") or [], errors)
    condition_raw = raw.get("condition") or {"and": [{"field": "system.dashboard_health", "operator": "exists"}]}
    try:
        condition = validate_condition(condition_raw)
    except ValidationFailure as exc:
        errors.extend(exc.errors)
        condition = {}
    if errors:
        raise ValidationFailure(errors)
    return {
        "id": automation_id, "name": name, "description": description, "enabled": enabled, "mode": mode,
        "trigger": trigger, "condition": condition, "actions": actions, "cooldown_sec": cooldown,
        "created_ts": int(raw.get("created_ts") or now), "updated_ts": now,
        "last_triggered_ts": raw.get("last_triggered_ts"), "last_result": raw.get("last_result"),
    }


def _empty_store() -> Dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "automations": []}


def _load_store() -> Dict[str, Any]:
    with _LOCK:
        if not AUTOMATIONS_PATH.exists():
            return _empty_store()
        try:
            payload = json.loads(AUTOMATIONS_PATH.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("automations"), list):
                return _empty_store()
            return {"schema_version": SCHEMA_VERSION, "automations": copy.deepcopy(payload["automations"])}
        except Exception:
            return _empty_store()


def _atomic_save(payload: Mapping[str, Any]) -> Optional[Path]:
    AUTOMATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = AUTOMATIONS_PATH.with_suffix(".json.tmp")
    backup = None
    with _LOCK:
        try:
            if AUTOMATIONS_PATH.exists():
                backup = AUTOMATIONS_PATH.with_name(f"automations.json.backup-{int(time.time())}")
                shutil.copy2(AUTOMATIONS_PATH, backup)
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, AUTOMATIONS_PATH)
            return backup
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup and backup.exists():
                shutil.copy2(backup, AUTOMATIONS_PATH)
            raise


def _audit(event: str, automation_id: Optional[str], result: str, detail: str = "") -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": int(time.time()), "event": event, "automation_id": automation_id, "result": result, "detail": str(detail)[:240]}
    with _LOCK:
        with EVENTS_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
        cutoff = int(time.time()) - EVENT_RETENTION_DAYS * 86400
        try:
            if EVENTS_PATH.stat().st_size > 1_000_000:
                valid = []
                for line in EVENTS_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        item = json.loads(line)
                        if int(item.get("ts") or 0) >= cutoff:
                            valid.append(item)
                    except Exception:
                        continue
                temp = EVENTS_PATH.with_suffix(".jsonl.tmp")
                temp.write_text("".join(json.dumps(item, separators=(",", ":"), sort_keys=True) + "\n" for item in valid), encoding="utf-8")
                os.replace(temp, EVENTS_PATH)
        except OSError:
            pass


def _state_mapping(*keys: str) -> Mapping[str, Any]:
    current: Any = getattr(app_module, "state", {})
    for key in keys:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def _number(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "", "unknown", "unavailable") else float(value)
    except (TypeError, ValueError):
        return None


def build_automation_context() -> Dict[str, Any]:
    try:
        from backend import electricity_provider
        electricity = electricity_provider.electricity_status()
    except Exception:
        electricity = {}
    presence_state = _state_mapping("presence") or _state_mapping("presence_state")
    beer = presence_state.get("beer")
    seem = presence_state.get("seem")
    normalized = [str(value).lower() for value in (beer, seem) if value is not None]
    pm = _state_mapping("pm25") or _state_mapping("air_quality")
    temp = _state_mapping("temperature") or _state_mapping("system_temperature")
    system = _state_mapping("system") or getattr(app_module, "state", {})
    now = datetime.now().astimezone()
    living = _number(pm.get("living_room"))
    bedroom = _number(pm.get("bedroom"))
    maximum = max([value for value in (living, bedroom) if value is not None], default=None)
    mqtt_value = system.get("mqtt_connected") if isinstance(system, Mapping) else None
    return {
        "electricity": {"power": _number(electricity.get("power")), "voltage": _number(electricity.get("voltage")), "current": _number(electricity.get("current")), "health": electricity.get("health")},
        "presence": {"beer": beer, "seem": seem, "any_home": True if "home" in normalized else (False if len(normalized) == 2 else None), "all_away": True if normalized == ["away", "away"] else (False if "home" in normalized else None)},
        "pm25": {"living_room": living, "bedroom": bedroom, "maximum": maximum},
        "temperature": {"cpu": _number(temp.get("cpu") if isinstance(temp, Mapping) else None)},
        "system": {"mqtt_connected": mqtt_value if isinstance(mqtt_value, bool) else None, "dashboard_health": system.get("dashboard_health") if isinstance(system, Mapping) else None},
        "time": {"hour": now.hour, "minute": now.minute, "weekday": now.weekday()},
    }


def resolve_field(context: Mapping[str, Any], field: str) -> Any:
    if field not in FIELD_TYPES:
        return None
    current: Any = context
    for part in field.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "exists": return actual is not None
    if operator == "not_exists": return actual is None
    if actual is None: return False
    try:
        if operator == "eq": return actual == expected
        if operator == "ne": return actual != expected
        if operator == "gt": return actual > expected
        if operator == "gte": return actual >= expected
        if operator == "lt": return actual < expected
        if operator == "lte": return actual <= expected
        if operator == "in": return actual in expected
        if operator == "not_in": return actual not in expected
    except (TypeError, ValueError):
        return False
    return False


def evaluate_condition(node: Mapping[str, Any], context: Mapping[str, Any], used: Optional[set[str]] = None) -> bool:
    used = used if used is not None else set()
    if "and" in node: return all(evaluate_condition(child, context, used) for child in node["and"])
    if "or" in node: return any(evaluate_condition(child, context, used) for child in node["or"])
    if "not" in node: return not evaluate_condition(node["not"], context, used)
    field = str(node.get("field") or "")
    used.add(field)
    return _compare(resolve_field(context, field), str(node.get("operator") or ""), node.get("value"))


def evaluate_automation(automation: Mapping[str, Any], context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    try:
        validated = validate_automation(automation, current_id=str(automation.get("id") or "") or None)
    except ValidationFailure as exc:
        return {"matched": False, "conditions_passed": False, "reason": "invalid_rule", "errors": exc.errors, "evaluated_ts": int(time.time()), "context_fields_used": []}
    used: set[str] = set()
    active_context = context or build_automation_context()
    passed = evaluate_condition(validated["condition"], active_context, used)
    return {"matched": bool(validated["enabled"] and passed), "conditions_passed": passed, "reason": "matched" if validated["enabled"] and passed else ("disabled" if not validated["enabled"] else "conditions_not_met"), "evaluated_ts": int(time.time()), "context_fields_used": sorted(used)}


def _override_context(base: Dict[str, Any], override: Any) -> Dict[str, Any]:
    if override in (None, {}): return base
    if not isinstance(override, Mapping) or _json_size(override) > 16384 or _contains_forbidden(override):
        raise ValidationFailure(["Context override must be a safe object."])
    result = copy.deepcopy(base)
    for namespace, values in override.items():
        if not isinstance(values, Mapping):
            raise ValidationFailure([f"Context namespace {namespace} must be an object."])
        for key, value in values.items():
            field = f"{namespace}.{key}"
            if field not in FIELD_TYPES or not _safe_scalar(value):
                raise ValidationFailure([f"Unsupported context override field: {field}."])
            result[namespace][key] = value
    return result


def _find(automation_id: str) -> Tuple[Dict[str, Any], Optional[int]]:
    store = _load_store()
    for index, item in enumerate(store["automations"]):
        if item.get("id") == automation_id:
            return store, index
    return store, None


@app.get("/api/automations")
def list_automations() -> Dict[str, Any]:
    return _load_store()


@app.get("/api/automations/status")
def automation_status() -> Dict[str, Any]:
    items = _load_store()["automations"]
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ready = os.access(DATA_DIR, os.W_OK)
    except OSError:
        ready = False
    return {"configured": True, "automation_count": len(items), "enabled_count": sum(item.get("enabled") is True for item in items), "storage_path_ready": ready, "schema_version": SCHEMA_VERSION, "last_validation_error": _RUNTIME["last_validation_error"], "last_simulation_ts": _RUNTIME["last_simulation_ts"], "execution_enabled": False}


@app.get("/api/automations/{automation_id}")
def get_automation(automation_id: str):
    store, index = _find(automation_id)
    if index is None: return JSONResponse({"detail": "automation_not_found"}, status_code=404)
    return store["automations"][index]


@app.post("/api/automations")
def create_automation(payload: Dict[str, Any] = Body(...)):
    store = _load_store()
    try:
        item = validate_automation(payload, {entry.get("id") for entry in store["automations"]})
        store["automations"].append(item)
        _atomic_save(store)
    except ValidationFailure as exc:
        _audit("validation_failed", str(payload.get("id") or "") or None, "rejected", exc.errors[0])
        return _safe_error_payload(exc.errors)
    except Exception:
        return JSONResponse({"ok": False, "errors": ["Automation could not be saved safely."]}, status_code=503)
    _audit("created", item["id"], "ok", item["name"])
    return JSONResponse(item, status_code=201)


@app.put("/api/automations/{automation_id}")
def update_automation(automation_id: str, payload: Dict[str, Any] = Body(...)):
    store, index = _find(automation_id)
    if index is None: return JSONResponse({"detail": "automation_not_found"}, status_code=404)
    merged = {**store["automations"][index], **payload, "id": automation_id, "created_ts": store["automations"][index].get("created_ts")}
    try:
        item = validate_automation(merged, {entry.get("id") for entry in store["automations"]}, automation_id)
        store["automations"][index] = item
        _atomic_save(store)
    except ValidationFailure as exc:
        _audit("validation_failed", automation_id, "rejected", exc.errors[0])
        return _safe_error_payload(exc.errors)
    except Exception:
        return JSONResponse({"ok": False, "errors": ["Automation could not be saved safely."]}, status_code=503)
    _audit("updated", automation_id, "ok", item["name"])
    return item


@app.delete("/api/automations/{automation_id}")
def delete_automation(automation_id: str):
    store, index = _find(automation_id)
    if index is None: return JSONResponse({"detail": "automation_not_found"}, status_code=404)
    removed = store["automations"].pop(index)
    try: _atomic_save(store)
    except Exception: return JSONResponse({"ok": False, "errors": ["Automation could not be deleted safely."]}, status_code=503)
    _audit("deleted", automation_id, "ok", removed.get("name") or "")
    return {"ok": True, "id": automation_id}


def _toggle(automation_id: str, enabled: bool):
    store, index = _find(automation_id)
    if index is None: return JSONResponse({"detail": "automation_not_found"}, status_code=404)
    store["automations"][index]["enabled"] = enabled
    store["automations"][index]["updated_ts"] = int(time.time())
    try: _atomic_save(store)
    except Exception: return JSONResponse({"ok": False, "errors": ["Automation state could not be saved safely."]}, status_code=503)
    _audit("enabled" if enabled else "disabled", automation_id, "ok")
    return store["automations"][index]


@app.post("/api/automations/{automation_id}/enable")
def enable_automation(automation_id: str): return _toggle(automation_id, True)


@app.post("/api/automations/{automation_id}/disable")
def disable_automation(automation_id: str): return _toggle(automation_id, False)


@app.post("/api/automations/simulate")
def simulate_automation(payload: Dict[str, Any] = Body(...)):
    try:
        automation = validate_automation(payload.get("automation"), current_id=str((payload.get("automation") or {}).get("id") or "") or None)
        context = _override_context(build_automation_context(), payload.get("context_override") or {})
    except ValidationFailure as exc:
        _audit("validation_failed", str((payload.get("automation") or {}).get("id") or "") or None, "rejected", exc.errors[0])
        return _safe_error_payload(exc.errors)
    result = evaluate_automation(automation, context)
    now = int(time.time())
    _RUNTIME["last_simulation_ts"] = now
    _audit("simulated", automation["id"], "matched" if result["matched"] else "not_matched", ",".join(result["context_fields_used"]))
    safe_summary = {namespace: {key: values.get(key) for key in values if f"{namespace}.{key}" in result["context_fields_used"]} for namespace, values in context.items() if isinstance(values, Mapping)}
    return {"valid": True, **result, "actions_executed": False, "reason": "simulation_only", "evaluation_reason": result["reason"], "context_summary": safe_summary}
