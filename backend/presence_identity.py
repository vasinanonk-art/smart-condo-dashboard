"""Validated non-secret household presence identities stored in settings.json."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

PEOPLE = ("beer", "seem")
DEFAULT_IDENTITIES = {
    "beer": {"name": "Beer", "ip": "192.168.1.218", "mac": "E6:2C:F5:81:D1:DA"},
    "seem": {"name": "Seem", "ip": "192.168.1.171", "mac": "12:F2:0D:7A:6E:8E"},
}
_MAC = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")


def normalize_mac(value: Any) -> str:
    text = str(value or "").strip().replace("-", ":").upper()
    if not _MAC.fullmatch(text):
        raise ValueError("invalid_presence_mac")
    return text


def validate_identities(raw: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_presence_identities")
    result: dict[str, dict[str, str]] = {}
    ips: set[str] = set()
    macs: set[str] = set()
    for person in PEOPLE:
        item = raw.get(person)
        if not isinstance(item, Mapping):
            raise ValueError(f"missing_presence_identity_{person}")
        try:
            ip = str(ipaddress.IPv4Address(str(item.get("ip") or "").strip()))
        except ipaddress.AddressValueError as exc:
            raise ValueError("invalid_presence_ipv4") from exc
        mac = normalize_mac(item.get("mac"))
        if ip in ips:
            raise ValueError("duplicate_presence_ip")
        if mac in macs:
            raise ValueError("duplicate_presence_mac")
        ips.add(ip)
        macs.add(mac)
        result[person] = {"name": str(item.get("name") or person.capitalize()).strip()[:40] or person.capitalize(), "ip": ip, "mac": mac}
    return result


def settings_path() -> Path:
    root = Path(os.getenv("SMART_CONDO_DATA_DIR", str(Path.home() / ".smart-condo-dashboard"))).expanduser()
    return root / "settings.json"


def load_identities(path: Path | None = None) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        return validate_identities(payload.get("presence", {}).get("people", {}))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, AttributeError):
        return validate_identities(DEFAULT_IDENTITIES)
