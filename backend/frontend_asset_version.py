"""Serve dashboard HTML with one stable build-version query string per deploy."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from fastapi.responses import HTMLResponse

from backend import app as app_module

app = app_module.app
FRONTEND_DIR = Path(app_module.FRONTEND_DIR)
TOKEN = "__ASSET_VERSION__"
CHART_DEBUG_TOKEN = "__CHART_DEBUG__"


def _git_revision() -> str | None:
    root = FRONTEND_DIR.parent
    git_dir = root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref_path = git_dir / head[5:].strip()
            value = ref_path.read_text(encoding="utf-8").strip()
        else:
            value = head
        if value:
            return value[:12]
    except Exception:
        return None
    return None


def _mtime_revision() -> str:
    latest = 0
    try:
        for path in FRONTEND_DIR.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".js", ".css", ".html", ".svg", ".png", ".ico"}:
                latest = max(latest, int(path.stat().st_mtime_ns))
    except Exception:
        latest = 0
    return f"mtime-{latest:x}" if latest else "build-unknown"


def build_version() -> str:
    explicit = os.getenv("DASHBOARD_BUILD_VERSION", "").strip()
    if explicit:
        return "".join(char for char in explicit if char.isalnum() or char in {"-", "_", "."})[:80] or "build"
    return _git_revision() or _mtime_revision()


def chart_debug_enabled() -> bool:
    return os.getenv("DASHBOARD_CHART_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}


BUILD_VERSION = build_version()


def render_html(filename: str, status_code: int = 200) -> HTMLResponse:
    content = (FRONTEND_DIR / filename).read_text(encoding="utf-8")
    content = content.replace(TOKEN, BUILD_VERSION).replace(CHART_DEBUG_TOKEN, "true" if chart_debug_enabled() else "false")
    return HTMLResponse(content, status_code=status_code, headers={"Cache-Control": "no-cache"})


def _replace_route(path: str, endpoint: Callable[[], HTMLResponse]) -> None:
    for route in app.routes:
        if getattr(route, "path", None) != path:
            continue
        route.endpoint = endpoint
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = endpoint
        return


def dashboard_index() -> HTMLResponse:
    return render_html("index.html")


def dashboard_login() -> HTMLResponse:
    return render_html("login.html")


def _install() -> None:
    if getattr(app_module, "_frontend_asset_version_installed", False):
        return
    _replace_route("/", dashboard_index)
    _replace_route("/login", dashboard_login)
    try:
        from backend import dashboard_auth

        def config_required(api: bool):
            dashboard_auth._log_not_configured_once()
            if api:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "dashboard authentication not configured", "configured": False}, status_code=503)
            return render_html("auth_required.html", status_code=503)

        dashboard_auth._config_required_response = config_required
    except Exception:
        pass
    app_module.state["frontend_build_version"] = BUILD_VERSION
    app_module._frontend_asset_version_installed = True


_install()
