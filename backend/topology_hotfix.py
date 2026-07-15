"""HOTFIX PACK 04: resilient topology response with per-provider isolation."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping

from backend import app as app_module
from backend import topology_runtime
from backend.device_registry import registry

app = app_module.app


def _safe_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _safe_error(provider: str, exc: BaseException) -> Dict[str, str]:
    return {"provider": provider, "error": type(exc).__name__}


def _unknown_node(node_id: str, error: str | None = None) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {"source": "topology_fallback"}
    if error:
        diagnostics["last_error"] = error
    return {
        "health": "unknown",
        "online": None,
        "last_update_ts": None,
        "latency_ms": None,
        "diagnostics": diagnostics,
    }


def _normalize_node(node_id: str, raw: Any) -> Dict[str, Any]:
    node = _safe_dict(raw)
    diagnostics = _safe_dict(node.get("diagnostics"))
    metadata = _safe_dict(node.get("metadata"))
    health = str(node.get("health") or "unknown")
    if health not in {"healthy", "warning", "offline", "unknown"}:
        health = "unknown"
    online = node.get("online") if isinstance(node.get("online"), bool) else None
    return {
        **node,
        "health": health,
        "online": online,
        "diagnostics": diagnostics,
        "metadata": metadata,
        "dependencies": [str(item) for item in _safe_list(node.get("dependencies")) if item is not None],
        "dependents": [str(item) for item in _safe_list(node.get("dependents")) if item is not None],
        "capabilities": [str(item) for item in _safe_list(node.get("capabilities")) if item is not None],
        "id": node_id,
        "name": str(node.get("name") or topology_runtime.NODE_LABELS.get(node_id) or node_id),
    }


def _dedupe_order() -> List[str]:
    seen = set()
    ordered: List[str] = []
    for raw in topology_runtime.NODE_ORDER:
        node_id = str(raw or "").strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        ordered.append(node_id)
    return ordered


def _safe_dependents(order: List[str]) -> Dict[str, List[str]]:
    result = {node_id: [] for node_id in order}
    known = set(order)
    for raw_node, raw_dependencies in dict(topology_runtime.DEPENDENCIES).items():
        node_id = str(raw_node or "").strip()
        if not node_id or node_id not in known:
            continue
        for raw_dependency in _safe_list(raw_dependencies):
            dependency = str(raw_dependency or "").strip()
            if dependency and dependency in known and node_id not in result.setdefault(dependency, []):
                result[dependency].append(node_id)
    return result


def _provider_errors() -> List[Dict[str, str]]:
    errors = registry.provider_errors()
    return [
        {"provider": str(provider), "error": str(error or "ProviderError")}
        for provider, error in sorted(errors.items())
    ]


def _enrich_electricity(nodes: Dict[str, Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    try:
        from backend import electricity_provider

        status = _safe_dict(electricity_provider.electricity_status())
        diagnostics = _safe_dict(status.get("diagnostics"))
        node = nodes.setdefault("electricity", _unknown_node("electricity"))
        node.update({
            "physical_site": "condo",
            "data_source": diagnostics.get("source") or "tuya_local",
            "voltage": status.get("voltage"),
            "power": status.get("power"),
            "runtime_ip": diagnostics.get("runtime_ip") or diagnostics.get("configured_ip"),
        })
        node["diagnostics"] = {
            **_safe_dict(node.get("diagnostics")),
            "source": diagnostics.get("source"),
            "voltage": status.get("voltage"),
            "current_power": status.get("power"),
            "runtime_ip": diagnostics.get("runtime_ip") or diagnostics.get("configured_ip"),
        }
    except Exception as exc:
        errors.append(_safe_error("electricity", exc))
        node = nodes.setdefault("electricity", _unknown_node("electricity"))
        node.update(_unknown_node("electricity", type(exc).__name__))
        node["physical_site"] = "condo"
        node["data_source"] = "unknown"


def _enrich_tapo(nodes: Dict[str, Dict[str, Any]], errors: List[Dict[str, str]]) -> None:
    node = nodes.setdefault("tapo_ir", _unknown_node("tapo_ir"))
    try:
        from backend import tapo_ir_local_bridge

        status = _safe_dict(tapo_ir_local_bridge.local_tapo_ir_status())
        diagnostics = _safe_dict(status.get("diagnostics"))
        node.update({
            "health": str(status.get("health") or "unknown"),
            "online": status.get("online") if isinstance(status.get("online"), bool) else None,
            "last_update_ts": status.get("last_update"),
            "latency_ms": diagnostics.get("latency_ms"),
            "physical_site": "condo",
            "data_source": "tapo_local",
            "host": status.get("host"),
            "model": status.get("model"),
            "firmware": status.get("firmware"),
            "capabilities": [str(item) for item in _safe_list(status.get("capabilities"))],
            "diagnostics": diagnostics,
        })
    except Exception as exc:
        errors.append(_safe_error("tapo_ir", exc))
        node.update(_unknown_node("tapo_ir", type(exc).__name__))
        node["physical_site"] = "condo"
        node["data_source"] = "tapo_local"


def topology_response() -> Dict[str, Any]:
    now = int(time.time())
    errors: List[Dict[str, str]] = []

    # Provider exceptions are already isolated by DeviceRegistry.snapshot().
    # The base graph remains the only fatal construction boundary.
    try:
        raw_nodes = topology_runtime._base_nodes(now)
    except Exception:
        raise

    nodes = _safe_dict(raw_nodes)
    errors.extend(_provider_errors())
    order = _dedupe_order()

    for node_id in order:
        if node_id not in nodes or not isinstance(nodes.get(node_id), Mapping):
            nodes[node_id] = _unknown_node(node_id, "MissingOrMalformedNode")
            errors.append({"provider": node_id, "error": "MissingOrMalformedNode"})
        else:
            nodes[node_id] = _normalize_node(node_id, nodes[node_id])

    _enrich_electricity(nodes, errors)
    _enrich_tapo(nodes, errors)

    # Re-normalize enriched nodes and keep physical placement/dependencies intact.
    for node_id in order:
        nodes[node_id] = _normalize_node(node_id, nodes.get(node_id))
    nodes["pm25"]["physical_site"] = "condo"
    nodes["electricity"]["physical_site"] = "condo"
    nodes["tapo_ir"]["physical_site"] = "condo"
    nodes["home_assistant"]["physical_site"] = "home"
    topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
    topology_runtime.DEPENDENCIES["tapo_ir"] = ["tinkerboard"]

    dependents = _safe_dependents(order)
    for node_id in order:
        node = nodes[node_id]
        node["dependencies"] = [
            str(dep) for dep in _safe_list(topology_runtime.DEPENDENCIES.get(node_id, []))
            if str(dep) in nodes
        ]
        node["dependents"] = dependents.get(node_id, [])
        if not node["capabilities"]:
            node["capabilities"] = ["status", "diagnostics"]

    try:
        roots = topology_runtime._apply_dependency_health(nodes)
    except Exception as exc:
        errors.append(_safe_error("dependency_health", exc))
        roots = []

    try:
        topology_runtime._capture_events(nodes)
    except Exception as exc:
        errors.append(_safe_error("events", exc))

    with topology_runtime._lock:
        events = list(topology_runtime._events)

    try:
        overall_health = topology_runtime._overall_health(nodes)
    except Exception as exc:
        errors.append(_safe_error("overall_health", exc))
        overall_health = 0

    try:
        tv = topology_runtime._tv_payload(now)
    except Exception as exc:
        errors.append(_safe_error("lg_tv", exc))
        tv = {"online": None, "health": "unknown", "source": "mqtt_state"}

    # De-duplicate safe diagnostics only; no payloads or secrets are included.
    unique_errors = []
    seen = set()
    for item in errors:
        key = (item.get("provider"), item.get("error"))
        if key in seen:
            continue
        seen.add(key)
        unique_errors.append(item)

    return {
        "ok": True,
        "ts": now,
        "overall_health": overall_health,
        "nodes": [nodes[node_id] for node_id in order],
        "root_causes": roots if isinstance(roots, list) else [],
        "events": events,
        "tv": tv,
        "topology_provider_errors": unique_errors,
    }


def _install() -> None:
    if getattr(app_module, "_topology_hotfix_04_installed", False):
        return
    for route in app.routes:
        if getattr(route, "path", None) != "/api/topology":
            continue
        route.endpoint = topology_response
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = topology_response
        app_module._topology_hotfix_04_installed = True
        return


_install()
