"""Strict, secret-reference-only schema for persistent camera inventory."""
from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Dict

SCHEMA_VERSION = 1
MAX_CAMERAS = 32
ALLOWED_ROOMS = {"living_room", "bed_room", "unknown"}
ALLOWED_PROVIDERS = {"auto", "onvif", "tapo_native", "rtsp", "unsupported"}
ALLOWED_VERIFICATION = {"unverified", "discovered", "verified", "unsupported"}
ALLOWED_CAPABILITIES = {
    "snapshot", "live_stream", "onvif_profiles", "ptz_move", "ptz_stop",
    "zoom", "presets", "home_position", "firmware_info",
}
CAMERA_FIELDS = {
    "id", "display_name", "room", "vendor", "model", "host", "enabled",
    "provider", "rtsp_port", "onvif_port", "stream_path", "credentials",
    "declared_capabilities", "verification_status",
}
CREDENTIAL_FIELDS = {"username_env", "password_env"}
_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ENV = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
_HOSTNAME = re.compile(r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class CameraConfigError(ValueError):
    pass


def _text(value: Any, field: str, *, required: bool, limit: int = 120) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CameraConfigError(f"{field}_required")
    normalized = value.strip()
    if len(normalized) > limit or any(ord(char) < 32 for char in normalized):
        raise CameraConfigError(f"{field}_invalid")
    return normalized


def _host(value: Any, *, placeholder_mode: bool) -> str | None:
    if value in (None, ""):
        return None
    host = _text(value, "host", required=True, limit=253)
    assert host is not None
    if placeholder_mode and host.startswith("<") and host.endswith(">"):
        return host
    if any(marker in host for marker in ("://", "@", "/", "\\", "?", "#")):
        raise CameraConfigError("host_invalid")
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        if not _HOSTNAME.fullmatch(host):
            raise CameraConfigError("host_invalid")
    return host.lower()


def _port(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise CameraConfigError(f"{field}_invalid")
    return value


def _stream_path(value: Any, *, placeholder_mode: bool) -> str | None:
    if value in (None, ""):
        return None
    path = _text(value, "stream_path", required=True, limit=512)
    assert path is not None
    if placeholder_mode and path.startswith("<") and path.endswith(">"):
        return path
    if not path.startswith("/") or "://" in path or "@" in path or any(ord(char) < 32 for char in path):
        raise CameraConfigError("stream_path_invalid")
    return path


def _credentials(value: Any, *, placeholder_mode: bool) -> Dict[str, str] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, dict) or set(value) != CREDENTIAL_FIELDS:
        raise CameraConfigError("credentials_invalid")
    result: Dict[str, str] = {}
    for field in sorted(CREDENTIAL_FIELDS):
        name = _text(value.get(field), field, required=True, limit=128)
        assert name is not None
        if placeholder_mode and name.startswith("<") and name.endswith(">"):
            result[field] = name
        elif not _ENV.fullmatch(name):
            raise CameraConfigError(f"{field}_invalid")
        else:
            result[field] = name
    return result


def validate_camera_config(payload: Any, *, placeholder_mode: bool = False) -> Dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "cameras"}:
        raise CameraConfigError("root_schema_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise CameraConfigError("schema_version_unsupported")
    cameras = payload.get("cameras")
    if not isinstance(cameras, list) or len(cameras) > MAX_CAMERAS:
        raise CameraConfigError("cameras_invalid")
    normalized = []
    identifiers: set[str] = set()
    for index, item in enumerate(cameras):
        prefix = f"camera_{index + 1}"
        if not isinstance(item, dict) or set(item) != CAMERA_FIELDS:
            raise CameraConfigError(f"{prefix}_schema_invalid")
        identifier = _text(item.get("id"), "id", required=True, limit=64)
        assert identifier is not None
        if not _ID.fullmatch(identifier) or identifier in identifiers:
            raise CameraConfigError(f"{prefix}_id_invalid")
        identifiers.add(identifier)
        room = item.get("room")
        provider = item.get("provider")
        verification = item.get("verification_status")
        if room not in ALLOWED_ROOMS:
            raise CameraConfigError(f"{prefix}_room_invalid")
        if provider not in ALLOWED_PROVIDERS:
            raise CameraConfigError(f"{prefix}_provider_invalid")
        if verification not in ALLOWED_VERIFICATION:
            raise CameraConfigError(f"{prefix}_verification_invalid")
        if not isinstance(item.get("enabled"), bool):
            raise CameraConfigError(f"{prefix}_enabled_invalid")
        capabilities = item.get("declared_capabilities")
        if (
            not isinstance(capabilities, list)
            or any(not isinstance(value, str) or value not in ALLOWED_CAPABILITIES for value in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise CameraConfigError(f"{prefix}_capabilities_invalid")
        normalized.append({
            "id": identifier,
            "display_name": _text(item.get("display_name"), "display_name", required=True),
            "room": room,
            "vendor": _text(item.get("vendor"), "vendor", required=False),
            "model": _text(item.get("model"), "model", required=False),
            "host": _host(item.get("host"), placeholder_mode=placeholder_mode),
            "enabled": item["enabled"],
            "provider": provider,
            "rtsp_port": _port(item.get("rtsp_port"), "rtsp_port"),
            "onvif_port": _port(item.get("onvif_port"), "onvif_port"),
            "stream_path": _stream_path(item.get("stream_path"), placeholder_mode=placeholder_mode),
            "credentials": _credentials(item.get("credentials"), placeholder_mode=placeholder_mode),
            "declared_capabilities": sorted(capabilities),
            "verification_status": verification,
        })
    return {"schema_version": SCHEMA_VERSION, "cameras": normalized}


def load_camera_config(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CameraConfigError("camera_config_unreadable") from exc
    return validate_camera_config(payload)
