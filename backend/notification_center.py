"""Authenticated, bounded notification lifecycle for the dashboard."""
from __future__ import annotations

import copy
import re
import threading
from typing import Any, Callable, Dict

from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import dashboard_settings as settings

app = app_module.app
_LOCK = threading.Lock()
_MAX_NOTIFICATIONS = 100
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")
_TYPES = {"info", "warning", "error", "success"}


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


def _safe_text(value: Any, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(value or "")).strip()[:limit]


def _kind(item: Dict[str, Any]) -> str:
    value = str(item.get("type") or item.get("severity") or "info").lower()
    return value if value in _TYPES else "warning"


def _source(item: Dict[str, Any]) -> str:
    explicit = _safe_text(item.get("source") or item.get("device"), 80)
    if explicit:
        return explicit
    kind = str(item.get("kind") or "")
    if any(word in kind for word in ("tariff", "billing", "electricity")):
        return "Electricity"
    if "camera" in kind:
        return "Cameras"
    if any(word in kind for word in ("lg", "tv", "pair")):
        return "LG TV"
    if any(word in kind for word in ("ir", "climate")):
        return "Climate"
    return "System"


def _public(item: Dict[str, Any]) -> Dict[str, Any] | None:
    identifier = str(item.get("id") or "")
    if not _SAFE_ID.fullmatch(identifier):
        return None
    return {
        "id": identifier,
        "type": _kind(item),
        "title": _safe_text(item.get("title") or "Dashboard notification", 120),
        "message": _safe_text(item.get("message") or item.get("detail"), 300),
        "source": _source(item),
        "created_ts": int(item.get("created_ts") or 0),
        "read": bool(item.get("read")),
    }


def _active(state: Dict[str, Any]) -> list[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in state.get("notifications", []):
        if not isinstance(item, dict) or item.get("dismissed"):
            continue
        public = _public(item)
        if public is not None:
            by_id[public["id"]] = public
    return sorted(by_id.values(), key=lambda item: item["created_ts"], reverse=True)[:_MAX_NOTIFICATIONS]


def notifications() -> Dict[str, Any]:
    with _LOCK:
        items = _active(settings._load_maintenance())
    return {
        "notifications": copy.deepcopy(items),
        "count": len(items),
        "unread_count": sum(not item["read"] for item in items),
    }


def _mutate(identifier: str | None, operation: str) -> Dict[str, Any]:
    if identifier is not None and not _SAFE_ID.fullmatch(identifier):
        return {"ok": False, "found": False}
    found = False
    with _LOCK:
        state = settings._load_maintenance()
        for item in state.get("notifications", []):
            if not isinstance(item, dict):
                continue
            if identifier is not None and item.get("id") != identifier:
                continue
            if operation == "read":
                item["read"] = True
            elif operation == "clear":
                item["dismissed"] = True
            found = True
        settings._save_maintenance(state)
    return {"ok": True, "found": found}


@app.post("/api/notifications/{notification_id}/read")
def read_notification(notification_id: str) -> Dict[str, Any]:
    return _mutate(notification_id, "read")


@app.post("/api/notifications/mark-all-read")
def read_all_notifications() -> Dict[str, Any]:
    return _mutate(None, "read")


@app.delete("/api/notifications/clear-all")
def clear_all_notifications() -> Dict[str, Any]:
    return _mutate(None, "clear")


@app.delete("/api/notifications/{notification_id}")
def clear_notification(notification_id: str):
    result = _mutate(notification_id, "clear")
    if not result["found"]:
        return JSONResponse({"detail": "notification_not_found"}, status_code=404)
    return result


_replace_endpoint("/api/notifications", {"GET"}, notifications)
