"""Runtime route ownership for EPIC 09 LG status and pairing presentation."""
from __future__ import annotations

from typing import Any, Dict

from backend import app as app_module
from backend import lg_tv_pairing as pairing
from backend import lg_tv_status as status

app = app_module.app


def pairing_status_epic09() -> Dict[str, Any]:
    live = status._public_status()
    key, source = pairing._current_key()
    connected = live.get("connection_state") == "connected" and bool(live.get("paired"))
    return {
        "tv_ip": status.TV_IP,
        "service_active": status._service_active(),
        "paired": connected,
        "connection_status": live.get("connection_state") or ("unpaired" if key else "key_missing"),
        "pairing_required": bool(live.get("pairing_required") or not key),
        "last_pair_attempt": pairing._RUNTIME.get("last_pair_attempt"),
        "last_pair_success": pairing._RUNTIME.get("last_pair_success"),
        "last_connection_success": pairing._RUNTIME.get("last_connection_success") or live.get("last_success_ts"),
        "last_error": live.get("last_error") or pairing._RUNTIME.get("last_error"),
        "key_source": source,
    }


for route in app.routes:
    if getattr(route, "path", None) == "/api/lg-tv/pairing/status" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = pairing_status_epic09
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = pairing_status_epic09
