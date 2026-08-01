"""Provider-neutral, read-only Smart Life / Tuya IR inventory discovery.

Providers are selected explicitly. The Smart Life cloud adapter is GET-only;
this module creates no polling loop, subscription, command route, or local
Tuya session.
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Mapping

from backend import app as app_module
from backend import tapo_ir_provider, tuya_cloud_readonly

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
    dp_metadata: tuple[Mapping[str, Any], ...] = ()
    state: Mapping[str, Any] = field(default_factory=dict)


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


def _safe_dp_code(value: Any) -> str | None:
    code = _safe_text(value, 64)
    if not code or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
        return None
    return code


def _cloud_dp_data(
    specification: Mapping[str, Any],
    statuses: Any,
) -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    definitions: Dict[str, Dict[str, Any]] = {}
    for source, writable in (("status", False), ("functions", False)):
        rows = specification.get(source)
        if not isinstance(rows, list):
            continue
        for raw in rows[:128]:
            if not isinstance(raw, Mapping):
                continue
            code = _safe_dp_code(raw.get("code"))
            if not code:
                continue
            item = definitions.setdefault(code, {"code": code, "writable": False})
            kind = _safe_text(raw.get("type"), 32)
            if kind:
                item["type"] = kind
            item["reported"] = item.get("reported", False) or source == "status"
            item["instruction"] = item.get("instruction", False) or source == "functions"
    state: Dict[str, Any] = {}
    if isinstance(statuses, list):
        for raw in statuses[:128]:
            if not isinstance(raw, Mapping):
                continue
            code = _safe_dp_code(raw.get("code"))
            value = raw.get("value")
            if code not in definitions:
                continue
            # Opaque/raw strings can contain IR payloads and are deliberately
            # excluded. Scalar telemetry remains useful and safe.
            if isinstance(value, bool) or (
                isinstance(value, (int, float)) and not isinstance(value, bool)
            ):
                state[code] = value
            elif (
                isinstance(value, str)
                and len(value) <= 64
                and not re.search(
                    r"(?:ir|raw|code|key|token|secret|device.?id)",
                    code,
                    re.IGNORECASE,
                )
            ):
                state[code] = value
    ordered = tuple(definitions[code] for code in sorted(definitions))
    return ordered, state


def _cloud_device(
    information: Mapping[str, Any],
    specification: Mapping[str, Any],
    statuses: Any,
    configured_id: str,
) -> IRInventoryDevice:
    device = information.get("result")
    spec = specification.get("result")
    if not isinstance(device, Mapping) or not isinstance(spec, Mapping):
        raise tuya_cloud_readonly.TuyaCloudError("tuya_cloud_payload_invalid")
    if str(device.get("id") or "") != configured_id:
        raise tuya_cloud_readonly.TuyaCloudError("tuya_cloud_device_mismatch")
    if str(device.get("category") or "").casefold() != "wnykq":
        raise tuya_cloud_readonly.TuyaCloudError("tuya_cloud_category_mismatch")
    if str(spec.get("category") or "").casefold() != "wnykq":
        raise tuya_cloud_readonly.TuyaCloudError("tuya_cloud_category_mismatch")
    online = device.get("online") if isinstance(device.get("online"), bool) else None
    health = "healthy" if online is True else "offline" if online is False else "unknown"
    quality = "confirmed" if online is not None else "unknown"
    # DP definitions and values prove inventory only. They never become command
    # capabilities in this read-only milestone.
    dp_metadata, state = _cloud_dp_data(spec, statuses)
    has_metadata = bool(dp_metadata)
    has_status = isinstance(statuses, list)
    reason = (
        "verified_tuya_cloud_device"
        if has_metadata and has_status
        else "tuya_cloud_dp_metadata_incomplete"
    )
    return IRInventoryDevice(
        provider="smartlife_cloud",
        product_name=(
            _safe_text(device.get("product_name"))
            or _safe_text(device.get("name"))
            or "Configured Smart Life device"
        ),
        model=_safe_text(device.get("model")),
        device_id=_redacted_id(configured_id),
        firmware=None,
        online=online,
        health=health,
        state_quality=quality,
        supported_command_categories=(),
        discovery_reason=reason,
        dp_metadata=dp_metadata,
        state=state,
    )


class SmartLifeCloudProvider(ReadOnlyIRProvider):
    provider = "smartlife_cloud"

    def discover(self) -> ProviderInventory:
        try:
            client = tuya_cloud_readonly.configured_client()
            information = client.device_information()
            specification = client.device_specification()
            spec_result = specification.get("result")
            if (
                isinstance(spec_result, Mapping)
                and not isinstance(spec_result.get("functions"), list)
            ):
                try:
                    function_payload = client.device_functions()
                    function_result = function_payload.get("result")
                    if (
                        isinstance(function_result, Mapping)
                        and isinstance(function_result.get("functions"), list)
                    ):
                        specification = dict(specification)
                        merged = dict(spec_result)
                        merged["functions"] = function_result["functions"]
                        specification["result"] = merged
                except tuya_cloud_readonly.TuyaCloudError:
                    # Functions are supplemental when specification is
                    # otherwise readable; the inventory remains fail-closed.
                    pass
            status_payload = client.device_status()
            device = _cloud_device(
                information,
                specification,
                status_payload.get("result"),
                client.config.device_id,
            )
        except tuya_cloud_readonly.TuyaCloudError as exc:
            return UnavailableProvider(self.provider, exc.reason).discover()
        return ProviderInventory(
            provider=self.provider,
            provider_detected=True,
            online=device.online,
            health=device.health,
            state_quality=device.state_quality,
            available_capabilities=(),
            discovery_reason="verified_inventory",
            devices=(device,),
        )


def _provider(provider: str, selection_reason: str | None) -> ReadOnlyIRProvider:
    if provider == "smartlife_cloud":
        return SmartLifeCloudProvider()
    if provider == "homeassistant":
        return HomeAssistantProvider()
    reasons = {
        "tuya_local": "tuya_local_ir_inventory_unavailable",
        "mqtt": "mqtt_ir_inventory_unavailable",
        "unsupported": selection_reason or "unsupported_provider",
    }
    return UnavailableProvider(provider, reasons[provider])


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.update({"key": None, "ts": 0.0, "payload": None})
    tuya_cloud_readonly.reset_client()


def _public_inventory(value: ProviderInventory) -> Dict[str, Any]:
    payload = asdict(value)
    payload["available_capabilities"] = list(value.available_capabilities)
    payload["devices"] = []
    for device in value.devices:
        item = asdict(device)
        item["supported_command_categories"] = list(
            device.supported_command_categories
        )
        item["dp_metadata"] = list(device.dp_metadata)
        item["state"] = dict(device.state)
        if not item["dp_metadata"]:
            item.pop("dp_metadata")
        if not item["state"]:
            item.pop("state")
        payload["devices"].append(item)
    payload["count"] = len(value.devices)
    payload["read_only"] = True
    return payload


def inventory(force: bool = False) -> Dict[str, Any]:
    provider, selection_reason = _selected_provider()
    cloud_key = tuple(
        hashlib.sha256(os.getenv(name, "").encode("utf-8")).hexdigest()
        for name in (
            "TUYA_CLOUD_ACCESS_ID",
            "TUYA_CLOUD_ACCESS_SECRET",
            "TUYA_CLOUD_DEVICE_ID",
            "TUYA_CLOUD_REGION",
        )
    ) if provider == "smartlife_cloud" else ()
    key = (provider, selection_reason, _explicit_ha_entities(), cloud_key)
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
