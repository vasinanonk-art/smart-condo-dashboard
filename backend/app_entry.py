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
