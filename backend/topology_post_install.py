"""Final production overrides that depend on installed registry providers."""
from __future__ import annotations

from typing import Any, Dict, Mapping

from backend import app as app_module
from backend import topology_stabilization


def sonoff_status_from_registry_state() -> Dict[str, Any]:
    devices = app_module.state.get("sonoff_devices")
    devices = devices if isinstance(devices, list) else []
    configured = bool(app_module.state.get("ewelink_config_loaded"))
    authenticated = str(app_module.state.get("sonoff_auth_status") or "") == "authenticated"
    online_count = sum(1 for item in devices if isinstance(item, Mapping) and item.get("online"))
    last_error = app_module.state.get("sonoff_last_error")
    if authenticated and devices:
        health = "healthy"
    elif configured and not authenticated:
        health = "warning"
    elif not configured:
        health = "unknown"
    elif last_error:
        health = "offline"
    else:
        health = "warning"
    return {
        "health": health,
        "online": True if health == "healthy" else (False if health == "offline" else None),
        "last_update_ts": app_module.state.get("sonoff_last_sync_ts"),
        "diagnostics": {
            "source": "device_registry_state",
            "configured": configured,
            "authenticated": authenticated,
            "device_count": len(devices),
            "online_count": online_count,
            "last_error": last_error,
        },
    }


topology_stabilization._sonoff_status = sonoff_status_from_registry_state
