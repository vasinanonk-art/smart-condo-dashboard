"""Stabilize the Sonoff registry provider without changing command/auth paths."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from backend import app as app_module
from backend.device_framework import UnifiedDevice
from backend.device_registry import registry


def _health(configured: bool, authenticated: bool, failed: bool) -> str:
    if failed:
        return "offline"
    if authenticated:
        return "healthy"
    if configured:
        return "warning"
    return "unknown"


def sonoff_provider() -> Iterable[UnifiedDevice]:
    import sonoff_client

    failed = False
    try:
        data = sonoff_client.devices()
    except Exception:
        data = {}
        failed = True
    data = data if isinstance(data, Mapping) else {}
    devices = data.get("devices") if isinstance(data.get("devices"), list) else []
    configured = bool(data.get("config_loaded"))
    authenticated = str(data.get("auth_status") or "") == "authenticated"
    app_module.state["ewelink_config_loaded"] = configured
    app_module.state["sonoff_devices"] = devices
    result = []
    for raw in devices:
        if not isinstance(raw, Mapping):
            continue
        device_id = str(raw.get("deviceid") or raw.get("id") or "").strip()
        if not device_id:
            continue
        online_value = raw.get("online")
        online = bool(online_value) if online_value is not None else None
        result.append(
            UnifiedDevice(
                id=device_id,
                type="sonoff",
                name=str(raw.get("name") or raw.get("deviceName") or device_id),
                room=None,
                online=online,
                health="healthy" if online else ("warning" if authenticated else _health(configured, authenticated, failed)),
                last_update_ts=raw.get("last_update_ts") or raw.get("updated_ts") or app_module.state.get("sonoff_last_sync_ts"),
                status={"state": raw.get("state"), "switch": raw.get("switch"), "channel_states": raw.get("channel_states")},
                diagnostics={"source": "sonoff_client", "configured": configured, "authenticated": authenticated, "last_error": data.get("last_error")},
                capabilities=("power", "automation"),
                actions=("on", "off"),
                metadata={"brand": "Sonoff"},
            )
        )
    return result


registry.register_provider("sonoff", sonoff_provider, replace=True)
app_module.state["device_registry_registered_modules"] = registry.provider_names()
