"""HOTFIX PACK 19 canonical runtime route map.

This module owns the final provider debug route only. It does not change selector
or parser behavior.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from backend import automatic_tariff_sync as sync
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_hotfix19_selector_runtime as selector_runtime


def runtime_route_map() -> Dict[str, Any]:
    provider = sync.PROVIDERS.get("mea")
    provider_class = (
        type(provider).__module__ + "." + type(provider).__name__
        if provider is not None else "missing"
    )
    selected = h17.select_residential_detail_link
    selector_name = (
        getattr(selected, "__module__", "unknown")
        + "."
        + getattr(selected, "__name__", "unknown")
    )
    return {
        "debug_route": {
            "file": "backend/mea_tariff_hotfix19_debug_runtime.py",
            "function": "get_provider_debug",
            "apirouter": "backend.app.app.router",
            "registration": "app.add_api_route('/api/tariff/provider/debug', get_provider_debug, methods=['GET'])",
        },
        "check_route": {
            "file": "backend/mea_tariff_hotfix18.py",
            "function": "tariff_check_hotfix18",
            "apirouter": "backend.app.app.router",
            "registration": "existing /api/tariff/check route endpoint replaced at import time",
        },
        "provider": provider_class,
        "selector": selector_name,
        "call_chain": [
            "POST /api/tariff/check",
            "backend.mea_tariff_hotfix18.tariff_check_hotfix18",
            "backend.mea_tariff_hotfix16_runtime.tariff_check_hotfix16",
            "backend.mea_tariff_runtime.tariff_check_071",
            "backend.automatic_tariff_sync.check_tariff",
            provider_class + ".fetch_latest",
            selector_name,
        ],
    }


def serialize_provider_debug() -> Dict[str, Any]:
    return {
        "provider": "mea",
        "official_source_only": True,
        **copy.deepcopy(h14._SAFE_DEBUG),
        **selector_runtime.selector_identity(),
        "runtime_route_map": runtime_route_map(),
    }


def get_provider_debug() -> Dict[str, Any]:
    return serialize_provider_debug()


# Remove every existing GET registration for this path, then register one owner.
for route in list(h14.app.router.routes):
    if (
        getattr(route, "path", None) == "/api/tariff/provider/debug"
        and "GET" in set(getattr(route, "methods", set()) or set())
    ):
        h14.app.router.routes.remove(route)

h14.app.add_api_route(
    "/api/tariff/provider/debug",
    get_provider_debug,
    methods=["GET"],
    name="get_provider_debug_hotfix19",
)

# Eliminate serializer duplication for internal callers.
h14.provider_debug = serialize_provider_debug
selector_runtime.provider_debug = serialize_provider_debug
