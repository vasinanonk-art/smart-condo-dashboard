import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
import pytest
from fastapi.testclient import TestClient

from backend import dashboard_settings as settings
from backend import electricity_billing_cycle as billing
from backend import electricity_reconciliation as reconciliation
from backend.app_entry import app


TZ = ZoneInfo("Asia/Bangkok")


def ts(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=TZ).timestamp())


def payload(start="2026-08-02T00:00:00", end="2026-09-02T00:00:00", cost=1000.0, kwh=250.0, missing_start=False, projected=1400.0):
    return {
        "billing_period_start": ts(start),
        "billing_period_end": ts(end),
        "actual_partial_cost": cost,
        "actual_partial_usage_kwh": kwh,
        "projected_cycle_bill": projected,
        "projection_status": "available" if projected is not None else "insufficient_projection_history",
        "coverage": {"missing_start": missing_start, "coverage_percent": 50.0, "complete": not missing_start, "coverage_complete": not missing_start},
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "electricity_reconciliation.json"
    monkeypatch.setattr(reconciliation, "RECONCILIATION_PATH", path)
    with reconciliation._RATE_LOCK:
        reconciliation._WRITE_ATTEMPTS.clear()
    return path


def authenticated_client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "reconciliation-test")
    monkeypatch.setenv("DASHBOARD_AUTH_PASSWORD_HASH", bcrypt.hashpw(b"password", bcrypt.gensalt(rounds=4)).decode())
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "reconciliation-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    response = client.post("/api/auth/login", json={"username": "reconciliation-test", "password": "password"})
    return client, response.json()["csrf_token"]


def write(client, csrf, method, path, payload=None):
    return client.request(method, path, json=payload, headers={"Origin": "http://testserver", "X-CSRF-Token": csrf})


def create_previous(monkeypatch, now="2026-09-02T03:00:00"):
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: payload())
    return reconciliation._upsert_authoritative("previous_billing_cycle", ts(now))


def test_cycle_identity_exclusive_end_and_due_date_example():
    start, end = ts("2026-08-02T00:00:00"), ts("2026-09-02T00:00:00")
    assert reconciliation.cycle_identity(start, end) == "2026-08-02_2026-09-02"
    assert reconciliation.due_date_for_cycle_end(end) == "2026-09-13"


@pytest.mark.parametrize(("end", "expected"), [("2026-12-02T00:00:00", "2026-12-13"), ("2027-01-02T00:00:00", "2027-01-13")])
def test_due_date_month_and_year_rollover(end, expected):
    assert reconciliation.due_date_for_cycle_end(ts(end)) == expected


def test_billing_month_clamping_and_formula_remain_unchanged(monkeypatch):
    monkeypatch.setattr(billing, "BILLING_CYCLE_DAY", 31)
    assert billing._cycle_boundary(2026, 2).day == 28
    rows = [{"ts": ts("2026-08-02T00:00:00"), "total_energy": 10, "power": 100}, {"ts": ts("2026-08-03T00:00:00"), "total_energy": 20, "power": 100}]
    monkeypatch.setattr(billing.history, "calculate_bill", lambda usage, estimated=True: {"configured": True, "total": usage * 4})
    monkeypatch.setattr(billing.time, "time", lambda: ts("2026-08-03T00:00:00"))
    result = billing._billing_cycle_payload_from_rows("current_billing_cycle", ts("2026-08-02T00:00:00"), ts("2026-09-02T00:00:00"), rows)
    assert result["actual_partial_usage_kwh"] == 10
    assert result["projected_cycle_usage_kwh"] == 310
    assert result["projected_cycle_bill"] == 1240


def test_atomic_private_persistence_restart_and_duplicate_prevention(store, monkeypatch):
    record = create_previous(monkeypatch)
    assert store.exists() and (store.stat().st_mode & 0o777) == 0o600
    assert reconciliation._load_store()["records"] == [record]
    assert reconciliation._upsert_authoritative("previous_billing_cycle")["cycle_id"] == record["cycle_id"]
    assert len(reconciliation._load_store()["records"]) == 1
    raw = json.loads(store.read_text())
    raw["records"].append(raw["records"][0])
    store.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="reconciliation_store_invalid"):
        reconciliation._load_store()


