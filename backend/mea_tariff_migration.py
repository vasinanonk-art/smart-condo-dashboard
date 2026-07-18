"""Non-destructive EPIC 07.1 settings migration."""
from __future__ import annotations

import json
from typing import Any, Mapping

from backend import dashboard_settings as settings
from backend import mea_tariff_provider as mea


def migrate() -> bool:
    if not settings.SETTINGS_PATH.exists():
        return False
    try:
        raw = json.loads(settings.SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, Mapping):
        return False
    changed = False
    migrated: dict[str, Any] = dict(raw)
    electricity = dict(migrated.get("electricity") or {})
    maintenance = dict(migrated.get("maintenance") or {})
    if "tariff_type" not in electricity:
        electricity["tariff_type"] = mea.EXPECTED_TARIFF_TYPE
        changed = True
    if "tariff_provider" not in maintenance:
        maintenance["tariff_provider"] = "mea"
        changed = True
    if "tariff_sync_enabled" not in maintenance:
        maintenance["tariff_sync_enabled"] = True
        changed = True
    if "tariff_sync_interval_days" not in maintenance:
        maintenance["tariff_sync_interval_days"] = 1
        changed = True
    if "tariff_auto_apply_mode" not in maintenance:
        maintenance["tariff_auto_apply_mode"] = "never"
        changed = True
    if not changed:
        return False
    migrated["electricity"] = electricity
    migrated["maintenance"] = maintenance
    settings.save_settings(migrated)
    return True


MIGRATED = migrate()
