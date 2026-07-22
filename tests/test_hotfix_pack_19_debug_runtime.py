from fastapi.testclient import TestClient

from backend.app_entry import app


def test_provider_debug_route_is_single_and_canonical():
    routes = [
        route for route in app.router.routes
        if getattr(route, "path", None) == "/api/tariff/provider/debug"
        and "GET" in set(getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "backend.mea_tariff_hotfix19_debug_runtime"
    assert routes[0].endpoint.__name__ == "get_provider_debug"

    response = TestClient(app).get("/api/tariff/provider/debug")
    assert response.status_code == 200
    payload = response.json()
    assert payload["selector_module"] == "backend.mea_tariff_hotfix19"
    assert payload["selector_function"] == "select_residential_detail_link"
    assert payload["selector_version"]
    assert payload["selector_commit"]
    assert payload["runtime_route_map"]["debug_route"]["function"] == "get_provider_debug"
