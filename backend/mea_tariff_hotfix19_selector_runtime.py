"""HOTFIX PACK 19 runtime selector binding and identity diagnostics.

This module is loaded after all MEA parser/filter layers. It makes the production
provider and tests share the exact same selector callable and exposes only safe
identity metadata.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict

from backend import automatic_tariff_sync as sync
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_hotfix19 as h19

SELECTOR_VERSION = "mea-1.5-runtime-bound"


def _selector_commit() -> str:
    configured = str(os.getenv("DASHBOARD_GIT_COMMIT") or "").strip()
    if configured:
        return configured[:40]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, timeout=1.0,
        ).decode("ascii", errors="ignore").strip()[:40] or "unknown"
    except Exception:
        return "unknown"


def selector_identity() -> Dict[str, Any]:
    selected = h17.select_residential_detail_link
    return {
        "selector_module": getattr(selected, "__module__", "unknown"),
        "selector_function": getattr(selected, "__name__", "unknown"),
        "selector_version": SELECTOR_VERSION,
        "selector_commit": _selector_commit(),
    }


# HOTFIX 17's provider method resolves this module global at request time. Bind it
# explicitly after HOTFIX 18, HOTFIX 19 and the strict path guard have loaded.
AUTHORITATIVE_SELECTOR = h19.select_residential_detail_link
h17.select_residential_detail_link = AUTHORITATIVE_SELECTOR
h18.select_residential_detail_link = AUTHORITATIVE_SELECTOR
h17.PARSER_VERSION = SELECTOR_VERSION
h18.PARSER_VERSION = SELECTOR_VERSION
h19.PARSER_VERSION = SELECTOR_VERSION
h16.PARSER_VERSION = SELECTOR_VERSION
h14._SAFE_DEBUG.update(selector_identity())

_original_debug = h19.provider_debug


def provider_debug() -> Dict[str, Any]:
    payload = _original_debug()
    identity = selector_identity()
    h14._SAFE_DEBUG.update(identity)
    return {**payload, **identity}


h19.provider_debug = provider_debug
h18.provider_debug = provider_debug
h16.provider_debug = provider_debug

# Replace the debug endpoint so production reports the callable actually bound into
# the provider call chain.
for route in h14.app.routes:
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = provider_debug
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = provider_debug

# Safe startup invariant: the registered MEA provider must be the HOTFIX 17 class,
# whose fetch_latest resolves h17.select_residential_detail_link dynamically.
provider = sync.PROVIDERS.get("mea")
h14._SAFE_DEBUG["selector_provider_class"] = type(provider).__module__ + "." + type(provider).__name__ if provider is not None else "missing"
