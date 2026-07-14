"""Physical-site metadata and data-dependency corrections for topology."""
from typing import Any, Dict

from backend import app as app_module
from backend import electricity_provider, topology_runtime

# Electricity is physically polled by the condo TinkerBoard local bridge.
# Tuya and PM2.5 remain condo devices even when Home Assistant is their data source.
topology_runtime.DEPENDENCIES["electricity"] = ["tinkerboard"]
topology_runtime.NODE_LABELS["electricity"] = "Digital Meter"


def _install_electricity_topology_detail() -> None:
    if getattr(app_module, "_electricity_topology_detail_installed", False):
        return
    for route in app_module.app.routes:
        if getattr(route, "path", None) != "/api/topology":
            continue
        original = route.endpoint

        def topology_with_meter_detail() -> Dict[str, Any]:
            payload = original()
            status = electricity_provider.electricity_status()
            diagnostics = status.get("diagnostics") or {}
            for node in payload.get("nodes", []):
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
            return payload

        route.endpoint = topology_with_meter_detail
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = topology_with_meter_detail
        app_module._electricity_topology_detail_installed = True
        return


_install_electricity_topology_detail()
