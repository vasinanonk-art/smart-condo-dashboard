import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import bcrypt
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import electricity_history as history
from backend.app_entry import app


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
        "peak_interval_kwh": None,
        "average_interval_kwh": None,
        "minimum_interval_kwh": None,
        "peak_timestamp": None,
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


def test_exact_four_hundred_day_range_is_allowed(history_store, monkeypatch):
    now = datetime.now(history.BANGKOK)
    monkeypatch.setattr(history, "MAX_QUERY_DAYS", 400)
    monkeypatch.setattr(history, "RETENTION_DAYS", 400)
    monkeypatch.setattr(history, "_indexed_aggregation", lambda *_args: ([], 0))
    monkeypatch.setattr(history, "_available_history_range", lambda: {"start": None, "end": None})

    payload = history.history_series_payload(
        (now - timedelta(days=400)).isoformat(), now.isoformat(), "day"
    )

    assert payload["summary"]["point_count"] == 0
    assert payload["bucket"] == "day"


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


@pytest.mark.parametrize(
    ("duration", "expected"),
    (
        (timedelta(hours=48), "30m"),
        (timedelta(hours=48, seconds=1), "3h"),
        (timedelta(days=14), "3h"),
        (timedelta(days=14, seconds=1), "day"),
    ),
)
def test_auto_bucket_boundaries(duration, expected):
    start = datetime(2026, 1, 1, tzinfo=history.BANGKOK)
    assert history._resolved_bucket("auto", start, start + duration) == expected


def test_subhour_bucket_sums_energy_and_preserves_missing_gap(history_store):
    start = datetime(2026, 7, 20, tzinfo=history.BANGKOK)
    rows = _write_rows(history_store, start, 2)
    points = history._aggregate_history(rows, "30m")
    assert points[0]["energy_kwh"] == pytest.approx(0.12)
    assert sum(point["energy_kwh"] for point in points) == pytest.approx(0.48)

    gap_rows = rows[:7] + rows[18:]
    gap_points = history._aggregate_history(gap_rows, "15m")
    timestamps = {point["timestamp"] for point in gap_points}
    assert (start + timedelta(minutes=45)).isoformat() not in timestamps
    assert all(point["energy_kwh"] >= 0 for point in gap_points)


def test_bucket_costs_remain_additive(monkeypatch):
    points = [
        {"energy_kwh": 1.0, "cost_thb": None},
        {"energy_kwh": 2.0, "cost_thb": None},
    ]
    monkeypatch.setattr(
        history,
        "calculate_bill",
        lambda energy: {"configured": True, "total": energy},
    )

    history._apply_point_costs(points)

    assert sum(point["cost_thb"] for point in points) == pytest.approx(3.0)


def test_frontend_range_switching_has_loading_empty_and_stale_response_guards():
    source = (
        Path(__file__).resolve().parents[1] / "frontend/assets/dashboard_electricity.js"
    ).read_text(encoding="utf-8")
    assert "const requestId = ++state.historyRequestId" in source
    assert "requestId !== state.historyRequestId" in source
    assert "state.history = null" not in source[source.index("async function loadHistory"):source.index("function chartData")]
    assert "state.historyLoading = true" in source
    assert "Loading electricity history…" in source
    assert "electricity-history-chart-wrap${state.historyLoading ? ' is-loading' : ''}" in source
    assert "No history samples for this range." in source
    assert "await window.get(historyRequest(range, customStart, customEnd))" in source
    assert "Start date" in source and "End date" in source
    assert "data-electricity-custom-reset" in source


def test_frontend_uses_explicit_contract_and_one_request_per_selection():
    source = (
        Path(__file__).resolve().parents[1] / "frontend/assets/dashboard_electricity.js"
    ).read_text(encoding="utf-8")
    handler = source[source.index("document.querySelectorAll('[data-electricity-range]"):]
    handler = handler[:handler.index("document.querySelectorAll('[data-electricity-comparison]')")]
    assert handler.count("loadHistory(") == 1
    assert "URLSearchParams" in source
    assert "start:" in source and "end:" in source and "bucket," in source


def test_today_and_yesterday_comparison_boundaries_use_bangkok(history_store):
    now = datetime(2026, 7, 27, 12, 0, tzinfo=history.BANGKOK)
    _write_rows(history_store, datetime(2026, 7, 25, tzinfo=history.BANGKOK), 60)

    today = history.comparison_payload("today", now)
    yesterday = history.comparison_payload("yesterday", now)

    assert today["current"]["start"] == "2026-07-27T00:00:00+07:00"
    assert today["current"]["end"] == "2026-07-27T12:00:00+07:00"
    assert today["previous"]["start"] == "2026-07-26T00:00:00+07:00"
    assert today["previous"]["end"] == "2026-07-26T12:00:00+07:00"
    assert yesterday["current"]["start"] == "2026-07-26T00:00:00+07:00"
    assert yesterday["current"]["end"] == "2026-07-27T00:00:00+07:00"
    assert yesterday["previous"]["start"] == "2026-07-25T00:00:00+07:00"
    assert yesterday["previous"]["end"] == "2026-07-26T00:00:00+07:00"
    assert today["percentage_difference"] == pytest.approx(0)