def test_actual_bill_update_due_override_and_differences(store, monkeypatch):
    record = create_previous(monkeypatch)
    updated = reconciliation._update_record(record["cycle_id"], {"actual_bill_amount": 1100, "due_date": "2026-09-15"}, ts("2026-09-05T10:00:00"))
    assert updated["calculated_cost"] == 1000
    assert updated["calculated_kwh"] == 250
    assert updated["actual_bill_amount"] == 1100
    assert updated["difference_amount"] == 100
    assert updated["difference_percent"] == 10
    assert updated["due_date"] == "2026-09-15"


def test_legacy_record_without_actual_usage_fields_remains_valid(store, monkeypatch):
    record = create_previous(monkeypatch)
    raw = json.loads(store.read_text())
    for field in ("actual_usage_kwh", "usage_difference_kwh", "usage_variance_percent"):
        raw["records"][0].pop(field)
    store.write_text(json.dumps(raw))

    loaded = reconciliation._load_store()["records"][0]

    assert loaded["cycle_id"] == record["cycle_id"]
    assert loaded["calculated_kwh"] == 250
    assert loaded["actual_usage_kwh"] is None
    assert loaded["usage_difference_kwh"] is None
    assert loaded["usage_variance_percent"] is None


def test_actual_usage_and_bill_can_be_saved_independently_or_together(store, monkeypatch):
    record = create_previous(monkeypatch)
    usage_only = reconciliation._update_record(record["cycle_id"], {"actual_usage_kwh": 275.1234})
    assert usage_only["actual_usage_kwh"] == 275.1234
    assert usage_only["actual_bill_amount"] is None
    assert usage_only["usage_difference_kwh"] == 25.1234
    assert usage_only["usage_variance_percent"] == 10.05

    bill_only = reconciliation._update_record(record["cycle_id"], {"actual_bill_amount": 1100})
    assert bill_only["actual_usage_kwh"] == 275.1234
    assert bill_only["actual_bill_amount"] == 1100
    assert bill_only["difference_amount"] == 100

    both = reconciliation._update_record(record["cycle_id"], {"actual_usage_kwh": 240, "actual_bill_amount": 950})
    assert both["actual_usage_kwh"] == 240
    assert both["actual_bill_amount"] == 950
    assert both["usage_difference_kwh"] == -10
    assert both["usage_variance_percent"] == -4

    restarted = reconciliation._load_store()["records"][0]
    assert restarted == both


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "bad", 1_000_000.0001])
def test_invalid_or_unreasonably_large_actual_usage_is_rejected(store, monkeypatch, value):
    record = create_previous(monkeypatch)
    with pytest.raises(ValueError, match="invalid_actual_usage_kwh"):
        reconciliation._update_record(record["cycle_id"], {"actual_usage_kwh": value})


def test_usage_difference_is_unavailable_for_invalid_calculated_baseline():
    assert reconciliation._usage_differences(10, None) == (None, None)
    assert reconciliation._usage_differences(10, float("nan")) == (None, None)
    assert reconciliation._usage_differences(10, -1) == (None, None)
    assert reconciliation._usage_differences(10, 0) == (10, None)


