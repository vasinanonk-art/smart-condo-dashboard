from backend.sonoff_client import *

# Route shim for legacy backend.app import style.
# backend.app imports this top-level module before registering /api/sonoff.
# Patch only Sonoff handlers so the API exposes safe diagnostics and channel control.
try:
    import os
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import HTMLResponse
    from fastapi.routing import APIRouter
except Exception:  # pragma: no cover
    os = None
    FastAPI = None
    HTTPException = None
    Request = None
    HTMLResponse = None
    APIRouter = None


def _sonoff_payload(data):
    payload = {
        "ok": bool(data.get("ok", True)),
        "config_loaded": bool(data.get("config_loaded")),
        "config_path": data.get("config_path"),
        "auth_status": data.get("auth_status"),
        "last_error": data.get("last_error"),
        "devices": data.get("devices", []),
    }
    if "results" in data:
        payload["results"] = data.get("results", [])
    return payload


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


async def _sonoff_device_handler(request: Request):
    body = await request.json()
    result = bulk_device_state(body.get("deviceid", ""), body.get("action", ""))
    if not result.get("devices") and not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff device bulk command failed"))
    return _sonoff_payload(result)


async def _sonoff_all_handler(request: Request):
    body = await request.json()
    result = bulk_all_state(body.get("action", ""))
    if not result.get("devices") and not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "sonoff bulk command failed"))
    return _sonoff_payload(result)


def _dashboard_index_handler():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "frontend", "index.html")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    script = '<script src="/assets/sonoff_bulk.js"></script>'
    if script not in html:
        html = html.replace("</body></html>", script + "</body></html>")
    return HTMLResponse(html)


def _install_extra_routes(app):
    if getattr(app, "_sonoff_bulk_routes_installed", False):
        return
    app._sonoff_bulk_routes_installed = True
    app.add_api_route("/api/sonoff/device", _sonoff_device_handler, methods=["POST"])
    app.add_api_route("/api/sonoff/all", _sonoff_all_handler, methods=["POST"])


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
            _install_extra_routes(self)
            if "GET" in methods:
                endpoint = _sonoff_get_handler
            elif "POST" in methods:
                endpoint = _sonoff_post_handler
        elif path == "/" and "GET" in methods and HTMLResponse is not None:
            endpoint = _dashboard_index_handler
        return _orig_fastapi_add_api_route(self, path, endpoint, **kwargs)

    FastAPI.add_api_route = _patched_fastapi_add_api_route
    FastAPI._sonoff_route_patch = True
