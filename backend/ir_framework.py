"""Generic, profile-driven IR device framework."""
from __future__ import annotations

import atexit
import copy
import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Mapping

from fastapi import Body, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend import app as app_module

app = app_module.app
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = ROOT / "config" / "ir" / "devices.json"
DEFAULT_PROFILE_DIR = ROOT / "config" / "ir" / "profiles"
IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CAPABILITY_IDS = frozenset({
    "power", "mute", "volume", "channel", "navigation", "ok", "back",
    "home", "input", "temperature", "mode", "fan_speed", "swing", "custom",
})
CAPABILITY_TYPES = frozenset({
    "button", "toggle", "select", "range", "navigation", "media", "custom",
})
MAX_QUEUE_DEPTH = 20
DEFAULT_TIMEOUT_SEC = max(0.1, min(30.0, float(os.getenv("IR_COMMAND_TIMEOUT_SEC", "4"))))
_DEVICE_QUEUES: Dict[str, "DeviceCommandQueue"] = {}
_DEVICE_QUEUES_GUARD = threading.Lock()
_RUNTIME_LOCK = threading.RLock()
_RUNTIME: Dict[str, Dict[str, Any]] = {}
_COMMAND_LOG = logging.getLogger("smart_condo.ir.command")


class IRConfigurationError(ValueError):
    pass


class IRDriverUnavailable(RuntimeError):
    pass


class IRTransientError(RuntimeError):
    pass


class IRCommandBusy(RuntimeError):
    pass


class IRRateLimited(RuntimeError):
    pass


@dataclass(frozen=True)
class IRDispatchCommand:
    device_id: str
    command_id: str
    capability: str
    code: str
    timeout: float
    value: str | int | float | bool | None = None
    authenticated_user: str = "unknown"


