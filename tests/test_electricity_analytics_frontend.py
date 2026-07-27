from pathlib import Path

import pytest

from frontend_runtime import electricity_analytics_behavior


ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "frontend/assets/dashboard_electricity.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend/assets/dashboard_electricity.css").read_text(encoding="utf-8")


def test_summary_and_statistics_cards_use_cached_payloads():
    for label in (
        "Today",
        "Estimated Cost",
        "Today’s Peak",
        "Comparison",
        "Compared with yesterday",
        "Highest Day",
        "Lowest Day",
        "Average Daily",
        "Average Hourly",
        "Maximum Interval",
        "Minimum Interval",
    ):
        assert label in JS
    assert "analyticsStatistics()" in JS
    assert "Calculated from the selected history already loaded" in JS


def test_moving_average_is_gap_aware_and_statistics_are_additive():
    result = electricity_analytics_behavior()
    assert result["average"][:3] == [None, None, pytest.approx(2)]
    assert result["average"][3] is None
    assert result["highest"] == pytest.approx(10)
    assert result["lowest"] == pytest.approx(10)
    assert result["averageHourly"] == pytest.approx(5)
    assert result["maximum"] == pytest.approx(4)
    assert result["minimum"] == pytest.approx(1)


def test_tooltip_contains_interval_cost_bucket_and_quality():
    result = electricity_analytics_behavior()
    for expected in (
        "26 Jul 2026",
        "00:00–00:30",
        "1.0000 kWh",
        "฿5.00",
        "30 minutes",
        "Good",
    ):
        assert expected in result["tooltip"]


def test_toolbar_custom_range_bucket_and_csv_contract():
    assert 'data-electricity-range="${key}"' in JS
    assert "['custom', 'Custom']" in JS
    assert "data-electricity-custom" in JS
    assert "data-electricity-bucket" in JS
    assert "Auto" in JS and "15 minutes" in JS and "1 day" in JS
    assert 'data-electricity-export="csv"' in JS
    assert "electricity-history-toolbar" in JS


def test_chart_uses_bars_and_moving_average_without_smoothing_gaps():
    assert 'class="history-bar energy"' in JS
    assert 'class="history-average-line"' in JS
    assert "movingAverage(rawRows, 3, maxGap)" in JS
    assert "timestamp - previousTimestamp > maxGap" in JS
    assert "Missing intervals remain gaps." in JS


def test_responsive_layout_and_no_duplicate_history_request():
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in CSS
    assert "@media(max-width:1180px)" in CSS
    assert "@media(max-width:820px)" in CSS
    assert "@media(max-width:560px)" in CSS
    initial = JS[JS.index("const initialData"):JS.index("window.DashboardElectricityHistory")]
    assert initial.count("loadHistory()") == 1
