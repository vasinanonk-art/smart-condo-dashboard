"""Physical-site metadata and data-dependency corrections for topology."""
from typing import Any, Dict

from backend import app as app_module
from backend import electricity_provider, topology_runtime

# Electricity is physically polled by the condo TinkerBoard local bridge.
topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["electricity"] = "Digital Meter"

# Tapo IR control is local to the condo TinkerBoard.
topology_runtime.DEPENDENCIES["tapo_ir"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["tapo_ir"] = "Tapo IR"
if "tapo_ir" not in topology_runtime.NODE_ORDER:
    topology_runtime.NODE_ORDER.append("tapo_ir")


def _safe_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _local_tapo_node() -> Dict[str, Any]:
    try:
        from backend import tapo_ir_local_bridge

        status = _safe_mapping(tapo_ir_local_bridge.local_tapo_ir_status())
        diagnostics = _safe_mapping(status.get("diagnostics"))
        return {
            "health": status.get("health") or "unknown",
            "online": status.get("online"),
            "last_update_ts": status.get("last_update"),
            "latency_ms": diagnostics.get("latency_ms"),
            "physical_site": "condo",
            "data_source": "tapo_local",
            "host": status.get("host"),
            "model": status.get("model"),
            "firmware": status.get("firmware"),
            "capabilities": status.get("capabilities") if isinstance(status.get("capabilities"), list) else [],
            "diagnostics": diagnostics,
        }
    except Exception as exc:
        return {
            "health": "unknown",
            "online": None,
            "physical_site": "condo",
            "data_source": "tapo_local",
            "capabilities": [],
            "diagnostics": {
                "source": "tapo_local",
                "last_error": type(exc).__name__,
                "local_control_supported": False,
            },
        }


def _install_base_node() -> None:
    if getattr(app_module, "_tapo_ir_topology_base_installed", False):
        return
    original = topology_runtime._base_nodes

    def base_nodes_with_local_tapo(now: int) -> Dict[str, Dict[str, Any]]:
        nodes = original(now)
        if not isinstance(nodes, dict):
            nodes = {}
        nodes["tapo_ir"] = _local_tapo_node()
        return nodes

    topology_runtime._base_nodes = base_nodes_with_local_tapo
    app_module._tapo_ir_topology_base_installed = True


def _install_topology_details() -> None:
    if getattr(app_module, "_device_topology_detail_installed", False):
        return
    for route in app_module.app.routes:
        if getattr(route, "path", None) != "/api/topology":
            continue
        original = route.endpoint

        def topology_with_device_detail() -> Dict[str, Any]:
            payload = original()
            if not isinstance(payload, dict):
                payload = {"nodes": [], "events": [], "root_causes": [], "diagnostics": {"enrichment_error": "InvalidTopologyPayload"}}
            nodes = payload.get("nodes")
            if not isinstance(nodes, list):
                nodes = []
                payload["nodes"] = nodes

            try:
                status = _safe_mapping(electricity_provider.electricity_status())
                diagnostics = _safe_mapping(status.get("diagnostics"))
            except Exception as exc:
                status = {}
                diagnostics = {"source": "unknown", "last_error": type(exc).__name__}

            for raw_node in nodes:
                if not isinstance(raw_node, dict):
                    continue
                node_id = str(raw_node.get("id") or "")
                if node_id == "electricity":
                    existing_diagnostics = _safe_mapping(raw_node.get("diagnostics"))
                    raw_node["physical_site"] = "condo"
                    raw_node["data_source"] = diagnostics.get("source") or "tuya_local"
                    raw_node["voltage"] = status.get("voltage")
                    raw_node["power"] = status.get("power")
                    raw_node["runtime_ip"] = diagnostics.get("runtime_ip") or diagnostics.get("configured_ip")
                    raw_node["capabilities"] = raw_node.get("capabilities") if isinstance(raw_node.get("capabilities"), list) else []
                    raw_node["dependencies"] = raw_node.get("dependencies") if isinstance(raw_node.get("dependencies"), list) else ["tinkerboard"]
                    raw_node["dependents"] = raw_node.get("dependents") if isinstance(raw_node.get("dependents"), list) else []
                    raw_node["diagnostics"] = {
                        **existing_diagnostics,
                        "voltage": status.get("voltage"),
                        "current_power": status.get("power"),
                        "runtime_ip": diagnostics.get("runtime_ip") or diagnostics.get("configured_ip"),
                        "source": diagnostics.get("source"),
                    }
                elif node_id == "tapo_ir":
                    raw_node["physical_site"] = "condo"
                    raw_node["data_source"] = "tapo_local"
                    raw_node["dependencies"] = raw_node.get("dependencies") if isinstance(raw_node.get("dependencies"), list) else ["tinkerboard"]
                    raw_node["dependents"] = raw_node.get("dependents") if isinstance(raw_node.get("dependents"), list) else []
                    raw_node["capabilities"] = raw_node.get("capabilities") if isinstance(raw_node.get("capabilities"), list) else []
                    raw_node["diagnostics"] = _safe_mapping(raw_node.get("diagnostics"))
            return payload

        route.endpoint = topology_with_device_detail
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = topology_with_device_detail
        app_module._device_topology_detail_installed = True
        return


_install_base_node()
_install_topology_details()
