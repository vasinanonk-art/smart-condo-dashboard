"""Journal-backed, redacted audit records for IR command attempts."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from typing import Any


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_WRITE_LOCK = threading.Lock()


def correlation_id(supplied: str | None = None) -> str:
    candidate = str(supplied or "").strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else uuid.uuid4().hex


def stable_user_identifier(user: str | None) -> str:
    value = str(user or "").strip()
    if not value or value == "anonymous":
        return "anonymous"
    return "user-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def safe_identifier(value: str | None, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else fallback


def safe_value(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if -1000 <= value <= 1000 else None
    if isinstance(value, float):
        return round(value, 3) if -1000 <= value <= 1000 else None
    candidate = str(value).strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else None


def emit(
    *,
    user: str | None,
    device_id: str | None,
    command_type: str | None,
    value: Any,
    outcome: str,
    http_status: int,
    result_code: str,
    latency_ms: float,
    outbound_attempts: int,
    retry_count: int,
    request_id: str,
    timestamp: int | None = None,
) -> None:
    record = {
        "event": "ir_command_audit",
        "timestamp": int(timestamp if timestamp is not None else time.time()),
        "authenticated_user": stable_user_identifier(user),
        "device": safe_identifier(device_id, "invalid_device"),
        "command_type": safe_identifier(command_type, "unknown"),
        "value": safe_value(value),
        "outcome": safe_identifier(outcome, "failed"),
        "http_status": int(http_status),
        "result_code": safe_identifier(result_code, "unknown_result"),
        "latency_ms": round(max(0.0, float(latency_ms)), 1),
        "outbound_attempts": max(0, int(outbound_attempts)),
        "retry_count": max(0, int(retry_count)),
        "correlation_id": correlation_id(request_id),
    }
    line = "ir_command_audit " + json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    # systemd captures stdout for the production service. A direct, flushed
    # write avoids relying on an unattached Python logging handler.
    with _WRITE_LOCK:
        print(line, flush=True)
