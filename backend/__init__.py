"""Smart Condo backend package bootstrap."""

# Start deferred runtime fixes without importing application modules here.
# runtime_services waits until backend.app, dashboard_extensions and the
# existing arrival module are fully loaded before installing anything.
from backend import runtime_services as _runtime_services  # noqa: F401,E402
