import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend import electricity_history as history


@pytest.fixture
def history_store(tmp_path, monkeypatch):
    path = tmp_path / "electricity_history.jsonl"
    monkeypatch.setattr(history, "HISTORY_PATH", path)
    monkeypatch.delenv("ELECTRICITY_HISTORY_DB_PATH", raising=False)
    monkeypatch.setattr(history, "_tariff_config", lambda: (None, "tariff_not_configured"))
    return path


def _write_rows(path: Path, start: datetime, hours: int, reset_at: int | None = None):
    total = 100.0
    rows = []
    intervals = hours * 12
    for index in range(intervals + 1):
        if reset_at is not None and index == reset_at:
            total = 0.0
        rows.append(
            {
                "ts": int((start + timedelta(minutes=index * 5)).timestamp()),
                "voltage": 230.0,
                "current": 1.0,
                "power": 230.0,
                "total_energy": round(total, 4),
                "source": "test",
                "health": "healthy",
            }
        )
        total += 0.02
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return rows


def test_seven_day_query_returns_hourly_interval_consumption(history_store):
    end = datetime.now(history.BANGKOK).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    _write_rows(history_store, start, 7 * 24)

    payload = history.history_series_payload(start.isoformat(), end.isoformat(), "hour")

    assert payload["timezone"] == "Asia/Bangkok"
    assert payload["bucket"] == "hour"
    assert payload["energy_semantics"] == "interval_consumption"
    assert len(payload["points"]) == 7 * 24
    assert payload["summary"]["total_energy_kwh"] == pytest.approx(40.32)


def test_thirty_day_query_returns_daily_points(history_store):
    end = datetime.now(history.BANGKOK).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=30)
    _write_rows(history_store, start, 30 * 24)

    payload = history.history_series_payload(start.isoformat(), end.isoformat(), "day")

    assert payload["bucket"] == "day"
    assert 30 <= len(payload["points"]) <= 31
    assert payload["summary"]["sample_count"] == 8641


def test_custom_range_uses_inclusive_bangkok_day_boundaries(history_store):
    selected = (datetime.now(history.BANGKOK) - timedelta(days=2)).date()
    start = datetime.combine(selected, datetime.min.time(), history.BANGKOK)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    _write_rows(history_store, start, 24)

    payload = history.history_series_payload(start.isoformat(), end.isoformat(), "hour")

    assert payload["start"].endswith("+07:00")
    assert payload["end"].endswith("+07:00")
    assert all(point["timestamp"].endswith("+07:00") for point in payload["points"])
    assert payload["summary"]["sample_count"] == 288


def test_empty_range_returns_no_points_without_fabrication(history_store):
    end = datetime.now(history.BANGKOK) - timedelta(days=1)
    start = end - timedelta(hours=4)
    payload = history.history_series_payload(start.isoformat(), end.isoformat(), "hour")
    assert payload["points"] == []
    assert payload["summary"] == {
        "point_count": 0,
        "sample_count": 0,
        "total_energy_kwh": 0,
        "total_cost_thb": None,
    }


def test_invalid_reversed_future_and_oversized_ranges_are_rejected(history_store):
    now = datetime.now(history.BANGKOK)
    with pytest.raises(HTTPException, match="start_after_end"):
        history.history_series_payload(now.isoformat(), (now - timedelta(days=1)).isoformat(), "hour")
    with pytest.raises(HTTPException, match="future_range_not_supported"):
        history.history_series_payload(now.isoformat(), (now + timedelta(days=1)).isoformat(), "hour")
    with pytest.raises(HTTPException, match="range_too_large"):
        history.history_series_payload(
            (now - timedelta(days=history.MAX_QUERY_DAYS + 1)).isoformat(),
            now.isoformat(),
            "day",
        )


def test_cumulative_meter_reset_never_creates_negative_consumption():
    rows = [
        {"ts": 1000, "total_energy": 10.0},
        {"ts": 1060, "total_energy": 10.1},
        {"ts": 1120, "total_energy": 0.0},
        {"ts": 1180, "total_energy": 0.2},
    ]
    points = history._aggregate_history(rows, "hour")
    assert sum(point["energy_kwh"] for point in points) == pytest.approx(0.3)
    assert all(point["energy_kwh"] >= 0 for point in points)


def test_frontend_range_switching_has_loading_empty_and_stale_response_guards():
    source = (
        Path(__file__).resolve().parents[1] / "frontend/assets/dashboard_electricity.js"
    ).read_text(encoding="utf-8")
    assert "const requestId = ++state.historyRequestId" in source
    assert "requestId !== state.historyRequestId" in source
    assert "state.history = null" in source
    assert "state.historyLoading = true" in source
    assert "Loading electricity history…" in source
    assert "No history samples for this range." in source
    assert "await window.get(historyRequest(range, customStart, customEnd))" in source
    assert "Start date" in source and "End date" in source
    assert "data-electricity-custom-reset" in source


def test_frontend_uses_explicit_contract_and_one_request_per_selection():
    source = (
        Path(__file__).resolve().parents[1] / "frontend/assets/dashboard_electricity.js"
    ).read_text(encoding="utf-8")
    handler = source[source.index("document.querySelectorAll('[data-electricity-range]"):]
    handler = handler[:handler.index("document.querySelector('[data-electricity-custom]')")]
    assert handler.count("loadHistory(") == 1
    assert "URLSearchParams" in source
    assert "start:" in source and "end:" in source and "bucket," in source
