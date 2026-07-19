"""Runtime/status glue for HOTFIX PACK 16."""
from __future__ import annotations

import copy
from typing import Any, Dict, Mapping

from backend import app as app_module
from backend import dashboard_settings as settings
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_runtime as runtime

app = app_module.app


def tariff_status_hotfix16() -> Dict[str, Any]:
    payload = runtime.tariff_status_071()
    saved = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
    debug = h16.provider_debug()
    diagnostics = {**copy.deepcopy(saved), **{k: v for k, v in debug.items() if k not in {"provider", "official_source_only"}}}
    code = diagnostics.get("parser_error_code")
    payload["diagnostics"] = diagnostics
    payload["last_error"] = code or payload.get("last_error")
    if code:
        payload["candidate_status"] = code
        payload["provider_available"] = code not in {
            "source_fetch_failed", "residential_detail_fetch_failed"
        }
    return payload


def tariff_check_hotfix16() -> Dict[str, Any]:
    result = runtime.tariff_check_071()
    code = h14._SAFE_DEBUG.get("parser_error_code")
    state = settings._load_maintenance()
    sync_state = state.setdefault("tariff_sync", {})
    if code:
        sync_state["last_error"] = code
        diagnostics = sync_state.get("diagnostics") if isinstance(sync_state.get("diagnostics"), Mapping) else {}
        sync_state["diagnostics"] = {**diagnostics, "error": code, "parser_error_code": code}
        sync_state["status"] = code
        state["tariff_status"] = code
        settings._save_maintenance(state)
        result = {**result, "status": code, "diagnostics": {**(result.get("diagnostics") or {}), "error": code, "parser_error_code": code}}
    return result


for route in app.routes:
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", set()) or set())
    endpoint = None
    if path == "/api/tariff/status" and "GET" in methods:
        endpoint = tariff_status_hotfix16
    elif path == "/api/tariff/check" and "POST" in methods:
        endpoint = tariff_check_hotfix16
    if endpoint is not None:
        route.endpoint = endpoint
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = endpoint
