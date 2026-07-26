"""Capability-aware camera inventory and bounded PTZ controls."""
from __future__ import annotations

import importlib.util
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict

from fastapi import Body
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from backend import app as app_module

app = app_module.app
NETWORK_TIMEOUT_SEC = 5.0
MAX_MOVE_SEC = 2.0
_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.Lock] = {}


class CameraCommand(BaseModel):
    command: str
    direction: str | None = None
    duration: float | None = None
    zoom: float | None = None
    preset: str | None = None


@dataclass
class CameraRecord:
    public_id: str
    config: Dict[str, Any]
    provider: str
    capabilities: Dict[str, bool]
    reason: str | None


def _provider(config: Dict[str, Any]) -> tuple[str, Dict[str, bool], str | None]:
    brand = str(config.get("brand") or "").lower()
    model = str(config.get("model") or "").lower()
    onvif_configured = bool(config.get("onvif_enabled") or config.get("onvif_port"))
    declared = {
        str(item) for item in config.get("control_capabilities", [])
        if isinstance(config.get("control_capabilities"), list)
    }
    if onvif_configured and importlib.util.find_spec("onvif") is not None:
        return "onvif", {
            "snapshot": bool(config.get("snapshot_url")),
            "live_stream": bool(config.get("rtsp_url") or config.get("rtsp")),
            "ptz_move": "ptz_move" in declared, "ptz_stop": "ptz_stop" in declared,
            "zoom": "zoom" in declared, "goto_preset": "goto_preset" in declared,
            "set_preset": "set_preset" in declared, "home_position": "home_position" in declared,
        }, None
    reason = "provider_dependency_unavailable" if onvif_configured or ("tapo" in brand and "c220" in model) else "control_provider_not_configured"
    return "read_only", {
        "snapshot": bool(config.get("snapshot_url")),
        "live_stream": bool(config.get("rtsp_url") or config.get("rtsp")),
        "ptz_move": False, "ptz_stop": False, "zoom": False,
        "goto_preset": False, "set_preset": False, "home_position": False,
    }, reason


def inventory() -> list[CameraRecord]:
    payload = app_module.camera_config_payload()
    records = []
    for index, config in enumerate(payload.get("cameras") or []):
        if not isinstance(config, dict) or config.get("enabled") is False:
            continue
        provider, capabilities, reason = _provider(config)
        records.append(CameraRecord(f"camera-{index + 1}", config, provider, capabilities, reason))
    return records


def _public(record: CameraRecord) -> Dict[str, Any]:
    config = record.config
    return {
        "id": record.public_id,
        "name": str(config.get("name") or f"Camera {record.public_id.split('-')[-1]}"),
        "brand": str(config.get("brand") or ""),
        "model": str(config.get("model") or ""),
        "provider": record.provider,
        "capabilities": dict(record.capabilities),
        "unsupported_reason": record.reason,
        "stream": {
            "available": record.capabilities["live_stream"],
            "access": "authenticated_proxy_required" if record.capabilities["live_stream"] else "unavailable",
        },
    }


def _record(camera_id: str) -> CameraRecord | None:
    return next((item for item in inventory() if item.public_id == camera_id), None)


def _lock(camera_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(camera_id, threading.Lock())


def _onvif_ptz(record: CameraRecord, payload: CameraCommand) -> None:
    from onvif import ONVIFCamera
    from zeep.transports import Transport

    config = record.config
    transport = Transport(timeout=NETWORK_TIMEOUT_SEC, operation_timeout=NETWORK_TIMEOUT_SEC)
    camera = ONVIFCamera(
        str(config.get("ip") or config.get("host") or ""),
        int(config.get("onvif_port") or 80),
        str(config.get("username") or ""),
        str(config.get("password") or ""),
        transport=transport,
    )
    media = camera.create_media_service()
    ptz = camera.create_ptz_service()
    profile = media.GetProfiles()[0]
    token = profile.token
    command = payload.command
    if command == "stop_ptz":
        ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
        return
    if command == "home_position":
        ptz.GotoHomePosition({"ProfileToken": token})
        return
    if command == "goto_preset":
        ptz.GotoPreset({"ProfileToken": token, "PresetToken": str(payload.preset or "")})
        return
    if command == "set_preset":
        ptz.SetPreset({"ProfileToken": token, "PresetName": str(payload.preset or "")})
        return
    if command == "zoom":
        if payload.zoom is None or not -1 <= payload.zoom <= 1:
            raise ValueError("invalid_zoom")
        request = {"ProfileToken": token, "Velocity": {"Zoom": {"x": payload.zoom}}}
    else:
        vectors = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}
        if payload.direction not in vectors:
            raise ValueError("invalid_direction")
        x, y = vectors[payload.direction]
        request = {"ProfileToken": token, "Velocity": {"PanTilt": {"x": x, "y": y}}}
    duration = min(MAX_MOVE_SEC, max(0.05, float(payload.duration or 0.3)))
    try:
        ptz.ContinuousMove(request)
        time.sleep(duration)
    finally:
        ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})


def _execute(record: CameraRecord, payload: CameraCommand) -> None:
    capability = {
        "move": "ptz_move", "stop_ptz": "ptz_stop", "zoom": "zoom",
        "goto_preset": "goto_preset", "set_preset": "set_preset",
        "home_position": "home_position",
    }.get(payload.command)
    if capability is None or not record.capabilities.get(capability):
        raise LookupError("unsupported_capability")
    if record.provider == "onvif":
        _onvif_ptz(record, payload)
        return
    raise LookupError("unsupported_provider")


@app.get("/api/camera-control/devices")
def camera_devices() -> Dict[str, Any]:
    payload = app_module.camera_config_payload()
    return {"config_loaded": bool(payload.get("loaded")), "cameras": [_public(item) for item in inventory()]}


@app.get("/api/camera-control/{camera_id}/snapshot")
def camera_snapshot(camera_id: str):
    record = _record(camera_id)
    if record is None:
        return JSONResponse({"detail": "camera_not_found"}, status_code=404)
    if not record.capabilities["snapshot"]:
        return JSONResponse({"detail": "unsupported_capability"}, status_code=422)
    request = urllib.request.Request(str(record.config["snapshot_url"]), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SEC) as response:
            content = response.read(5_000_000)
            content_type = response.headers.get_content_type()
    except Exception:
        return JSONResponse({"detail": "snapshot_unavailable"}, status_code=502)
    if not content_type.startswith("image/"):
        return JSONResponse({"detail": "invalid_snapshot_response"}, status_code=502)
    return Response(content, media_type=content_type, headers={"Cache-Control": "no-store"})


@app.post("/api/camera-control/{camera_id}/command")
def camera_command(camera_id: str, payload: CameraCommand = Body(...)):
    record = _record(camera_id)
    if record is None:
        return JSONResponse({"detail": "camera_not_found"}, status_code=404)
    lock = _lock(camera_id)
    if not lock.acquire(blocking=False):
        return JSONResponse({"detail": "camera_busy"}, status_code=409)
    try:
        _execute(record, payload)
        return {"ok": True, "camera": _public(record), "command": payload.command, "movement_stopped": payload.command in {"move", "zoom"}}
    except LookupError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=422)
    except TimeoutError:
        return JSONResponse({"detail": "camera_timeout", "stop_attempted": True, "movement_stopped": False}, status_code=504)
    except Exception:
        return JSONResponse({"detail": "camera_command_failed", "stop_attempted": True, "movement_stopped": False}, status_code=502)
    finally:
        lock.release()
