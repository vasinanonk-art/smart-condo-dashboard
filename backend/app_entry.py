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

# Use confirmed Home Assistant entities as the authoritative lighting transport.
from backend import runtime_ha_lighting as _runtime_ha_lighting  # noqa: F401,E402
from backend import runtime_ha_lighting_stable as _runtime_ha_lighting_stable  # noqa: F401,E402

# Install authoritative MQTT ingestion using the existing client.
from backend import runtime_lg_tv_mqtt as _runtime_lg_tv_mqtt  # noqa: F401,E402

# Register the read-only electricity provider and start the single local bridge.
from backend import electricity_provider as _electricity_provider  # noqa: F401,E402
from backend import pj1103_electricity_bridge as _pj1103_electricity_bridge  # noqa: F401,E402
from backend import runtime_electricity_mqtt as _runtime_electricity_mqtt  # noqa: F401,E402

# Apply physical-site topology metadata after the existing topology engine loads.
from backend import topology_location_model as _topology_location_model  # noqa: F401,E402
