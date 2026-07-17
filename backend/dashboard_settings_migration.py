"""One-time migration of existing non-secret environment settings into settings.json."""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Dict

from backend import dashboard_settings as settings


def _integer(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _initial_settings() -> Dict[str, Any]:
    payload = copy.deepcopy(settings._DEFAULTS)
    electricity = payload["electricity"]
    maintenance = payload["maintenance"]
    electricity["billing_cycle_day"] = _integer("ELECTRICITY_BILLING_CYCLE_DAY", 2)
    electricity["timezone"] = os.getenv("ELECTRICITY_BILLING_TIMEZONE", "Asia/Bangkok").strip() or "Asia/Bangkok"
    payload["dashboard"]["timezone"] = electricity["timezone"]
    maintenance["daily_hour"] = _integer("ELECTRICITY_TARIFF_SYNC_HOUR", 3)
    maintenance["history_retention_days"] = _integer("ELECTRICITY_HISTORY_RETENTION_DAYS", 400)
    maintenance["tariff_sync_interval_days"] = _integer("ELECTRICITY_TARIFF_SYNC_INTERVAL_DAYS", 1)
    maintenance["tariff_sync_enabled"] = os.getenv("ELECTRICITY_TARIFF_SYNC_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}

    raw_tariff = os.getenv("ELECTRICITY_TARIFF_CONFIG_JSON", "").strip()
    if raw_tariff:
        try:
            tariff = json.loads(raw_tariff)
        except json.JSONDecodeError:
            tariff = None
        if isinstance(tariff, dict):
            electricity["tariff"] = {
                "tariff_name": tariff.get("tariff_name", ""),
                "source": tariff.get("source") or os.getenv("ELECTRICITY_TARIFF_SOURCE", "manual"),
                "effective_date": tariff.get("effective_date", ""),
                "version": tariff.get("version", ""),
                "tiers": tariff.get("tiers", []),
                "ft_rate": tariff.get("ft_rate", 0),
                "service_charge": tariff.get("service_charge", 0),
                "vat_percent": tariff.get("vat_percent", 7),
                "minimum_charge": tariff.get("minimum_charge", 0),
            }
    return payload


def migrate_once() -> bool:
    if settings.SETTINGS_PATH.exists():
        settings._apply_runtime(settings.load_settings())
        return False
    try:
        validated = settings.validate_settings(_initial_settings())
        settings._atomic_json_write(settings.SETTINGS_PATH, validated, backup=False)
        settings._apply_runtime(validated)
        return True
    except Exception:
        # Dashboard remains operational with validated in-memory defaults.
        settings._apply_runtime(settings.load_settings())
        return False


MIGRATED = migrate_once()
