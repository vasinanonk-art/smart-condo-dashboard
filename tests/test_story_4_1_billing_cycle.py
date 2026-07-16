from __future__ import annotations

import importlib
from datetime import datetime
from zoneinfo import ZoneInfo


BANGKOK = ZoneInfo("Asia/Bangkok")


def test_billing_cycle_cuts_on_day_two(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_BILLING_CYCLE_DAY", "2")
    module = importlib.import_module("backend.electricity_billing_cycle")
    module = importlib.reload(module)
    now = int(datetime(2026, 7, 16, 12, 0, tzinfo=BANGKOK).timestamp())
    start, end = module.billing_period_bounds("current_billing_cycle", now)
    assert datetime.fromtimestamp(start, BANGKOK) == datetime(2026, 7, 2, 0, 0, tzinfo=BANGKOK)
    assert datetime.fromtimestamp(end, BANGKOK) == datetime(2026, 8, 2, 0, 0, tzinfo=BANGKOK)


def test_previous_cycle_crosses_month(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_BILLING_CYCLE_DAY", "2")
    module = importlib.import_module("backend.electricity_billing_cycle")
    module = importlib.reload(module)
    now = int(datetime(2026, 7, 16, 12, 0, tzinfo=BANGKOK).timestamp())
    start, end = module.billing_period_bounds("previous_billing_cycle", now)
    assert datetime.fromtimestamp(start, BANGKOK) == datetime(2026, 6, 2, 0, 0, tzinfo=BANGKOK)
    assert datetime.fromtimestamp(end, BANGKOK) == datetime(2026, 7, 2, 0, 0, tzinfo=BANGKOK)


def test_short_month_uses_last_valid_day(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_BILLING_CYCLE_DAY", "31")
    module = importlib.import_module("backend.electricity_billing_cycle")
    module = importlib.reload(module)
    boundary = module._cycle_boundary(2026, 2)
    assert boundary.day == 28


def test_coverage_reports_partial(monkeypatch):
    monkeypatch.setenv("ELECTRICITY_BILLING_CYCLE_DAY", "2")
    module = importlib.import_module("backend.electricity_billing_cycle")
    module = importlib.reload(module)
    rows = [{"ts": 2000}, {"ts": 3000}]
    coverage = module._coverage(1000, 5000, rows)
    assert coverage["complete"] is False
    assert coverage["missing_start"] is True
    assert coverage["coverage_percent"] == 25.0


def test_importer_rejects_unrelated_sensor():
    importer = importlib.import_module("scripts.import_electricity_history")
    row = importer._candidate_from_item({"ts": 1780000000, "metric": "pm25", "power": 123})
    assert row is None


def test_importer_accepts_valid_meter_bundle():
    importer = importlib.import_module("scripts.import_electricity_history")
    row = importer._candidate_from_item({
        "ts": 1780000000,
        "source": "pj1103",
        "voltage": 231.2,
        "current": 1.5,
        "power": 320.0,
        "total_energy": 12.3,
    })
    assert row is not None
    assert row["source"] == "sensor_history_import"
