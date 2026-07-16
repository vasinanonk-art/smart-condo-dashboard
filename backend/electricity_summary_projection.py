"""Add projected month-end billing to the existing electricity summary route."""
from __future__ import annotations

from typing import Any, Dict

from backend import app as app_module
from backend import electricity_history, electricity_provider


def _install() -> None:
    if getattr(app_module, "_electricity_summary_projection_installed", False):
        return

    def summary_endpoint() -> Dict[str, Any]:
        status = electricity_provider.electricity_status()
        payload = electricity_history.usage_summary(status.get("power"))
        projected_bill = electricity_history.calculate_bill(payload.get("estimated_month_end_kwh"))
        payload["estimated_month_end_bill"] = projected_bill.get("total")
        payload["projected_billing"] = projected_bill
        return payload

    for route in app_module.app.routes:
        if getattr(route, "path", None) == "/api/electricity/summary":
            route.endpoint = summary_endpoint
            if getattr(route, "dependant", None) is not None:
                route.dependant.call = summary_endpoint
            break
    app_module._electricity_summary_projection_installed = True


_install()
