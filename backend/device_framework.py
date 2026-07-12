"""Shared internal device model for Smart Condo Dashboard.

This module is intentionally transport- and API-agnostic. Existing modules keep
their current routes and command behavior while exposing a common internal
representation through the device registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


DEVICE_TYPES: Tuple[str, ...] = (
    "sonoff",
    "tuya_light",
    "lg_tv",
    "presence",
    "camera",
    "pm25",
    "electricity",
    "roborock",
    "tapo_ir",
    "connectlife",
)

HEALTH_VALUES: Tuple[str, ...] = ("healthy", "warning", "offline", "unknown")

SENSITIVE_KEY_PARTS = (
    "token",
    "secret",
    "password",
    "passwd",
    "local_key",
    "apikey",
    "api_key",
    "credential",
)


def _safe_mapping(value: Any) -> Any:
    """Return a JSON-friendly copy with secret-looking fields removed."""

    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in SENSITIVE_KEY_PARTS):
                continue
            result[key_text] = _safe_mapping(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_safe_mapping(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_health(health: Optional[str], online: Optional[bool]) -> str:
    value = str(health or "").strip().lower()
    if value in HEALTH_VALUES:
        return value
    if online is True:
        return "healthy"
    if online is False:
        return "offline"
    return "unknown"


@dataclass(frozen=True)
class UnifiedDevice:
    id: str
    type: str
    name: str
    room: Optional[str] = None
    online: Optional[bool] = None
    health: str = "unknown"
    last_update_ts: Optional[int] = None
    latency_ms: Optional[float] = None
    status: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    capabilities: Tuple[str, ...] = field(default_factory=tuple)
    actions: Tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "type", str(self.type))
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "health", normalize_health(self.health, self.online))
        object.__setattr__(self, "capabilities", tuple(dict.fromkeys(str(x) for x in self.capabilities if x)))
        object.__setattr__(self, "actions", tuple(dict.fromkeys(str(x) for x in self.actions if x)))
        object.__setattr__(self, "status", _safe_mapping(self.status or {}))
        object.__setattr__(self, "diagnostics", _safe_mapping(self.diagnostics or {}))
        object.__setattr__(self, "metadata", _safe_mapping(self.metadata or {}))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "room": self.room,
            "online": self.online,
            "health": self.health,
            "last_update_ts": self.last_update_ts,
            "latency_ms": self.latency_ms,
            "status": dict(self.status),
            "diagnostics": dict(self.diagnostics),
            "capabilities": list(self.capabilities),
            "actions": list(self.actions),
            "metadata": dict(self.metadata),
        }


def device_list(items: Iterable[UnifiedDevice]) -> list[Dict[str, Any]]:
    return [item.to_dict() for item in items]
