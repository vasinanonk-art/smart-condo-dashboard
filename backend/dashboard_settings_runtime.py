"""Replace legacy environment-only tariff status handlers with settings-aware views."""
from __future__ import annotations

from typing import Any, Callable, Dict

from backend import app as app_module
from backend import dashboard_settings as settings

app = app_module.app


def _replace_endpoint(path: str, methods: set[str], endpoint: Callable[..., Any]) -> bool:
    replaced = False
    for route in app.routes:
        route_methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == path and methods.issubset(route_methods):
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint
            replaced = True
    return replaced


def tariff_status_from_settings() -> Dict[str, Any]:
    tariff = settings.load_settings()["electricity"]["tariff"]
    valid = bool(tariff.get("tariff_name") and tariff.get("effective_date") and tariff.get("tiers"))
    return {
        "configured": valid,
        "valid": valid,
        "tariff_name": tariff.get("tariff_name") or None,
        "effective_date": tariff.get("effective_date") or None,
        "source": tariff.get("source") or "manual",
        "version": tariff.get("version") or None,
        "ft_rate": tariff.get("ft_rate") if valid else None,
        "service_charge": tariff.get("service_charge") if valid else None,
        "vat_percent": tariff.get("vat_percent") if valid else None,
        "minimum_charge": tariff.get("minimum_charge") if valid else None,
        "tier_count": len(tariff.get("tiers") or []),
        "diagnostics": {"reason": None if valid else "tariff_not_configured", "source": "settings_json"},
    }


def tariff_sync_status_from_settings() -> Dict[str, Any]:
    config = settings.load_settings()
    tariff = config["electricity"]["tariff"]
    maintenance = config["maintenance"]
    state = settings._load_maintenance()
    source = tariff.get("source") or "manual"
    enabled = bool(maintenance.get("tariff_sync_enabled"))
    status = state.get("tariff_status") or ("manual_update_required" if source == "manual" or not enabled else "provider_not_configured")
    return {
        "enabled": enabled,
        "source": source,
        "sync_interval_days": maintenance.get("tariff_sync_interval_days", 1),
        "sync_hour": maintenance.get("daily_hour", 3),
        "timezone": config["electricity"]["timezone"],
        "last_checked_ts": state.get("last_tariff_check_ts"),
        "last_updated_ts": state.get("last_tariff_update_ts"),
        "effective_date": tariff.get("effective_date") or None,
        "version": tariff.get("version") or None,
        "status": status,
        "diagnostics": {"reason": "verified_provider_not_configured"} if status == "provider_not_configured" else {},
    }


_replace_endpoint("/api/electricity/tariff/status", {"GET"}, tariff_status_from_settings)
_replace_endpoint("/api/electricity/tariff/sync-status", {"GET"}, tariff_sync_status_from_settings)


@app.get("/api/settings/electricity/status")
def electricity_configuration_status() -> Dict[str, Any]:
    config = settings.load_settings()
    state = settings._load_maintenance()
    try:
        from backend import electricity_billing_cycle as cycle
        period = cycle.billing_cycle_status().get("current_period")
    except Exception:
        period = None
    return {
        "billing_cycle": period,
        "billing_cycle_day": config["electricity"]["billing_cycle_day"],
        "timezone": config["electricity"]["timezone"],
        "coverage_percent": state.get("billing_coverage_percent"),
        "coverage_complete": state.get("billing_coverage_complete"),
        "history_starts": state.get("history_first_ts"),
        "history_ends": state.get("history_last_ts"),
        "tariff_version": config["electricity"]["tariff"].get("version") or None,
        "last_tariff_check": state.get("last_tariff_check_ts"),
        "last_history_prune": state.get("last_history_prune_ts"),
        "projection_status": state.get("projection_status"),
    }
