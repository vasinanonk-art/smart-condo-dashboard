"""Runtime glue for EPIC 07 after all legacy maintenance wrappers are loaded."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from backend import automatic_tariff_sync as sync


_original_audit = sync._audit
_original_compare_version = sync.compare_version


def _audit_compatible(event: str, result: str, detail: str = "", version: str | None = None, **extra: Any) -> None:
    provider = extra.get("provider")
    if provider and not detail:
        detail = f"provider={provider}"
    _original_audit(event, result, detail, version)


def _check_due_from_sync_state(config: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    if not config["maintenance"].get("tariff_sync_enabled"):
        return False
    runtime = state.get("tariff_sync") if isinstance(state.get("tariff_sync"), Mapping) else {}
    last = runtime.get("last_check_ts")
    return not last or sync.time.time() >= sync._next_check_ts(config, int(last))


def _strict_newer_comparison(candidate: Mapping[str, Any], active: Mapping[str, Any]) -> int:
    """Only a newer effective date/version may become a candidate.

    An exact tariff remains equal so the unchanged notification is produced. A tariff
    with the same date/version but changed monetary values is rejected as not newer.
    """
    order = _original_compare_version(candidate, active)
    if order != 0:
        return order
    fields = ("tiers", "ft_rate", "service_charge", "vat_percent", "effective_date", "version")
    return 0 if all(active.get(field) == candidate.get(field) for field in fields) else -1


def _maintenance_with_persistent_tariff_state():
    previous = sync.settings._load_maintenance()
    previous_sync = copy.deepcopy(previous.get("tariff_sync") or {})
    snapshot = sync._original_maintenance_once()
    if previous_sync:
        snapshot["tariff_sync"] = previous_sync
    config = sync.settings.load_settings()
    if _check_due_from_sync_state(config, snapshot):
        sync.check_tariff(force=True, request_path=False)
        return sync.settings._load_maintenance()
    runtime = copy.deepcopy(snapshot.get("tariff_sync") or {})
    runtime.setdefault("provider", config["maintenance"].get("tariff_provider", "manual"))
    runtime["next_check_ts"] = sync._next_check_ts(config, runtime.get("last_check_ts"))
    snapshot["tariff_sync"] = runtime
    sync.settings._save_maintenance(snapshot)
    return snapshot


sync._audit = _audit_compatible
sync._check_due = _check_due_from_sync_state
sync.compare_version = _strict_newer_comparison
sync.settings._maintenance_once = _maintenance_with_persistent_tariff_state