def test_existing_v1_record_is_enriched_idempotently_without_financial_or_state_changes(store, monkeypatch):
    previous = payload(cost=1286.86, kwh=311.14, missing_start=True)
    previous["coverage"]["coverage_percent"] = 52.55
    values = {
        "current_billing_cycle": payload("2026-08-02T00:00:00", "2026-09-02T00:00:00"),
        "previous_billing_cycle": previous,
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    record = reconciliation._upsert_authoritative("previous_billing_cycle")
    record = reconciliation._update_record(record["cycle_id"], {"actual_bill_amount": 1300})
    raw = json.loads(store.read_text())
    raw["records"][0].pop("coverage")
    raw["records"][0]["reminder_events"] = ["due_soon_day_10"]
    store.write_text(json.dumps(raw))
    unchanged = {key: raw["records"][0][key] for key in (
        "calculated_cost", "calculated_kwh", "actual_bill_amount", "difference_amount",
        "difference_percent", "due_date", "payment_status", "paid_at", "reminder_events",
    )}

    first = reconciliation.current_reconciliation()["latest_closed_cycle"]
    first_bytes = store.read_bytes()
    second = reconciliation.current_reconciliation()["latest_closed_cycle"]

    assert first["coverage"] == {"status": "incomplete", "percent": 52.55, "start_complete": False}
    assert {key: first[key] for key in unchanged} == unchanged
    assert second == first
    assert store.read_bytes() == first_bytes
    assert json.loads(first_bytes)["records"][0]["coverage"] == first["coverage"]


def test_existing_record_with_unavailable_authoritative_coverage_stays_readable(store, monkeypatch):
    record = create_previous(monkeypatch)
    raw = json.loads(store.read_text())
    raw["records"][0].pop("coverage")
    store.write_text(json.dumps(raw))
    previous = payload()
    previous["coverage"] = {}
    values = {
        "current_billing_cycle": payload("2026-09-02T00:00:00", "2026-10-02T00:00:00"),
        "previous_billing_cycle": previous,
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    result = reconciliation.current_reconciliation()["latest_closed_cycle"]
    assert result["coverage"] == {"status": "unavailable", "percent": None, "start_complete": None}
    assert result["calculated_cost"] == record["calculated_cost"]
    assert result["calculated_kwh"] == record["calculated_kwh"]
    assert "coverage" not in json.loads(store.read_text())["records"][0]


def test_zero_calculated_cost_has_safe_difference_percent(store, monkeypatch):
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: payload(cost=0, kwh=0))
    record = reconciliation._upsert_authoritative("previous_billing_cycle")
    updated = reconciliation._update_record(record["cycle_id"], {"actual_bill_amount": 50})
    assert updated["difference_amount"] == 50
    assert updated["difference_percent"] is None


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "bad"])
def test_invalid_actual_bill_values_are_rejected(store, monkeypatch, value):
    record = create_previous(monkeypatch)
    with pytest.raises(ValueError, match="invalid_actual_bill_amount"):
        reconciliation._update_record(record["cycle_id"], {"actual_bill_amount": value})


def test_paid_and_explicit_unpaid_transitions(store, monkeypatch):
    record = create_previous(monkeypatch)
    paid = reconciliation._update_record(record["cycle_id"], {"payment_status": "paid"}, ts("2026-09-09T12:00:00"))
    assert paid["payment_status"] == "paid" and paid["paid_at"] is not None
    unpaid = reconciliation._update_record(record["cycle_id"], {"payment_status": "unpaid"}, ts("2026-09-10T12:00:00"))
    assert unpaid["payment_status"] == "unpaid" and unpaid["paid_at"] is None


def test_day_2_close_reminder_and_restart_deduplication(store, monkeypatch):
    monkeypatch.setattr(billing, "BILLING_CYCLE_DAY", 2)
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: payload())
    now = ts("2026-09-02T03:00:00")
    first = reconciliation.process_daily_reminders({"notifications": []}, now)
    second = reconciliation.process_daily_reminders(first, now)
    assert [item["id"] for item in second["notifications"]] == ["electricity.2026-08-02_2026-09-02.cycle_closed"]
    assert reconciliation._load_store()["records"][0]["reminder_events"] == ["cycle_closed"]


def test_strict_store_rejects_unknown_or_inconsistent_fields(store, monkeypatch):
    record = create_previous(monkeypatch)
    raw = json.loads(store.read_text())
    raw["records"][0]["unexpected"] = True
    store.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="reconciliation_store_invalid"):
        reconciliation._load_store()
    raw["records"][0].pop("unexpected")
    raw["records"][0]["difference_amount"] = 1
    store.write_text(json.dumps(raw))
    with pytest.raises(RuntimeError, match="reconciliation_store_invalid"):
        reconciliation._load_store()


@pytest.mark.parametrize(("day", "kind"), [(10, "due_soon_day_10"), (13, "due_today_day_13")])
def test_due_reminders_and_restart_deduplication(store, monkeypatch, day, kind):
    create_previous(monkeypatch)
    now = ts(f"2026-09-{day:02d}T03:00:00")
    first = reconciliation.process_daily_reminders({"notifications": []}, now)
    second = reconciliation.process_daily_reminders(first, now)
    ids = [item["id"] for item in second["notifications"]]
    assert ids == [f"electricity.2026-08-02_2026-09-02.{kind}"]