class IRDriver(ABC):
    """Stable driver lifecycle. Learning remains deliberately unavailable."""

    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def shutdown(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def supports(self, profile: "IRProfile") -> bool:
        raise NotImplementedError

    @abstractmethod
    def send(self, command: IRDispatchCommand) -> None:
        raise NotImplementedError

    def learn(self, timeout: float) -> str:
        del timeout
        raise NotImplementedError("ir_learning_not_implemented")

    def save(self, name: str, code: str) -> None:
        del name, code
        raise NotImplementedError("ir_learning_not_implemented")

    def delete(self, name: str) -> None:
        del name
        raise NotImplementedError("ir_learning_not_implemented")

    def rename(self, old_name: str, new_name: str) -> None:
        del old_name, new_name
        raise NotImplementedError("ir_learning_not_implemented")


class TapoIRDriver(IRDriver):
    """Production H110 lifecycle and health adapter.

    Production discovery currently verifies bridge authentication and metadata,
    but not an IR transmit callable. A sender can only be registered by an
    audited adapter after its command format is verified.
    """

    driver_version = "2"

    def __init__(
        self,
        status_loader: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self._status_loader = status_loader
        self._sender: Callable[[str, float], Any] | None = None
        self._initialized = False
        self._last_error: str | None = None
        self._last_command: str | None = None
        self._last_response: str | None = None
        self._last_latency_ms: float | None = None
        self._bridge_lock = threading.Lock()

    def register_verified_sender(self, sender: Callable[[str, float], Any] | None) -> None:
        self._sender = sender

    def initialize(self) -> None:
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False
        self._sender = None

    def _status(self) -> Mapping[str, Any]:
        if self._status_loader is not None:
            return self._status_loader()
        from backend import tapo_ir_local_bridge

        return tapo_ir_local_bridge.local_tapo_ir_status()

    def health(self) -> Dict[str, Any]:
        try:
            payload = self._status() if self._initialized else {}
        except Exception as exc:
            self._last_error = type(exc).__name__
            payload = {}
        diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
        online = payload.get("online") if isinstance(payload.get("online"), bool) else None
        authenticated = bool(payload.get("configured") is True and online is True)
        bridge_error = diagnostics.get("last_error")
        error = self._last_error or (str(bridge_error)[:80] if bridge_error else None)
        ready = bool(self._initialized and authenticated and self._sender)
        if authenticated and not self._sender and error is None:
            error = "tapo_ir_send_unsupported"
        return {
            "online": online,
            "authenticated": authenticated,
            "ready": ready,
            "last_error": error,
            "driver_version": self.driver_version,
            "firmware_version": str(payload.get("firmware"))[:40] if payload.get("firmware") else None,
            "model": str(payload.get("model"))[:40] if payload.get("model") else None,
            "latency_ms": diagnostics.get("latency_ms") if isinstance(diagnostics.get("latency_ms"), (int, float)) else None,
            "last_command": self._last_command,
            "last_response": self._last_response,
            "last_command_latency_ms": self._last_latency_ms,
        }

    def supports(self, profile: "IRProfile") -> bool:
        return bool(profile.commands and self.health()["ready"])

    def send(self, command: IRDispatchCommand) -> None:
        health = self.health()
        if not health["ready"] or not self._sender:
            self._last_error = str(health.get("last_error") or "tapo_ir_sender_not_verified")
            self._last_command = command.command_id
            self._last_response = "rejected"
            raise IRDriverUnavailable(self._last_error)
        started = time.monotonic()
        self._last_command = command.command_id
        try:
            with self._bridge_lock:
                self._sender(command.code, command.timeout)
            self._last_error = None
            self._last_response = "sent"
        except Exception as exc:
            self._last_error = type(exc).__name__
            self._last_response = "failed"
            raise
        finally:
            self._last_latency_ms = round((time.monotonic() - started) * 1000, 1)


class TuyaIRACDriver(IRDriver):
    """Verified T3 cloud driver limited to Bedroom AC power and temperature."""

    driver_version = "1"
    max_attempts = 1
    reject_when_busy = True
    minimum_interval_sec = 1.0

    def __init__(self) -> None:
        self._initialized = False
        self._send_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_attempt = 0.0
        self._last_error: str | None = None
        self._last_response: str | None = None
        self._last_latency_ms: float | None = None
        self._last_commanded: Dict[str, Any] = {}

    def initialize(self) -> None:
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    @staticmethod
    def _configured() -> bool:
        required = (
            "TUYA_CLOUD_ACCESS_ID",
            "TUYA_CLOUD_ACCESS_SECRET",
            "TUYA_CLOUD_DEVICE_ID",
        )
        return bool(
            os.getenv("SMARTLIFE_IR_PROVIDER", "").strip().casefold()
            == "smartlife_cloud"
            and all(os.getenv(key, "").strip() for key in required)
        )

    def health(self) -> Dict[str, Any]:
        ready = bool(self._initialized and self._configured())
        return {
            "online": None,
            "authenticated": ready,
            "ready": ready,
            "last_error": self._last_error if ready else "tuya_cloud_not_configured",
            "driver_version": self.driver_version,
            "last_response": self._last_response,
            "last_command_latency_ms": self._last_latency_ms,
            "last_commanded": copy.deepcopy(self._last_commanded),
        }

    def supports(self, profile: "IRProfile") -> bool:
        return bool(
            self.health()["ready"]
            and profile.metadata.get("transport") == "tuya_ir_ac"
            and set(profile.commands) == {
                "power_off", "power_on",
                *(f"temperature_{value}" for value in range(18, 31)),
            }
        )

    def send(self, command: IRDispatchCommand) -> None:
        if not self._send_lock.acquire(blocking=False):
            raise IRCommandBusy("ir_command_busy")
        try:
            now = time.monotonic()
            with self._rate_lock:
                if now - self._last_attempt < self.minimum_interval_sec:
                    raise IRRateLimited("ir_command_rate_limited")
                self._last_attempt = now
            if command.code == "power" and command.value in (0, 1):
                value = int(command.value)
                state_key = "power"
            elif (
                command.code == "temp"
                and isinstance(command.value, int)
                and not isinstance(command.value, bool)
                and 18 <= command.value <= 30
            ):
                value = command.value
                state_key = "target_temperature"
            else:
                raise IRDriverUnavailable("tuya_ir_command_not_allowed")
            from backend import tuya_cloud_readonly

            started = time.monotonic()
            try:
                payload = tuya_cloud_readonly.configured_ir_client().send_ac_command(
                    command.code,
                    value,
                )
                if payload.get("result") is not True:
                    raise IRDriverUnavailable("tuya_ir_command_rejected")
                self._last_commanded[state_key] = value
                self._last_commanded["updated_at"] = int(time.time())
                self._last_error = None
                self._last_response = "sent"
            except Exception as exc:
                self._last_error = (
                    exc.reason
                    if isinstance(exc, tuya_cloud_readonly.TuyaCloudError)
                    else type(exc).__name__
                )
                self._last_response = "failed"
                if (
                    isinstance(exc, tuya_cloud_readonly.TuyaCloudError)
                    and exc.reason == "tuya_cloud_timeout"
                ):
                    raise TimeoutError("tuya_cloud_timeout") from exc
                if isinstance(exc, tuya_cloud_readonly.TuyaCloudError):
                    raise IRDriverUnavailable(exc.reason) from exc
                raise
            finally:
                self._last_latency_ms = round(
                    (time.monotonic() - started) * 1000,
                    1,
                )
        finally:
            self._send_lock.release()

    def read_last_commanded(self) -> Dict[str, Any]:
        from backend import tuya_cloud_readonly

        payload = tuya_cloud_readonly.configured_ir_client().ac_status()
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise tuya_cloud_readonly.TuyaCloudError("tuya_ir_status_invalid")
        state: Dict[str, Any] = {}
        power = result.get("power")
        temperature = result.get("temp")
        if str(power) in {"0", "1"}:
            state["power"] = int(power)
        try:
            parsed_temperature = int(temperature)
        except (TypeError, ValueError):
            parsed_temperature = 0
        if 18 <= parsed_temperature <= 30:
            state["target_temperature"] = parsed_temperature
        state["retrieved_at"] = int(time.time())
        return state


@dataclass(frozen=True)
class IRCommandDefinition:
    id: str
    capability: str
    label: str
    icon: str
    code: str
    value: str | int | float | bool | None = None


@dataclass(frozen=True)
class IRProfile:
    id: str
    schema_version: int
    brand: str
    model: str
    device_type: str
    capabilities: tuple[Dict[str, Any], ...]
    commands: Dict[str, IRCommandDefinition]
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class IRDevice:
    id: str
    display_name: str
    room: str
    type: str
    driver: str
    profile: str
    capabilities: tuple[str, ...]
    enabled: bool


@dataclass
class QueuedCommand:
    dispatch: IRDispatchCommand
    profile: IRProfile
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None


class DeviceCommandQueue:
    """Synchronous FIFO coordinator with no unmanaged worker thread."""

    def __init__(self, device_id: str, maximum_depth: int = MAX_QUEUE_DEPTH) -> None:
        self.device_id = device_id
        self.maximum_depth = maximum_depth
        self._lock = threading.Lock()
        self._pending: Deque[QueuedCommand] = deque()
        self._draining = False

    @property
    def pending(self) -> int:
        with self._lock:
            return len(self._pending)

    def put(self, job: QueuedCommand) -> tuple[bool, QueuedCommand | None]:
        dropped = None
        with self._lock:
            if len(self._pending) >= self.maximum_depth:
                dropped = self._pending.popleft()
            self._pending.append(job)
            owner = not self._draining
            if owner:
                self._draining = True
        if dropped:
            dropped.result = JSONResponse(
                {"detail": "ir_queue_overflow", "state_quality": "unknown"},
                status_code=429,
            )
            dropped.done.set()
            _log_dispatch(dropped.dispatch, 0.0, "dropped", "ir_queue_overflow")
        self._publish_pending()
        return owner, dropped

    def pop(self) -> QueuedCommand | None:
        with self._lock:
            if not self._pending:
                self._draining = False
                job = None
            else:
                job = self._pending.popleft()
        self._publish_pending()
        return job

    def _publish_pending(self) -> None:
        with _RUNTIME_LOCK:
            status = _RUNTIME.get(self.device_id)
            if status is not None:
                status["pending_queue"] = self.pending

    def submit(
        self,
        job: QueuedCommand,
        executor: Callable[[QueuedCommand], Any],
        *,
        reject_when_busy: bool = False,
    ) -> Any:
        if reject_when_busy:
            with self._lock:
                if self._draining or self._pending:
                    _log_dispatch(
                        job.dispatch,
                        0.0,
                        "rejected",
                        "ir_command_busy",
                    )
                    return JSONResponse(
                        {"detail": "ir_command_busy"},
                        status_code=409,
                    )
                self._pending.append(job)
                self._draining = True
                owner = True
            self._publish_pending()
        else:
            owner, _ = self.put(job)
        if owner:
            while True:
                current = self.pop()
                if current is None:
                    break
                current.result = executor(current)
                current.done.set()
        job.done.wait()
        return job.result


class _DuplicateKey(ValueError):
    pass


def _registry_path() -> Path:
    return Path(os.getenv("IR_DEVICE_REGISTRY_FILE", str(DEFAULT_REGISTRY_PATH))).expanduser()


def _profile_dir() -> Path:
    return Path(os.getenv("IR_PROFILE_DIR", str(DEFAULT_PROFILE_DIR))).expanduser()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(str(key))
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except FileNotFoundError as exc:
        raise IRConfigurationError("configuration_not_found") from exc
    except _DuplicateKey as exc:
        raise IRConfigurationError(f"duplicate_json_key:{exc}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IRConfigurationError("configuration_invalid") from exc


def _identifier(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not IDENTIFIER.fullmatch(text):
        raise IRConfigurationError(f"invalid_{field_name}")
    return text


def _profile_schema(payload: Mapping[str, Any]) -> int:
    if "schema_version" not in payload:
        raise IRConfigurationError("missing_schema_version")
    if payload["schema_version"] != 1:
        raise IRConfigurationError("unknown_profile_schema")
    return 1


def _capability(item: Any, seen: set[str]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise IRConfigurationError("invalid_capability")
    identifier = str(item.get("id") or "")
    if identifier not in CAPABILITY_IDS:
        raise IRConfigurationError("invalid_capability_id")
    if identifier in seen:
        raise IRConfigurationError("duplicate_capability_id")
    seen.add(identifier)
    capability_type = str(item.get("type") or "")
    if capability_type not in CAPABILITY_TYPES:
        raise IRConfigurationError("unknown_capability_type")
    result: Dict[str, Any] = {
        "id": identifier,
        "type": capability_type,
        "label": str(item.get("label") or identifier.replace("_", " ").title())[:80],
        "icon": str(item.get("icon") or "")[:40],
        "group": str(item.get("group") or "main")[:40],
        "confirm": item.get("confirm") is True,
    }
    if capability_type == "range":
        minimum, maximum, step = item.get("min"), item.get("max"), item.get("step")
        if (
            not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (minimum, maximum, step))
            or minimum >= maximum or step <= 0
        ):
            raise IRConfigurationError("invalid_capability_range")
        result.update({"min": minimum, "max": maximum, "step": step, "unit": str(item.get("unit") or "")[:20]})
    if capability_type == "select":
        values = item.get("values")
        if (
            not isinstance(values, list) or not values or len(values) > 100
            or any(not isinstance(value, str) or not value for value in values)
            or len(set(values)) != len(values)
        ):
            raise IRConfigurationError("invalid_capability_values")
        result["values"] = values[:100]
    return result


def load_profile(profile_id: str, directory: Path | None = None) -> IRProfile:
    identifier = _identifier(profile_id, "profile_id")
    root = (directory or _profile_dir()).resolve()
    path = (root / f"{identifier}.json").resolve()
    if path.parent != root:
        raise IRConfigurationError("invalid_profile_path")
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise IRConfigurationError("invalid_profile")
    schema_version = _profile_schema(payload)
    if _identifier(payload.get("id"), "profile_id") != identifier:
        raise IRConfigurationError("profile_id_mismatch")
    for required in ("brand", "model", "device_type", "metadata"):
        if required not in payload:
            raise IRConfigurationError(f"missing_{required}")
    if not isinstance(payload["metadata"], dict):
        raise IRConfigurationError("invalid_metadata")
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list):
        raise IRConfigurationError("invalid_capabilities")
    seen: set[str] = set()
    capabilities = tuple(_capability(item, seen) for item in raw_capabilities)
    raw_commands = payload.get("commands")
    if not isinstance(raw_commands, dict) or len(raw_commands) > 256:
        raise IRConfigurationError("invalid_commands")
    commands: Dict[str, IRCommandDefinition] = {}
    for command_id, item in raw_commands.items():
        command_identifier = _identifier(command_id, "command_id")
        if not isinstance(item, dict) or str(item.get("capability") or "") not in seen:
            raise IRConfigurationError("invalid_command")
        code = item.get("code")
        if not isinstance(code, str) or not code or len(code) > 65536:
            raise IRConfigurationError("invalid_command_code")
        value = item.get("value")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise IRConfigurationError("invalid_command_value")
        commands[command_identifier] = IRCommandDefinition(
            id=command_identifier,
            capability=str(item["capability"]),
            label=str(item.get("label") or command_identifier.replace("_", " ").title())[:80],
            icon=str(item.get("icon") or "")[:40],
            code=code,
            value=value,
        )
    capability_by_id = {item["id"]: item for item in capabilities}
    seen_values: Dict[str, set[Any]] = {}
    for command in commands.values():
        capability = capability_by_id[command.capability]
        capability_type = capability["type"]
        if capability_type == "toggle" and not isinstance(command.value, bool):
            raise IRConfigurationError("invalid_toggle_command_value")
        if capability_type == "select" and command.value not in capability["values"]:
            raise IRConfigurationError("invalid_select_command_value")
        if capability_type == "range":
            value = command.value
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise IRConfigurationError("invalid_range_command_value")
            offset = (value - capability["min"]) / capability["step"]
            if value < capability["min"] or value > capability["max"] or abs(offset - round(offset)) > 1e-9:
                raise IRConfigurationError("invalid_range_command_value")
        if command.value is not None:
            values = seen_values.setdefault(command.capability, set())
            if command.value in values:
                raise IRConfigurationError("duplicate_command_value")
            values.add(command.value)
    return IRProfile(
        identifier,
        schema_version,
        str(payload["brand"])[:80],
        str(payload["model"])[:80],
        _identifier(payload["device_type"], "device_type"),
        capabilities,
        commands,
        copy.deepcopy(payload["metadata"]),
    )


def load_devices(path: Path | None = None) -> list[IRDevice]:
    payload = _read_json(path or _registry_path())
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise IRConfigurationError("invalid_registry_schema")
    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list) or len(raw_devices) > 100:
        raise IRConfigurationError("invalid_devices")
    devices = []
    seen = set()
    for item in raw_devices:
        if not isinstance(item, dict):
            raise IRConfigurationError("invalid_device")
        identifier = _identifier(item.get("id"), "device_id")
        if identifier in seen:
            raise IRConfigurationError("duplicate_device_id")
        seen.add(identifier)
        declared = item.get("capabilities")
        if not isinstance(declared, list) or any(value not in CAPABILITY_IDS for value in declared):
            raise IRConfigurationError("invalid_device_capabilities")
        display_name = str(item.get("display_name") or "").strip()[:80]
        if not display_name:
            raise IRConfigurationError("invalid_display_name")
        devices.append(IRDevice(
            id=identifier,
            display_name=display_name,
            room=_identifier(item.get("room"), "room"),
            type=_identifier(item.get("type"), "device_type"),
            driver=_identifier(item.get("driver"), "driver"),
            profile=_identifier(item.get("profile"), "profile"),
            capabilities=tuple(dict.fromkeys(str(value) for value in declared)),
            enabled=item.get("enabled") is True,
        ))
    return devices


DRIVERS: Dict[str, IRDriver] = {}


def register_driver(name: str, driver: IRDriver) -> None:
    identifier = _identifier(name, "driver")
    previous = DRIVERS.get(identifier)
    if previous is not None:
        try:
            previous.shutdown()
        except Exception:
            pass
    driver.initialize()
    DRIVERS[identifier] = driver


def shutdown_drivers() -> None:
    for driver in tuple(DRIVERS.values()):
        try:
            driver.shutdown()
        except Exception:
            pass


def _device(device_id: str) -> IRDevice | None:
    try:
        return next((item for item in load_devices() if item.id == device_id), None)
    except IRConfigurationError:
        return None


def _queue(device_id: str) -> DeviceCommandQueue:
    with _DEVICE_QUEUES_GUARD:
        return _DEVICE_QUEUES.setdefault(device_id, DeviceCommandQueue(device_id))


def _runtime_status(device: IRDevice, driver: IRDriver | None, profile: IRProfile | None) -> Dict[str, Any]:
    health = _driver_health(driver)
    try:
        supported = bool(driver and profile and driver.supports(profile))
    except Exception:
        supported = False
    if not driver:
        health = {
        "online": None, "ready": False, "last_error": "driver_not_found", "driver_version": None,
        }
    now = int(time.time())
    with _RUNTIME_LOCK:
        status = _RUNTIME.setdefault(device.id, {
            "enabled": device.enabled,
            "online": None,
            "healthy": False,
            "driver": device.driver,
            "profile": device.profile,
            "firmware_version": None,
            "last_seen": None,
            "last_command": None,
            "last_success": None,
            "last_failure": None,
            "pending_queue": 0,
            "retry_count": 0,
            "authenticated": False,
            "model": None,
            "latency_ms": None,
            "last_response": None,
            "last_error": None,
        })
        status.update({
            "enabled": device.enabled,
            "online": health.get("online"),
            "healthy": bool(health.get("ready") and supported),
            "driver": device.driver,
            "profile": device.profile,
            "firmware_version": health.get("firmware_version"),
            "driver_version": health.get("driver_version"),
            "last_seen": now if health.get("online") is True else status.get("last_seen"),
            "pending_queue": _queue(device.id).pending,
            "authenticated": health.get("authenticated") is True,
            "model": health.get("model"),
            "latency_ms": health.get("last_command_latency_ms") or health.get("latency_ms"),
            "last_response": health.get("last_response"),
            "last_error": health.get("last_error"),
        })
    return copy.deepcopy(status)


def _driver_health(driver: IRDriver | None) -> Dict[str, Any]:
    if driver is None:
        return {
            "online": None,
            "authenticated": False,
            "ready": False,
            "last_error": "driver_not_found",
            "driver_version": None,
        }
    try:
        value = driver.health()
        if not isinstance(value, dict):
            raise TypeError("invalid_driver_health")
        return {
            "online": value.get("online") if isinstance(value.get("online"), bool) else None,
            "authenticated": value.get("authenticated") is True,
            "ready": value.get("ready") is True,
            "last_error": str(value.get("last_error"))[:80] if value.get("last_error") else None,
            "driver_version": str(value.get("driver_version"))[:40] if value.get("driver_version") else None,
            "firmware_version": str(value.get("firmware_version"))[:40] if value.get("firmware_version") else None,
            "model": str(value.get("model"))[:40] if value.get("model") else None,
            "latency_ms": value.get("latency_ms") if isinstance(value.get("latency_ms"), (int, float)) else None,
            "last_command": str(value.get("last_command"))[:64] if value.get("last_command") else None,
            "last_response": str(value.get("last_response"))[:40] if value.get("last_response") else None,
            "last_command_latency_ms": (
                value.get("last_command_latency_ms")
                if isinstance(value.get("last_command_latency_ms"), (int, float)) else None
            ),
        }
    except Exception as exc:
        return {
            "online": None,
            "authenticated": False,
            "ready": False,
            "last_error": type(exc).__name__,
            "driver_version": None,
            "firmware_version": None,
            "model": None,
            "latency_ms": None,
            "last_command": None,
            "last_response": None,
            "last_command_latency_ms": None,
        }


def _public_status(status: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(status.get(key))
        for key in (
            "enabled", "online", "healthy", "firmware_version", "last_seen",
            "last_command", "last_success", "last_failure", "pending_queue", "retry_count",
            "authenticated", "model", "latency_ms", "last_response", "last_error",
        )
    }


def last_commanded_state(device_id: str) -> Dict[str, Any]:
    device = _device(device_id)
    driver = DRIVERS.get(device.driver) if device else None
    if not isinstance(driver, TuyaIRACDriver):
        return {}
    return copy.deepcopy(driver._last_commanded)


def _public_device(device: IRDevice) -> Dict[str, Any]:
    reason = None
    profile = None
    try:
        profile = load_profile(device.profile)
    except IRConfigurationError as exc:
        reason = str(exc)
    declared = set(device.capabilities)
    commands_by_capability: Dict[str, list[Dict[str, Any]]] = {}
    if profile:
        for command in profile.commands.values():
            if command.capability in declared:
                commands_by_capability.setdefault(command.capability, []).append({
                    "id": command.id,
                    "label": command.label,
                    "icon": command.icon,
                    "value": command.value,
                })
    capabilities = []
    if profile:
        for item in profile.capabilities:
            if item["id"] in declared:
                public = copy.deepcopy(item)
                public["commands"] = commands_by_capability.get(item["id"], [])
                capabilities.append(public)
    driver = DRIVERS.get(device.driver)
    status = _runtime_status(device, driver, profile)
    if not device.enabled:
        reason = "device_disabled"
    elif reason is None and not any(item["commands"] for item in capabilities):
        reason = "profile_has_no_commands"
    elif reason is None and not status["healthy"]:
        reason = _driver_health(driver).get("last_error") or "driver_unavailable"
    return {
        "device": {
            "id": device.id,
            "friendly_name": device.display_name,
            "room": device.room,
            "type": device.type,
        },
        "capabilities": capabilities,
        "runtime_status": _public_status(status),
        "controllable": bool(device.enabled and capabilities and status["healthy"]),
        "unavailable_reason": reason,
    }


def public_devices() -> list[Dict[str, Any]]:
    try:
        return [_public_device(device) for device in load_devices()]
    except IRConfigurationError:
        return []


class IRCommandRequest(BaseModel):
    command: str | None = None
    capability: str | None = None
    value: str | int | float | bool | None = None


def _resolve_command(profile: IRProfile, request: IRCommandRequest) -> IRCommandDefinition | None:
    if request.command:
        return profile.commands.get(request.command)
    if request.capability:
        for command in profile.commands.values():
            if command.capability == request.capability and command.value == request.value:
                return command
    return None


def _log_dispatch(
    dispatch: IRDispatchCommand,
    duration_ms: float,
    result: str,
    error_reason: str | None,
) -> None:
    _COMMAND_LOG.info(
        "ir_command timestamp=%d user=%s device=%s command=%s value=%s duration_ms=%.1f result=%s error_reason=%s",
        int(time.time()),
        re.sub(r"[^A-Za-z0-9_.@-]", "_", dispatch.authenticated_user)[:64],
        dispatch.device_id,
        dispatch.command_id,
        str(dispatch.value)[:16] if dispatch.value is not None else "none",
        duration_ms,
        result,
        error_reason or "none",
    )


def _log_rejection(
    device_id: str,
    command_id: str | None,
    started: float,
    error_reason: str,
) -> None:
    safe_device = device_id if IDENTIFIER.fullmatch(device_id) else "invalid_device"
    safe_command = command_id if command_id and IDENTIFIER.fullmatch(command_id) else "invalid_command"
    dispatch = IRDispatchCommand(safe_device, safe_command, "custom", "", 0)
    _log_dispatch(
        dispatch,
        (time.monotonic() - started) * 1000,
        "rejected",
        error_reason,
    )


def _execute_job(driver: IRDriver, job: QueuedCommand) -> Any:
    device_id = job.dispatch.device_id
    attempts = 0
    started = time.monotonic()
    with _RUNTIME_LOCK:
        _RUNTIME[device_id]["last_command"] = job.dispatch.command_id
    maximum_attempts = max(1, min(2, int(getattr(driver, "max_attempts", 2))))
    for attempts in range(1, maximum_attempts + 1):
        try:
            driver.send(job.dispatch)
            now = int(time.time())
            with _RUNTIME_LOCK:
                status = _RUNTIME[device_id]
                status.update({
                    "last_success": now,
                    "last_failure": None,
                    "last_response": "sent",
                    "last_error": None,
                })
            _log_dispatch(job.dispatch, (time.monotonic() - started) * 1000, "sent", None)
            response = {
                "ok": True,
                "device_id": device_id,
                "command": job.dispatch.command_id,
                "capability": job.dispatch.capability,
                "attempts": attempts,
                "state_quality": "assumed",
                "updated_at": now,
            }
            if isinstance(driver, TuyaIRACDriver):
                response.update({
                    "last_commanded": copy.deepcopy(driver._last_commanded),
                    "physical_state_confirmed": False,
                    "latency_ms": driver._last_latency_ms,
                })
            return response
        except IRRateLimited:
            _log_dispatch(
                job.dispatch,
                (time.monotonic() - started) * 1000,
                "rejected",
                "ir_command_rate_limited",
            )
            return JSONResponse(
                {"detail": "ir_command_rate_limited", "retry_after_sec": 1},
                status_code=429,
            )
        except IRCommandBusy:
            _log_dispatch(
                job.dispatch,
                (time.monotonic() - started) * 1000,
                "rejected",
                "ir_command_busy",
            )
            return JSONResponse({"detail": "ir_command_busy"}, status_code=409)
        except (TimeoutError, IRTransientError):
            if attempts < maximum_attempts:
                with _RUNTIME_LOCK:
                    _RUNTIME[device_id]["retry_count"] += 1
                continue
            with _RUNTIME_LOCK:
                _RUNTIME[device_id].update({
                    "last_failure": int(time.time()),
                    "last_response": "timeout",
                    "last_error": "ir_command_timeout",
                })
            _log_dispatch(
                job.dispatch,
                (time.monotonic() - started) * 1000,
                "timeout",
                "ir_command_timeout",
            )
            return JSONResponse(
                {"detail": "ir_command_timeout", "attempts": attempts, "state_quality": "unknown"},
                status_code=504,
            )
        except Exception:
            with _RUNTIME_LOCK:
                _RUNTIME[device_id].update({
                    "last_failure": int(time.time()),
                    "last_response": "failed",
                    "last_error": "ir_command_failed",
                })
            _log_dispatch(
                job.dispatch,
                (time.monotonic() - started) * 1000,
                "failed",
                "ir_command_failed",
            )
            return JSONResponse(
                {"detail": "ir_command_failed", "attempts": attempts, "state_quality": "unknown"},
                status_code=502,
            )
    raise AssertionError("unreachable")


def execute_command(
    device_id: str,
    command_or_request: str | IRCommandRequest,
    timeout: float | None = None,
    authenticated_user: str = "unknown",
):
    started = time.monotonic()
    requested_id = (
        command_or_request
        if isinstance(command_or_request, str)
        else command_or_request.command or command_or_request.capability
    )
    device = _device(device_id)
    if device is None:
        _log_rejection(device_id, requested_id, started, "ir_device_not_found")
        return JSONResponse({"detail": "ir_device_not_found"}, status_code=404)
    try:
        profile = load_profile(device.profile)
    except IRConfigurationError:
        _log_rejection(device_id, requested_id, started, "ir_profile_missing")
        return JSONResponse({"detail": "ir_profile_missing"}, status_code=422)
    request = (
        IRCommandRequest(command=command_or_request)
        if isinstance(command_or_request, str) else command_or_request
    )
    command = _resolve_command(profile, request)
    if command is None:
        _log_rejection(device_id, requested_id, started, "ir_command_unknown")
        return JSONResponse({"detail": "ir_command_unknown"}, status_code=422)
    if command.capability not in device.capabilities:
        _log_rejection(device_id, command.id, started, "ir_capability_unsupported")
        return JSONResponse({"detail": "ir_capability_unsupported"}, status_code=422)
    driver = DRIVERS.get(device.driver)
    status = _runtime_status(device, driver, profile)
    if not device.enabled or not driver or not status["healthy"]:
        _log_rejection(device_id, command.id, started, "ir_driver_unavailable")
        return JSONResponse({"detail": "ir_driver_unavailable"}, status_code=422)
    dispatch = IRDispatchCommand(
        device.id,
        command.id,
        command.capability,
        command.code,
        timeout or DEFAULT_TIMEOUT_SEC,
        command.value,
        authenticated_user,
    )
    return _queue(device.id).submit(
        QueuedCommand(dispatch, profile),
        lambda job: _execute_job(driver, job),
        reject_when_busy=bool(getattr(driver, "reject_when_busy", False)),
    )


@app.get("/api/ir/devices")
def ir_devices() -> Dict[str, Any]:
    devices = public_devices()
    return {"devices": devices, "count": len(devices)}


@app.post("/api/ir/{device_id}/command")
def ir_command(
    device_id: str,
    request: Request,
    payload: IRCommandRequest = Body(...),
):
    return execute_command(
        device_id,
        payload,
        authenticated_user=str(
            getattr(request.state, "dashboard_user", None) or "unknown"
        ),
    )


@app.get("/api/ir/bed-room-air-conditioner/status")
def bedroom_ac_status():
    driver = DRIVERS.get("tuya_ir_ac")
    if not isinstance(driver, TuyaIRACDriver) or not driver.health()["ready"]:
        return JSONResponse(
            {"detail": "ir_driver_unavailable"},
            status_code=503,
        )
    try:
        state = driver.read_last_commanded()
    except Exception:
        return JSONResponse(
            {"detail": "tuya_ir_status_unavailable"},
            status_code=503,
        )
    return {
        "device_id": "bed-room-air-conditioner",
        "last_commanded": state,
        "state_quality": "assumed",
        "physical_state_confirmed": False,
    }


register_driver("tapo_ir", TapoIRDriver())
register_driver("tuya_ir_ac", TuyaIRACDriver())
atexit.register(shutdown_drivers)
