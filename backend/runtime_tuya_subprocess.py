"""Use an isolated executable for bounded Tuya zone commands.

Forking from FastAPI's worker thread can inherit locked synchronization state.
This module replaces only the internal bounded runner with a fresh Python
subprocess. Public routes and response contracts remain unchanged.
"""

import json
import os
import subprocess
import sys

from backend import runtime_fixes

WORKER_PATH = os.path.join(os.path.dirname(__file__), "tuya_zone_worker.py")


def _run_bounded_subprocess(device, body_data, preset):
    payload = {
        "device": {
            "id": device.get("id"),
            "ip": device.get("ip"),
            "key": device.get("key"),
            "version": device.get("version") or device.get("ver") or 3.3,
        },
        "action": body_data.get("action"),
        "value": body_data.get("value"),
        "h": body_data.get("h"),
        "s": body_data.get("s"),
        "v": body_data.get("v"),
        "preset": preset,
    }
    try:
        completed = subprocess.run(
            [sys.executable, WORKER_PATH],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=runtime_fixes.ZONE_DEVICE_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout"}
    except Exception:
        return {"ok": False, "error": "command failed"}

    if completed.returncode != 0:
        return {"ok": False, "error": "command failed"}
    try:
        result = json.loads((completed.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": "command failed"}
    return result if isinstance(result, dict) else {"ok": False, "error": "command failed"}


runtime_fixes._run_bounded = _run_bounded_subprocess
