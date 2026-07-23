from pathlib import Path

import pytest

from backend import mea_tariff_hotfix19 as h19

FIXTURE = Path(__file__).parent / "fixtures" / "mea_residential_type_1_2_production.html"
SOURCE_URL = "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"


def test_production_type_1_2_fixture_parses_exact_bounded_section():
    body = FIXTURE.read_bytes()
    result = h19._parse_production_type_1_2(body, "text/html", SOURCE_URL)

    assert result["tiers"] == [
        {"up_to_kwh": 150.0, "rate": 3.2484},
        {"up_to_kwh": 400.0, "rate": 4.2218},
        {"up_to_kwh": None, "rate": 4.4217},
    ]
    assert result["service_charge"] == 24.62
    assert result["source_title"] == "ประเภทที่ 1 บ้านอยู่อาศัย"
    assert result["source_url"] == SOURCE_URL
    assert result["parser_confidence"] == "high"


def test_production_type_1_2_parser_stops_before_1_3_boundary():
    body = FIXTURE.read_bytes()
    container, heading = h19._find_unique_type_1_2_container(body)

    assert heading.startswith("1.2 ")
    text = container.text()
    assert "3.2484 บาท" in text
    assert "24.62" in text
    assert "1.3 อัตราตามช่วงเวลาของการใช้" not in text
    assert "5.1135" not in text


def test_production_type_1_2_parser_rejects_incomplete_fixture():
    body = FIXTURE.read_bytes().replace(b"<td>4.4217 \xe0\xb8\x9a\xe0\xb8\xb2\xe0\xb8\x97</td>", b"<td></td>", 1)

    with pytest.raises(ValueError, match="tier_parse_failed"):
        h19._parse_production_type_1_2(body, "text/html", SOURCE_URL)


def test_production_type_1_2_parser_rejects_ambiguous_section():
    body = FIXTURE.read_bytes()
    marker = "<h3>1.2 อัตราปกติปริมาณการใช้พลังงานไฟฟ้าเกินกว่า 150 หน่วยต่อเดือน</h3>".encode("utf-8")
    body = body.replace(marker, marker + marker, 1)

    with pytest.raises(ValueError, match="type_1_2_section_ambiguous"):
        h19._parse_production_type_1_2(body, "text/html", SOURCE_URL)


def test_production_type_1_2_parser_ignores_extra_non_tier_rows():
    body = FIXTURE.read_bytes()
    marker = b"<tbody>"
    extra_rows = b"""
    <tr class=\"decorative\"><th colspan=\"3\">decorative heading</th></tr>
    <tr class=\"empty\"><td></td><td></td><td></td></tr>
    """
    body = body.replace(marker, marker + extra_rows, 1)

    result = h19._parse_production_type_1_2(body, "text/html", SOURCE_URL)

    assert result["tiers"] == [
        {"up_to_kwh": 150.0, "rate": 3.2484},
        {"up_to_kwh": 400.0, "rate": 4.2218},
        {"up_to_kwh": None, "rate": 4.4217},
    ]
    assert result["service_charge"] == 24.62


def test_production_type_1_2_parser_does_not_recover_rate_from_elsewhere():
    body = FIXTURE.read_bytes()
    body = body.replace(b"<td>4.4217 \xe0\xb8\x9a\xe0\xb8\xb2\xe0\xb8\x97</td>", b"<td></td>", 1)
    body += b"<script>window.fakeRate = '4.4217';</script><div hidden>4.4217</div>"

    with pytest.raises(ValueError, match="tier_parse_failed"):
        h19._parse_production_type_1_2(body, "text/html", SOURCE_URL)
