"""Physical-site metadata and data-dependency corrections for topology."""
from typing import Any, Dict

from backend import app as app_module
from backend import electricity_provider, topology_runtime

# Electricity is physically polled by the condo TinkerBoard local bridge.
# Tuya and PM2.5 remain condo devices even when Home Assistant is their data source.
topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["electricity"] = "Digital Meter"

# Tapo IR is physically at the condo while Home Assistant is the authoritative
# discovery/data path. It remains a separate node from the existing camera type.
topology_runtime.DEPENDENCIES["tapo_ir"] = ["home_assistant"]
topology_runtime.NODE_LABELS["tapo_ir"] = "Tapo IR"
if "tapo_ir" not in topology_runtime.NODE_ORDER:
    topology_runtime.NODE_ORDER.append("tapo_ir")


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
                if node.get("id") != "electricity":
                    continue
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
                break

            try:
                from backend import tapo_ir_provider

                tapo = tapo_ir_provider.tapo_ir_status()
                tapo_diag = tapo.get("diagnostics") or {}
            except Exception as exc:
                tapo = {
                    "configured": False,
                    "online": None,
                    "health": "unknown",
                    "last_update": None,
                    "capabilities": [],
                }
                tapo_diag = {
                    "source": "home_assistant",
                    "configured": False,
                    "last_error": type(exc).__name__,
                }

            tapo_node = {
                "id": "tapo_ir",
                "name": "Tapo IR",
                "online": tapo.get("online"),
                "health": tapo.get("health") or "unknown",
                "last_update_ts": tapo.get("last_update"),
                "latency_ms": tapo_diag.get("latency_ms"),
                "dependencies": ["home_assistant"],
                "dependents": [],
                "capabilities": tapo.get("capabilities") or [],
                "physical_site": "condo",
                "data_source": "home_assistant",
                "diagnostics": tapo_diag,
            }
            existing = next((index for index, node in enumerate(nodes) if node.get("id") == "tapo_ir"), None)
            if existing is None:
                nodes.append(tapo_node)
            else:
                nodes[existing] = tapo_node
            for node in nodes:
                if node.get("id") == "home_assistant":
                    dependents = list(node.get("dependents") or [])
                    if "tapo_ir" not in dependents:
                        dependents.append("tapo_ir")
                    node["dependents"] = dependents
                    break
            return payload

        route.endpoint = topology_with_device_detail
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = topology_with_device_detail
        app_module._device_topology_detail_installed = True
        return


_install_topology_details()
