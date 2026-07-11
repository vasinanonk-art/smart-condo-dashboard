"""Smart Condo backend package bootstrap."""

# Start deferred runtime fixes without importing application modules here.
# runtime_stability waits until backend.app, dashboard_extensions and the
# existing arrival module are fully loaded before installing anything.
from backend import runtime_stability as _runtime_stability  # noqa: F401,E402
