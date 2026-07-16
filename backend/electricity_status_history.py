"""Enrich the existing electricity status with persisted daily/monthly usage."""
from __future__ import annotations

from typing import Any, Dict

from backend import app as app_module
from backend import electricity_history, electricity_provider


def _install() -> None:
    if getattr(app_module, "_electricity_status_history_installed", False):
        return
    original = electricity_provider.electricity_status

    def status_with_history(force: bool = False) -> Dict[str, Any]:
        payload = dict(original(force=force))
        summary = electricity_history.usage_summary(payload.get("power"))
        payload.update({
            "energy_today": summary.get("today_kwh"),
            "energy_yesterday": summary.get("yesterday_kwh"),
            "energy_this_month": summary.get("month_kwh"),
            "energy_last_month": summary.get("last_month_kwh"),
        })
        diagnostics = dict(payload.get("diagnostics") or {})
        diagnostics["history"] = summary.get("diagnostics")
        if (summary.get("diagnostics") or {}).get("insufficient_history"):
            diagnostics["history_status"] = "insufficient_history"
        payload["diagnostics"] = diagnostics
        return payload

    electricity_provider.electricity_status = status_with_history
    for route in app_module.app.routes:
        if getattr(route, "path", None) == "/api/electricity/status":
            def endpoint() -> Dict[str, Any]:
                return status_with_history()
            route.endpoint = endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = endpoint
            break
    app_module._electricity_status_history_installed = True


_install()
