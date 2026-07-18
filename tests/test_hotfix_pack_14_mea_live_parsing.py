from pathlib import Path

import pytest

from backend import mea_tariff_hotfix14 as hotfix

FIXTURE = Path(__file__).parent / "fixtures" / "mea_tariff_categories_live_2026.html"


def _body(text: str) -> bytes:
    return text.encode("utf-8")


def test_live_generic_thai_parent_and_type_12_section():
    result = hotfix.parse_live_base(FIXTURE.read_bytes(), "text/html", "https://www.mea.or.th/official")
    assert result["tariff_type"] == hotfix.EXPECTED
    assert result["category_match_score"] >= 70
    assert result["tiers"][-1]["up_to_kwh"] is None


@pytest.mark.parametrize("heading", [
    "ประเภทที่ 1 บ้านอยู่อาศัย 1.2 อัตราปกติ ใช้พลังงานไฟฟ้าเกิน 150 หน่วยต่อเดือน",
    "ประเภท 1 บ้านอยู่อาศัย บ้านอยู่อาศัย ประเภท 1.2 อัตราปกติ ใช้พลังงานไฟฟ้าเกิน 150 หน่วยต่อเดือน",
    "Residential Type 1.2 over 150 kWh",
])
def test_supported_category_labels(heading):
    text = f"""<h2>{heading}</h2>
    ไม่เกิน 150 หน่วย 3.20 ไม่เกิน 400 หน่วย 4.20 เกิน 400 หน่วย 4.40
    service charge 38.22 effective 2026-01-01
    """
    result = hotfix.parse_live_base(_body(text), "text/html", "https://www.mea.or.th/official")
    assert result["tariff_type"] == hotfix.EXPECTED


def test_generic_parent_is_ambiguous_not_mismatch():
    text = "ประเภทที่ 1 บ้านอยู่อาศัย ประเภทที่ 2 กิจการขนาดเล็ก"
    with pytest.raises(ValueError, match="category_ambiguous"):
        hotfix.parse_live_base(_body(text), "text/html", "https://www.mea.or.th/official")


def test_positive_other_category_is_mismatch():
    text = "ประเภทที่ 2 กิจการขนาดเล็ก ไม่เกิน 100 หน่วย 3.00 เกิน 100 หน่วย 4.00 ค่าบริการ 46.16"
    with pytest.raises(ValueError, match="category_mismatch"):
        hotfix.parse_live_base(_body(text), "text/html", "https://www.mea.or.th/official")


def test_category_not_found_is_distinct():
    with pytest.raises(ValueError, match="category_not_found"):
        hotfix.parse_live_base(_body("อัตราค่าไฟฟ้าประเภทต่าง ๆ"), "text/html", "https://www.mea.or.th/official")


def test_tier_failure_is_distinct():
    text = "ประเภทที่ 1 บ้านอยู่อาศัย 1.2 อัตราปกติ ใช้พลังงานไฟฟ้าเกิน 150 หน่วยต่อเดือน"
    with pytest.raises(ValueError, match="tier_parse_failed"):
        hotfix.parse_live_base(_body(text), "text/html", "https://www.mea.or.th/official")


def test_safe_diagnostics_exclude_raw_content():
    debug = hotfix.provider_debug()
    expected = {
        "base_source_http_status", "base_source_content_type", "base_source_bytes",
        "category_candidates", "category_match_method", "category_match_score",
        "expected_category", "detected_category", "ft_source_http_status",
        "ft_latest_period", "parser_stage", "parser_error_code",
    }
    assert expected.issubset(set(debug) | set(hotfix._SAFE_DEBUG))
    rendered = repr(debug).lower()
    assert "cookie" not in rendered
    assert "authorization" not in rendered
    assert "raw_html" not in rendered


def test_error_codes_are_not_collapsed():
    assert {
        "source_fetch_failed", "category_not_found", "category_ambiguous",
        "category_mismatch", "tier_parse_failed", "ft_not_found", "ft_period_expired",
    }.issubset(hotfix.ERROR_CODES)
