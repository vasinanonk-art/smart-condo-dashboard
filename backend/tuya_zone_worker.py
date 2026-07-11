"""Isolated Tuya zone command worker.

Reads one JSON request from stdin and writes one safe JSON result to stdout.
Secrets stay inside the subprocess input and are never printed.
"""

import json
import sys
from typing import Any

import tinytuya


def safe_error(value: Any) -> str:
    text = str(value or "").lower()
    if "timeout" in text:
        return "timeout"
    if "905" in text or "unreachable" in text or "no reply" in text:
        return "device unreachable"
    return "command failed"


def reply_ok(value: Any) -> bool:
    return isinstance(value, dict) and not value.get("Error") and not value.get("Err")


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def hsv_hex(h: int, s: int, v: int) -> str:
    return f"{clamp(h, 0, 360):04x}{clamp(s, 0, 1000):04x}{clamp(v, 0, 1000):04x}"


def main() -> None:
    payload = json.load(sys.stdin)
    device = payload["device"]
    action = str(payload.get("action") or "").strip().lower()
    preset = payload.get("preset") if isinstance(payload.get("preset"), dict) else None

    client = tinytuya.Device(device["id"], device["ip"], device["key"])
    client.set_version(float(device.get("version") or device.get("ver") or 3.3))
    try:
        client.set_socketTimeout(1.5)
    except Exception:
        pass

    steps = []

    def set_dp(name: str, dp: int, value: Any) -> bool:
        try:
            result = client.set_status(value, dp)
            ok = reply_ok(result)
            item = {"step": name, "ok": ok}
            if not ok:
                item["error"] = safe_error(result)
            steps.append(item)
            return ok
        except Exception as exc:
            steps.append({"step": name, "ok": False, "error": safe_error(exc)})
            return False

    outcomes = []
    if action == "brightness":
        outcomes.append(set_dp("brightness", 22, clamp(payload["value"], 10, 1000)))
    elif action in ("temperature", "temp", "cct"):
        outcomes.append(set_dp("mode", 21, "white"))
        outcomes.append(set_dp("temperature", 23, clamp(payload["value"], 0, 1000)))
    elif action == "rgb":
        outcomes.append(set_dp("mode", 21, "colour"))
        outcomes.append(set_dp("rgb", 24, hsv_hex(payload["h"], payload["s"], payload["v"])))
    elif action == "preset" and preset:
        mode = str(preset.get("mode") or "")
        if mode == "white":
            outcomes.append(set_dp("mode", 21, "white"))
            outcomes.append(set_dp("brightness", 22, clamp(preset["brightness"], 10, 1000)))
            outcomes.append(set_dp("temperature", 23, clamp(preset["temperature"], 0, 1000)))
        elif mode == "brightness":
            outcomes.append(set_dp("brightness", 22, clamp(preset["value"], 10, 1000)))
        elif mode == "temperature":
            outcomes.append(set_dp("mode", 21, "white"))
            outcomes.append(set_dp("temperature", 23, clamp(preset["value"], 0, 1000)))
        elif mode == "colour":
            outcomes.append(set_dp("mode", 21, "colour"))
            outcomes.append(set_dp("rgb", 24, hsv_hex(preset["h"], preset["s"], preset["v"])))
        else:
            steps.append({"step": "preset", "ok": False, "error": "capability not supported"})
            outcomes.append(False)
    else:
        steps.append({"step": action or "command", "ok": False, "error": "command failed"})
        outcomes.append(False)

    print(json.dumps({"ok": bool(outcomes) and all(outcomes), "steps": steps}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": safe_error(exc)}), flush=True)
