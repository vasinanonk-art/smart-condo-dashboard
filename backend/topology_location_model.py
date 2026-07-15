"""Physical-site metadata and data-dependency corrections for topology."""
from typing import Any, Dict

from backend import app as app_module
from backend import electricity_provider, topology_runtime

# Electricity is physically polled by the condo TinkerBoard local bridge.
# Tuya and PM2.5 remain condo devices even when Home Assistant is their data source.
topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["electricity"] = "Digital Meter"

# STORY 3.2 moves Tapo IR control to the local TinkerBoard data path. The node is
# unknown when unconfigured and only offline after a configured device is confirmed
# unreachable by the local discovery provider.
topology_runtime.DEPENDENCIES["tapo_ir"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["tapo_ir"] = "Tapo IR"
if "tapo_ir" not in topology_runtime.NODE_ORDER:
    topology_runtime.NODE_ORDER.append("tapo_ir")


def _local_tapo_node() -> Dict[str, Any]:
    try:
        from backend import tapo_ir_local_bridge

        status = tapo_ir_local_bridge.local_tapo_ir_status()
        diagnostics = status.get("diagnostics") or {}
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
            "capabilities": status.get("capabilities") or [],
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
            status = electricity_provider.electricity_status()
            diagnostics = status.get("diagnostics") or {}
            nodes = payload.get("nodes", [])
            for node in nodes:
                if node.get("id") == "electricity":
                    node["physical_site"] = "condo"
                    node["data_source"] = diagnostics.get("source") or "tuya_local"
                    node["voltage"] = status.get("voltage")
                    node["power"] = status.get("power")
                    node["runtime_ip"] = diagnostics.get("runtime_ip")
                    node["diagnostics"] = {
                        **(node.get("diagnostics") or {}),
                        "voltage": status.get("voltage"),
                        "current_power": status.get("power"),
                        "runtime_ip": diagnostics.get("runtime_ip"),
                        "source": diagnostics.get("source"),
                    }
                elif node.get("id") == "tapo_ir":
                    node["physical_site"] = "condo"
                    node["data_source"] = "tapo_local"
            return payload

        route.endpoint = topology_with_device_detail
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = topology_with_device_detail
        app_module._device_topology_detail_installed = True
        return


_install_base_node()
_install_topology_details()
