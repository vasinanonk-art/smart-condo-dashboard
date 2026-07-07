from backend.sonoff_client import *

# Route shim for legacy backend.app import style.
# backend.app imports this top-level module before registering /api/sonoff.
# Patch only /api/sonoff handlers so the API can expose safe diagnostics and channel control
# without touching Tuya, MQTT, camera, presence, or light routes.
try:
    from fastapi import FastAPI, HTTPException, Request
except Exception:  # pragma: no cover
    FastAPI = None
    HTTPException = None
    Request = None

if FastAPI is not None and not getattr(FastAPI, "_sonoff_route_patch", False):
    _orig_add_api_route = FastAPI.add_api_route

    def _sonoff_get_handler():
        data = devices()
        return {
            "ok": True,
            "config_loaded": bool(data.get("config_loaded")),
            "config_path": data.get("config_path"),
            "auth_status": data.get("auth_status"),
            "last_error": data.get("last_error"),
            "devices": data.get("devices", []),
        }

    async def _sonoff_post_handler(request: Request):
        body = await request.json()
        result = set_state(body.get("deviceid", ""), body.get("action", ""), int(body.get("channel") or 1))
        if not result.get("ok"):
            raise HTTPException(status_code=502, detail=result.get("error", "sonoff command failed"))
        return {
            "ok": True,
            "config_loaded": True,
            "config_path": config_payload().get("path"),
            "auth_status": result.get("auth_status"),
            "last_error": result.get("last_error"),
            "deviceid": result.get("deviceid"),
            "channel": result.get("channel", 1),
            "action": result.get("action"),
            "devices": result.get("devices", []),
        }

    def _patched_add_api_route(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or [])
        if path == "/api/sonoff":
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        return _orig_add_api_route(self, path, endpoint, **kwargs)

    FastAPI.add_api_route = _patched_add_api_route
    FastAPI._sonoff_route_patch = True
