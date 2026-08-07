#!/usr/bin/env python3
"""Read-only production release verification for Smart Condo Dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import time
import urllib.request
from typing import Any, Mapping

BASE_URL = "http://127.0.0.1:8090"
TAPO_PUBLIC_ID = "tapo-c220"
JOURNAL_SERVICES = (
    "smart-condo-dashboard.service",
    "smart-condo-go2rtc.service",
)
GO2RTC_PORTS = (1984, 8554)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def verified_tapo_camera(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the documented camera inventory envelope and Tapo gates."""
    if payload.get("config_loaded") is not True:
        raise ValueError("camera_configuration_not_loaded")
    if payload.get("configuration_status") != "configured":
        raise ValueError("camera_configuration_not_configured")
    cameras = payload.get("cameras")
    if not isinstance(cameras, list):
        raise ValueError("camera_inventory_invalid")
    camera = next(
        (item for item in cameras if isinstance(item, dict) and item.get("id") == TAPO_PUBLIC_ID),
        None,
    )
    if camera is None:
        raise ValueError("verified_tapo_camera_not_found")
    capabilities = camera.get("capabilities")
    if camera.get("verification_status") != "verified":
        raise ValueError("tapo_camera_not_verified")
    if not isinstance(capabilities, dict) or capabilities.get("snapshot") is not True:
        raise ValueError("tapo_snapshot_not_verified")
    if capabilities.get("live_stream") is not True:
        raise ValueError("tapo_live_stream_not_verified")
    return camera


def count_journal_json_entries(output: str) -> int:
    """Count machine-readable journal records, never human status text."""
    count = 0
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("journal_output_not_json") from exc
        if not isinstance(record, dict):
            raise ValueError("journal_record_invalid")
        count += 1
    return count


def journal_error_count(service: str, *, since: str) -> int:
    if service not in JOURNAL_SERVICES:
        raise ValueError("journal_service_not_allowed")
    output = subprocess.check_output(
        [
            "journalctl",
            "--quiet",
            "--output=json",
            "--priority=err",
            "--unit",
            service,
            "--since",
            since,
            "--no-pager",
        ],
        text=True,
    )
    return count_journal_json_entries(output)


def verify_go2rtc_listener_output(output: str) -> dict[int, str]:
    """Validate go2rtc endpoints without depending on ss column positions."""
    bindings: dict[int, set[str]] = {port: set() for port in GO2RTC_PORTS}
    for line in output.splitlines():
        for token in line.split():
            if ":" not in token:
                continue
            host, separator, port_text = token.rpartition(":")
            if not separator or not port_text.isdigit():
                continue
            port = int(port_text)
            if port not in bindings:
                continue
            bindings[port].add(host.removeprefix("[").removesuffix("]"))
    expected = {"127.0.0.1"}
    for port, hosts in bindings.items():
        if hosts != expected:
            raise ValueError(f"go2rtc_listener_not_loopback:{port}")
    return {port: "127.0.0.1" for port in GO2RTC_PORTS}


def verify_go2rtc_listeners() -> dict[int, str]:
    output = subprocess.check_output(
        ["ss", "--listening", "--tcp", "--numeric", "--no-header"],
        text=True,
    )
    return verify_go2rtc_listener_output(output)


def _session_cookie() -> str:
    pid = subprocess.check_output(
        ["systemctl", "show", "-p", "MainPID", "--value", "smart-condo-dashboard.service"],
        text=True,
    ).strip()
    with open(f"/proc/{int(pid)}/environ", "rb") as stream:
        environment = dict(
            item.split(b"=", 1) for item in stream.read().split(b"\0") if b"=" in item
        )
    username = environment[b"DASHBOARD_AUTH_USERNAME"].decode()
    secret = environment[b"DASHBOARD_SESSION_SECRET"]
    now = int(time.time())
    payload = {
        "u": username,
        "iat": now,
        "exp": now + 300,
        "csrf": "release-verification",
        "nonce": "release-verification",
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"smart_condo_session={body}.{signature}"


def _fetch(cookie: str, path: str, *, limit: int = 2_000_000) -> tuple[int, str, bytes]:
    request = urllib.request.Request(
        BASE_URL + path,
        headers={"Cookie": cookie, "User-Agent": "release-verifier/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, response.headers.get("Content-Type", ""), response.read(limit)


def main() -> int:
    cookie = _session_cookie()
    results: dict[str, Any] = {}
    payloads: dict[str, Any] = {}
    for path in (
        "/api/auth/status",
        "/api/cameras",
        "/api/camera-control/devices",
        "/api/topology",
        "/api/electricity/status",
    ):
        status, _, content = _fetch(cookie, path)
        payloads[path] = json.loads(content)
        results[path] = {"status": status, "json": True, "bytes": len(content)}

    camera = verified_tapo_camera(payloads["/api/camera-control/devices"])
    camera_id = str(camera["id"])
    status, content_type, content = _fetch(cookie, f"/api/camera-control/{camera_id}/snapshot")
    if status != 200 or not content_type.startswith("image/") or not content:
        raise RuntimeError("snapshot_verification_failed")
    results["camera_snapshot"] = {
        "status": status,
        "content_type": content_type,
        "bytes": len(content),
    }

    status, content_type, content = _fetch(
        cookie, f"/api/camera-control/{camera_id}/live", limit=65_536
    )
    if status != 200 or not content:
        raise RuntimeError("live_verification_failed")
    results["camera_live"] = {
        "status": status,
        "content_type": content_type,
        "bytes": len(content),
    }

    status, content_type, content = _fetch(cookie, "/")
    results["homepage_authenticated"] = {
        "status": status,
        "content_type": content_type,
        "bytes": len(content),
    }
    status, _, content = _fetch(cookie, "/assets/dashboard_home.js")
    source = content.decode("utf-8", "replace")
    if "quick-action" not in source.lower() and "quickAction" not in source:
        raise RuntimeError("quick_actions_verification_failed")
    results["quick_actions"] = {"status": status, "present": True}

    listeners = verify_go2rtc_listeners()
    results["go2rtc_listeners"] = {
        str(port): f"{host}:{port}" for port, host in listeners.items()
    }

    journal_since = os.getenv("RELEASE_VERIFY_SINCE", "-10min")
    for service in JOURNAL_SERVICES:
        error_count = journal_error_count(service, since=journal_since)
        if error_count:
            raise RuntimeError(f"journal_errors:{service}:{error_count}")
        results[f"journal:{service}"] = {"errors": 0}
    print(json.dumps(results, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
