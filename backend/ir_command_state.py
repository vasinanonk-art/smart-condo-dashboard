"""Persistent, generation-protected assumed state for successful IR commands."""
from __future__ import annotations

import copy
import json
import os
import re
import threading
from pathlib import Path
from typing import Any


_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DEFAULT_STATE_PATH = Path(
    os.getenv(
        "IR_LAST_COMMANDED_STATE_FILE",
        "/root/.smart-condo-dashboard/state/ir_last_commanded.json",
    )
).expanduser()


class AssumedCommandStateStore:
    def __init__(self, path: Path | None = DEFAULT_STATE_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._devices: dict[str, dict[str, Any]] = {}
        self._generations: dict[str, int] = {}
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        devices = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(devices, dict):
            return
        for device_id, raw in devices.items():
            if not _SAFE_ID.fullmatch(str(device_id)) or not isinstance(raw, dict):
                continue
            state = self._normalize_record(raw)
            if state:
                self._devices[str(device_id)] = state
                self._generations[str(device_id)] = int(raw.get("generation") or 0)

    @staticmethod
    def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
        commanded = raw.get("last_commanded")
        if not isinstance(commanded, dict):
            commanded = {}
        clean: dict[str, int] = {}
        if commanded.get("power") in (0, 1):
            clean["power"] = int(commanded["power"])
        temperature = commanded.get("target_temperature")
        if isinstance(temperature, int) and not isinstance(temperature, bool) and 18 <= temperature <= 30:
            clean["target_temperature"] = temperature
        timestamp = raw.get("last_commanded_at")
        correlation = str(raw.get("last_commanded_correlation_id") or "")
        if not clean or not isinstance(timestamp, int) or timestamp <= 0:
            return {}
        if not _SAFE_ID.fullmatch(correlation):
            return {}
        return {
            "last_commanded": clean,
            "last_commanded_at": timestamp,
            "last_commanded_correlation_id": correlation,
        }

    def begin(self, device_id: str) -> int:
        if not _SAFE_ID.fullmatch(device_id):
            raise ValueError("invalid_ir_device_id")
        with self._lock:
            generation = self._generations.get(device_id, 0) + 1
            self._generations[device_id] = generation
            return generation

    def commit(
        self,
        device_id: str,
        generation: int,
        command_type: str,
        value: int,
        timestamp: int,
        correlation_id: str,
    ) -> dict[str, Any] | None:
        if command_type == "power" and value in (0, 1):
            state_key = "power"
        elif (
            command_type == "temperature"
            and isinstance(value, int)
            and not isinstance(value, bool)
            and 18 <= value <= 30
        ):
            state_key = "target_temperature"
        else:
            raise ValueError("invalid_ir_assumed_state")
        if not _SAFE_ID.fullmatch(correlation_id):
            raise ValueError("invalid_ir_correlation_id")
        with self._lock:
            if generation != self._generations.get(device_id):
                return None
            previous = self._devices.get(device_id, {})
            commanded = copy.deepcopy(previous.get("last_commanded") or {})
            commanded[state_key] = int(value)
            record = {
                "last_commanded": commanded,
                "last_commanded_at": int(timestamp),
                "last_commanded_correlation_id": correlation_id,
            }
            devices = copy.deepcopy(self._devices)
            devices[device_id] = record
            self._persist(devices)
            self._devices = devices
            return copy.deepcopy(record)

    def _persist(self, devices: dict[str, dict[str, Any]]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        payload = {
            "schema_version": 1,
            "devices": {
                device_id: {
                    **record,
                    "generation": self._generations.get(device_id, 0),
                }
                for device_id, record in devices.items()
            },
        }
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def snapshot(self, device_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._devices.get(device_id) or {})
