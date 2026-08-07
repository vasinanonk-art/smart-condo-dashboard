#!/usr/bin/env python3
"""Render a root-only go2rtc config without exposing camera credentials."""
from __future__ import annotations

import argparse
import json
import os
import signal
import stat
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

CAMERA_ID = "tapo-c220"
STREAM_NAME = "tapo_c200_main"
DEFAULT_ENV_FILE = Path("/etc/default/smart-condo-dashboard")
DEFAULT_CAMERA_FILE = Path("/root/.smart-condo-dashboard/cameras.local.json")


class ProvisioningError(RuntimeError):
    pass


def read_environment_file(path: Path) -> dict[str, str]:
    """Read EnvironmentFile values as data; never evaluate them as shell."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProvisioningError("environment_file_unavailable") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def camera_entry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError("camera_config_unavailable") from exc
    cameras = payload.get("cameras") if isinstance(payload, dict) else None
    if not isinstance(cameras, list):
        raise ProvisioningError("camera_config_invalid")
    camera = next((item for item in cameras if isinstance(item, dict) and item.get("id") == CAMERA_ID), None)
    if camera is None:
        raise ProvisioningError("tapo_camera_missing")
    if camera.get("enabled") is not True or camera.get("verification_status") != "verified":
        raise ProvisioningError("tapo_camera_unverified")
    if "live_stream" not in (camera.get("declared_capabilities") or []):
        raise ProvisioningError("tapo_live_not_declared")
    return camera


def credentials(camera: dict[str, Any], environment: dict[str, str]) -> tuple[str, str]:
    references = camera.get("credentials")
    if not isinstance(references, dict):
        raise ProvisioningError("camera_credentials_missing")
    username = environment.get(str(references.get("username_env") or ""), "")
    password = environment.get(str(references.get("password_env") or ""), "")
    if not username or not password:
        raise ProvisioningError("camera_credentials_missing")
    return username, password


def discover_main_uri(camera: dict[str, Any], username: str, password: str) -> str:
    try:
        from onvif import ONVIFCamera
    except ImportError as exc:
        raise ProvisioningError("onvif_dependency_missing") from exc
    host, port = str(camera.get("host") or "").strip(), camera.get("onvif_port")
    if not host or not isinstance(port, int):
        raise ProvisioningError("onvif_configuration_incomplete")
    try:
        media = ONVIFCamera(host, port, username, password, adjust_time=True).create_media_service()
        profiles = list(media.GetProfiles() or [])
    except Exception as exc:
        raise ProvisioningError("onvif_discovery_failed") from exc
    if not profiles:
        raise ProvisioningError("onvif_profiles_missing")

    def area(profile: Any) -> int:
        resolution = getattr(getattr(profile, "VideoEncoderConfiguration", None), "Resolution", None)
        try:
            return int(getattr(resolution, "Width", 0)) * int(getattr(resolution, "Height", 0))
        except (TypeError, ValueError):
            return 0

    token = getattr(max(profiles[:16], key=area), "token", None)
    if not token:
        raise ProvisioningError("onvif_profile_invalid")
    try:
        response = media.GetStreamUri({
            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
            "ProfileToken": token,
        })
    except Exception as exc:
        raise ProvisioningError("onvif_stream_uri_failed") from exc
    parsed = urllib.parse.urlsplit(str(getattr(response, "Uri", "") or ""))
    if parsed.scheme != "rtsp" or parsed.hostname != host or parsed.fragment:
        raise ProvisioningError("onvif_stream_uri_invalid")
    rtsp_port = parsed.port or int(camera.get("rtsp_port") or 554)
    netloc = "%s:%s@%s:%d" % (
        urllib.parse.quote(username, safe=""), urllib.parse.quote(password, safe=""), host, rtsp_port,
    )
    return urllib.parse.urlunsplit(("rtsp", netloc, parsed.path or "/", parsed.query, ""))


def bounded_main_uri(camera: dict[str, Any], username: str, password: str) -> str:
    def timed_out(_signum, _frame):
        raise ProvisioningError("onvif_discovery_timeout")

    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(15)
    try:
        return discover_main_uri(camera, username, password)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def render(uri: str) -> str:
    return "\n".join((
        "api:", '  listen: "127.0.0.1:1984"',
        "rtsp:", '  listen: "127.0.0.1:8554"',
        "webrtc:", '  listen: ""',
        "streams:", f"  {STREAM_NAME}:", f"    - {json.dumps(uri)}", "",
    ))


def atomic_write(path: Path, content: str) -> bool:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    previous = path.read_text(encoding="utf-8") if path.is_file() else None
    if previous == content:
        os.chmod(path, 0o600)
        return False
    descriptor, temporary = tempfile.mkstemp(prefix=".go2rtc.", dir=path.parent)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--camera-config")
    parser.add_argument("--output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    environment = read_environment_file(Path(args.environment_file))
    camera_path = Path(args.camera_config or environment.get("CAMERA_CONFIG_FILE", str(DEFAULT_CAMERA_FILE)))
    camera = camera_entry(camera_path)
    username, password = credentials(camera, environment)
    if args.validate_only:
        return 0
    if not args.output:
        raise ProvisioningError("output_required")
    atomic_write(Path(args.output), render(bounded_main_uri(camera, username, password)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProvisioningError as exc:
        print(f"go2rtc configuration error: {exc}", file=os.sys.stderr)
        raise SystemExit(1)
