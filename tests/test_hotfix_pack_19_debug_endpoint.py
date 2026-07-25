import bcrypt
from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import mea_tariff_hotfix19_debug_runtime as debug_runtime


def _authenticated_client(monkeypatch):
    username = "hotfix19-debug"
    password = "hotfix19-password"
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", username)
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "hotfix19-debug-secret")
    monkeypatch.setenv("DASHBOARD_COOKIE_SECURE", "false")
    client = TestClient(app, base_url="http://testserver")
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return client


def test_real_provider_debug_endpoint_exposes_selector_identity(monkeypatch):
    routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tariff/provider/debug"
        and "GET" in set(getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].endpoint is debug_runtime.get_provider_debug

    response = _authenticated_client(monkeypatch).get("/api/tariff/provider/debug")
    assert response.status_code == 200
    payload = response.json()
    for key in (
        "selector_module",
        "selector_function",
        "selector_version",
        "selector_commit",
    ):
        assert key in payload
        assert payload[key]

    assert payload["selector_module"] == "backend.mea_tariff_hotfix19"
    assert payload["selector_function"] == "select_residential_detail_link"
    assert payload["runtime_call_chain"][-1] == (
        "backend.mea_tariff_hotfix19.select_residential_detail_link"
    )
    assert payload["request_endpoint_function"] == "get_provider_debug"
    assert "state_runtime_provider_debug_canonical" in payload["debug_object_snapshots"]


def test_reported_check_owner_matches_active_route(monkeypatch):
    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tariff/check"
        and "POST" in set(getattr(route, "methods", set()) or set())
    )
    endpoint_name = f"{route.endpoint.__module__}.{route.endpoint.__name__}"

    response = _authenticated_client(monkeypatch).get("/api/tariff/provider/debug")
    assert response.status_code == 200
    payload = response.json()

    assert route.endpoint.__name__ == "tariff_check_canonical"
    assert payload["runtime_route_map"]["check_route"]["function"] == route.endpoint.__name__
    assert payload["runtime_call_chain"][1] == endpoint_name
