#!/usr/bin/env python3
"""Validate recovery JSON without displaying secret values."""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.camera_inventory_schema import CameraConfigError, validate_camera_config

PLACEHOLDER = re.compile(r"<[^<>]+>")
REGIONS = {"as", "eu", "us", "cn"}
TEMPLATE_ROOT = Path(__file__).parent / "templates" / "root" / ".smart-condo-dashboard"
EXPECTED_SONOFF = [
    ("<SONOFF_DEVICE_ID_1>", "BASICR2", "BASICR2", 1),
    ("<SONOFF_DEVICE_ID_2>", "M5-2C-120W", "M5-2C-120W", 2),
    ("<SONOFF_DEVICE_ID_3>", "M5-3C-120W", "M5-3C-120W", 3),
    ("<SONOFF_DEVICE_ID_4>", "M5-1C-120W", "M5-1C-120W", 1),
]


def is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER.search(value))


def load_object(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{path}: file not found")
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid JSON ({type(exc).__name__})")
        return {}
    if not isinstance(value, dict):
        errors.append(f"{path}: top level must be an object")
        return {}
    return value


def required_text(
    item: dict[str, Any],
    key: str,
    location: str,
    errors: list[str],
    allow_placeholders: bool,
) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{location}.{key}: required non-empty string")
        return ""
    if not allow_placeholders and is_placeholder(value):
        errors.append(f"{location}.{key}: placeholder has not been replaced")
    return value.strip()


def validate_permissions(path: Path, warnings: list[str]) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    if mode != 0o600:
        warnings.append(f"{path}: recommended mode is 0600 (current mode {mode:04o})")


def validate_cameras(
    path: Path,
    allow_placeholders: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    payload = load_object(path, errors)
    if not payload:
        return
    try:
        validated = validate_camera_config(payload, placeholder_mode=allow_placeholders)
    except CameraConfigError as exc:
        errors.append(f"{path}: camera schema invalid ({exc})")
        return
    if len(validated["cameras"]) != 2:
        errors.append(f"{path}: exactly two camera entries are required")
    validate_permissions(path, warnings)


def validate_ewelink(
    path: Path,
    allow_placeholders: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    payload = load_object(path, errors)
    email = payload.get("email")
    phone = payload.get("phoneNumber")
    usable_email = isinstance(email, str) and bool(email.strip()) and (allow_placeholders or not is_placeholder(email))
    usable_phone = isinstance(phone, str) and bool(phone.strip()) and (allow_placeholders or not is_placeholder(phone))
    if allow_placeholders:
        if not isinstance(email, str) or not isinstance(phone, str):
            errors.append(f"{path}: email and phoneNumber template fields are required")
    elif usable_email == usable_phone:
        errors.append(f"{path}: retain and fill exactly one of email or phoneNumber")
    required_text(payload, "password", str(path), errors, allow_placeholders)
    country = required_text(payload, "countryCode", str(path), errors, allow_placeholders)
    region = required_text(payload, "region", str(path), errors, allow_placeholders)
    if not allow_placeholders and region not in REGIONS:
        errors.append(f"{path}.region: must be one of as, eu, us, or cn")
    if not allow_placeholders and country and not re.fullmatch(r"\+\d{1,4}", country):
        errors.append(f"{path}.countryCode: must use international form such as +66")
    use_config_app = payload.get("use_config_app")
    if not isinstance(use_config_app, bool):
        errors.append(f"{path}.use_config_app: boolean required")
    if use_config_app:
        required_text(payload, "app_id", str(path), errors, allow_placeholders)
        required_text(payload, "app_secret", str(path), errors, allow_placeholders)
    devices = payload.get("devices")
    if not isinstance(devices, list):
        errors.append(f"{path}.devices: array required")
    else:
        actual: list[tuple[str, str, str, int]] = []
        seen_device_ids: set[str] = set()
        for index, device in enumerate(devices):
            location = f"{path}:devices[{index}]"
            if not isinstance(device, dict):
                errors.append(f"{location}: must be an object")
                continue
            device_id = required_text(device, "deviceid", location, errors, allow_placeholders)
            name = required_text(device, "name", location, errors, False)
            model = required_text(device, "model", location, errors, False)
            if device_id in seen_device_ids:
                errors.append(f"{location}.deviceid: duplicate device id")
            seen_device_ids.add(device_id)
            gang_count = device.get("gang_count")
            if not isinstance(gang_count, int) or isinstance(gang_count, bool) or gang_count < 1:
                errors.append(f"{location}.gang_count: positive integer required")
                continue
            actual.append((device_id, name, model, gang_count))
        if allow_placeholders:
            if actual != EXPECTED_SONOFF:
                errors.append(f"{path}.devices: sanitized template mappings do not match")
        elif [(name, model, gangs) for _, name, model, gangs in actual] != [
            (name, model, gangs) for _, name, model, gangs in EXPECTED_SONOFF
        ]:
            errors.append(f"{path}.devices: model and gang mappings do not match")
    validate_permissions(path, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "cameras",
        type=Path,
        nargs="?",
        default=TEMPLATE_ROOT / "cameras.local.json.example",
    )
    parser.add_argument(
        "ewelink",
        type=Path,
        nargs="?",
        default=TEMPLATE_ROOT / "ewelink.local.json.example",
    )
    parser.add_argument(
        "--allow-placeholders", "--placeholder-mode",
        dest="allow_placeholders",
        action="store_true",
        help="validate template structure before secret placeholders are filled",
    )
    args = parser.parse_args()
    errors: list[str] = []
    warnings: list[str] = []
    validate_cameras(args.cameras, args.allow_placeholders, errors, warnings)
    validate_ewelink(args.ewelink, args.allow_placeholders, errors, warnings)
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Validation failed with {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Validation passed; no configuration values were printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