def test_paid_cycle_suppresses_due_reminders(store, monkeypatch):
    record = create_previous(monkeypatch)
    reconciliation._update_record(record["cycle_id"], {"payment_status": "paid"}, ts("2026-09-09T12:00:00"))
    result = reconciliation.process_daily_reminders({"notifications": []}, ts("2026-09-10T03:00:00"))
    assert result["notifications"] == []


def test_projection_quality_marks_incomplete_start_without_changing_projection():
    source = payload(missing_start=True, projected=1400)
    quality = reconciliation._projection_quality(source)
    assert source["projected_cycle_bill"] == 1400
    assert quality == {"start_coverage": "incomplete", "projection_status": "available", "projection_reliability": "limited_incomplete_start", "coverage_percent": 50.0}
    assert reconciliation._projection_quality(payload(projected=None))["projection_status"] == "unavailable"


def test_projection_quality_complete_start_is_normal_with_partial_cycle_coverage():
    source = payload(missing_start=False, projected=1400)
    source["coverage"]["coverage_percent"] = 28.84
    assert reconciliation._projection_quality(source) == {
        "start_coverage": "complete",
        "projection_status": "available",
        "projection_reliability": "normal",
        "coverage_percent": 28.84,
    }


def test_closed_cycle_coverage_uses_authoritative_flags_not_percent_thresholds():
    complete = payload()
    complete["coverage"]["coverage_percent"] = 28.84
    incomplete = payload(missing_start=True)
    incomplete["coverage"]["coverage_percent"] = 52.55
    unavailable = payload()
    unavailable["coverage"] = {}
    assert reconciliation._coverage_from_payload(complete) == {"status": "complete", "percent": 28.84, "start_complete": True}
    assert reconciliation._coverage_from_payload(incomplete) == {"status": "incomplete", "percent": 52.55, "start_complete": False}
    assert reconciliation._coverage_from_payload(unavailable) == {"status": "unavailable", "percent": None, "start_complete": None}


def test_api_auth_csrf_update_paid_unpaid_and_calculated_fields_protected(store, monkeypatch):
    record = create_previous(monkeypatch)
    client, csrf = authenticated_client(monkeypatch)
    client.cookies.clear()
    assert client.put(f"/api/electricity/reconciliation/{record['cycle_id']}", json={"actual_bill_amount": 1100}).status_code == 401
    client, csrf = authenticated_client(monkeypatch)
    assert client.put(f"/api/electricity/reconciliation/{record['cycle_id']}", json={"actual_bill_amount": 1100}).status_code == 403
    response = write(client, csrf, "PUT", f"/api/electricity/reconciliation/{record['cycle_id']}", {"actual_bill_amount": 1100, "calculated_cost": 1})
    assert response.status_code == 422
    response = write(client, csrf, "PUT", f"/api/electricity/reconciliation/{record['cycle_id']}", {"actual_usage_kwh": 275, "calculated_kwh": 1})
    assert response.status_code == 422
    response = write(client, csrf, "PUT", f"/api/electricity/reconciliation/{record['cycle_id']}", {"actual_bill_amount": 1100, "actual_usage_kwh": 275})
    assert response.status_code == 200
    assert response.json()["record"]["calculated_cost"] == 1000
    assert response.json()["record"]["calculated_kwh"] == 250
    assert response.json()["record"]["usage_difference_kwh"] == 25
    assert write(client, csrf, "POST", f"/api/electricity/reconciliation/{record['cycle_id']}/paid").json()["record"]["payment_status"] == "paid"
    assert write(client, csrf, "POST", f"/api/electricity/reconciliation/{record['cycle_id']}/unpaid").json()["record"]["payment_status"] == "unpaid"


def test_current_status_adds_days_and_keeps_latest_closed_separate(store, monkeypatch):
    values = {"current_billing_cycle": payload("2026-09-02T00:00:00", "2026-10-02T00:00:00"), "previous_billing_cycle": payload()}
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    record = reconciliation._upsert_authoritative("previous_billing_cycle")
    client, _ = authenticated_client(monkeypatch)
    response = client.get("/api/electricity/reconciliation/current")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_closed_cycle"]["cycle_id"] == record["cycle_id"]
    assert body["active_cycle"]["cycle_end"] == "2026-10-02"
    assert isinstance(body["active_cycle"]["days_until_cycle_end"], int)


