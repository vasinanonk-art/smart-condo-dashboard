"""Provider-neutral, read-only Smart Life / Tuya IR inventory discovery.

Providers are selected explicitly. This module creates no client, polling loop,
subscription, command route, cloud login, or local Tuya session.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Mapping

from backend import app as app_module
from backend import tapo_ir_provider

app = app_module.app
PROVIDER_STATES = frozenset({
    "smartlife_cloud",
    "tuya_local",
    "homeassistant",
    "mqtt",
    "unsupported",
})
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_CACHE_TTL_SEC = max(
    5,
    min(300, int(os.getenv("SMARTLIFE_IR_CACHE_SEC", "15"))),
)
_CACHE_LOCK = threading.RLock()
_CACHE: Dict[str, Any] = {"key": None, "ts": 0.0, "payload": None}


def _safe_text(value: Any, limit: int = 120) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text or any(ord(character) < 32 for character in text):
        return None
    return text[:limit]


def _redacted_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"ir-{digest}"


def _selected_provider() -> tuple[str, str | None]:
    requested = os.getenv("SMARTLIFE_IR_PROVIDER", "").strip().casefold()
    if not requested:
        return "unsupported", "provider_not_configured"
    if requested not in PROVIDER_STATES:
        return "unsupported", "unsupported_provider"
    return requested, None


def _explicit_ha_entities() -> tuple[str, ...]:
    raw = os.getenv("SMARTLIFE_IR_HA_ENTITY_IDS", "")
    result = []
    for value in raw.split(","):
        entity_id = value.strip().casefold()
        if entity_id and _ENTITY_ID.fullmatch(entity_id) and entity_id not in result:
            result.append(entity_id)
    return tuple(result[:32])


def _capabilities(entity_id: str) -> tuple[str, ...]:
    domain = entity_id.partition(".")[0]
    return {
        "remote": ("remote",),
        "climate": ("climate",),
        "fan": ("fan",),
        "media_player": ("media",),
    }.get(domain, ())


@dataclass(frozen=True)
class IRInventoryDevice:
    provider: str
    product_name: str
    model: str | None
    device_id: str
    firmware: str | None
    online: bool | None
    health: str
    state_quality: str
    supported_command_categories: tuple[str, ...]
    discovery_reason: str


@dataclass(frozen=True)
class ProviderInventory:
    provider: str
    provider_detected: bool
    online: bool | None
    health: str
    state_quality: str
    available_capabilities: tuple[str, ...]
    discovery_reason: str
    devices: tuple[IRInventoryDevice, ...]


class ReadOnlyIRProvider(ABC):
    provider: str

    @abstractmethod
    def discover(self) -> ProviderInventory:
        raise NotImplementedError


class UnavailableProvider(ReadOnlyIRProvider):
    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason

    def discover(self) -> ProviderInventory:
        return ProviderInventory(
            provider=self.provider,
            provider_detected=False,
            online=None,
            health="unknown",
            state_quality="unknown",
            available_capabilities=(),
            discovery_reason=self.reason,
            devices=(),
        )


def _homeassistant_states():
    """Reuse the existing bounded Home Assistant state reader."""
    return tapo_ir_provider._ha_states()


def _ha_device(entity: Mapping[str, Any]) -> IRInventoryDevice:
    entity_id = str(entity.get("entity_id") or "")
    attributes = (
        entity.get("attributes")
        if isinstance(entity.get("attributes"), Mapping)
        else {}
    )
    state = str(entity.get("state") or "").casefold()
    if state == "unavailable":
        online: bool | None = False
    elif state in {"", "unknown"}:
        online = None
    else:
        online = True
    health = "healthy" if online is True else "offline" if online is False else "unknown"
    quality = "confirmed" if online is not None else "unknown"
    categories = _capabilities(entity_id)
    name = (
        _safe_text(attributes.get("friendly_name"))
        or _safe_text(attributes.get("device_name"))
        or "Configured IR device"
    )
    return IRInventoryDevice(
        provider="homeassistant",
        product_name=name,
        model=_safe_text(attributes.get("model")),
        device_id=_redacted_id(entity_id),
        firmware=_safe_text(
            attributes.get("firmware_version")
            or attributes.get("sw_version")
            or attributes.get("software_version")
        ),
        online=online,
        health=health,
        state_quality=quality,
        supported_command_categories=categories,
        discovery_reason="verified_homeassistant_entity",
    )


class HomeAssistantProvider(ReadOnlyIRProvider):
    provider = "homeassistant"

    def discover(self) -> ProviderInventory:
        states, error, _latency, configured = _homeassistant_states()
        if not configured:
            return UnavailableProvider(
                self.provider,
                "homeassistant_unavailable",
            ).discover()
        if error:
            return ProviderInventory(
                provider=self.provider,
                provider_detected=True,
                online=False,
                health="offline",
                state_quality="unknown",
                available_capabilities=(),
                discovery_reason="homeassistant_request_failed",
                devices=(),
            )
        selected = set(_explicit_ha_entities())
        matching: Iterable[Mapping[str, Any]] = (
            item
            for item in states
            if isinstance(item, Mapping)
            and str(item.get("entity_id") or "").casefold() in selected
        )
        devices = tuple(_ha_device(item) for item in matching)
        if not devices:
            return ProviderInventory(
                provider=self.provider,
                provider_detected=True,
                online=None,
                health="unknown",
                state_quality="unknown",
                available_capabilities=(),
                discovery_reason="empty_inventory",
                devices=(),
            )
        available = tuple(sorted({
            capability
            for device in devices
            for capability in device.supported_command_categories
        }))
        online = (
            True if any(device.online is True for device in devices)
            else False if all(device.online is False for device in devices)
            else None
        )
        health = "healthy" if online is True else "offline" if online is False else "unknown"
        quality = "confirmed" if online is not None else "unknown"
        return ProviderInventory(
            provider=self.provider,
            provider_detected=True,
            online=online,
            health=health,
            state_quality=quality,
            available_capabilities=available,
            discovery_reason="verified_inventory",
            devices=devices,
        )


def _provider(provider: str, selection_reason: str | None) -> ReadOnlyIRProvider:
    if provider == "homeassistant":
        return HomeAssistantProvider()
    reasons = {
        "smartlife_cloud": "smartlife_cloud_unavailable",
        "tuya_local": "tuya_local_ir_inventory_unavailable",
        "mqtt": "mqtt_ir_inventory_unavailable",
        "unsupported": selection_reason or "unsupported_provider",
    }
    return UnavailableProvider(provider, reasons[provider])


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update({"key": None, "ts": 0.0, "payload": None})


def _public_inventory(value: ProviderInventory) -> Dict[str, Any]:
    payload = asdict(value)
    payload["available_capabilities"] = list(value.available_capabilities)
    payload["devices"] = []
    for device in value.devices:
        item = asdict(device)
        item["supported_command_categories"] = list(
            device.supported_command_categories
        )
        payload["devices"].append(item)
    payload["count"] = len(value.devices)
    payload["read_only"] = True
    return payload


def inventory(force: bool = False) -> Dict[str, Any]:
    provider, selection_reason = _selected_provider()
    key = (provider, selection_reason, _explicit_ha_entities())
    now = time.monotonic()
    with _CACHE_LOCK:
        if (
            not force
            and _CACHE["payload"] is not None
            and _CACHE["key"] == key
            and now - float(_CACHE["ts"]) < _CACHE_TTL_SEC
        ):
            return copy.deepcopy(_CACHE["payload"])
    payload = _public_inventory(_provider(provider, selection_reason).discover())
    with _CACHE_LOCK:
        _CACHE.update({"key": key, "ts": now, "payload": copy.deepcopy(payload)})
    return payload


@app.get("/api/ir/inventory")
def ir_inventory() -> Dict[str, Any]:
    return inventory()
