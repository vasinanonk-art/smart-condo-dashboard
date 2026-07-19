"""Runtime hardening for pywebostv 0.8.9 registration states.

pywebostv 0.8.9 yields WebOSClient.PROMPTED / WebOSClient.REGISTERED from
WebOSClient.register().  Keep pairing and stored-key validation aligned with those
real constants without changing API payloads, key handling, rollback, or LG commands.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from pywebostv.connection import WebOSClient

from backend import lg_tv_pairing as pairing


def _webos_register_089(store: Dict[str, Any], cancel: Optional[threading.Event] = None) -> str:
    client = WebOSClient(pairing.TV_IP, secure=True)
    client.connect()
    deadline = time.monotonic() + pairing.PAIR_TIMEOUT_SEC
    prompted = False
    for registration in client.register(store):
        if cancel and cancel.is_set():
            raise RuntimeError("cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("pairing_timeout")
        if registration == WebOSClient.PROMPTED:
            prompted = True
            with pairing._STATE_LOCK:
                pairing._JOB.update({"state": "prompted", "updated_ts": int(time.time())})
        elif registration == WebOSClient.REGISTERED:
            return "registered"
    return "prompted" if prompted else "connection_failed"


def _validate_key_089(value: str) -> bool:
    store = {"client_key": value}
    try:
        valid = _webos_register_089(store) == "registered" and store.get("client_key") == value
        if valid:
            with pairing._STATE_LOCK:
                pairing._RUNTIME["last_connection_error"] = None
                pairing._RUNTIME["last_error"] = None
            return True
        return False
    except Exception as exc:
        with pairing._STATE_LOCK:
            pairing._RUNTIME["last_connection_error"] = pairing._safe_error(exc)
        return False


# Patch the functions consumed by status, pairing jobs, save validation and rollback.
pairing._webos_register = _webos_register_089
pairing._validate_key = _validate_key_089
