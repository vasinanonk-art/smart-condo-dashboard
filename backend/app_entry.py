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

# Persist successful results from that existing worker and expose history/billing.
from backend import electricity_history as _electricity_history  # noqa: F401,E402
from backend import electricity_history_hook as _electricity_history_hook  # noqa: F401,E402
from backend import electricity_status_history as _electricity_status_history  # noqa: F401,E402
from backend import electricity_summary_projection as _electricity_summary_projection  # noqa: F401,E402
from backend import electricity_history_coverage as _electricity_history_coverage  # noqa: F401,E402
from backend import electricity_billing_cycle as _electricity_billing_cycle  # noqa: F401,E402

# Load persistent non-secret settings after electricity modules so saved values can
# replace environment-only tariff and billing configuration without a restart.
from backend import dashboard_settings as _dashboard_settings  # noqa: F401,E402
from backend import dashboard_settings_migration as _dashboard_settings_migration  # noqa: F401,E402
from backend import dashboard_tariff_sync as _dashboard_tariff_sync  # noqa: F401,E402
from backend import dashboard_settings_runtime as _dashboard_settings_runtime  # noqa: F401,E402
from backend import dashboard_settings_hotfix09 as _dashboard_settings_hotfix09  # noqa: F401,E402
from backend import dashboard_polish_hotfix10 as _dashboard_polish_hotfix10  # noqa: F401,E402

# EPIC 07 reuses the existing daily maintenance thread for safe tariff checks.
from backend import automatic_tariff_sync as _automatic_tariff_sync  # noqa: F401,E402
from backend import automatic_tariff_sync_runtime as _automatic_tariff_sync_runtime  # noqa: F401,E402

# EPIC 07.1 installs the real allow-listed official MEA provider, reviewed future
# tariff scheduling, source archive APIs, migration, and billing segmentation.
from backend import mea_tariff_provider as _mea_tariff_provider  # noqa: F401,E402
from backend import mea_tariff_migration as _mea_tariff_migration  # noqa: F401,E402
from backend import mea_tariff_runtime as _mea_tariff_runtime  # noqa: F401,E402
from backend import tariff_segment_billing as _tariff_segment_billing  # noqa: F401,E402

# Register the declarative automation core and its single non-executing trigger
# worker. HOTFIX 13 replaces the fixed one-second loop with bounded scheduling.
from backend import automation_core as _automation_core  # noqa: F401,E402
from backend import automation_trigger_engine as _automation_trigger_engine  # noqa: F401,E402
from backend import automation_trigger_guard as _automation_trigger_guard  # noqa: F401,E402
from backend import automation_trigger_hotfix13 as _automation_trigger_hotfix13  # noqa: F401,E402

# Subscribe the existing client to the retained electricity state for restart
# fallback. Current in-process bridge state remains authoritative.
from backend import runtime_electricity_mqtt as _runtime_electricity_mqtt  # noqa: F401,E402

# Discover Tapo IR entities lazily from Home Assistant for STORY 3.1 diagnostics.
from backend import tapo_ir_provider as _tapo_ir_provider  # noqa: F401,E402

# Register the local condo Tapo bridge last so its registry provider replaces the
# HA-only provider. It is on-demand, read-only, and creates no worker thread.
from backend import tapo_ir_local_bridge as _tapo_ir_local_bridge  # noqa: F401,E402

# Expose read-only H110 modules, features, components, children and callable
# signatures. This performs no IR command, pairing, learning, or deletion.
from backend import tapo_ir_capability_debug as _tapo_ir_capability_debug  # noqa: F401,E402

# Correct physical-site topology relationships after all providers are registered.
from backend import topology_location_model as _topology_location_model  # noqa: F401,E402

# Replace the strict topology endpoint with a provider-isolated response that keeps
# valid nodes available when one optional provider or enrichment fails.
from backend import topology_hotfix as _topology_hotfix  # noqa: F401,E402

# Install authentication last so it protects every dashboard and extension route.
from backend import dashboard_auth as _dashboard_auth  # noqa: F401,E402

# Serve HTML with one stable build version per deployment so updated JS/CSS assets
# are requested automatically without disabling API caching.
from backend import frontend_asset_version as _frontend_asset_version  # noqa: F401,E402
