"""HOTFIX PACK 16 regression tests."""
from pathlib import Path

from backend import mea_tariff_hotfix16 as h16

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "tests/fixtures/mea_tariff_index_live_2026.html").read_bytes()
DETAIL = (ROOT / "tests/fixtures/mea_residential_detail_live_2026.html").read_bytes()


def test_category_index_selects_residential_link_only():
    selected = h16._select_residential_link(INDEX, "https://www.mea.or.th/our-services/tariff-calculation/other/evlowpriority")
    assert selected["url"] == "https://www.mea.or.th/our-services/tariff-calculation/residential"
    assert selected["score"] >= 60


def test_relative_mea_url_is_resolved_and_allowlisted():
    selected = h16._select_residential_link(INDEX, "https://www.mea.or.th/index")
    assert selected["url"].startswith("https://www.mea.or.th/")


def test_wrong_category_link_is_rejected():
    body = b'<section><h2>\xe0\xb8\x9b\xe0\xb8\xa3\xe0\xb8\xb0\xe0\xb9\x80\xe0\xb8\xa0\xe0\xb8\x97\xe0\xb8\x97\xe0\xb8\xb5\xe0\xb9\x88 2 \xe0\xb8\x81\xe0\xb8\xb4\xe0\xb8\x88\xe0\xb8\x81\xe0\xb8\xb2\xe0\xb8\xa3\xe0\xb8\x82\xe0\xb8\x99\xe0\xb8\xb2\xe0\xb8\x94\xe0\xb9\x80\xe0\xb8\xa5\xe0\xb9\x87\xe0\xb8\x81</h2><a href="/business">\xe0\xb8\x94\xe0\xb8\xb9\xe0\xb9\x80\xe0\xb8\x99\xe0\xb8\xb7\xe0\xb9\x89\xe0\xb8\xad\xe0\xb8\xab\xe0\xb8\xb2</a></section>'
    try:
        h16._select_residential_link(body, "https://www.mea.or.th/index")
    except ValueError as exc:
        assert str(exc) == "residential_detail_link_not_found"
    else:
        raise AssertionError("wrong category link was accepted")


def test_type_1_2_subsection_is_bounded_and_type_1_1_excluded():
    section = h16._extract_type_1_2_section(DETAIL, "text/html")
    assert "1.2" in section["text"]
    assert "1.1" not in section["text"]
    assert "TOU content" not in section["text"]
    assert "next category" not in section["text"]


def test_type_1_2_extracts_expected_tiers_and_service_charge():
    result = h16._parse_type_1_2(DETAIL, "text/html", "https://www.mea.or.th/residential")
    assert [tier["up_to_kwh"] for tier in result["tiers"]] == [150.0, 400.0, None]
    assert result["service_charge"] == 24.62
    assert result["tariff_type"] == "MEA Residential Type 1.2"


def test_detail_link_missing_has_specific_error():
    try:
        h16._select_residential_link(b"<html><a href='/business'>Business</a></html>", "https://www.mea.or.th/index")
    except ValueError as exc:
        assert str(exc) == "residential_detail_link_not_found"
    else:
        raise AssertionError("missing detail link did not fail")


def test_type_1_2_missing_has_specific_error():
    try:
        h16._extract_type_1_2_section(b"<h2>1.1 residential</h2>", "text/html")
    except ValueError as exc:
        assert str(exc) == "type_1_2_section_not_found"
    else:
        raise AssertionError("missing section did not fail")


def test_type_1_2_ambiguous_has_specific_error():
    body = b"<h2>1.2 Residential Type 1.2</h2><p>first content</p><h2>1.3 next</h2><h2>1.2 Residential Type 1.2</h2><p>second content</p>"
    try:
        h16._extract_type_1_2_section(body, "text/html")
    except ValueError as exc:
        assert str(exc) == "type_1_2_section_ambiguous"
    else:
        raise AssertionError("ambiguous section did not fail")


def test_error_mapping_preserves_specific_codes():
    for code in (
        "residential_detail_link_not_found", "residential_detail_fetch_failed",
        "type_1_2_section_not_found", "type_1_2_section_ambiguous", "tier_parse_failed",
    ):
        assert h16._map_error(ValueError(code)) == code


def test_safe_debug_does_not_expose_raw_html_or_headers():
    payload = h16.provider_debug()
    serialized = str(payload).lower()
    assert "raw_html" not in serialized
    assert "cookie" not in serialized
    assert "authorization" not in serialized
    assert "headers" not in serialized


def test_ft_fetch_is_after_base_parse_in_provider_source():
    source = (ROOT / "backend/mea_tariff_hotfix16.py").read_text(encoding="utf-8")
    base_position = source.index("base = _parse_type_1_2")
    ft_position = source.index('h14._SAFE_DEBUG["parser_stage"] = "ft_metadata_fetch"')
    assert base_position < ft_position


def test_runtime_maps_last_error_to_actual_parser_code():
    source = (ROOT / "backend/mea_tariff_hotfix16_runtime.py").read_text(encoding="utf-8")
    assert 'payload["last_error"] = code or payload.get("last_error")' in source
    assert 'sync_state["last_error"] = code' in source
    assert 'sync_state["diagnostics"] = {**diagnostics, "error": code' in source
