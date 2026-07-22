from fastapi.testclient import TestClient

from backend import mea_tariff_hotfix18 as h18
from backend.app_entry import app


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

    client = TestClient(app)
    checked = client.post("/api/tariff/check")
    assert checked.status_code == 200
    checked_payload = checked.json()

    status = client.get("/api/tariff/status").json()
    candidate = client.get("/api/tariff/candidate").json()
    debug = client.get("/api/tariff/provider/debug").json()

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
