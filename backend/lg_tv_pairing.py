"""Secure LG webOS pairing and client-key management.

Pairing is bounded to one background job. API responses, diagnostics and logs never
contain the client key. Existing MQTT topics and LG command routes are untouched.
"""
from __future__ import annotations

import ast
import copy
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module

app = app_module.app
TV_IP = os.getenv("LG_TV_IP", "192.168.1.33").strip() or "192.168.1.33"
PAIR_TIMEOUT_SEC = max(30, min(120, int(os.getenv("LG_TV_PAIR_TIMEOUT_SEC", "120"))))
PAIR_RATE_LIMIT_SEC = max(30, int(os.getenv("LG_TV_PAIR_RATE_LIMIT_SEC", "60")))
SECRET_DIR = Path("/root/.smart-condo-dashboard/secrets")
KEY_PATH = SECRET_DIR / "lg_tv_client_key"
MIGRATION_MARKER = SECRET_DIR / ".lg_tv_client_key_migrated"
LEGACY_SCRIPT = Path("/root/lgtv/lg_mqtt.py")
SERVICE_NAME = os.getenv("LG_TV_SERVICE", "lgtv-mqtt.service")
_STATE_LOCK = threading.RLock()
_CANCEL = threading.Event()
_JOB: Dict[str, Any] = {
    "state": "idle", "started_ts": None, "updated_ts": None, "expires_ts": None,
    "result": None, "error": None, "pending_key": None, "thread": None,
}
_RUNTIME: Dict[str, Any] = {
    "last_pair_attempt": None, "last_pair_success": None, "last_error": None,
    "last_connection_error": None, "last_pairing_result": None,
}


def _safe_error(exc: BaseException) -> str:
    text = str(exc).strip().lower()
    if "reject" in text or "denied" in text:
        return "rejected"
    if "timeout" in text or isinstance(exc, TimeoutError):
        return "timeout"
    return type(exc).__name__


def _run(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)


def _service_active() -> bool:
    try:
        return _run(["systemctl", "is-active", "--quiet", SERVICE_NAME], 5).returncode == 0
    except Exception:
        return False


