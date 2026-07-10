from backend.app_runtime import app

# Register dashboard-only extension routes after the stable runtime app is loaded.
from backend import dashboard_extensions_v2 as _dashboard_extensions  # noqa: F401,E402