def test_comparison_omits_percentage_for_empty_or_zero_previous_period(history_store):
    now = datetime(2026, 7, 27, 12, 0, tzinfo=history.BANGKOK)
    _write_rows(history_store, now.replace(hour=0), 12)

    payload = history.comparison_payload("today", now)

    assert payload["current"]["point_count"] > 0
    assert payload["previous"]["point_count"] == 0
    assert payload["percentage_difference"] is None


def test_last_seven_days_compares_with_the_previous_seven_days(history_store):
    now = datetime(2026, 7, 27, 12, 0, tzinfo=history.BANGKOK)
    _write_rows(history_store, now - timedelta(days=14), 14 * 24)

    payload = history.comparison_payload("7d", now)

    assert payload["bucket"] == "day"
    assert payload["current"]["start"] == "2026-07-20T12:00:00+07:00"
    assert payload["previous"]["start"] == "2026-07-13T12:00:00+07:00"
    assert payload["previous"]["end"] == payload["current"]["start"]
    assert payload["percentage_difference"] == pytest.approx(0)


def test_summary_reports_peak_average_minimum_and_peak_timestamp(history_store):
    start = datetime(2026, 7, 20, tzinfo=history.BANGKOK)
    _write_rows(history_store, start, 3)

    payload = history.history_series_payload(
        start.isoformat(), (start + timedelta(hours=3)).isoformat(), "hour"
    )
    summary = payload["summary"]

    assert summary["peak_interval_kwh"] is not None
    assert summary["average_interval_kwh"] is not None
    assert summary["minimum_interval_kwh"] is not None
    assert summary["peak_timestamp"].endswith("+07:00")
    assert summary["peak_interval_kwh"] >= summary["average_interval_kwh"] >= summary["minimum_interval_kwh"]
    assert payload["available_range"]["start"].endswith("+07:00")
    assert payload["available_range"]["end"].endswith("+07:00")


def test_csv_export_has_bom_safe_columns_and_date_filename(history_store):
    start = datetime(2026, 7, 20, tzinfo=history.BANGKOK)
    end = start + timedelta(hours=2)
    _write_rows(history_store, start, 2)
    payload = history.history_series_payload(start.isoformat(), end.isoformat(), "hour")
    response = history.history_csv_response(payload)

    async def body():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
        return b"".join(chunks)

    content = asyncio.run(body())
    text = content.decode("utf-8-sig")
    assert content.startswith(b"\xef\xbb\xbf")
    assert text.splitlines()[0] == (
        "timestamp_local,interval_start,interval_end,energy_kwh,"
        "cost_thb,bucket,data_quality"
    )
    assert "2026-07-20T00:00:00+07:00" in text
    assert ",hour,valid" in text
    assert response.headers["content-disposition"] == (
        'attachment; filename="electricity-history-2026-07-20-to-2026-07-20.csv"'
    )


def test_authenticated_csv_endpoint_preserves_contract(history_store, monkeypatch):
    start = datetime(2026, 7, 20, tzinfo=history.BANGKOK)
    end = start + timedelta(hours=2)
    _write_rows(history_store, start, 2)
    monkeypatch.setenv("DASHBOARD_AUTH_USERNAME", "csv-test")
    monkeypatch.setenv(
        "DASHBOARD_AUTH_PASSWORD_HASH",
        bcrypt.hashpw(b"csv-password", bcrypt.gensalt(rounds=4)).decode(),
    )
    monkeypatch.setenv("DASHBOARD_SESSION_SECRET", "csv-test-session-secret-long-enough")
    client = TestClient(app, base_url="http://testserver")
    assert client.post(
        "/api/auth/login",
        json={"username": "csv-test", "password": "csv-password"},
    ).status_code == 200

    response = client.get(
        "/api/electricity/history",
        params={
            "start": start.isoformat(),
            "end": end.isoformat(),
            "bucket": "30m",
            "format": "csv",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="electricity-history-'
    )
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert b",30m," in response.content


def test_frontend_has_comparison_metrics_server_csv_and_distinct_states():
    source = (
        Path(__file__).resolve().parents[1] / "frontend/assets/dashboard_electricity.js"
    ).read_text(encoding="utf-8")
    for label in (
        "Today", "Yesterday", "Last 7 days", "Peak Interval",
        "Average Interval", "Minimum Interval", "Peak Usage Time",
    ):
        assert label in source
    assert "comparisonRequestId" in source
    assert "format: 'csv'" in source
    assert "await response.blob()" in source
    assert "Available history:" in source
    assert "No history samples for this range." in source
    assert "electricity-history-message error" in source
    assert "Actual:" in source
    assert "state.exportLoading" in source
    assert "document.body.appendChild(anchor)" in source
    assert "URL.revokeObjectURL(url)" in source
