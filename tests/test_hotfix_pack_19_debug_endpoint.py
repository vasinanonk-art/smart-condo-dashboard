from fastapi.testclient import TestClient

from backend.app_entry import app
from backend import mea_tariff_hotfix19_debug_runtime as debug_runtime


def test_real_provider_debug_endpoint_exposes_selector_identity():
    routes = [
        route for route in app.routes
        if getattr(route, "path", None) == "/api/tariff/provider/debug"
        and "GET" in set(getattr(route, "methods", set()) or set())
    ]
    assert len(routes) == 1
    assert routes[0].endpoint is debug_runtime.get_provider_debug

    response = TestClient(app).get("/api/tariff/provider/debug")
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
