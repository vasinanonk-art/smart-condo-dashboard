"""Canonical HOTFIX PACK 19 tariff run state shared by all tariff endpoints.

This module does not alter parser, selector, scoring, or provider behavior. It only
normalizes one completed check result into a single persisted runtime object and makes
status, candidate, and debug responses read that same object.
"""
from __future__ import annotations

import copy
import time
import uuid
from typing import Any, Dict, Mapping

from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_runtime as runtime
from backend import mea_tariff_hotfix19_debug_runtime as debug_runtime


def _canonical_run_from_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    run = state.get("tariff_run") if isinstance(state.get("tariff_run"), Mapping) else {}
    return copy.deepcopy(dict(run))


def _write_canonical_run(result: Mapping[str, Any]) -> Dict[str, Any]:
    checked_at = int(time.time())
    run_id = uuid.uuid4().hex
    diagnostics = copy.deepcopy(result.get("diagnostics") if isinstance(result.get("diagnostics"), Mapping) else {})
    error = diagnostics.get("parser_error_code") or diagnostics.get("error") or result.get("last_error")
    status = result.get("status") or error or "unknown"
    state = settings._load_maintenance()
    runtime_state = state.get("tariff_sync") if isinstance(state.get("tariff_sync"), Mapping) else {}
    canonical = {
        "run_id": run_id,
        "checked_at": checked_at,
        "status": status,
        "error": error,
        "diagnostics": diagnostics,
        "provider": result.get("provider") or runtime_state.get("provider") or "mea",
        "candidate": copy.deepcopy(runtime_state.get("candidate")),
        "comparison": copy.deepcopy(runtime_state.get("comparison")),
    }
    state["tariff_run"] = canonical
    settings._save_maintenance(state)
    return copy.deepcopy(canonical)


_original_check = h18.tariff_check_hotfix18


def tariff_check_canonical() -> Dict[str, Any]:
    result = _original_check()
    run = _write_canonical_run(result)
    return {**result, "run_id": run["run_id"], "checked_at": run["checked_at"]}


def tariff_status_canonical() -> Dict[str, Any]:
    payload = runtime.tariff_status_071()
    run = _canonical_run_from_state(settings._load_maintenance())
    if run:
        payload.update({
            "run_id": run.get("run_id"),
            "checked_at": run.get("checked_at"),
            "candidate_status": run.get("status"),
            "last_error": run.get("error"),
            "diagnostics": copy.deepcopy(run.get("diagnostics") or {}),
        })
    return payload


def tariff_candidate_canonical() -> Dict[str, Any]:
    payload = runtime.tariff_candidate_071()
    run = _canonical_run_from_state(settings._load_maintenance())
    if run:
        payload.update({
            "run_id": run.get("run_id"),
            "checked_at": run.get("checked_at"),
            "status": run.get("status"),
            "diagnostics": copy.deepcopy(run.get("diagnostics") or {}),
        })
    return payload


def provider_debug_canonical() -> Dict[str, Any]:
    payload = debug_runtime.serialize_provider_debug()
    run = _canonical_run_from_state(settings._load_maintenance())
    if run:
        payload.update({
            "run_id": run.get("run_id"),
            "checked_at": run.get("checked_at"),
            "status": run.get("status"),
            "parser_error_code": run.get("error"),
            **copy.deepcopy(run.get("diagnostics") or {}),
        })
    return payload


for route in h14.app.routes:
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", set()) or set())
    endpoint = None
    if path == "/api/tariff/check" and "POST" in methods:
        endpoint = tariff_check_canonical
    elif path == "/api/tariff/status" and "GET" in methods:
        endpoint = tariff_status_canonical
    elif path == "/api/tariff/candidate" and "GET" in methods:
        endpoint = tariff_candidate_canonical
    elif path == "/api/tariff/provider/debug" and "GET" in methods:
        endpoint = provider_debug_canonical
    if endpoint is not None:
        route.endpoint = endpoint
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = endpoint

# Collapse internal readers onto the same canonical state-backed serializers.
runtime.tariff_status_071 = tariff_status_canonical
runtime.tariff_candidate_071 = tariff_candidate_canonical
debug_runtime.serialize_provider_debug = provider_debug_canonical
h14.provider_debug = provider_debug_canonical
sync._TARIFF_CANONICAL_RUN_STATE = "maintenance.tariff_run"
