"""Attach history persistence to the existing PJ-1103 store path.

No worker or polling loop is created. The wrapper records only successful online
samples and then delegates to the original bridge state store.
"""
from __future__ import annotations

from typing import Any, Mapping

from backend import app as app_module
from backend import electricity_history
from backend import pj1103_electricity_bridge as bridge


def _install() -> None:
    if getattr(app_module, "_electricity_history_hook_installed", False):
        return
    original = bridge._store_state

    def store_with_history(payload: Mapping[str, Any]) -> None:
        original(payload)
        if payload.get("online") is True and not payload.get("last_error"):
            try:
                electricity_history.append_success(payload)
            except Exception as exc:
                app_module.state["electricity_history_last_error"] = type(exc).__name__

    bridge._store_state = store_with_history
    app_module._electricity_history_hook_installed = True


_install()
