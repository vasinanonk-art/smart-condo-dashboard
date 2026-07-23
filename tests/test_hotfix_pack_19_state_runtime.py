import bcrypt
from fastapi.testclient import TestClient

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix18 as h18
from backend.app_entry import app


_DETAIL_FIELDS = {
    "detail_fixture_guard_entered": True,
    "detail_fixture_requested_url": "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU",
    "detail_fixture_final_url": "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU",
    "detail_fixture_final_scheme": "https",
    "detail_fixture_final_host": "www.mea.or.th",
    "detail_fixture_final_path": "/our-services/service-rates/other/D5xEaEwgU",
    "detail_fixture_http_status": 200,
    "detail_fixture_content_type": "text/html",
    "detail_fixture_path_matches": True,
    "detail_fixture_exact_url_match": True,
    "detail_fixture_capture_status": "captured",
    "detail_fixture_capture_reason": None,
}
_FETCH_TRACE_FIELDS = {
    "fetch_call_stage": "residential_detail_fetch",
    "fetch_call_url": "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU",
    "fetch_return_stage": "residential_detail_fetch",
    "fetch_return_url": "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU",
    "fetch_return_http_status": 200,
    "fetch_return_content_type": "text/html",
    "fetch_exception_stage": "type_1_2_parser",
    "fetch_exception_type": "ValueError",
    "fetch_exception_message": "type_1_2_section_not_found",
}
_FT_FIELDS = {
    "ft_csv_header": "type,start,end,ft",
    "ft_csv_column_names": ["type", "start", "end", "ft"],
    "ft_csv_row_count": 3,
    "ft_candidate_rows": [{"row_index": 0, "ft_rate": 0.3972, "effective_from": "2026-05-01", "effective_to": "2026-08-31", "status": "currently_effective"}],
    "ft_selected_row": {"row_index": 0, "ft_rate": 0.3972, "effective_from": "2026-05-01", "effective_to": "2026-08-31", "status": "currently_effective"},
    "ft_rejected_rows": [{"row_index": 1, "reason": "missing_ft"}, {"row_index": 2, "reason": "future_effective_date"}],
    "ft_detected_effective_dates": [{"from": "2026-05-01", "to": "2026-08-31"}, {"from": "2026-09-01", "to": None}],
    "ft_detected_value_column": "ft",
    "ft_detected_ft_column": "ft",
    "ft_rejection_reason": "future_effective_date",
}


def _authenticated_client(monkeypatch):
    username = "hotfix19-test"
    password = "hotfix19-password"
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", username)
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8"),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "hotfix19-session-secret")
    monkeypatch.setenv("DASHBOARD_COOKIE_SECURE", "false")
    client = TestClient(app, base_url="http://testserver")
    login = client.post("/api/auth/login", json={"username": username, "password": password})
    assert login.status_code == 200
    return client, login.json()["csrf_token"]


def test_all_tariff_endpoints_share_canonical_run_state(monkeypatch):
    def fake_check():
        return {
            "checked": True,
            "status": "residential_detail_link_not_found",
            "provider": "mea",
            "last_error": "residential_detail_link_not_found",
            "diagnostics": {
                "error": "residential_detail_link_not_found",
                "parser_error_code": "residential_detail_link_not_found",
            },
        }

    monkeypatch.setattr(h18, "tariff_check_hotfix18", fake_check)

    from backend import mea_tariff_hotfix19_state_runtime as canonical
    monkeypatch.setattr(canonical, "_original_check", fake_check)

    client, csrf_token = _authenticated_client(monkeypatch)
    checked = client.post(
        "/api/tariff/check",
        headers={"origin": "http://testserver", "x-csrf-token": csrf_token},
    )
    assert checked.status_code == 200
    checked_payload = checked.json()

    status_response = client.get("/api/tariff/status")
    candidate_response = client.get("/api/tariff/candidate")
    debug_response = client.get("/api/tariff/provider/debug")
    assert status_response.status_code == 200
    assert candidate_response.status_code == 200
    assert debug_response.status_code == 200

    status = status_response.json()
    candidate = candidate_response.json()
    debug = debug_response.json()

    run_id = checked_payload["run_id"]
    checked_at = checked_payload["checked_at"]
    for payload in (status, candidate, debug):
        assert payload["run_id"] == run_id
        assert payload["checked_at"] == checked_at

    assert status["candidate_status"] == "residential_detail_link_not_found"
    assert candidate["status"] == "residential_detail_link_not_found"
    assert debug["status"] == "residential_detail_link_not_found"
    assert status["last_error"] == "residential_detail_link_not_found"
    assert candidate["diagnostics"]["parser_error_code"] == "residential_detail_link_not_found"
    assert debug["parser_error_code"] == "residential_detail_link_not_found"


def _assert_projected_unchanged(monkeypatch, values):
    client, _csrf_token = _authenticated_client(monkeypatch)
    previous = {key: h14._SAFE_DEBUG.get(key) for key in values}
    missing = {key for key in values if key not in h14._SAFE_DEBUG}
    try:
        h14._SAFE_DEBUG.update(values)
        response = client.get("/api/tariff/provider/debug")
        assert response.status_code == 200
        payload = response.json()
        for key, value in values.items():
            assert key in payload
            assert payload[key] == value
            assert type(payload[key]) is type(value)
    finally:
        for key, value in previous.items():
            if key in missing:
                h14._SAFE_DEBUG.pop(key, None)
            else:
                h14._SAFE_DEBUG[key] = value


def test_provider_debug_exposes_detail_capture_diagnostics_unchanged(monkeypatch):
    _assert_projected_unchanged(monkeypatch, _DETAIL_FIELDS)


def test_provider_debug_exposes_fetch_trace_diagnostics_unchanged(monkeypatch):
    _assert_projected_unchanged(monkeypatch, _FETCH_TRACE_FIELDS)


def test_provider_debug_exposes_ft_parser_diagnostics_unchanged(monkeypatch):
    _assert_projected_unchanged(monkeypatch, _FT_FIELDS)
