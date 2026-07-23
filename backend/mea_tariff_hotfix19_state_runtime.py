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

from fastapi import Request

from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_runtime as runtime
from backend import mea_tariff_hotfix19_debug_runtime as debug_runtime


# Save every original callable before any runtime symbol is monkey-patched. Canonical
# wrappers must only call these stable references so a later assignment cannot create
# a self-recursive lookup through the module globals.
_original_check = h18.tariff_check_hotfix18
_original_status_endpoint = runtime.tariff_status_071
_original_candidate_endpoint = runtime.tariff_candidate_071
_original_provider_debug_endpoint = debug_runtime.serialize_provider_debug

_DETAIL_CAPTURE_DEBUG_FIELDS = (
    "detail_fixture_guard_entered",
    "detail_fixture_requested_url",
    "detail_fixture_final_url",
    "detail_fixture_final_scheme",
    "detail_fixture_final_host",
    "detail_fixture_final_path",
    "detail_fixture_http_status",
    "detail_fixture_content_type",
    "detail_fixture_path_matches",
    "detail_fixture_exact_url_match",
    "detail_fixture_capture_status",
    "detail_fixture_capture_reason",
)


def _debug_object_snapshot(location: str) -> Dict[str, Any]:
    debug = h14._SAFE_DEBUG
    snapshot = {
        "location": location,
        "object_id": id(debug),
        "module": getattr(debug, "__module__", type(debug).__module__),
        "type": type(debug).__name__,
        "keys": sorted(str(key) for key in debug.keys()),
        "key_count": len(debug),
    }
    print(f"HOTFIX19.2 debug object {snapshot}", flush=True)
    return snapshot


def _registered_provider_debug_routes() -> list[Dict[str, Any]]:
    routes: list[Dict[str, Any]] = []
    for order, route in enumerate(h14.app.router.routes):
        if getattr(route, "path", None) != "/api/tariff/provider/debug":
            continue
        endpoint = getattr(route, "endpoint", None)
        routes.append({
            "module": getattr(endpoint, "__module__", "unknown"),
            "function": getattr(endpoint, "__name__", "unknown"),
            "object_id": id(endpoint) if endpoint is not None else None,
            "registration_order": order,
        })
    return routes


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


def tariff_check_canonical() -> Dict[str, Any]:
    result = _original_check()
    run = _write_canonical_run(result)
    return {**result, "run_id": run["run_id"], "checked_at": run["checked_at"]}


def tariff_status_canonical() -> Dict[str, Any]:
    payload = _original_status_endpoint()
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
    payload = _original_candidate_endpoint()
    run = _canonical_run_from_state(settings._load_maintenance())
    if run:
        payload.update({
            "run_id": run.get("run_id"),
            "checked_at": run.get("checked_at"),
            "status": run.get("status"),
            "diagnostics": copy.deepcopy(run.get("diagnostics") or {}),
        })
    return payload


def provider_debug_canonical(request: Request) -> Dict[str, Any]:
    state_snapshot = _debug_object_snapshot("backend.mea_tariff_hotfix19_state_runtime.provider_debug_canonical")
    payload = _original_provider_debug_endpoint()
    run = _canonical_run_from_state(settings._load_maintenance())
    if run:
        payload.update({
            "run_id": run.get("run_id"),
            "checked_at": run.get("checked_at"),
            "status": run.get("status"),
            "parser_error_code": run.get("error"),
            **copy.deepcopy(run.get("diagnostics") or {}),
        })
    # Project capture-guard diagnostics directly from the exact object mutated by the
    # runtime fetch. Use .get() for every key so missing values are explicitly null.
    payload.update({key: copy.deepcopy(h14._SAFE_DEBUG.get(key)) for key in _DETAIL_CAPTURE_DEBUG_FIELDS})
    snapshots = payload.get("debug_object_snapshots") if isinstance(payload.get("debug_object_snapshots"), Mapping) else {}
    snapshots = {**copy.deepcopy(dict(snapshots)), "state_runtime_provider_debug_canonical": state_snapshot}
    endpoint = request.scope.get("endpoint")
    route = request.scope.get("route")
    payload.update({
        "debug_object_identity": state_snapshot["object_id"],
        "debug_module": state_snapshot["module"],
        "debug_key_count": state_snapshot["key_count"],
        "debug_object_snapshots": snapshots,
        "request_url_path": request.url.path,
        "request_endpoint_module": getattr(endpoint, "__module__", "unknown"),
        "request_endpoint_function": getattr(endpoint, "__name__", "unknown"),
        "request_route_path": getattr(route, "path", None),
        "request_endpoint_object_id": id(endpoint) if endpoint is not None else None,
        "registered_provider_debug_routes": _registered_provider_debug_routes(),
    })
    object_ids = {
        name: item.get("object_id")
        for name, item in snapshots.items()
        if isinstance(item, Mapping) and item.get("object_id") is not None
    }
    if len(set(object_ids.values())) > 1:
        payload["debug_object_id_mismatch"] = object_ids
    return payload


# Remove duplicate GET registrations for the canonical read endpoints, then register
# exactly one owner for each. The POST check route is replaced in place.
for route in list(h14.app.router.routes):
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", set()) or set())
    if path == "/api/tariff/check" and "POST" in methods:
        route.endpoint = tariff_check_canonical
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = tariff_check_canonical
    elif path in {
        "/api/tariff/status",
        "/api/tariff/candidate",
        "/api/tariff/provider/debug",
    } and "GET" in methods:
        h14.app.router.routes.remove(route)

h14.app.add_api_route(
    "/api/tariff/status",
    tariff_status_canonical,
    methods=["GET"],
    name="tariff_status_hotfix19_canonical",
)
h14.app.add_api_route(
    "/api/tariff/candidate",
    tariff_candidate_canonical,
    methods=["GET"],
    name="tariff_candidate_hotfix19_canonical",
)
h14.app.add_api_route(
    "/api/tariff/provider/debug",
    provider_debug_canonical,
    methods=["GET"],
    name="provider_debug_hotfix19_canonical",
)

# Internal aliases point to the canonical wrappers, but the wrappers themselves only
# call the saved originals above and therefore cannot recurse through these names.
runtime.tariff_status_071 = tariff_status_canonical
runtime.tariff_candidate_071 = tariff_candidate_canonical
debug_runtime.serialize_provider_debug = provider_debug_canonical
h14.provider_debug = provider_debug_canonical
sync._TARIFF_CANONICAL_RUN_STATE = "maintenance.tariff_run"
