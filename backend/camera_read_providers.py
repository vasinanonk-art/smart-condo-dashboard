"""Bounded, read-only camera providers with secret-safe API projections."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from fastapi.responses import JSONResponse, Response

from backend import app as app_module
from backend.camera_inventory_schema import CameraConfigError

app = app_module.app
NETWORK_TIMEOUT_SEC = min(10.0, max(0.5, float(os.getenv("CAMERA_READ_TIMEOUT_SEC", "4"))))
MAX_SNAPSHOT_BYTES = 5_000_000
CAPABILITY_KEYS = (
    "snapshot", "live_stream", "onvif_profiles", "ptz_move", "ptz_stop",
    "zoom", "presets", "home_position", "firmware_info",
)
PUBLIC_CAMERA_ALIASES = {
    "camera-1": "tapo-c220",
    "camera-2": "xiaomi-camera-1",
    "camera-3": "xiaomi-camera-2",
}


@dataclass(frozen=True)
class CameraSpec:
    id: str
    display_name: str
    room: str
    vendor: str | None
    model: str | None
    host: str | None
    enabled: bool
    provider: str
    rtsp_port: int | None
    onvif_port: int | None
    stream_path: str | None
    username_env: str | None
    password_env: str | None
    declared_capabilities: frozenset[str]
    verification_status: str


def _replace_endpoint(path: str, methods: set[str], endpoint: Callable[..., Any]) -> bool:
    replaced = False
    for route in app.routes:
        route_methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and methods.issubset(route_methods):
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint
            replaced = True
    return replaced


def _config_path() -> Path | None:
    return next((Path(path) for path in app_module.camera_config_paths() if path and Path(path).is_file()), None)


def load_inventory() -> tuple[str, list[CameraSpec]]:
    status, cameras, _ = load_inventory_details()
    return status, cameras


def load_inventory_details() -> tuple[str, list[CameraSpec], list[Dict[str, Any]]]:
    path = _config_path()
    if path is None:
        return "configuration_missing", [], []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "configuration_invalid", [], [{"index": None, "error": "camera_config_unreadable"}]
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "cameras"}
        or raw.get("schema_version") != 1
        or not isinstance(raw.get("cameras"), list)
        or len(raw["cameras"]) > 32
    ):
        return "configuration_invalid", [], [{"index": None, "error": "root_schema_invalid"}]

    normalized: list[Dict[str, Any]] = []
    invalid: list[Dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, item in enumerate(raw["cameras"]):
        try:
            payload = load_camera_config_entry(item)
            camera = payload["cameras"][0]
            if camera["id"] in identifiers:
                raise CameraConfigError("duplicate_camera_id")
            identifiers.add(camera["id"])
            normalized.append(camera)
        except CameraConfigError as exc:
            invalid.append({"index": index, "error": str(exc)[:80]})

    cameras = []
    for item in normalized:
        credentials = item.get("credentials") or {}
        cameras.append(CameraSpec(
            id=item["id"], display_name=item["display_name"], room=item["room"],
            vendor=item.get("vendor"), model=item.get("model"), host=item.get("host"),
            enabled=item["enabled"], provider=item["provider"],
            rtsp_port=item.get("rtsp_port"), onvif_port=item.get("onvif_port"),
            stream_path=item.get("stream_path"),
            username_env=credentials.get("username_env"),
            password_env=credentials.get("password_env"),
            declared_capabilities=frozenset(item["declared_capabilities"]),
            verification_status=item["verification_status"],
        ))
    status = "configuration_partial" if invalid else "configured"
    return status, cameras, invalid


def load_camera_config_entry(item: Any) -> Dict[str, Any]:
    """Validate one entry with the canonical schema without weakening it."""
    from backend.camera_inventory_schema import validate_camera_config

    return validate_camera_config({"schema_version": 1, "cameras": [item]})


def _credentials(spec: CameraSpec) -> tuple[str, str] | None:
    if not spec.username_env or not spec.password_env:
        return None
    username = os.getenv(spec.username_env, "")
    password = os.getenv(spec.password_env, "")
    return (username, password) if username and password else None


def _tcp(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_SEC):
            return True
    except OSError:
        return False


def _empty_capabilities() -> Dict[str, bool]:
    return {key: False for key in CAPABILITY_KEYS}


def _safe_text(value: Any, limit: int = 120) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    return "".join(char for char in text if ord(char) >= 32)[:limit]


def _redacted_serial(value: Any) -> str | None:
    serial = _safe_text(value, 120)
    if not serial:
        return None
    return "****" if len(serial) <= 4 else f"***{serial[-4:]}"


def _authentication_error(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    if any(marker in name for marker in ("auth", "unauthorized", "forbidden")):
        return True
    response = getattr(exc, "response", None)
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(response, "status_code", None)
    return status in {401, 403}


def _base_result(spec: CameraSpec, provider: str, reason: str | None) -> Dict[str, Any]:
    return {
        "id": spec.id,
        "display_name": spec.display_name,
        "room": spec.room,
        "vendor": spec.vendor if spec.verification_status == "verified" else None,
        "manufacturer": spec.vendor if spec.verification_status == "verified" else None,
        "model": spec.model if spec.verification_status == "verified" else None,
        "firmware": None,
        "serial": None,
        "provider": provider,
        "enabled": spec.enabled,
        "online": None,
        "health": "unknown",
        "verification_status": spec.verification_status,
        "capabilities": _empty_capabilities(),
        "discovered_capabilities": [],
        "profiles": [],
        "profiles_available": False,
        "profile_count": 0,
        "ptz_capability": False,
        "snapshot_capability": False,
        "presets": [],
        "last_update": None,
        "unavailable_reason": reason,
        "stream": {"available": False, "access": "unavailable"},
    }


def _onvif_client(spec: CameraSpec):
    from onvif import ONVIFCamera
    from zeep.transports import Transport

    credentials = _credentials(spec)
    if credentials is None or not spec.host or not spec.onvif_port:
        raise LookupError("onvif_configuration_incomplete")
    username, password = credentials
    transport = Transport(timeout=NETWORK_TIMEOUT_SEC, operation_timeout=NETWORK_TIMEOUT_SEC)
    return ONVIFCamera(spec.host, spec.onvif_port, username, password, transport=transport)


def _safe_snapshot_uri(media: Any, profile_token: Any, host: str | None) -> str | None:
    if not profile_token or not host:
        return None
    uri_result = media.GetSnapshotUri({"ProfileToken": profile_token})
    uri = str(getattr(uri_result, "Uri", "") or "")
    parsed_uri = urllib.parse.urlsplit(uri)
    if (
        parsed_uri.scheme not in {"http", "https"}
        or not parsed_uri.hostname
        or parsed_uri.hostname.casefold() != host.casefold()
        or parsed_uri.username is not None
        or parsed_uri.password is not None
    ):
        return None
    return uri


def _discover_onvif(spec: CameraSpec) -> Dict[str, Any]:
    result = _base_result(spec, "onvif", None)
    started = time.monotonic()
    try:
        camera = _onvif_client(spec)
        information = camera.devicemgmt.GetDeviceInformation()
        media = camera.create_media_service()
        raw_profiles = list(media.GetProfiles() or [])
        profile_count = min(len(raw_profiles), 16)
        discovered = {"onvif_profiles", "firmware_info"} if raw_profiles else {"firmware_info"}
        snapshot_available = False
        if raw_profiles:
            try:
                snapshot_available = bool(
                    _safe_snapshot_uri(media, getattr(raw_profiles[0], "token", None), spec.host)
                )
                if snapshot_available:
                    discovered.add("snapshot")
            except Exception:
                snapshot_available = False
        ptz_available = False
        try:
            ptz = camera.create_ptz_service()
            configurations = list(ptz.GetConfigurations() or [])
            ptz_available = bool(configurations)
            if ptz_available:
                discovered.update({"ptz_move", "ptz_stop"})
        except Exception:
            ptz_available = False
        result["capabilities"]["onvif_profiles"] = bool(raw_profiles)
        result["capabilities"]["firmware_info"] = True
        manufacturer = _safe_text(getattr(information, "Manufacturer", None))
        result.update({
            "vendor": manufacturer or result["vendor"],
            "manufacturer": manufacturer or result["manufacturer"],
            "model": _safe_text(getattr(information, "Model", None)) or result["model"],
            "firmware": _safe_text(getattr(information, "FirmwareVersion", None)),
            "serial": _redacted_serial(getattr(information, "SerialNumber", None)),
            "online": True,
            "health": "healthy",
            "profiles_available": bool(raw_profiles),
            "profile_count": profile_count,
            "ptz_capability": ptz_available,
            "snapshot_capability": snapshot_available,
            "discovered_capabilities": sorted(discovered),
            "last_update": int(time.time()),
            "unavailable_reason": None,
            "stream": {
                "available": False,
                "access": "unavailable",
            },
        })
        return result
    except (TimeoutError, socket.timeout):
        result.update({"online": False, "health": "offline", "unavailable_reason": "camera_timeout"})
        return result
    except PermissionError:
        result.update({"online": False, "health": "offline", "unavailable_reason": "invalid_credentials"})
        return result
    except (ConnectionError, OSError):
        result.update({"online": False, "health": "offline", "unavailable_reason": "onvif_unavailable"})
        return result
    except Exception as exc:
        reason = "invalid_credentials" if _authentication_error(exc) else "onvif_unavailable"
        result.update({"online": False, "health": "offline", "unavailable_reason": reason})
        return result
    finally:
        result["latency_ms"] = int((time.monotonic() - started) * 1000)


def _discover_rtsp(spec: CameraSpec, reason: str | None = None) -> Dict[str, Any]:
    result = _base_result(spec, "rtsp", reason)
    complete = bool(spec.host and spec.rtsp_port and spec.stream_path and _credentials(spec))
    if not complete:
        result["unavailable_reason"] = reason or "rtsp_configuration_incomplete"
        return result
    started = time.monotonic()
    online = _tcp(spec.host or "", int(spec.rtsp_port or 0))
    declared = spec.declared_capabilities
    # A reachable RTSP socket is metadata, not a browser-safe live-view proxy.
    result["capabilities"]["live_stream"] = False
    result["discovered_capabilities"] = ["live_stream"] if online else []
    result.update({
        "online": online,
        "health": "healthy" if online else "offline",
        "last_update": int(time.time()),
        "unavailable_reason": None if online else "rtsp_unreachable",
        "stream": {
            "available": False,
            "access": "metadata_only" if online and "live_stream" in declared else "unavailable",
        },
        "latency_ms": int((time.monotonic() - started) * 1000),
    })
    return result


def discover(spec: CameraSpec) -> Dict[str, Any]:
    if not spec.enabled:
        return _base_result(spec, "unsupported", "camera_disabled")
    onvif_requested = spec.provider in {"auto", "onvif"} and bool(spec.onvif_port)
    if onvif_requested and _credentials(spec) is None:
        return _base_result(spec, "onvif", "camera_credentials_missing")
    if onvif_requested and importlib.util.find_spec("onvif") is not None:
        result = _discover_onvif(spec)
        if result["online"] is True or spec.provider == "onvif":
            return result
    if spec.provider in {"auto", "rtsp", "onvif"} and spec.rtsp_port:
        return _discover_rtsp(spec, "onvif_provider_unavailable" if onvif_requested else None)
    if spec.provider == "tapo_native":
        return _base_result(spec, "unsupported", "tapo_native_provider_unavailable")
    return _base_result(spec, "unsupported", "read_only_provider_unavailable")


def _inventory_payload(*, discover_live: bool) -> Dict[str, Any]:
    config_status, specs, invalid = load_inventory_details()
    if config_status in {"configuration_missing", "configuration_invalid"}:
        return {
            "config_loaded": False,
            "configuration_status": config_status,
            "invalid_camera_count": len(invalid),
            "cameras": [],
        }
    cameras = [discover(spec) if discover_live else _base_result(spec, "pending", None) for spec in specs]
    return {
        "config_loaded": True,
        "configuration_status": config_status,
        "invalid_camera_count": len(invalid),
        "cameras": cameras,
    }


def camera_devices_readonly() -> Dict[str, Any]:
    return _inventory_payload(discover_live=True)


def cameras_runtime() -> Dict[str, Any]:
    payload = camera_devices_readonly()
    return {
        "ok": True,
        "config_loaded": payload["config_loaded"],
        "config_path": str(_config_path()) if _config_path() is not None else None,
        "configuration_status": payload["configuration_status"],
        "invalid_camera_count": payload.get("invalid_camera_count", 0),
        "camera_count": len(payload["cameras"]),
        "cameras": copy.deepcopy(payload["cameras"]),
    }


def _spec(camera_id: str) -> CameraSpec | None:
    status, specs = load_inventory()
    if status not in {"configured", "configuration_partial"}:
        return None
    resolved_id = PUBLIC_CAMERA_ALIASES.get(camera_id, camera_id)
    return next((item for item in specs if item.id == resolved_id), None)


def camera_status_readonly(camera_id: str):
    spec = _spec(camera_id)
    if spec is None:
        return JSONResponse({"detail": "camera_not_found"}, status_code=404)
    return discover(spec)


def camera_stream_metadata(camera_id: str):
    result = camera_status_readonly(camera_id)
    if isinstance(result, JSONResponse):
        return result
    return {"id": result["id"], "stream": copy.deepcopy(result["stream"])}


def camera_profiles(camera_id: str):
    result = camera_status_readonly(camera_id)
    if isinstance(result, JSONResponse):
        return result
    return {"id": result["id"], "profiles": copy.deepcopy(result["profiles"])}


def camera_presets(camera_id: str):
    result = camera_status_readonly(camera_id)
    if isinstance(result, JSONResponse):
        return result
    return {"id": result["id"], "presets": copy.deepcopy(result["presets"])}


def _snapshot_onvif(spec: CameraSpec):
    camera = _onvif_client(spec)
    media = camera.create_media_service()
    profiles = list(media.GetProfiles() or [])
    if not profiles:
        raise LookupError("snapshot_unavailable")
    profile_token = getattr(profiles[0], "token", None)
    uri = _safe_snapshot_uri(media, profile_token, spec.host)
    if uri is None:
        raise LookupError("snapshot_unavailable")
    credentials = _credentials(spec)
    if credentials is None:
        raise LookupError("snapshot_unavailable")
    username, password = credentials
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, uri, username, password)
    opener = urllib.request.build_opener(
        urllib.request.HTTPBasicAuthHandler(manager),
        urllib.request.HTTPDigestAuthHandler(manager),
    )
    request = urllib.request.Request(uri, method="GET")
    with opener.open(request, timeout=NETWORK_TIMEOUT_SEC) as response:
        content_type = response.headers.get_content_type()
        content = response.read(MAX_SNAPSHOT_BYTES + 1)
    if not content_type.startswith("image/") or len(content) > MAX_SNAPSHOT_BYTES:
        raise ValueError("invalid_snapshot_response")
    return Response(content, media_type=content_type, headers={"Cache-Control": "no-store"})


def camera_snapshot_readonly(camera_id: str):
    spec = _spec(camera_id)
    if spec is None:
        return JSONResponse({"detail": "camera_not_found"}, status_code=404)
    if (
        not spec.enabled
        or spec.verification_status != "verified"
        or "snapshot" not in spec.declared_capabilities
        or spec.provider not in {"auto", "onvif"}
        or importlib.util.find_spec("onvif") is None
    ):
        return JSONResponse({"detail": "snapshot_unavailable"}, status_code=422)
    try:
        return _snapshot_onvif(spec)
    except (TimeoutError, socket.timeout):
        return JSONResponse({"detail": "camera_timeout"}, status_code=504)
    except Exception:
        return JSONResponse({"detail": "snapshot_unavailable"}, status_code=502)


@app.get("/api/camera-control/{camera_id}/status")
def camera_status_route(camera_id: str):
    return camera_status_readonly(camera_id)


@app.get("/api/camera-control/{camera_id}/stream")
def camera_stream_route(camera_id: str):
    return camera_stream_metadata(camera_id)


@app.get("/api/camera-control/{camera_id}/profiles")
def camera_profiles_route(camera_id: str):
    return camera_profiles(camera_id)


@app.get("/api/camera-control/{camera_id}/presets")
def camera_presets_route(camera_id: str):
    return camera_presets(camera_id)


_replace_endpoint("/api/camera-control/devices", {"GET"}, camera_devices_readonly)
_replace_endpoint("/api/camera-control/{camera_id}/snapshot", {"GET"}, camera_snapshot_readonly)
_replace_endpoint("/api/cameras", {"GET"}, cameras_runtime)
