"""HOTFIX PACK 19 canonical runtime diagnostics endpoint.

Owns the single GET /api/tariff/provider/debug serializer after every earlier MEA
runtime layer is imported. No selector/filter behavior is changed here.
"""
from __future__ import annotations

import copy
from typing import Any, Dict

from backend import automatic_tariff_sync as sync
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_hotfix19_selector_runtime as selector_runtime


def runtime_call_chain() -> list[str]:
    provider = sync.PROVIDERS.get("mea")
    provider_name = type(provider).__module__ + "." + type(provider).__name__ if provider is not None else "missing"
    selected = h17.select_residential_detail_link
    selector_name = getattr(selected, "__module__", "unknown") + "." + getattr(selected, "__name__", "unknown")
    return [
        "GET /api/tariff/provider/debug",
        "backend.mea_tariff_hotfix19_debug_runtime.get_provider_debug",
        provider_name + ".fetch_latest",
        selector_name,
    ]


def serialize_provider_debug() -> Dict[str, Any]:
    identity = selector_runtime.selector_identity()
    payload = {
        "provider": "mea",
        "official_source_only": True,
        **copy.deepcopy(h14._SAFE_DEBUG),
        **identity,
        "selector_provider_class": type(sync.PROVIDERS.get("mea")).__module__ + "." + type(sync.PROVIDERS.get("mea")).__name__ if sync.PROVIDERS.get("mea") is not None else "missing",
        "runtime_call_chain": runtime_call_chain(),
    }
    return payload


def get_provider_debug() -> Dict[str, Any]:
    return serialize_provider_debug()


# Remove all duplicate registrations and leave exactly one canonical route owner.
for route in list(h14.app.routes):
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        h14.app.routes.remove(route)

h14.app.add_api_route(
    "/api/tariff/provider/debug",
    get_provider_debug,
    methods=["GET"],
    name="get_provider_debug_hotfix19",
)

# Keep module-level serializers aligned for internal callers.
h14.provider_debug = serialize_provider_debug
selector_runtime.provider_debug = serialize_provider_debug