def _tcp_reachable(port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((TV_IP, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_key_file() -> Optional[str]:
    try:
        value = KEY_PATH.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def _legacy_key() -> Optional[str]:
    """Read only a literal CLIENT_KEY assignment; never execute the legacy script."""
    try:
        source = LEGACY_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        return None
    for match in re.finditer(r"(?m)^\s*CLIENT_KEY\s*=\s*(.+?)\s*$", source):
        try:
            value = ast.literal_eval(match.group(1))
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _current_key() -> tuple[Optional[str], str]:
    environment = os.getenv("LG_TV_CLIENT_KEY", "").strip()
    if environment:
        return environment, "environment"
    stored = _read_key_file()
    if stored:
        return stored, "secure_file"
    legacy = _legacy_key()
    if legacy:
        return legacy, "legacy_fallback"
    return None, "none"


def _atomic_write_key(value: str) -> Optional[Path]:
    SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(SECRET_DIR, 0o700)
    backup: Optional[Path] = None
    if KEY_PATH.exists():
        backup = KEY_PATH.with_name(f"{KEY_PATH.name}.backup-{int(time.time())}")
        shutil.copy2(KEY_PATH, backup)
        os.chmod(backup, 0o600)
    temporary = KEY_PATH.with_name(f".{KEY_PATH.name}.tmp-{os.getpid()}")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value.strip() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, KEY_PATH)
        os.chmod(KEY_PATH, 0o600)
        return backup
    except Exception:
        temporary.unlink(missing_ok=True)
        if backup and backup.exists():
            os.replace(backup, KEY_PATH)
            os.chmod(KEY_PATH, 0o600)
        raise


def _restore_key(backup: Optional[Path], previous: Optional[str]) -> None:
    if backup and backup.exists():
        os.replace(backup, KEY_PATH)
        os.chmod(KEY_PATH, 0o600)
    elif previous is None:
        KEY_PATH.unlink(missing_ok=True)
    else:
        _atomic_write_key(previous)


def _install_legacy_loader() -> bool:
    """Patch only the literal assignment while preserving a migration fallback."""
    try:
        source = LEGACY_SCRIPT.read_text(encoding="utf-8")
    except OSError:
        return False
    if "SMART_CONDO_LG_KEY_LOADER" in source:
        return True
    pattern = re.compile(r"(?m)^(\s*)CLIENT_KEY\s*=\s*(.+?)\s*$")
    match = pattern.search(source)
    if not match:
        return False
    try:
        fallback = ast.literal_eval(match.group(2))
    except Exception:
        return False
    if not isinstance(fallback, str):
        return False
    indent = match.group(1)
    replacement = (
        f"{indent}# SMART_CONDO_LG_KEY_LOADER\n"
        f"{indent}_legacy_client_key = {fallback!r}\n"
        f"{indent}_key_path = '/root/.smart-condo-dashboard/secrets/lg_tv_client_key'\n"
        f"{indent}try:\n"
        f"{indent}    _file_client_key = open(_key_path, encoding='utf-8').read().strip()\n"
        f"{indent}except OSError:\n"
        f"{indent}    _file_client_key = ''\n"
        f"{indent}CLIENT_KEY = os.getenv('LG_TV_CLIENT_KEY', '').strip() or _file_client_key or _legacy_client_key"
    )
    if "import os" not in source:
        source = "import os\n" + source
    patched = pattern.sub(replacement, source, count=1)
    backup = LEGACY_SCRIPT.with_name(f"{LEGACY_SCRIPT.name}.pre-key-loader-{int(time.time())}.bak")
    shutil.copy2(LEGACY_SCRIPT, backup)
    temporary = LEGACY_SCRIPT.with_name(f".{LEGACY_SCRIPT.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(patched, encoding="utf-8")
        os.chmod(temporary, LEGACY_SCRIPT.stat().st_mode & 0o777)
        os.replace(temporary, LEGACY_SCRIPT)
        return True
    except Exception:
        temporary.unlink(missing_ok=True)
        shutil.copy2(backup, LEGACY_SCRIPT)
        return False


def migrate_legacy_key() -> bool:
    if MIGRATION_MARKER.exists() and KEY_PATH.exists():
        _install_legacy_loader()
        return True
    if KEY_PATH.exists():
        SECRET_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(SECRET_DIR, 0o700)
        os.chmod(KEY_PATH, 0o600)
        _install_legacy_loader()
        return True
    legacy = _legacy_key()
    if not legacy:
        return False
    try:
        _atomic_write_key(legacy)
        _install_legacy_loader()
        MIGRATION_MARKER.write_text(str(int(time.time())), encoding="utf-8")
        os.chmod(MIGRATION_MARKER, 0o600)
        return True
    except Exception:
        return False


def _webos_register(store: Dict[str, Any], cancel: Optional[threading.Event] = None) -> str:
    from pywebostv.connection import WebOSClient

    client = WebOSClient(TV_IP, secure=True)
    client.connect()
    deadline = time.monotonic() + PAIR_TIMEOUT_SEC
    prompted = False
    for registration in client.register(store):
        if cancel and cancel.is_set():
            raise RuntimeError("cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("pairing_timeout")
        if registration == WebOSClient.PROMPTED:
            prompted = True
            with _STATE_LOCK:
                _JOB.update({"state": "prompted", "updated_ts": int(time.time())})
        elif registration == WebOSClient.REGISTERED:
            return "registered"
    return "prompted" if prompted else "connection_failed"


def _validate_key(value: str) -> bool:
    store = {"client_key": value}
    try:
        valid = _webos_register(store) == "registered" and store.get("client_key") == value
        if valid:
            with _STATE_LOCK:
                _RUNTIME["last_connection_error"] = None
                _RUNTIME["last_error"] = None
        return valid
    except Exception as exc:
        with _STATE_LOCK:
            _RUNTIME["last_connection_error"] = _safe_error(exc)
        return False


def _pair_worker() -> None:
    store: Dict[str, Any] = {}
    result = "connection_failed"
    error: Optional[str] = None
    try:
        with _STATE_LOCK:
            _JOB.update({"state": "connecting", "updated_ts": int(time.time())})
        result = _webos_register(store, _CANCEL)
        if result == "registered" and isinstance(store.get("client_key"), str) and store["client_key"].strip():
            with _STATE_LOCK:
                _JOB.update({"state": "registered", "result": "registered", "pending_key": store["client_key"].strip(), "updated_ts": int(time.time())})
                _RUNTIME.update({"last_pair_success": int(time.time()), "last_error": None, "last_connection_error": None, "last_pairing_result": "registered"})
            return
        error = result
    except Exception as exc:
        error = _safe_error(exc)
        result = "timeout" if error == "timeout" else "rejected" if error == "rejected" else "connection_failed"
    with _STATE_LOCK:
        state = "expired" if result == "timeout" else "failed"
        _JOB.update({"state": state, "result": result, "error": error, "pending_key": None, "updated_ts": int(time.time())})
        _RUNTIME.update({"last_error": error, "last_pairing_result": result})


def _job_public() -> Dict[str, Any]:
    with _STATE_LOCK:
        return {key: copy.deepcopy(_JOB.get(key)) for key in ("state", "started_ts", "updated_ts", "expires_ts", "result", "error")}


@app.on_event("startup")
def migrate_lg_tv_key_on_startup() -> None:
    migrate_legacy_key()


@app.get("/api/lg-tv/pairing/status")
def pairing_status() -> Dict[str, Any]:
    key, source = _current_key()
    paired = bool(key and _validate_key(key))
    return {
        "tv_ip": TV_IP, "service_active": _service_active(), "paired": paired,
        "connection_status": "connected" if paired else "unpaired" if key else "key_missing",
        "pairing_required": not paired, "last_pair_attempt": _RUNTIME["last_pair_attempt"],
        "last_pair_success": _RUNTIME["last_pair_success"], "last_error": _RUNTIME["last_error"],
        "key_source": source,
    }


@app.post("/api/lg-tv/pairing/request")
def pairing_request(payload: Dict[str, Any] = Body(default={})):
    del payload
    now = int(time.time())
    with _STATE_LOCK:
        thread = _JOB.get("thread")
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return JSONResponse({"detail": "pairing_job_active", **_job_public()}, status_code=409)
        last = _RUNTIME.get("last_pair_attempt")
        if last and now - int(last) < PAIR_RATE_LIMIT_SEC:
            return JSONResponse({"detail": "pairing_rate_limited", "retry_after_sec": PAIR_RATE_LIMIT_SEC - (now - int(last))}, status_code=429)
        _CANCEL.clear()
        worker = threading.Thread(target=_pair_worker, name="lg-tv-pairing", daemon=True)
        _JOB.update({"state": "connecting", "started_ts": now, "updated_ts": now, "expires_ts": now + PAIR_TIMEOUT_SEC,
                     "result": None, "error": None, "pending_key": None, "thread": worker})
        _RUNTIME["last_pair_attempt"] = now
        worker.start()
    return {"prompted": False, "registered": False, "timeout": False, "rejected": False, "connection_failed": False, "job": _job_public()}


@app.get("/api/lg-tv/pairing/job")
def pairing_job() -> Dict[str, Any]:
    return _job_public()


@app.post("/api/lg-tv/pairing/cancel")
def pairing_cancel(payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    del payload
    _CANCEL.set()
    with _STATE_LOCK:
        if _JOB.get("state") in {"connecting", "prompted"}:
            _JOB.update({"state": "failed", "result": "cancelled", "error": "cancelled", "pending_key": None, "updated_ts": int(time.time())})
    return {"ok": True, "job": _job_public()}


@app.post("/api/lg-tv/pairing/save")
def pairing_save(payload: Dict[str, Any] = Body(default={})):
    del payload
    with _STATE_LOCK:
        if _JOB.get("state") != "registered" or not isinstance(_JOB.get("pending_key"), str):
            return JSONResponse({"detail": "registered_pairing_job_required"}, status_code=409)
        candidate = str(_JOB["pending_key"])
    if not _validate_key(candidate):
        return JSONResponse({"detail": "new_key_validation_failed", "rolled_back": True}, status_code=422)
    previous = _read_key_file()
    backup: Optional[Path] = None
    try:
        backup = _atomic_write_key(candidate)
        if not _install_legacy_loader():
            raise RuntimeError("legacy_loader_install_failed")
        restarted = _run(["systemctl", "restart", SERVICE_NAME], 20).returncode == 0
        time.sleep(1)
        service_ok = restarted and _service_active()
        connection_ok = _validate_key(candidate)
        if not service_ok or not connection_ok:
            raise RuntimeError("post_save_validation_failed")
    except Exception as exc:
        _restore_key(backup, previous)
        try:
            _run(["systemctl", "restart", SERVICE_NAME], 20)
        except Exception:
            pass
        _RUNTIME["last_error"] = _safe_error(exc)
        return JSONResponse({"detail": "save_or_reconnect_failed", "rolled_back": True}, status_code=503)
    with _STATE_LOCK:
        _JOB.update({"state": "idle", "pending_key": None, "result": "saved", "error": None, "updated_ts": int(time.time())})
        _RUNTIME.update({"last_pair_success": int(time.time()), "last_error": None, "last_connection_error": None, "last_pairing_result": "saved"})
    return {"ok": True, "saved": True, "service_active": True, "connection_status": "connected", "rolled_back": False}


@app.get("/api/lg-tv/pairing/diagnostics")
def pairing_diagnostics() -> Dict[str, Any]:
    key, source = _current_key()
    return {
        "secure_websocket": True, "tv_reachable": _tcp_reachable(3000) or _tcp_reachable(3001),
        "websocket_port_reachable": _tcp_reachable(3001), "service_active": _service_active(),
        "current_key_present": bool(key), "current_key_source": source,
        "last_connection_error": _RUNTIME["last_connection_error"],
        "last_pairing_result": _RUNTIME["last_pairing_result"],
    }
