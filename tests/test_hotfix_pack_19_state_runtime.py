import bcrypt
from fastapi.testclient import TestClient

from backend import mea_tariff_hotfix18 as h18
from backend.app_entry import app


def test_all_tariff_endpoints_share_canonical_run_state(monkeypatch):
    username = "hotfix19-test"
    password = "hotfix19-password"
    session_secret = "hotfix19-session-secret"
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", username)
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8"),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", session_secret)
    monkeypatch.setenv("DASHBOARD_COOKIE_SECURE", "false")

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

    client = TestClient(app, base_url="http://testserver")
    login = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]

    checked = client.post(
        "/api/tariff/check",
        headers={
            "origin": "http://testserver",
            "x-csrf-token": csrf_token,
        },
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
