"""Read-only capability investigation for the production Tapo H110."""
from __future__ import annotations

import asyncio
import importlib.metadata
import inspect
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from backend import app as app_module
from backend import tapo_ir_local_bridge as bridge

app = app_module.app
_SENSITIVE = ("username","password","token","secret","credential","cookie","session","auth","key","encrypt","decrypt","payload","raw")
_IR_TERMS = ("ir","infrared","remote","learn","transmit","command")
_COMMAND_METHODS = {"send_command","learn_command","delete_command","transmit","play","send_ir","learn_ir","delete_ir","send_ir_command","learn_ir_command"}


def _safe_name(name: Any) -> bool:
    text = str(name or "").lower()
    return bool(text) and not text.startswith("_") and not any(part in text for part in _SENSITIVE)


def _safe_signature(value: Any) -> Optional[str]:
    try:
        signature = inspect.signature(value)
    except (TypeError, ValueError):
        return None
    parts = []
    for parameter in signature.parameters.values():
        name = parameter.name
        sensitive = any(part in name.lower() for part in _SENSITIVE)
        shown = "<redacted>" if sensitive else name
        default = ""
        if parameter.default is not inspect.Parameter.empty:
            default = "=<redacted>" if sensitive else f"={parameter.default!r}"
        prefix = "*" if parameter.kind is inspect.Parameter.VAR_POSITIONAL else "**" if parameter.kind is inspect.Parameter.VAR_KEYWORD else ""
        parts.append(f"{prefix}{shown}{default}")
    return f"({', '.join(parts)})"


def _public_methods(value: Any, limit: int = 160) -> list[Dict[str, Any]]:
    result = []
    for name in sorted(set(dir(value))):
        if not _safe_name(name):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if callable(item):
            result.append({"name": name, "signature": _safe_signature(item), "ir_related": any(term in name.lower() for term in _IR_TERMS)})
        if len(result) >= limit:
            break
    return result


def _names(value: Any) -> list[str]:
    if isinstance(value, Mapping):
        return sorted(str(key) for key in value if _safe_name(key))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return sorted(str(item) for item in value if _safe_name(item))
    return []


def _components(device: Any) -> list[Dict[str, Any]]:
    result = []
    for attr in ("components", "component_nego", "component_list", "_components"):
        try:
            value = getattr(device, attr, None)
        except Exception:
            value = None
        if value is not None:
            result.append({"source": attr.lstrip("_"), "value": bridge._safe_value(value)})
    info = bridge._device_info(device)
    for key in ("component_nego", "component_list", "components"):
        if key in info:
            result.append({"source": f"sys_info.{key}", "value": bridge._safe_value(info.get(key))})
    return result


def _modules(device: Any) -> tuple[list[str], list[Dict[str, Any]]]:
    modules = bridge._read_attr(device, "modules")
    names = _names(modules)
    details = []
    if isinstance(modules, Mapping):
        for key, module in list(modules.items())[:80]:
            if _safe_name(key):
                details.append({"name": str(key), "class": type(module).__name__, "methods": _public_methods(module, 60)})
    return names, details


def _features(device: Any) -> tuple[list[str], list[Dict[str, Any]]]:
    features = bridge._read_attr(device, "features")
    names = _names(features)
    details = []
    if isinstance(features, Mapping):
        for key, feature in list(features.items())[:120]:
            if _safe_name(key):
                details.append({"name": str(key), "class": type(feature).__name__, "value": bridge._safe_value(getattr(feature, "value", None))})
    return names, details


def _library_version() -> Optional[str]:
    try:
        return importlib.metadata.version("python-kasa")
    except importlib.metadata.PackageNotFoundError:
        return None


def _support(methods: Iterable[str], modules: Iterable[str], features: Iterable[str], components: Any) -> Dict[str, Any]:
    method_set = {name.lower() for name in methods}
    confirmed = sorted(name for name in method_set if name in _COMMAND_METHODS)
    searchable = " ".join([*method_set, *[x.lower() for x in modules], *[x.lower() for x in features], str(components).lower()])
    visible = any(term in searchable for term in _IR_TERMS)
    if confirmed:
        reason = "python-kasa exposes callable local IR methods; parameters still require production verification"
    elif visible:
        reason = "IR metadata is visible, but no callable send/learn/delete IR method is exposed"
    else:
        reason = "No IR module, feature, component, child record, or callable local IR method is exposed for H110"
    return {"local_ir_supported": bool(confirmed), "confirmed_callable_methods": confirmed, "ir_metadata_visible": visible, "reason": reason}


