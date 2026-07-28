"""Generic, profile-driven IR device framework."""
from __future__ import annotations

import atexit
import copy
import json
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Mapping

from fastapi import Body
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


class IRConfigurationError(ValueError):
    pass


class IRDriverUnavailable(RuntimeError):
    pass


class IRTransientError(RuntimeError):
    pass


@dataclass(frozen=True)
class IRDispatchCommand:
    device_id: str
    command_id: str
    capability: str
    code: str
    timeout: float


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
    """Adapter boundary for a future verified Tapo IR bridge sender."""

    driver_version = "1"

    def __init__(self) -> None:
        self._sender: Callable[[str, float], None] | None = None
        self._initialized = False
        self._last_error: str | None = None

    def register_sender(self, sender: Callable[[str, float], None] | None) -> None:
        self._sender = sender

    def initialize(self) -> None:
        self._initialized = True

    def shutdown(self) -> None:
        self._initialized = False

    def health(self) -> Dict[str, Any]:
        ready = bool(self._initialized and self._sender)
        return {
            "online": None,
            "ready": ready,
            "last_error": self._last_error,
            "driver_version": self.driver_version,
        }

    def supports(self, profile: "IRProfile") -> bool:
        return bool(profile.commands)

    def send(self, command: IRDispatchCommand) -> None:
        if not self._initialized or not self._sender:
            self._last_error = "tapo_ir_sender_not_verified"
            raise IRDriverUnavailable(self._last_error)
        try:
            self._sender(command.code, command.timeout)
            self._last_error = None
        except Exception as exc:
            self._last_error = type(exc).__name__
            raise


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

    def submit(self, job: QueuedCommand, executor: Callable[[QueuedCommand], Any]) -> Any:
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
        })
    return copy.deepcopy(status)


def _driver_health(driver: IRDriver | None) -> Dict[str, Any]:
    if driver is None:
        return {
            "online": None,
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
            "ready": value.get("ready") is True,
            "last_error": str(value.get("last_error"))[:80] if value.get("last_error") else None,
            "driver_version": str(value.get("driver_version"))[:40] if value.get("driver_version") else None,
            "firmware_version": str(value.get("firmware_version"))[:40] if value.get("firmware_version") else None,
        }
    except Exception as exc:
        return {
            "online": None,
            "ready": False,
            "last_error": type(exc).__name__,
            "driver_version": None,
            "firmware_version": None,
        }


def _public_status(status: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: copy.deepcopy(status.get(key))
        for key in (
            "enabled", "online", "healthy", "firmware_version", "last_seen",
            "last_command", "last_success", "last_failure", "pending_queue", "retry_count",
        )
    }


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


def _execute_job(driver: IRDriver, job: QueuedCommand) -> Any:
    device_id = job.dispatch.device_id
    attempts = 0
    with _RUNTIME_LOCK:
        _RUNTIME[device_id]["last_command"] = job.dispatch.command_id
    for attempts in (1, 2):
        try:
            driver.send(job.dispatch)
            now = int(time.time())
            with _RUNTIME_LOCK:
                status = _RUNTIME[device_id]
                status.update({
                    "last_success": now,
                    "last_failure": None,
                })
            return {
                "ok": True,
                "device_id": device_id,
                "command": job.dispatch.command_id,
                "capability": job.dispatch.capability,
                "attempts": attempts,
                "state_quality": "assumed",
                "updated_at": now,
            }
        except (TimeoutError, IRTransientError):
            if attempts == 1:
                with _RUNTIME_LOCK:
                    _RUNTIME[device_id]["retry_count"] += 1
                continue
            with _RUNTIME_LOCK:
                _RUNTIME[device_id]["last_failure"] = int(time.time())
            return JSONResponse(
                {"detail": "ir_command_timeout", "attempts": attempts, "state_quality": "unknown"},
                status_code=504,
            )
        except Exception:
            with _RUNTIME_LOCK:
                _RUNTIME[device_id]["last_failure"] = int(time.time())
            return JSONResponse(
                {"detail": "ir_command_failed", "attempts": attempts, "state_quality": "unknown"},
                status_code=502,
            )
    raise AssertionError("unreachable")


def execute_command(
    device_id: str,
    command_or_request: str | IRCommandRequest,
    timeout: float | None = None,
):
    device = _device(device_id)
    if device is None:
        return JSONResponse({"detail": "ir_device_not_found"}, status_code=404)
    try:
        profile = load_profile(device.profile)
    except IRConfigurationError:
        return JSONResponse({"detail": "ir_profile_missing"}, status_code=422)
    request = (
        IRCommandRequest(command=command_or_request)
        if isinstance(command_or_request, str) else command_or_request
    )
    command = _resolve_command(profile, request)
    if command is None:
        return JSONResponse({"detail": "ir_command_unknown"}, status_code=422)
    if command.capability not in device.capabilities:
        return JSONResponse({"detail": "ir_capability_unsupported"}, status_code=422)
    driver = DRIVERS.get(device.driver)
    status = _runtime_status(device, driver, profile)
    if not device.enabled or not driver or not status["healthy"]:
        return JSONResponse({"detail": "ir_driver_unavailable"}, status_code=422)
    dispatch = IRDispatchCommand(
        device.id,
        command.id,
        command.capability,
        command.code,
        timeout or DEFAULT_TIMEOUT_SEC,
    )
    return _queue(device.id).submit(
        QueuedCommand(dispatch, profile),
        lambda job: _execute_job(driver, job),
    )


@app.get("/api/ir/devices")
def ir_devices() -> Dict[str, Any]:
    devices = public_devices()
    return {"devices": devices, "count": len(devices)}


@app.post("/api/ir/{device_id}/command")
def ir_command(device_id: str, payload: IRCommandRequest = Body(...)):
    return execute_command(device_id, payload)


register_driver("tapo_ir", TapoIRDriver())
atexit.register(shutdown_drivers)
