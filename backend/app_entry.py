from backend.app_runtime import app

# Register dashboard-only extension routes after the stable runtime app is loaded.
from backend import dashboard_extensions as _dashboard_extensions  # noqa: F401,E402


def _safe_dashboard_error(exc):
    return type(exc).__name__ if exc is not None else "operation failed"


# Keep extension errors safe: no tokens, URLs, credentials, or filesystem details.
_dashboard_extensions._safe_error = _safe_dashboard_error

# Install runtime-only reliability fixes after the app and extension routes exist.
from backend import runtime_fixes as _runtime_fixes  # noqa: F401,E402
from backend import runtime_tuya_subprocess as _runtime_tuya_subprocess  # noqa: F401,E402

# Use confirmed Home Assistant entities as the authoritative lighting transport
# and state source. This loads last so it owns only the two existing zone routes.
from backend import runtime_ha_lighting as _runtime_ha_lighting  # noqa: F401,E402

# Keep recent slider values stable until Home Assistant confirms them and force
# brightness/temperature controls into white mode rather than retaining HS color.
from backend import runtime_ha_lighting_stable as _runtime_ha_lighting_stable  # noqa: F401,E402

# Install the final MQTT callback owner for LG TV state and heartbeat.
from backend import runtime_lg_tv_mqtt as _runtime_lg_tv_mqtt  # noqa: F401,E402

# Register the read-only electricity provider and status endpoint.
from backend import electricity_provider as _electricity_provider  # noqa: F401,E402

# Start the single PJ-1103 local polling bridge using the existing MQTT client.
from backend import pj1103_electricity_bridge as _pj1103_electricity_bridge  # noqa: F401,E402

# Subscribe the existing client to the retained electricity state for restart
# fallback. Current in-process bridge state remains authoritative.
from backend import runtime_electricity_mqtt as _runtime_electricity_mqtt  # noqa: F401,E402

# Discover Tapo IR entities lazily from Home Assistant for STORY 3.1 diagnostics.
from backend import tapo_ir_provider as _tapo_ir_provider  # noqa: F401,E402

# Register the local condo Tapo bridge last so its registry provider replaces the
# HA-only provider. It is on-demand, read-only, and creates no worker thread.
from backend import tapo_ir_local_bridge as _tapo_ir_local_bridge  # noqa: F401,E402

# Correct physical-site topology relationships after all providers are registered.
from backend import topology_location_model as _topology_location_model  # noqa: F401,E402
