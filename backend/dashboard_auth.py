"""Session authentication and CSRF protection for Smart Condo Dashboard 8090."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional
from urllib.parse import quote

import bcrypt
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from backend import app as app_module

app = app_module.app
FRONTEND_DIR = Path(app_module.FRONTEND_DIR)
COOKIE_NAME = "smart_condo_session"
SESSION_MAX_AGE = max(300, int(os.getenv("DASHBOARD_SESSION_MAX_AGE_SEC", "43200")))
COOKIE_SECURE = os.getenv("DASHBOARD_COOKIE_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
MAX_ATTEMPTS = max(1, int(os.getenv("DASHBOARD_LOGIN_MAX_ATTEMPTS", "5")))
WINDOW_SEC = max(30, int(os.getenv("DASHBOARD_LOGIN_WINDOW_SEC", "300")))
LOCKOUT_SEC = max(30, int(os.getenv("DASHBOARD_LOGIN_LOCKOUT_SEC", "300")))

_attempt_lock = threading.RLock()
_attempts: Dict[str, Deque[int]] = defaultdict(deque)
_lockouts: Dict[str, int] = {}
_config_log_lock = threading.Lock()
_config_log_emitted = False

_PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/status", "/favicon.ico"}
_STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginRequest(BaseModel):
    username: str
    password: str
    next: Optional[str] = None


def _username() -> str:
    return os.getenv("DASHBOARD_AUTH_USERNAME", "").strip()


def _password_hash() -> str:
    return os.getenv("DASHBOARD_AUTH_PASSWORD_HASH", "").strip()


def _session_secret() -> str:
    return os.getenv("DASHBOARD_SESSION_SECRET", "").strip()


def configured() -> bool:
    return bool(_username() and _password_hash() and _session_secret())


def _log_not_configured_once() -> None:
    global _config_log_emitted
    with _config_log_lock:
        if not _config_log_emitted:
            print("dashboard authentication not configured", flush=True)
            _config_log_emitted = True


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: Dict[str, Any]) -> str:
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def _decode(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token or not configured() or "." not in token:
        return None
    body, supplied = token.rsplit(".", 1)
    expected = hmac.new(_session_secret().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(expected, _b64decode(supplied)):
            return None
        payload = json.loads(_b64decode(body))
    except Exception:
        return None
    now = int(time.time())
    if not isinstance(payload, dict) or int(payload.get("exp") or 0) <= now:
        return None
    if not hmac.compare_digest(str(payload.get("u") or ""), _username()):
        return None
    return payload


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _prune_attempts(ip: str, now: int) -> None:
    queue = _attempts[ip]
    while queue and now - queue[0] > WINDOW_SEC:
        queue.popleft()


def _is_locked(ip: str, now: int) -> int:
    with _attempt_lock:
        until = int(_lockouts.get(ip) or 0)
        if until <= now:
            _lockouts.pop(ip, None)
            return 0
        return until - now


def _record_failure(ip: str, now: int) -> int:
    with _attempt_lock:
        _prune_attempts(ip, now)
        queue = _attempts[ip]
        queue.append(now)
        if len(queue) >= MAX_ATTEMPTS:
            until = now + LOCKOUT_SEC
            _lockouts[ip] = until
            queue.clear()
            return LOCKOUT_SEC
        return 0


def _clear_failures(ip: str) -> None:
    with _attempt_lock:
        _attempts.pop(ip, None)
        _lockouts.pop(ip, None)


def _safe_next(value: Optional[str]) -> str:
    candidate = str(value or "/").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/"
    return candidate


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    expected_host = request.headers.get("host", "")
    if origin:
        try:
            return origin.split("//", 1)[-1].rstrip("/") == expected_host
        except Exception:
            return False
    if referer:
        try:
            return referer.split("//", 1)[-1].split("/", 1)[0] == expected_host
        except Exception:
            return False
    return False


def _api_unauthorized(detail: str = "authentication required") -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=401)


def _config_required_response(api: bool) -> JSONResponse | FileResponse:
    _log_not_configured_once()
    if api:
        return JSONResponse({"detail": "dashboard authentication not configured", "configured": False}, status_code=503)
    return FileResponse(FRONTEND_DIR / "auth_required.html", status_code=503)


@app.middleware("http")
async def dashboard_auth_middleware(request: Request, call_next):
    path = request.url.path
    is_api = path.startswith("/api/")
    is_public = path in _PUBLIC_PATHS or path.startswith("/assets/")

    if is_public:
        return await call_next(request)

    if not configured():
        return _config_required_response(is_api)

    session = _decode(request.cookies.get(COOKIE_NAME))
    if session is None:
        if is_api:
            return _api_unauthorized()
        destination = _safe_next(path + (f"?{request.url.query}" if request.url.query else ""))
        return RedirectResponse(url=f"/login?next={quote(destination, safe='/%?#=&')}", status_code=303)

    request.state.dashboard_user = session.get("u")
    request.state.dashboard_session = session

    if request.method.upper() in _STATE_METHODS:
        if not _same_origin(request):
            return JSONResponse({"detail": "csrf origin validation failed"}, status_code=403)
        supplied = request.headers.get("x-csrf-token", "")
        expected = str(session.get("csrf") or "")
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            return JSONResponse({"detail": "csrf token validation failed"}, status_code=403)

    return await call_next(request)


@app.get("/login")
def login_page() -> FileResponse:
    if not configured():
        _log_not_configured_once()
        return FileResponse(FRONTEND_DIR / "auth_required.html", status_code=503)
    return FileResponse(FRONTEND_DIR / "login.html")


@app.post("/api/auth/login")
def login(body: LoginRequest, request: Request) -> JSONResponse:
    if not configured():
        return JSONResponse({"detail": "dashboard authentication not configured", "configured": False}, status_code=503)
    now = int(time.time())
    ip = _client_ip(request)
    remaining = _is_locked(ip, now)
    if remaining:
        return JSONResponse({"detail": "too many login attempts", "retry_after_sec": remaining}, status_code=429)

    username_ok = hmac.compare_digest(body.username.strip(), _username())
    password_ok = False
    try:
        password_ok = bcrypt.checkpw(body.password.encode("utf-8"), _password_hash().encode("utf-8"))
    except Exception:
        password_ok = False

    if not (username_ok and password_ok):
        lockout = _record_failure(ip, now)
        payload: Dict[str, Any] = {"detail": "invalid credentials"}
        status = 401
        if lockout:
            payload = {"detail": "too many login attempts", "retry_after_sec": lockout}
            status = 429
        return JSONResponse(payload, status_code=status)

    _clear_failures(ip)
    csrf = secrets.token_urlsafe(32)
    token = _sign({"u": _username(), "iat": now, "exp": now + SESSION_MAX_AGE, "csrf": csrf, "nonce": secrets.token_urlsafe(12)})
    response = JSONResponse({"ok": True, "authenticated": True, "next": _safe_next(body.next), "csrf_token": csrf})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/api/auth/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True, "authenticated": False})
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, secure=COOKIE_SECURE, samesite="strict")
    return response


@app.get("/api/auth/status")
def auth_status(request: Request) -> Dict[str, Any]:
    if not configured():
        return {"configured": False, "authenticated": False}
    session = _decode(request.cookies.get(COOKIE_NAME))
    if session is None:
        return {"configured": True, "authenticated": False}
    return {
        "configured": True,
        "authenticated": True,
        "username": session.get("u"),
        "expires_at": session.get("exp"),
        "csrf_token": session.get("csrf"),
    }
