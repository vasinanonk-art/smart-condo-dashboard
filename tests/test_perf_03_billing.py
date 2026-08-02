import threading
import time
from pathlib import Path

import pytest

from backend import electricity_billing_cycle as billing
from backend import tariff_segment_billing as segmented
from tests.frontend_runtime import electricity_billing_owner_behavior

ROOT = Path(__file__).resolve().parents[1]


ROWS = [
    {"ts": 100, "total_energy": 10.0},
    {"ts": 200, "total_energy": 11.5},
    {"ts": 300, "total_energy": 12.0},
]


@pytest.fixture(autouse=True)
def reset_billing_cache(monkeypatch):
    monkeypatch.setattr(segmented, "_billing_cache_key", None)
    monkeypatch.setattr(segmented, "_billing_cache_value", None)
    monkeypatch.setattr(segmented, "_billing_cache_at", 0.0)
    monkeypatch.setattr(segmented, "_billing_inflight", {})
    monkeypatch.setattr(segmented, "_request_key", lambda selected, start, end: (selected, start, end))
    monkeypatch.setattr(segmented, "segmented_bill", lambda start, end, rows: None)


def test_frontend_billing_owner_is_page_scoped_and_non_overlapping():
    result = electricity_billing_owner_behavior()
    assert result["firstCycle"]["calls"] == 2
    assert result["firstCycle"]["timerCreates"] == 0
    assert result["afterLeave"]["calls"] == 2
    assert result["afterLeave"]["diagnostics"]["active"] is False
    assert result["afterLeave"]["diagnostics"]["timer_active"] is False
    assert result["afterLeave"]["timerClears"] == 0
    assert result["afterReturn"]["calls"] == 4
    assert result["afterReturn"]["timerCreates"] == 0


def test_global_refresh_and_polish_startup_do_not_fetch_billing_endpoints():
    electricity = (ROOT / "frontend/assets/dashboard_electricity.js").read_text()
    polish = (ROOT / "frontend/assets/dashboard_polish10.js").read_text()
    global_refresh = electricity.split("window.refresh = async function refreshWithElectricity()", 1)[1].split("window.renderPage =", 1)[0]
    initial = electricity.split("const initialData", 1)[1].split("initialData", 1)[0]
    polish_load = polish.split("async function load()", 1)[1].split("function ensureHistoryPage", 1)[0]
    for source in (global_refresh, initial, polish_load):
        assert "/api/electricity/billing-cycle" not in source
        assert "loadBilling()" not in source
        assert "loadBillingCycleStatus()" not in source


def test_segmented_request_reads_history_once(monkeypatch):
    calls = 0

    def read_samples(start, end):
        nonlocal calls
        calls += 1
        return list(ROWS)

    monkeypatch.setattr(segmented.history, "read_samples", read_samples)
    result = segmented.billing_cycle_payload_segmented("today", 100, 300)
    assert calls == 1
    assert result["actual_partial_usage_kwh"] == pytest.approx(2.0)


def test_preloaded_rows_preserve_legacy_payload_values(monkeypatch):
    selected, start, end = billing._billing_request_bounds("today", 100, 300)
    expected = billing._billing_cycle_payload_from_rows(selected, start, end, list(ROWS))
    monkeypatch.setattr(segmented.history, "read_samples", lambda _start, _end: list(ROWS))
    actual = segmented.billing_cycle_payload_segmented("today", 100, 300)
    assert actual == {**expected, "tariff_segments": []}


def test_concurrent_identical_requests_share_one_calculation(monkeypatch):
    calls = 0
    barrier = threading.Barrier(2)

    def read_samples(start, end):
        nonlocal calls
        calls += 1
        time.sleep(0.05)
        return list(ROWS)

    monkeypatch.setattr(segmented.history, "read_samples", read_samples)
    results = []

    def worker():
        barrier.wait()
        results.append(segmented.billing_cycle_payload_segmented("today", 100, 300))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert calls == 1
    assert len(results) == 2
    assert results[0] == results[1]


def test_failed_calculation_is_not_cached(monkeypatch):
    calls = 0

    def read_samples(start, end):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fixture failure")
        return list(ROWS)

    monkeypatch.setattr(segmented.history, "read_samples", read_samples)
    with pytest.raises(OSError, match="fixture failure"):
        segmented.billing_cycle_payload_segmented("today", 100, 300)
    assert segmented.billing_cycle_payload_segmented("today", 100, 300)
    assert calls == 2
