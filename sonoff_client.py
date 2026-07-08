from backend.sonoff_client import *

# Route shim for legacy backend.app import style.
# backend.app imports this top-level module before registering /api/sonoff.
# Patch only /api/sonoff handlers so the API exposes safe diagnostics and channel control.
try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.routing import APIRouter
except Exception:  # pragma: no cover
    FastAPI = None
    HTTPException = None
    Request = None
    APIRouter = None


def _sonoff_payload(data):
    return {
        "ok": True,
        "config_loaded": bool(data.get("config_loaded")),
        "config_path": data.get("config_path"),
        "auth_status": data.get("auth_status"),
        "last_error": data.get("last_error"),
        "devices": data.get("devices", []),
    }


def _sonoff_get_handler():
    return _sonoff_payload(devices())


async def _sonoff_post_handler(request: Request):
    body = await request.json()
    result = set_state(body.get("deviceid", ""), body.get("action", ""), int(body.get("channel") or 1))
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff command failed"))
    data = {
        "config_loaded": True,
        "config_path": config_payload().get("path"),
        "auth_status": result.get("auth_status"),
        "last_error": result.get("last_error"),
        "devices": result.get("devices", []),
    }
    payload = _sonoff_payload(data)
    payload.update({"deviceid": result.get("deviceid"), "channel": result.get("channel", 1), "action": result.get("action")})
    return payload


if APIRouter is not None and not getattr(APIRouter, "_sonoff_route_patch", False):
    _orig_router_add_api_route = APIRouter.add_api_route

    def _patched_router_add_api_route(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or [])
        if path == "/api/sonoff":
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        return _orig_router_add_api_route(self, path, endpoint, **kwargs)

    APIRouter.add_api_route = _patched_router_add_api_route
    APIRouter._sonoff_route_patch = True

if FastAPI is not None and not getattr(FastAPI, "_sonoff_route_patch", False):
    _orig_fastapi_add_api_route = FastAPI.add_api_route

    def _patched_fastapi_add_api_route(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or [])
        if path == "/api/sonoff":
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        return _orig_fastapi_add_api_route(self, path, endpoint, **kwargs)

    FastAPI.add_api_route = _patched_fastapi_add_api_route
    FastAPI._sonoff_route_patch = True
