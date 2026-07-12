"""Thread-safe registry for unified dashboard devices."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Callable, Dict, Iterable, List, Optional

from backend.device_framework import UnifiedDevice


DeviceProvider = Callable[[], Iterable[UnifiedDevice]]


class DeviceRegistry:
    """Stores explicit devices and lazy module providers.

    Providers are evaluated only when a registry snapshot is requested. This
    keeps the framework passive and avoids changing current polling, command,
    MQTT, API, or frontend behavior.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._devices: Dict[str, UnifiedDevice] = {}
        self._providers: "OrderedDict[str, DeviceProvider]" = OrderedDict()
        self._provider_errors: Dict[str, str] = {}

    def register(self, device: UnifiedDevice) -> UnifiedDevice:
        with self._lock:
            self._devices[device.id] = device
        return device

    def unregister(self, device_id: str) -> None:
        with self._lock:
            self._devices.pop(str(device_id), None)

    def register_provider(self, name: str, provider: DeviceProvider, *, replace: bool = False) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("provider name is required")
        if not callable(provider):
            raise TypeError("provider must be callable")
        with self._lock:
            if key in self._providers and not replace:
                return
            self._providers[key] = provider

    def provider_names(self) -> List[str]:
        with self._lock:
            return list(self._providers.keys())

    def provider_errors(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._provider_errors)

    def snapshot(self, *, device_type: Optional[str] = None, room: Optional[str] = None) -> List[UnifiedDevice]:
        with self._lock:
            devices = dict(self._devices)
            providers = list(self._providers.items())

        errors: Dict[str, str] = {}
        for provider_name, provider in providers:
            try:
                provided = provider() or ()
                for device in provided:
                    if not isinstance(device, UnifiedDevice):
                        continue
                    devices[device.id] = device
            except Exception as exc:
                errors[provider_name] = type(exc).__name__

        with self._lock:
            self._provider_errors = errors

        result = list(devices.values())
        if device_type is not None:
            result = [item for item in result if item.type == device_type]
        if room is not None:
            result = [item for item in result if item.room == room]
        return sorted(result, key=lambda item: (item.type, item.room or "", item.name, item.id))

    def get(self, device_id: str) -> Optional[UnifiedDevice]:
        key = str(device_id)
        for device in self.snapshot():
            if device.id == key:
                return device
        return None

    def diagnostics(self) -> Dict[str, object]:
        snapshot = self.snapshot()
        return {
            "registered_providers": self.provider_names(),
            "device_count": len(snapshot),
            "provider_errors": self.provider_errors(),
        }


registry = DeviceRegistry()
