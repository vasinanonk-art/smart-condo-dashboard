from backend.app_runtime import app

# Register optional dashboard-only APIs after the stable runtime application
# has completed its existing Sonoff, Presence, history, PM2.5 and static setup.
import backend.dashboard_features  # noqa: F401,E402
