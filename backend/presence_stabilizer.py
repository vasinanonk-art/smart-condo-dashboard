import os
import platform
import re
import subprocess
import time
from typing import Any, Dict

GRACE_PERIOD_SEC = 180
PING_TIMEOUT_SEC = 1
PEOPLE = ("beer", "seem")
ACTIVE_NEIGHBOR_STATES = {"REACHABLE", "DELAY", "PROBE"}
INACTIVE_NEIGHBOR_STATES = {"STALE", "FAILED", "INCOMPLETE", "NONE"}

_presence_cache: Dict[str, Dict[str, Any]] = {}


def _now() -> int:
    return int(time.time())


def _truthy(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("home", "on", "online", "present", "true", "yes", "1", "connected"):
            return True
        if text in ("away", "off", "offline", "not_home", "false", "no", "0", "disconnected"):
            return False
    return None


def _mqtt_presence_value(raw: Dict[str, Any]) -> bool | None:
    if not isinstance(raw, dict):
        return None
    for key in ("home", "online", "present", "presence", "state", "status", "value"):
        if key in raw:
            result = _truthy(raw.get(key))
            if result is not None:
                return result
    return None


def _payload_ts(raw: Dict[str, Any]) -> int:
    value = raw.get("ts") if isinstance(raw, dict) else None
    try:
        return int(value or 0)
    except Exception:
        return 0


def _mqtt_is_fresh(raw_ts: int) -> bool:
    return bool(raw_ts and (_now() - raw_ts) <= GRACE_PERIOD_SEC)


def _name(person: str, raw: Dict[str, Any] | None = None) -> str:
    if isinstance(raw, dict) and raw.get("name"):
        return str(raw["name"])
    return person.capitalize()


def _ip(raw: Dict[str, Any] | None, cached: Dict[str, Any] | None = None) -> str | None:
    for item in (raw, cached):
        if isinstance(item, dict):
            for key in ("ip", "address", "host"):
                value = item.get(key)
                if value:
                    return str(value)
    return None


def _neighbor_state(ip: str | None) -> str:
    if not ip:
        return "NONE"
    try:
        result = subprocess.run(["ip", "neigh", "show", ip], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
        text = (result.stdout or "").upper()
        if not text.strip():
            return "NONE"
        for state in ACTIVE_NEIGHBOR_STATES | INACTIVE_NEIGHBOR_STATES:
            if re.search(r"\b" + re.escape(state) + r"\b", text):
                return state
        return "NONE"
    except Exception:
        return "NONE"


def _ping(ip: str | None) -> bool:
    if not ip:
        return False
    param = "-n" if platform.system().lower().startswith("win") else "-c"
    timeout = "-w" if platform.system().lower().startswith("linux") else "-W"
    try:
        result = subprocess.run(["ping", param, "1", timeout, str(PING_TIMEOUT_SEC), ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=PING_TIMEOUT_SEC + 1.0)
        return result.returncode == 0
    except Exception:
        return False


def _finalize(person: str, raw: Dict[str, Any] | None, home: bool, online: bool, source: str, confidence: int, last_seen: int | None, ip: str | None) -> Dict[str, Any]:
    now = _now()
    previous = _presence_cache.get(person, {})
    if last_seen is None:
        last_seen = int(previous.get("last_seen") or 0)
    age = now - last_seen if last_seen else 999999

    if source == "Cached":
        if last_seen and age <= GRACE_PERIOD_SEC:
            home = True
            online = False
            confidence = max(confidence, 55)
            status = "Recently Seen"
        else:
            home = False
            online = False
            source = "Expired"
            status = "Away"
    elif not home and last_seen and age <= GRACE_PERIOD_SEC:
        home = True
        online = False
        source = "Cached"
        confidence = max(confidence, 55)
        status = "Recently Seen"
    elif home:
        status = "Home"
    else:
        status = "Away"

    item = {
        "name": _name(person, raw),
        "home": bool(home),
        "online": bool(online),
        "last_seen": int(last_seen or 0),
        "source": source,
        "confidence": int(confidence),
        "state": status,
        "status": status,
        "ip": ip,
    }
    _presence_cache[person] = item
    print(f"presence diagnostic: name={item['name']} source={item['source']} home={item['home']} online={item['online']} last_seen={item['last_seen']}", flush=True)
    return item


def resolve_person(person: str, raw: Dict[str, Any] | None) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    previous = _presence_cache.get(person, {})
    ip = _ip(raw, previous)
    mqtt_value = _mqtt_presence_value(raw)
    raw_ts = _payload_ts(raw)
    mqtt_fresh = _mqtt_is_fresh(raw_ts)

    if mqtt_value is True and mqtt_fresh:
        return _finalize(person, raw, True, True, "MQTT", 100, raw_ts, ip)
    if mqtt_value is False and mqtt_fresh:
        # MQTT away is respected, but grace still prevents immediate false away.
        return _finalize(person, raw, False, False, "MQTT", 80, int(previous.get("last_seen") or raw_ts), ip)

    neighbor_state = _neighbor_state(ip)
    if neighbor_state in ACTIVE_NEIGHBOR_STATES:
        return _finalize(person, raw, True, True, f"Router:{neighbor_state}", 85, _now(), ip)
    if neighbor_state == "STALE":
        if _ping(ip):
            return _finalize(person, raw, True, True, "Router:STALE+Ping", 75, _now(), ip)
        return _finalize(person, raw, False, False, "Cached", 45, int(previous.get("last_seen") or raw_ts or 0), ip)

    if _ping(ip):
        return _finalize(person, raw, True, True, "Ping", 70, _now(), ip)
    return _finalize(person, raw, False, False, "Cached", 40, int(previous.get("last_seen") or raw_ts or 0), ip)


def resolve_presence(raw_presence: Dict[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    raw_presence = raw_presence if isinstance(raw_presence, dict) else {}
    people = set(PEOPLE)
    people.update(k for k, v in raw_presence.items() if isinstance(v, dict))
    resolved: Dict[str, Dict[str, Any]] = {}
    for person in sorted(people):
        raw = raw_presence.get(person) if isinstance(raw_presence.get(person), dict) else raw_presence if person in PEOPLE and any(k in raw_presence for k in ("home", "online", "present", "state", "status")) else {}
        resolved[person] = resolve_person(person, raw)
    return resolved
