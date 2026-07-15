"""Production-safe local Tapo IR discovery foundation.

Discovery is read-only and on demand. No IR command, learning, pairing, polling
thread, subprocess, or additional MQTT client is created here.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import re
import threading
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from backend import app as app_module
from backend.device_framework import UnifiedDevice
from backend.device_registry import registry

app = app_module.app
_CACHE_SEC = max(30, int(os.getenv("TAPO_IR_LOCAL_CACHE_SEC", "30")))
_DISCOVERY_TIMEOUT_SEC = max(3.0, float(os.getenv("TAPO_IR_DISCOVERY_TIMEOUT_SEC", "8")))
_lock = threading.RLock()
_cache: Dict[str, Any] = {"ts": 0, "payload": None}

_SENSITIVE_PARTS = (
    "username", "password", "token", "secret", "credential", "cookie",
    "session", "auth", "key", "encrypt", "decrypt", "payload",
)


def invalidate_cache() -> None:
    with _lock:
        _cache["ts"] = 0
        _cache["payload"] = None


def _safe_error(exc: BaseException) -> str:
    return type(exc).__name__


def _configuration() -> Dict[str, Optional[str]]:
    return {
        "host": os.getenv("TAPO_IR_HOST", "").strip() or None,
        "username": os.getenv("TAPO_IR_USERNAME", "").strip() or None,
        "password": os.getenv("TAPO_IR_PASSWORD", "").strip() or None,
        "device_id": os.getenv("TAPO_IR_DEVICE_ID", "").strip() or None,
        "model": os.getenv("TAPO_IR_MODEL", "").strip() or None,
        "mac": os.getenv("TAPO_IR_MAC", "").strip() or None,
    }


def _configured(config: Mapping[str, Optional[str]]) -> bool:
    identity = bool(config.get("host") or config.get("device_id") or config.get("model") or config.get("mac"))
    credentials = bool(config.get("username") and config.get("password"))
    return identity and credentials


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<max-depth>"
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(part in lowered for part in _SENSITIVE_PARTS):
                continue
            result[key_text] = _safe_value(item, depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe_value(item, depth + 1) for item in list(value)[:100]]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        try:
            return _safe_value(value.to_dict(), depth + 1)
        except Exception:
            pass
    return str(value)


def _read_attr(value: Any, *names: str) -> Any:
    for name in names:
        try:
            item = getattr(value, name, None)
        except Exception:
            item = None
        if item not in (None, ""):
            return item
    return None


def _mapping_value(value: Any, *names: str) -> Any:
    if not isinstance(value, Mapping):
        return None
    for name in names:
        item = value.get(name)
        if item not in (None, ""):
            return item
    return None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is not None and hasattr(value, "to_dict"):
        try:
            mapped = value.to_dict()
            return mapped if isinstance(mapped, Mapping) else {}
        except Exception:
            return {}
    return {}


def _device_info(device: Any) -> Mapping[str, Any]:
    for name in ("sys_info", "device_info", "info"):
        mapped = _as_mapping(_read_attr(device, name))
        if mapped:
            return mapped
    return {}


def _host_of(device: Any, fallback: Optional[str] = None) -> Optional[str]:
    config = _read_attr(device, "config")
    host = _read_attr(config, "host") if config is not None else None
    return str(host or _read_attr(device, "host") or fallback or "").strip() or None


def _normalize_mac(value: Any) -> str:
    return re.sub(r"[^0-9a-f]", "", str(value or "").lower())


def _normalize_model(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    for prefix in ("tplink", "tapo"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def _device_identity(device: Any, host: Optional[str]) -> Dict[str, Optional[str]]:
    info = _device_info(device)
    hw_info = _as_mapping(_read_attr(device, "hw_info"))
    model = _read_attr(device, "model") or _mapping_value(info, "model", "device_model")
    device_id = _read_attr(device, "device_id", "id") or _mapping_value(info, "device_id", "deviceId", "dev_id")
    mac = _read_attr(device, "mac") or _mapping_value(info, "mac", "mac_address")
    firmware = (
        _read_attr(device, "firmware_version")
        or _mapping_value(info, "sw_ver", "firmware_version", "fw_ver", "software_version")
    )
    hardware = (
        _read_attr(device, "hardware_version")
        or _mapping_value(info, "hw_ver", "hardware_version", "hardware_ver")
        or _mapping_value(hw_info, "hw_ver", "hardware_version")
    )
    device_type = _mapping_value(info, "type", "device_type", "category") or type(device).__name__
    alias = _read_attr(device, "alias") or _mapping_value(info, "alias", "nickname", "device_name")
    return {
        "host": host,
        "device_id": str(device_id) if device_id not in (None, "") else None,
        "mac": str(mac) if mac not in (None, "") else None,
        "model": str(model) if model not in (None, "") else None,
        "firmware": str(firmware) if firmware not in (None, "") else None,
        "hardware_version": str(hardware) if hardware not in (None, "") else None,
        "device_type": str(device_type) if device_type not in (None, "") else None,
        "alias": str(alias) if alias not in (None, "") else None,
        "name": str(alias) if alias not in (None, "") else None,
    }


def _iter_feature_names(device: Any) -> Iterable[str]:
    seen: set[str] = set()
    for container_name in ("features", "modules"):
        container = _read_attr(device, container_name)
        if isinstance(container, Mapping):
            values = container.keys()
        elif isinstance(container, Sequence) and not isinstance(container, (str, bytes, bytearray)):
            values = container
        else:
            values = ()
        for value in values:
            text = str(value).strip()
            if text and text not in seen:
                seen.add(text)
                yield text
    for name in ("send_command", "learn_command", "delete_command", "transmit", "play"):
        try:
            present = callable(getattr(device, name, None))
        except Exception:
            present = False
        if present and name not in seen:
            seen.add(name)
            yield name


def _capability_snapshot(device: Any, identity: Mapping[str, Optional[str]]) -> Dict[str, Any]:
    feature_names = sorted(_iter_feature_names(device))
    text = " ".join([
        str(identity.get("model") or ""), str(identity.get("device_type") or ""), *feature_names,
    ]).lower().replace("_", " ")
    ir_markers = ("infrared", " ir ", "ir remote", "remote control", "send command", "learn command")
    exposes_ir = any(marker in f" {text} " for marker in ir_markers)
    capabilities = [name for name in feature_names if any(marker in name.lower() for marker in ("ir", "remote", "command", "learn"))]
    return {
        "exposes_ir": exposes_ir,
        "capabilities": capabilities,
        "feature_list": feature_names,
        "supported_actions": [],
        "local_control_supported": exposes_ir and any(name in {"send_command", "transmit", "play"} for name in feature_names),
    }


def _identity_comparison(identity: Mapping[str, Optional[str]], config: Mapping[str, Optional[str]], *, targeted: bool) -> Dict[str, Any]:
    expected_host = str(config.get("host") or "").strip() or None
    actual_host = str(identity.get("host") or "").strip() or None
    expected_model = str(config.get("model") or "").strip() or None
    actual_model = str(identity.get("model") or "").strip() or None
    expected_mac = str(config.get("mac") or "").strip() or None
    actual_mac = str(identity.get("mac") or "").strip() or None
    expected_id = str(config.get("device_id") or "").strip() or None
    actual_id = str(identity.get("device_id") or "").strip() or None

    host_match = bool(expected_host and actual_host and expected_host == actual_host)
    mac_match = bool(expected_mac and actual_mac and _normalize_mac(expected_mac) == _normalize_mac(actual_mac))
    id_match = bool(expected_id and actual_id and expected_id.lower() == actual_id.lower())
    model_match = bool(expected_model and actual_model and _normalize_model(expected_model) == _normalize_model(actual_model))

    failures: list[str] = []
    if expected_id and actual_id and not id_match:
        failures.append("device_id")
    if expected_mac and actual_mac and not mac_match:
        failures.append("mac")
    if expected_host and actual_host and not host_match:
        failures.append("host")
    if expected_model and actual_model and not model_match:
        failures.append("model")

    # Strong identity wins. A targeted connection to the configured host is valid
    # after successful update unless a stronger configured ID/MAC explicitly differs.
    strong_match = id_match or mac_match or (host_match and mac_match)
    hard_mismatch = any(name in failures for name in ("device_id", "mac"))
    accepted = False
    matched_by: Optional[str] = None
    if strong_match:
        accepted = True
        matched_by = "device_id" if id_match else "mac"
    elif targeted and host_match and not hard_mismatch:
        accepted = True
        matched_by = "host"
    elif host_match and not hard_mismatch:
        accepted = True
        matched_by = "host"
    elif model_match and not hard_mismatch:
        accepted = True
        matched_by = "model"

    return {
        "accepted": accepted,
        "matched_by": matched_by,
        "comparison_failed": failures,
        "expected_host": expected_host,
        "actual_host": actual_host,
        "expected_model": expected_model,
        "actual_model": actual_model,
        "expected_mac": expected_mac,
        "actual_mac": actual_mac,
        "expected_device_id": expected_id,
        "actual_device_id": actual_id,
        "host_match": host_match,
        "model_match": model_match,
        "mac_match": mac_match,
        "device_id_match": id_match,
    }


def _safe_debug_snapshot(device: Any, identity: Mapping[str, Optional[str]], capability: Mapping[str, Any]) -> Dict[str, Any]:
    sys_info = _safe_value(_device_info(device))
    hw_info = _safe_value(_as_mapping(_read_attr(device, "hw_info")))
    child_devices_raw = _read_attr(device, "child_devices", "children")
    child_devices = _safe_value(child_devices_raw if child_devices_raw is not None else [])
    modules = _read_attr(device, "modules")
    module_names = sorted(str(key) for key in modules.keys()) if isinstance(modules, Mapping) else []
    features = _read_attr(device, "features")
    feature_names = sorted(str(key) for key in features.keys()) if isinstance(features, Mapping) else list(capability.get("feature_list") or [])
    return {
        **identity,
        "firmware_version": identity.get("firmware"),
        "hw_info": hw_info,
        "sys_info": sys_info,
        "child_devices": child_devices,
        "feature_list": feature_names,
        "supported_modules": module_names,
        "capabilities": list(capability.get("capabilities") or []),
        "exposes_ir": bool(capability.get("exposes_ir")),
        "local_control_supported": bool(capability.get("local_control_supported")),
    }


async def _close_device(device: Any) -> None:
    try:
        close = getattr(device, "disconnect", None) or getattr(device, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
    except Exception:
        pass


async def _update_device(device: Any) -> None:
    update = getattr(device, "update", None)
    if callable(update):
        result = update()
        if inspect.isawaitable(result):
            await result


def _credentials(kasa_module: Any, config: Mapping[str, Optional[str]]) -> Any:
    cls = getattr(kasa_module, "Credentials", None)
    return cls(config.get("username"), config.get("password")) if cls is not None else None


async def _discover_single(discover: Any, host: str, credentials: Any, config: Mapping[str, Optional[str]]) -> Any:
    method = getattr(discover, "discover_single", None)
    if not callable(method):
        return None
    variants = [
        {"host": host, "credentials": credentials, "timeout": _DISCOVERY_TIMEOUT_SEC},
        {"host": host, "credentials": credentials},
        {"host": host, "username": config.get("username"), "password": config.get("password")},
        {"host": host},
    ]
    last_type_error: Optional[BaseException] = None
    for kwargs in variants:
        try:
            return await method(**{key: value for key, value in kwargs.items() if value is not None})
        except TypeError as exc:
            last_type_error = exc
    if last_type_error:
        raise last_type_error
    return None


async def _discover_all(discover: Any, credentials: Any, config: Mapping[str, Optional[str]]) -> Mapping[str, Any]:
    method = getattr(discover, "discover", None)
    if not callable(method):
        return {}
    variants = [
        {"credentials": credentials, "timeout": _DISCOVERY_TIMEOUT_SEC},
        {"credentials": credentials},
        {"username": config.get("username"), "password": config.get("password")},
        {},
    ]
    last_type_error: Optional[BaseException] = None
    for kwargs in variants:
        try:
            value = await method(**{key: item for key, item in kwargs.items() if item is not None})
            return value if isinstance(value, Mapping) else {}
        except TypeError as exc:
            last_type_error = exc
    if last_type_error:
        raise last_type_error
    return {}


async def _discover_async(config: Mapping[str, Optional[str]]) -> Dict[str, Any]:
    try:
        import kasa  # type: ignore
    except Exception as exc:
        return {"device": None, "method": "python_kasa", "library": "python-kasa", "error": _safe_error(exc), "comparison": {}}

    discover = getattr(kasa, "Discover", None)
    if discover is None:
        return {"device": None, "method": "python_kasa", "library": "python-kasa", "error": "DiscoverUnavailable", "comparison": {}}
    credentials = _credentials(kasa, config)
    host = config.get("host")
    errors: list[str] = []
    last_comparison: Dict[str, Any] = {}

    if host:
        try:
            device = await asyncio.wait_for(_discover_single(discover, host, credentials, config), timeout=_DISCOVERY_TIMEOUT_SEC + 2)
            if device is not None:
                await _update_device(device)
                identity = _device_identity(device, _host_of(device, host))
                comparison = _identity_comparison(identity, config, targeted=True)
                if comparison["accepted"]:
                    return {"device": device, "method": "python_kasa_targeted", "library": "python-kasa", "error": None, "comparison": comparison}
                last_comparison = comparison
                await _close_device(device)
                errors.append("IdentityMismatch")
        except Exception as exc:
            errors.append(_safe_error(exc))

    try:
        devices = await asyncio.wait_for(_discover_all(discover, credentials, config), timeout=_DISCOVERY_TIMEOUT_SEC + 2)
        for discovered_host, device in devices.items():
            candidate_host = _host_of(device, str(discovered_host))
            try:
                await _update_device(device)
                identity = _device_identity(device, candidate_host)
                comparison = _identity_comparison(identity, config, targeted=False)
                if comparison["accepted"]:
                    return {"device": device, "method": "python_kasa_broadcast", "library": "python-kasa", "error": None, "comparison": comparison}
                last_comparison = comparison
            except Exception as exc:
                errors.append(_safe_error(exc))
            await _close_device(device)
    except Exception as exc:
        errors.append(_safe_error(exc))

    return {
        "device": None,
        "method": "python_kasa_targeted_then_broadcast" if host else "python_kasa_broadcast",
        "library": "python-kasa",
        "error": errors[-1] if errors else "DeviceNotFound",
        "comparison": last_comparison,
    }


def _unknown(configured: bool, *, host: Optional[str], error: Optional[str], method: str, library: str, latency: Optional[float], comparison: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    reason = "not_configured" if not configured else (error or "device_not_found")
    return {
        "configured": configured,
        "online": None,
        "health": "unknown",
        "host": host,
        "mac": None,
        "model": None,
        "device_id": None,
        "device_type": None,
        "firmware": None,
        "hardware_version": None,
        "capabilities": [],
        "supported_actions": [],
        "last_update": None,
        "diagnostics": {
            "source": "tapo_local",
            "latency_ms": latency,
            "last_error": reason,
            "discovery_method": method,
            "library": library,
            "local_control_supported": False,
            "exposes_ir": False,
            **dict(comparison or {}),
        },
        "debug": {},
    }


def _snapshot_uncached() -> Dict[str, Any]:
    config = _configuration()
    configured = _configured(config)
    if not configured:
        return _unknown(False, host=config.get("host"), error=None, method="not_started", library="python-kasa", latency=None)

    started = time.monotonic()
    try:
        result = asyncio.run(_discover_async(config))
    except Exception as exc:
        latency = round((time.monotonic() - started) * 1000, 1)
        return _unknown(True, host=config.get("host"), error=_safe_error(exc), method="python_kasa", library="python-kasa", latency=latency)

    latency = round((time.monotonic() - started) * 1000, 1)
    device = result.get("device")
    comparison = result.get("comparison") if isinstance(result.get("comparison"), Mapping) else {}
    if device is None:
        return _unknown(
            True,
            host=config.get("host"),
            error=result.get("error"),
            method=str(result.get("method") or "python_kasa"),
            library=str(result.get("library") or "python-kasa"),
            latency=latency,
            comparison=comparison,
        )

    host = _host_of(device, config.get("host"))
    identity = _device_identity(device, host)
    capability = _capability_snapshot(device, identity)
    debug = _safe_debug_snapshot(device, identity, capability)
    now = int(time.time())
    payload = {
        "configured": True,
        "online": True,
        "health": "healthy",
        **identity,
        "capabilities": capability["capabilities"],
        "supported_actions": capability["supported_actions"],
        "last_update": now,
        "diagnostics": {
            "source": "tapo_local",
            "latency_ms": latency,
            "last_error": None,
            "discovery_method": result.get("method"),
            "library": result.get("library"),
            "local_control_supported": capability["local_control_supported"],
            "exposes_ir": capability["exposes_ir"],
            **comparison,
        },
        "debug": debug,
    }
    try:
        asyncio.run(_close_device(device))
    except Exception:
        pass
    return payload


def local_tapo_ir_status(force: bool = False) -> Dict[str, Any]:
    now = int(time.time())
    with _lock:
        if not force and _cache.get("payload") is not None and now - int(_cache.get("ts") or 0) < _CACHE_SEC:
            return dict(_cache["payload"])
        payload = _snapshot_uncached()
        _cache["ts"] = now
        _cache["payload"] = dict(payload)
        return dict(payload)


def local_tapo_ir_provider() -> Iterable[UnifiedDevice]:
    payload = local_tapo_ir_status()
    diagnostics = payload.get("diagnostics") or {}
    yield UnifiedDevice(
        id="tapo_ir:condo",
        type="tapo_ir",
        name=payload.get("model") or "Tapo IR",
        room="condo",
        online=payload.get("online"),
        health=str(payload.get("health") or "unknown"),
        last_update_ts=payload.get("last_update"),
        latency_ms=diagnostics.get("latency_ms"),
        status={
            "configured": payload.get("configured"), "host": payload.get("host"),
            "model": payload.get("model"), "firmware": payload.get("firmware"),
            "hardware_version": payload.get("hardware_version"), "device_type": payload.get("device_type"),
        },
        diagnostics=diagnostics,
        capabilities=tuple(payload.get("capabilities") or ()),
        actions=(),
        metadata={"source": "tapo_local", "physical_site": "condo", "read_only": True},
    )


@app.get("/api/tapo-ir/local/status")
def get_local_tapo_ir_status() -> Dict[str, Any]:
    payload = local_tapo_ir_status()
    return {key: value for key, value in payload.items() if key != "debug"}


@app.get("/api/tapo-ir/debug")
def get_local_tapo_ir_debug() -> Dict[str, Any]:
    payload = local_tapo_ir_status(force=True)
    return {
        "configured": payload.get("configured"),
        "online": payload.get("online"),
        "health": payload.get("health"),
        "last_update": payload.get("last_update"),
        "diagnostics": payload.get("diagnostics") or {},
        "properties": payload.get("debug") or {},
    }


@app.get("/api/tapo-ir/commands")
def get_local_tapo_ir_commands() -> Dict[str, Any]:
    return {"commands": [], "source": "tapo_local"}


registry.register_provider("tapo_ir", local_tapo_ir_provider, replace=True)
app_module.state["device_registry_registered_modules"] = registry.provider_names()
