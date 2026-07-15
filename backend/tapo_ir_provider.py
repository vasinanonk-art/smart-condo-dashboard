"""Read-only Home Assistant discovery foundation for Tapo IR devices.

This module performs no command calls and creates no polling loop. Home Assistant
state is fetched lazily through the existing HA configuration when the endpoint or
Unified Device Registry requests a snapshot.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from backend import app as app_module
from backend.device_framework import UnifiedDevice
from backend.device_registry import registry

app = app_module.app
_CACHE_SEC = max(5, int(os.getenv("TAPO_IR_CACHE_SEC", "15")))
_TIMEOUT_SEC = max(0.5, float(os.getenv("TAPO_IR_HA_TIMEOUT_SEC", "3")))
_STALE_SEC = max(30, int(os.getenv("TAPO_IR_STALE_SEC", "180")))
_lock = threading.RLock()
_cache: Dict[str, Any] = {"ts": 0, "payload": None}

EXPECTED_CAPABILITIES = (
    "remote.send_command",
    "remote.learn_command",
    "remote.delete_command",
    "power",
    "temperature",
    "fan",
    "climate",
    "media",
    "scene",
    "script",
)

_TAPO_MARKERS = ("tapo", "tp-link", "tplink", "kasa")
_IR_MARKERS = ("ir", "infrared", "remote", "hub", "h100", "tapo hub")
_PLATFORM_KEYS = ("platform", "integration", "integration_platform", "config_entry_domain")


def invalidate_cache() -> None:
    with _lock:
        _cache["ts"] = 0
        _cache["payload"] = None


def _safe_error(exc: BaseException) -> str:
    return type(exc).__name__


def _epoch(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1_000_000_000_000 else int(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError):
        return None


def _ha_states() -> tuple[list[Dict[str, Any]], Optional[str], Optional[float], bool]:
    base_url = os.getenv("HA_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("HA_TOKEN", "").strip()
    configured = bool(base_url and token)
    if not configured:
        return [], "not_configured", None, False
    started = time.monotonic()
    request = urllib.request.Request(
        f"{base_url}/api/states",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "smart-condo-dashboard-tapo-ir",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            payload = json.load(response)
        latency = round((time.monotonic() - started) * 1000, 1)
        states = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
        return states, None, latency, True
    except Exception as exc:
        return [], _safe_error(exc), round((time.monotonic() - started) * 1000, 1), True


def _attributes(entity: Mapping[str, Any]) -> Mapping[str, Any]:
    value = entity.get("attributes")
    return value if isinstance(value, Mapping) else {}


def _entity_text(entity: Mapping[str, Any]) -> str:
    attrs = _attributes(entity)
    values = [
        entity.get("entity_id"),
        attrs.get("friendly_name"),
        attrs.get("device_class"),
        attrs.get("model"),
        attrs.get("manufacturer"),
        attrs.get("device_name"),
        attrs.get("via_device"),
    ]
    for key in _PLATFORM_KEYS:
        values.append(attrs.get(key))
    return " ".join(str(value or "") for value in values).lower().replace("_", " ")


def _platform_match(entity: Mapping[str, Any]) -> bool:
    attrs = _attributes(entity)
    values = [str(attrs.get(key) or "").lower() for key in _PLATFORM_KEYS]
    return any(any(marker in value for marker in _TAPO_MARKERS) for value in values)


def _candidate_score(entity: Mapping[str, Any]) -> int:
    entity_id = str(entity.get("entity_id") or "").lower()
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    text = _entity_text(entity)
    score = 0
    if _platform_match(entity):
        score += 12
    if any(marker in text for marker in _TAPO_MARKERS):
        score += 8
    if any(marker in text for marker in _IR_MARKERS):
        score += 6
    if domain == "remote":
        score += 10
    elif domain in {"climate", "fan", "media_player", "scene", "script", "switch", "sensor"}:
        score += 2
    # Require a strong Tapo/platform association or a remote entity whose name is
    # explicitly Tapo/IR-related. This avoids collecting unrelated HA remotes.
    if not (_platform_match(entity) or any(marker in text for marker in _TAPO_MARKERS)):
        return -100
    return score


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _entity_capabilities(entity: Mapping[str, Any]) -> set[str]:
    entity_id = str(entity.get("entity_id") or "")
    domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
    attrs = _attributes(entity)
    text = _entity_text(entity)
    capabilities: set[str] = set()

    if domain == "remote":
        capabilities.add("remote.send_command")
        advertised = []
        for key in ("supported_commands", "commands", "services", "supported_services", "features"):
            advertised.extend(_string_list(attrs.get(key)))
        advertised_text = " ".join(advertised).lower().replace("_", " ")
        if "learn command" in advertised_text or "learn" in advertised_text:
            capabilities.add("remote.learn_command")
        if "delete command" in advertised_text or "delete" in advertised_text:
            capabilities.add("remote.delete_command")
    if domain in {"switch", "light", "remote"} or "power" in text:
        capabilities.add("power")
    if domain == "climate":
        capabilities.add("climate")
    if domain == "fan":
        capabilities.add("fan")
    if domain == "media_player":
        capabilities.add("media")
    if domain == "scene":
        capabilities.add("scene")
    if domain == "script":
        capabilities.add("script")
    device_class = str(attrs.get("device_class") or "").lower()
    if domain == "sensor" and (device_class == "temperature" or "temperature" in text):
        capabilities.add("temperature")
    return capabilities


def _public_entity(entity: Mapping[str, Any]) -> Dict[str, Any]:
    attrs = _attributes(entity)
    entity_id = str(entity.get("entity_id") or "")
    capabilities = sorted(_entity_capabilities(entity))
    return {
        "entity_id": entity_id,
        "name": attrs.get("friendly_name") or entity_id,
        "domain": entity_id.split(".", 1)[0] if "." in entity_id else None,
        "state": entity.get("state"),
        "last_update": _epoch(entity.get("last_updated") or entity.get("last_changed")),
        "device_class": attrs.get("device_class"),
        "supported_features": attrs.get("supported_features"),
        "capabilities": capabilities,
    }


def _discover(states: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ranked = [(_candidate_score(item), item) for item in states]
    selected = [item for score, item in ranked if score >= 12]
    return sorted(selected, key=lambda item: str(item.get("entity_id") or ""))


def _snapshot_uncached() -> Dict[str, Any]:
    now = int(time.time())
    states, error, latency, ha_configured = _ha_states()
    discovered = _discover(states) if states else []
    public_entities = [_public_entity(item) for item in discovered]
    capabilities = sorted({cap for item in discovered for cap in _entity_capabilities(item)})
    updates = [item.get("last_update") for item in public_entities if item.get("last_update")]
    last_update = max(updates) if updates else None
    stale = bool(last_update and now - int(last_update) > _STALE_SEC)
    available_count = len(public_entities)
    configured = available_count > 0

    if not ha_configured:
        health = "unknown"
        online: Optional[bool] = None
    elif error:
        health = "warning" if configured else "unknown"
        online = None
    elif not configured:
        health = "unknown"
        online = None
    elif stale:
        health = "warning"
        online = True
    else:
        unavailable = all(str(item.get("state") or "").lower() in {"unavailable", "unknown"} for item in public_entities)
        health = "warning" if unavailable else "healthy"
        online = False if unavailable else True

    missing = [item for item in EXPECTED_CAPABILITIES if item not in capabilities]
    diagnostics = {
        "source": "home_assistant",
        "configured": configured,
        "discovered_entities": [item["entity_id"] for item in public_entities],
        "capabilities": capabilities,
        "available_entity_count": available_count,
        "missing_capabilities": missing,
        "stale": stale,
        "latency_ms": latency,
        "last_error": error,
    }
    return {
        "configured": configured,
        "online": online,
        "health": health,
        "last_update": last_update,
        "devices": public_entities,
        "entities": public_entities,
        "capabilities": capabilities,
        "diagnostics": diagnostics,
        "missing_capabilities": missing,
    }


def tapo_ir_status(force: bool = False) -> Dict[str, Any]:
    now = int(time.time())
    with _lock:
        if not force and _cache.get("payload") is not None and now - int(_cache.get("ts") or 0) < _CACHE_SEC:
            return dict(_cache["payload"])
    payload = _snapshot_uncached()
    with _lock:
        _cache["ts"] = now
        _cache["payload"] = dict(payload)
    return payload


def tapo_ir_provider() -> Iterable[UnifiedDevice]:
    payload = tapo_ir_status()
    diagnostics = payload.get("diagnostics") or {}
    entities = payload.get("entities") or []
    entity_ids = [str(item.get("entity_id")) for item in entities if item.get("entity_id")]
    yield UnifiedDevice(
        id="tapo_ir:home_assistant",
        type="tapo_ir",
        name="Tapo IR",
        room="condo",
        online=payload.get("online"),
        health=str(payload.get("health") or "unknown"),
        last_update_ts=payload.get("last_update"),
        latency_ms=diagnostics.get("latency_ms"),
        status={"configured": payload.get("configured"), "entities": entity_ids},
        diagnostics=diagnostics,
        capabilities=tuple(payload.get("capabilities") or ()),
        actions=(),
        metadata={"source": "home_assistant", "physical_site": "condo", "read_only": True},
    )


@app.get("/api/tapo-ir/status")
def get_tapo_ir_status() -> Dict[str, Any]:
    return tapo_ir_status()


registry.register_provider("tapo_ir", tapo_ir_provider, replace=True)
app_module.state["device_registry_registered_modules"] = registry.provider_names()