def _compatibility(version: Optional[str]) -> Dict[str, Any]:
    return {
        "installed_python_kasa_version": version,
        "project_requirement": ">=0.10,<1.0",
        "minimum_python_kasa_version_with_confirmed_h110_ir_support": None,
        "upgrade_recommendation": "do_not_upgrade_blindly",
        "research_result": "No H110 IR implementation or fixture was found in the official python-kasa source/changelog reviewed for this story.",
        "risk": "A newer release may change authentication, discovery, modules, or device behavior without adding H110 IR support.",
    }


def _next_paths() -> list[Dict[str, str]]:
    return [
        {"path":"official_tapo_cloud_api","status":"not confirmed as a public consumer IR API","risk":"cloud dependency and account authentication changes"},
        {"path":"python_kasa_local_protocol_extension","status":"preferred if component negotiation reveals H110 IR RPC methods","risk":"requires a fixture and reviewed protocol implementation"},
        {"path":"dedicated_h110_bridge_library","status":"no production-approved library selected","risk":"maintenance and credential handling require audit"},
        {"path":"packet_capture_reverse_engineering","status":"last-resort investigation","risk":"high effort and sensitive session data must remain protected"},
    ]


def _unknown(error: str, latency: Optional[float] = None) -> Dict[str, Any]:
    return {"configured": bridge._configured(bridge._configuration()), "online": None, "health": "unknown", "protocol": None, "device_type": None, "supported_modules": [], "features": [], "components": [], "child_devices": [], "available_methods": [], "local_ir_support": {"local_ir_supported": False, "confirmed_callable_methods": [], "ir_metadata_visible": False, "reason": "Capability inspection could not complete."}, "compatibility": _compatibility(_library_version()), "next_viable_paths": _next_paths(), "diagnostics": {"source": "tapo_local", "latency_ms": latency, "last_error": error}}


def capability_debug() -> Dict[str, Any]:
    config = bridge._configuration()
    if not bridge._configured(config):
        return _unknown("not_configured")
    started = time.monotonic()
    try:
        result = asyncio.run(bridge._discover_async(config))
    except Exception as exc:
        return _unknown(type(exc).__name__, round((time.monotonic() - started) * 1000, 1))
    latency = round((time.monotonic() - started) * 1000, 1)
    device = result.get("device")
    if device is None:
        return _unknown(str(result.get("error") or "DeviceNotFound"), latency)
    try:
        identity = bridge._device_identity(device, bridge._host_of(device, config.get("host")))
        module_names, module_details = _modules(device)
        feature_names, feature_details = _features(device)
        components = _components(device)
        methods = _public_methods(device)
        support = _support([item["name"] for item in methods], module_names, feature_names, components)
        info = bridge._device_info(device)
        return {
            "configured": True, "online": True, "health": "healthy",
            "host": identity.get("host"), "model": identity.get("model"), "mac": identity.get("mac"),
            "firmware": identity.get("firmware"), "hardware_version": identity.get("hardware_version"),
            "protocol": info.get("type") or info.get("device_type") or identity.get("device_type"),
            "device_type": identity.get("device_type"), "supported_modules": module_names,
            "module_details": module_details, "features": feature_names, "feature_details": feature_details,
            "components": components, "child_devices": bridge._safe_value(bridge._read_attr(device, "child_devices", "children") or []),
            "available_methods": methods, "local_ir_support": support,
            "compatibility": _compatibility(_library_version()),
            "next_viable_paths": [] if support["local_ir_supported"] else _next_paths(),
            "diagnostics": {"source":"tapo_local","latency_ms":latency,"last_error":None,"discovery_method":result.get("method"),"library":result.get("library"),"read_only":True,"commands_invoked":False},
        }
    finally:
        try:
            asyncio.run(bridge._close_device(device))
        except Exception:
            pass


@app.get("/api/tapo-ir/capabilities/debug")
def get_tapo_ir_capability_debug() -> Dict[str, Any]:
    return capability_debug()
