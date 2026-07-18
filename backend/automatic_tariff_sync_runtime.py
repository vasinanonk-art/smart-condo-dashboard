"""Runtime glue for EPIC 07 after all legacy maintenance wrappers are loaded."""
from __future__ import annotations

from typing import Any, Mapping

from backend import automatic_tariff_sync as sync


_original_audit = sync._audit


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


sync._audit = _audit_compatible
sync._check_due = _check_due_from_sync_state
