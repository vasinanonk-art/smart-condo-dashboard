from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend import mea_tariff_hotfix19_ft_parser as ft_parser

SOURCE_URL = "https://opendata.mea.or.th/ft.csv"
NOW = datetime(2026, 7, 24, tzinfo=ZoneInfo("Asia/Bangkok"))


def _csv(rows):
    return ("year,month,type,type_name,ft_rate\n" + "\n".join(rows) + "\n").encode("utf-8")


def test_production_schema_selects_current_residential_ft():
    result = ft_parser.parse_ft_with_distinct_status(
        _csv([
            "2026,7,1,ประเภทที่ 1 บ้านอยู่อาศัย,0.3972",
            "2026,7,2,กิจการขนาดเล็ก,0.4120",
        ]),
        SOURCE_URL,
        NOW,
    )
    assert result["ft_rate"] == 0.3972
    assert result["effective_from"] == "2026-07-01"
    assert result["effective_to"] == "2026-07-31"


def test_production_schema_selects_latest_non_future_period():
    result = ft_parser.parse_ft_with_distinct_status(
        _csv([
            "2026,5,1,Residential,0.3000",
            "2026,7,1,Residential,0.3972",
            "2026,9,1,Residential,0.5000",
        ]),
        SOURCE_URL,
        NOW,
    )
    assert result["ft_rate"] == 0.3972
    assert result["effective_from"] == "2026-07-01"


def test_production_schema_accepts_historical_negative_ft_and_selects_latest():
    result = ft_parser.parse_ft_with_distinct_status(
        _csv([
            "2021,1,1,Residential,-0.1532",
            "2021,12,1,Residential,-0.1532",
            "2026,7,1,Residential,0.3972",
        ]),
        SOURCE_URL,
        NOW,
    )
    assert result["ft_rate"] == 0.3972
    assert result["effective_from"] == "2026-07-01"
    assert result["effective_to"] == "2026-07-31"


def test_production_schema_rejects_malformed_ft_rate():
    with pytest.raises(ValueError, match="invalid_ft_rate"):
        ft_parser.parse_ft_with_distinct_status(
            _csv(["2026,7,1,Residential,not-a-number"]),
            SOURCE_URL,
            NOW,
        )


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-Infinity"])
def test_production_schema_rejects_non_finite_ft_rate(rate):
    with pytest.raises(ValueError, match="invalid_ft_rate"):
        ft_parser.parse_ft_with_distinct_status(
            _csv([f"2026,7,1,Residential,{rate}"]),
            SOURCE_URL,
            NOW,
        )


def test_production_schema_rejects_invalid_month():
    with pytest.raises(ValueError, match="invalid_ft_period"):
        ft_parser.parse_ft_with_distinct_status(
            _csv(["2026,13,1,Residential,0.3972"]),
            SOURCE_URL,
            NOW,
        )


def test_production_schema_rejects_duplicate_applicable_period():
    with pytest.raises(ValueError, match="duplicate_ft_period"):
        ft_parser.parse_ft_with_distinct_status(
            _csv([
                "2026,7,1,Residential,0.3972",
                "2026,7,01,ประเภทที่ 1 บ้านอยู่อาศัย,0.3972",
            ]),
            SOURCE_URL,
            NOW,
        )


def test_production_schema_rejects_unrelated_tariff_types():
    with pytest.raises(ValueError, match="no_current_official_ft_period"):
        ft_parser.parse_ft_with_distinct_status(
            _csv(["2026,7,2,กิจการขนาดเล็ก,0.4120"]),
            SOURCE_URL,
            NOW,
        )


def test_legacy_schema_remains_supported():
    body = (
        "type,start,end,ft\n"
        "residential,2026-05-01,2026-08-31,0.3972\n"
    ).encode("utf-8")
    result = ft_parser.parse_ft_with_distinct_status(body, SOURCE_URL, NOW)
    assert result["ft_rate"] == 0.3972
    assert result["effective_from"] == "2026-05-01"


def test_production_schema_rejects_future_only_data():
    with pytest.raises(ValueError, match="no_current_official_ft_period"):
        ft_parser.parse_ft_with_distinct_status(
            _csv(["2026,9,1,Residential,0.5000"]),
            SOURCE_URL,
            NOW,
        )
