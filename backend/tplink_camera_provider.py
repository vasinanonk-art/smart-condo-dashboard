"""Inactive read-only TP-Link camera provider.

The provider maps camera observations supplied by a future approved integration
into the safe EPIC 15 connector models.  It does not authenticate, create a
network client, discover devices, register globally, or activate runtime
behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Sequence

from backend.tplink_connector import (
    TPLinkConnector,
    TPLinkDevice,
    TPLinkDeviceKind,
    TPLinkHealth,
    TPLinkHealthState,
    TPLinkProvider,
    TPLinkProviderCapabilities,
    TPLinkProviderMetadata,
    TPLinkSupportStatus,
)


PROVIDER_ID = "tplink_camera"
_PROVIDER_CAPABILITIES = TPLinkProviderCapabilities(
    frozenset({"inventory", "health"})
)
_CAMERA_CAPABILITIES = (
    "inventory",
    "health",
    "snapshot",
    "livestream",
    "recordings",
    "motion",
    "microphone",
    "speaker",
    "ptz",
)
_IMPLEMENTATION_STATUS = "read_only_skeleton"


def _bounded_text(value: object, limit: int = 80) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _redacted_serial(value: object) -> str | None:
    """Return a non-identifying serial hint suitable for connector inventory."""

    serial = str(value or "").strip()
    if not serial:
        return None
    if len(serial) <= 4:
        return "****"
    return f"***{serial[-4:]}"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TPLinkCameraObservation:
    """Private input record from a future approved camera inventory source."""

    id: str
    alias: str
    model: str | None = None
    device_type: str = "camera"
    serial: str | None = None
    firmware: str | None = None
    hardware_version: str | None = None
    online: bool | None = None


@dataclass(frozen=True)
class TPLinkCameraProviderDescription:
    provider_name: str
    provider_version: str
    api_version: str
    implementation_status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_name": self.provider_name,
            "provider_version": self.provider_version,
            "api_version": self.api_version,
            "implementation_status": self.implementation_status,
        }


class TPLinkCameraProvider(TPLinkProvider):
    """Read-only inventory mapper with no transport or authentication logic."""

    def __init__(
        self,
        cameras: Sequence[TPLinkCameraObservation] = (),
    ) -> None:
        self._cameras = tuple(cameras)
        self._initialized = False
        self._initialized_at: str | None = None
        self._initialized_monotonic: float | None = None
        self._last_refresh_at: str | None = None
        self._last_latency_ms: float | None = None
        self._last_error: str | None = None

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def supported_kinds(self) -> frozenset[TPLinkDeviceKind]:
        return frozenset({TPLinkDeviceKind.CAMERA})

    @property
    def metadata(self) -> TPLinkProviderMetadata:
        return TPLinkProviderMetadata(
            provider_name="TP-Link Camera Provider",
            provider_version="1.0.0",
            api_version="inventory-v1",
        )

    @property
    def capabilities(self) -> TPLinkProviderCapabilities:
        return _PROVIDER_CAPABILITIES

    def capability_discovery(self) -> dict[str, str]:
        return {
            capability: (
                TPLinkSupportStatus.SUPPORTED.value
                if self.capabilities.supports(capability)
                else TPLinkSupportStatus.NOT_SUPPORTED.value
            )
            for capability in _CAMERA_CAPABILITIES
        }

    def describe(self) -> TPLinkCameraProviderDescription:
        metadata = self.metadata
        return TPLinkCameraProviderDescription(
            provider_name=metadata.provider_name,
            provider_version=metadata.provider_version,
            api_version=metadata.api_version,
            implementation_status=_IMPLEMENTATION_STATUS,
        )

    def diagnostics(self) -> dict[str, Any]:
        capability_status = self.capability_discovery()
        supported_count = sum(
            status == TPLinkSupportStatus.SUPPORTED.value
            for status in capability_status.values()
        )
        unsupported_count = len(capability_status) - supported_count
        uptime_seconds = (
            max(0.0, perf_counter() - self._initialized_monotonic)
            if self._initialized_monotonic is not None
            else None
        )
        return {
            "supported_capability_count": supported_count,
            "unsupported_capability_count": unsupported_count,
            "provider_uptime_seconds": (
                round(uptime_seconds, 3)
                if uptime_seconds is not None
                else None
            ),
            "initialization_timestamp": self._initialized_at,
        }

    async def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._initialized_at = _utc_timestamp()
        self._initialized_monotonic = perf_counter()

    async def shutdown(self) -> None:
        self._initialized = False
        self._initialized_monotonic = None

    async def inventory(self) -> tuple[TPLinkDevice, ...]:
        started_at = perf_counter()
        try:
            inventory = tuple(
                self._map_camera(camera) for camera in self._cameras
            )
        except Exception:
            self._last_error = "inventory_mapping_failed"
            raise
        finally:
            self._last_latency_ms = round(
                (perf_counter() - started_at) * 1000,
                3,
            )
            self._last_refresh_at = _utc_timestamp()
        self._last_error = None
        return inventory

    async def health(self) -> TPLinkHealth:
        inventory_available = self._last_refresh_at is not None
        if self._last_error:
            status = TPLinkHealthState.DEGRADED
        elif self._initialized:
            status = TPLinkHealthState.HEALTHY
        else:
            status = TPLinkHealthState.UNKNOWN
        return TPLinkHealth(
            status=status,
            online=None,
            ready=self._initialized,
            authenticated=None,
            latency_ms=self._last_latency_ms,
            last_checked_at=self._last_refresh_at,
            last_error=self._last_error,
            details={"inventory_available": inventory_available},
        )

    def _map_camera(self, camera: TPLinkCameraObservation) -> TPLinkDevice:
        return TPLinkDevice(
            id=camera.id,
            provider_id=self.provider_id,
            display_name=camera.alias,
            kind=TPLinkDeviceKind.CAMERA,
            model=_bounded_text(camera.model),
            firmware_version=_bounded_text(camera.firmware),
            online=camera.online,
            capabilities=(),
            metadata={
                "device_type": _bounded_text(camera.device_type),
                "serial_redacted": _redacted_serial(camera.serial),
                "hardware_version": _bounded_text(camera.hardware_version),
            },
        )


def register_camera_provider(
    connector: TPLinkConnector,
    provider: TPLinkCameraProvider,
) -> None:
    """Explicitly register an already constructed inactive provider."""

    connector.register(provider)