def test_first_read_bootstraps_latest_closed_cycle_only_and_is_idempotent(store, monkeypatch):
    values = {
        "current_billing_cycle": payload("2026-09-02T00:00:00", "2026-10-02T00:00:00", cost=50, kwh=10),
        "previous_billing_cycle": payload("2026-08-02T00:00:00", "2026-09-02T00:00:00", cost=1286.86, kwh=311.14),
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])

    first = reconciliation.current_reconciliation()
    second = reconciliation.current_reconciliation()
    records = reconciliation._load_store()["records"]

    assert first["bootstrap_status"] == "created"
    assert second["bootstrap_status"] == "existing"
    assert first["latest_closed_cycle"]["cycle_id"] == "2026-08-02_2026-09-02"
    assert first["latest_closed_cycle"]["due_date"] == "2026-09-13"
    assert first["latest_closed_cycle"]["calculated_cost"] == 1286.86
    assert first["latest_closed_cycle"]["calculated_kwh"] == 311.14
    assert first["latest_closed_cycle"]["coverage"] == {"status": "complete", "percent": 50.0, "start_complete": True}
    assert first["latest_closed_cycle"]["reminder_events"] == []
    assert [item["cycle_id"] for item in records] == ["2026-08-02_2026-09-02"]
    assert records == reconciliation._load_store()["records"]


def test_bootstrap_restart_and_due_reminder_deduplication(store, monkeypatch):
    values = {
        "current_billing_cycle": payload("2026-08-02T00:00:00", "2026-09-02T00:00:00"),
        "previous_billing_cycle": payload("2026-07-02T00:00:00", "2026-08-02T00:00:00"),
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    reconciliation.current_reconciliation()
    assert reconciliation.current_reconciliation()["bootstrap_status"] == "existing"

    now = ts("2026-08-13T03:00:00")
    first = reconciliation.process_daily_reminders({"notifications": []}, now)
    second = reconciliation.process_daily_reminders(first, now)
    assert [item["id"] for item in second["notifications"]] == [
        "electricity.2026-07-02_2026-08-02.due_today_day_13"
    ]


def test_bootstrap_year_rollover_uses_closed_cycle_and_day_13_due_date(store, monkeypatch):
    values = {
        "current_billing_cycle": payload("2027-01-02T00:00:00", "2027-02-02T00:00:00"),
        "previous_billing_cycle": payload("2026-12-02T00:00:00", "2027-01-02T00:00:00"),
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    result = reconciliation.current_reconciliation()["latest_closed_cycle"]
    assert result["cycle_id"] == "2026-12-02_2027-01-02"
    assert result["due_date"] == "2027-01-13"


@pytest.mark.parametrize(("cost", "kwh"), [(None, 100), (100, None)])
def test_bootstrap_unavailable_closed_calculation_does_not_create_zero_record(store, monkeypatch, cost, kwh):
    values = {
        "current_billing_cycle": payload("2026-09-02T00:00:00", "2026-10-02T00:00:00"),
        "previous_billing_cycle": payload(cost=cost, kwh=kwh),
    }
    monkeypatch.setattr(billing, "billing_cycle_payload", lambda period: values[period])
    result = reconciliation.current_reconciliation()
    assert result["bootstrap_status"] == "unavailable"
    assert result["latest_closed_cycle"] is None
    assert result["latest_closed_cycle_id"] == "2026-08-02_2026-09-02"
    assert not store.exists()


def test_reconciliation_is_separate_from_history_tariff_and_maintenance_paths():
    assert reconciliation.RECONCILIATION_PATH.parent == settings.DATA_DIR
    assert reconciliation.RECONCILIATION_PATH.name == "electricity_reconciliation.json"
    assert reconciliation.RECONCILIATION_PATH not in {billing.history.HISTORY_PATH, settings.MAINTENANCE_PATH, settings.SETTINGS_PATH}
    assert "tariff" not in reconciliation.RECONCILIATION_PATH.name


def test_no_new_scheduler_and_existing_maintenance_owner_is_wrapped():
    source = Path(reconciliation.__file__).read_text()
    assert "threading.Thread" not in source
    assert "start_daily_maintenance" not in source
    app_entry = (Path(reconciliation.__file__).parent / "app_entry.py").read_text()
    assert "from backend import electricity_reconciliation" in app_entry
