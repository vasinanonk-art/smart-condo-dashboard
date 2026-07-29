import asyncio
import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import app as app_module
from backend import dashboard_auth as auth
from backend import tplink_dashboard
from backend.tplink_camera_provider import TPLinkCameraObservation


app = app_module.app


def run(awaitable):
    return asyncio.run(awaitable)


def auth_environment():
    return {
        "DASHBOARD_AUTH_USERNAME": "admin",
        "DASHBOARD_AUTH_PASSWORD_HASH": "configured-for-session-test",
        "DASHBOARD_SESSION_SECRET": "epic-17-test-session-secret",
    }


def authenticated_client():
    client = TestClient(app)
    now = int(time.time())
    token = auth._sign({
        "u": "admin",
        "iat": now,
        "exp": now + 60,
        "csrf": "read-only-route-test",
    })
    client.cookies.set(auth.COOKIE_NAME, token)
    return client


def test_tplink_endpoints_require_dashboard_authentication():
    paths = (
        "/api/tplink/providers/status",
        "/api/tplink/providers/metadata",
        "/api/tplink/providers/capabilities",
        "/api/tplink/providers/diagnostics",
        "/api/tplink/cameras",
    )
    with patch.dict(os.environ, auth_environment(), clear=True):
        client = TestClient(app)
        for path in paths:
            response = client.get(path)
            assert response.status_code == 401
            assert response.json() == {"detail": "authentication required"}


def test_authenticated_provider_projection_is_read_only_and_fail_closed():
    with patch.dict(os.environ, auth_environment(), clear=True):
        run(tplink_dashboard.initialize_tplink_dashboard_provider())
        client = authenticated_client()

        status = client.get("/api/tplink/providers/status")
        metadata = client.get("/api/tplink/providers/metadata")
        capabilities = client.get("/api/tplink/providers/capabilities")
        diagnostics = client.get("/api/tplink/providers/diagnostics")

        assert status.status_code == 200
        assert status.json()["providers"]["tplink_camera"]["ready"] is True
        assert metadata.json()["providers"]["tplink_camera"] == {
            "provider_name": "TP-Link Camera Provider",
            "provider_version": "1.0.0",
            "api_version": "inventory-v1",
            "implementation_status": "read_only_skeleton",
        }
        matrix = capabilities.json()["providers"]["tplink_camera"]
        assert matrix["inventory"] == "Supported"
        assert matrix["health"] == "Supported"
        assert all(
            status == "Not Supported"
            for name, status in matrix.items()
            if name not in {"inventory", "health"}
        )
        assert diagnostics.json()["providers"]["tplink_camera"][
            "supported_capability_count"
        ] == 2
        run(tplink_dashboard.shutdown_tplink_dashboard_provider())


def test_camera_inventory_endpoint_uses_safe_connector_projection():
    original = tplink_dashboard._camera_provider._cameras
    tplink_dashboard._camera_provider._cameras = (
        TPLinkCameraObservation(
            id="living_camera",
            alias="Living Camera",
            model="Verified Model",
            serial="PRIVATE-SERIAL-5678",
            firmware="1.2.3",
            hardware_version="2.0",
            online=True,
        ),
    )
    try:
        payload = run(tplink_dashboard.tplink_camera_inventory())
    finally:
        tplink_dashboard._camera_provider._cameras = original

    assert payload["camera_count"] == 1
    assert payload["cameras"][0]["display_name"] == "Living Camera"
    assert payload["cameras"][0]["capabilities"] == []
    assert payload["cameras"][0]["metadata"]["serial_redacted"] == "***5678"
    assert "PRIVATE-SERIAL-5678" not in str(payload)


def test_no_write_or_operational_tplink_routes_are_registered():
    routes = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or ())
        if route.path.startswith("/api/tplink")
    }

    assert routes == {
        ("/api/tplink/providers/status", "GET"),
        ("/api/tplink/providers/metadata", "GET"),
        ("/api/tplink/providers/capabilities", "GET"),
        ("/api/tplink/providers/diagnostics", "GET"),
        ("/api/tplink/cameras", "GET"),
    }
