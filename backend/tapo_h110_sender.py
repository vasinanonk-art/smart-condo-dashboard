"""Guarded local sender for stored Tapo H110 IR keys.

The sender is inert unless explicitly enabled. It resolves checked-in logical
command tokens to an existing remote/key pair and never accepts raw IR data.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend import app as app_module
from backend import ir_framework
from backend import tapo_ir_local_bridge

app = app_module.app
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP_PATH = ROOT / "config" / "ir" / "tapo_h110_commands.json"
TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
Connector = Callable[[Mapping[str, str]], Awaitable[Any]]


class TapoH110SenderError(RuntimeError):
    pass


def _enabled() -> bool:
    return os.getenv("TAPO_IR_SENDER_ENABLED", "").strip().casefold() in {
        "1", "true", "yes", "on",
    }


def _map_path() -> Path:
    return Path(
        os.getenv("TAPO_IR_COMMAND_MAP_FILE", str(DEFAULT_MAP_PATH))
    ).expanduser()


def _required_configuration() -> dict[str, str]:
    raw = tapo_ir_local_bridge._configuration()
    fields = ("host", "username", "password", "device_id", "mac", "model")
    required = ("host", "username", "password", "mac", "model")
    config = {key: str(raw.get(key) or "").strip() for key in fields}
    if any(not config[key] for key in required):
        raise TapoH110SenderError("tapo_ir_sender_configuration_incomplete")
    if tapo_ir_local_bridge._normalize_model(config["model"]) != "h110":
        raise TapoH110SenderError("tapo_ir_sender_model_not_allowed")
    return config


def _load_mapping(path: Path | None = None) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads((path or _map_path()).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TapoH110SenderError("tapo_ir_command_map_missing") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise TapoH110SenderError("tapo_ir_command_map_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise TapoH110SenderError("tapo_ir_command_map_invalid")
    commands = payload.get("commands")
    if not isinstance(commands, dict) or not commands or len(commands) > 64:
        raise TapoH110SenderError("tapo_ir_command_map_invalid")
    result: dict[str, dict[str, str]] = {}
    selectors: set[tuple[str, str]] = set()
    for token, selector in commands.items():
        if not isinstance(token, str) or not TOKEN.fullmatch(token):
            raise TapoH110SenderError("tapo_ir_command_map_invalid")
        if not isinstance(selector, dict) or set(selector) != {
            "remote_name", "key_label",
        }:
            raise TapoH110SenderError("tapo_ir_command_map_invalid")
        remote_name = str(selector.get("remote_name") or "").strip()
        key_label = str(selector.get("key_label") or "").strip()
        if not remote_name or len(remote_name) > 80 or not key_label or len(key_label) > 80:
            raise TapoH110SenderError("tapo_ir_command_map_invalid")
        normalized = (_normalize_label(remote_name), _normalize_label(key_label))
        if normalized in selectors:
            raise TapoH110SenderError("tapo_ir_command_map_duplicate_selector")
        selectors.add(normalized)
        result[token] = {"remote_name": remote_name, "key_label": key_label}
    return result


def _decode_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        decoded = base64.b64decode(text, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        decoded = text
    return decoded.replace("\x00", "").strip()


def _normalize_label(value: Any) -> str:
    return " ".join(_decode_label(value).casefold().split())


async def _connect(config: Mapping[str, str]) -> Any:
    from kasa import Credentials, Discover  # type: ignore

    device = await Discover.discover_single(
        config["host"],
        credentials=Credentials(config["username"], config["password"]),
    )
    if device is None:
        raise TapoH110SenderError("tapo_ir_bridge_not_found")
    await device.update()
    return device


async def _close(device: Any) -> None:
    protocol = getattr(device, "protocol", None)
    close = getattr(protocol, "close", None)
    if callable(close):
        with suppress(Exception):
            await asyncio.wait_for(close(), timeout=1.0)


class TapoH110Sender:
    def __init__(
        self,
        config: Mapping[str, str],
        commands: Mapping[str, Mapping[str, str]],
        connector: Connector | None = None,
    ) -> None:
        self._config = dict(config)
        self._commands = {key: dict(value) for key, value in commands.items()}
        self._connector = connector or _connect
        self.last_outbound_attempts = 0

    def __call__(self, command_token: str, timeout: float) -> None:
        self.last_outbound_attempts = 0
        bounded_timeout = max(0.2, min(10.0, float(timeout)))
        try:
            asyncio.run(
                asyncio.wait_for(
                    self._send(command_token),
                    timeout=bounded_timeout,
                )
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError from exc

    def _verify_identity(self, device: Any) -> None:
        identity = tapo_ir_local_bridge._device_identity(
            device,
            tapo_ir_local_bridge._host_of(device, self._config["host"]),
        )
        comparison = tapo_ir_local_bridge._identity_comparison(
            identity,
            self._config,
            targeted=True,
        )
        required_matches = ("host_match", "model_match", "mac_match")
        matches = all(comparison.get(key) is True for key in required_matches)
        if self._config.get("device_id"):
            matches = matches and comparison.get("device_id_match") is True
        if not matches:
            raise TapoH110SenderError("tapo_ir_bridge_identity_mismatch")

    def _resolve_key(self, device: Any, command_token: str) -> tuple[Any, str]:
        selector = self._commands.get(command_token)
        if selector is None:
            raise TapoH110SenderError("tapo_ir_command_not_mapped")
        remote_name = _normalize_label(selector["remote_name"])
        key_label = _normalize_label(selector["key_label"])
        remote_matches = [
            child for child in getattr(device, "children", ())
            if str(getattr(child, "sys_info", {}).get("category") or "").casefold()
            == "ir.remote"
            and _normalize_label(getattr(child, "alias", "")) == remote_name
        ]
        if len(remote_matches) != 1:
            raise TapoH110SenderError("tapo_ir_remote_selector_mismatch")
        child = remote_matches[0]
        key_matches = [
            key for key in getattr(child, "sys_info", {}).get("key_list", ())
            if isinstance(key, Mapping)
            and _normalize_label(key.get("display_name")) == key_label
            and isinstance(key.get("name"), str)
            and 0 < len(key["name"]) <= 80
        ]
        if len(key_matches) != 1:
            raise TapoH110SenderError("tapo_ir_key_selector_mismatch")
        return child, str(key_matches[0]["name"])

    async def _send(self, command_token: str) -> None:
        device = None
        try:
            device = await self._connector(self._config)
            self._verify_identity(device)
            child, key_name = self._resolve_key(device, command_token)
            protocol = getattr(child, "protocol", None)
            query = getattr(protocol, "query", None)
            if not callable(query):
                raise TapoH110SenderError("tapo_ir_child_protocol_unavailable")
            request = {
                "multipleRequest": {
                    "requests": [
                        {
                            "method": "sendIrCmdById",
                            "params": {"name": key_name},
                        }
                    ]
                }
            }
            self.last_outbound_attempts = 1
            response = await query(request, retry_count=0)
            if not isinstance(response, Mapping) or "sendIrCmdById" not in response:
                raise TapoH110SenderError("tapo_ir_send_response_invalid")
        finally:
            if device is not None:
                await _close(device)


_STATUS_LOCK = threading.Lock()
_STATUS: dict[str, Any] = {
    "enabled": False,
    "configured": False,
    "ready": False,
    "mapping_count": 0,
    "guard_mode": "disabled_pending_physical_test",
    "last_error": None,
}


def _set_status(**changes: Any) -> None:
    with _STATUS_LOCK:
        _STATUS.update(changes)


def sender_status() -> dict[str, Any]:
    with _STATUS_LOCK:
        return dict(_STATUS)


def install_sender() -> None:
    enabled = _enabled()
    try:
        commands = _load_mapping()
        config = _required_configuration()
    except Exception as exc:
        _set_status(
            enabled=enabled,
            configured=False,
            ready=False,
            mapping_count=0,
            guard_mode="configuration_blocked",
            last_error=str(exc),
        )
        return
    _set_status(
        enabled=enabled,
        configured=True,
        ready=False,
        mapping_count=len(commands),
        guard_mode=(
            "enabled_pending_physical_verification"
            if enabled else "disabled_pending_physical_test"
        ),
        last_error=None,
    )
    if not enabled:
        return
    driver = ir_framework.DRIVERS.get("tapo_ir")
    if not isinstance(driver, ir_framework.TapoIRDriver):
        _set_status(last_error="tapo_ir_driver_unavailable")
        return
    driver.register_verified_sender(TapoH110Sender(config, commands))
    _set_status(ready=True, guard_mode="enabled")


@app.get("/api/tapo-ir/sender/status")
def get_sender_status() -> dict[str, Any]:
    return sender_status()


install_sender()
