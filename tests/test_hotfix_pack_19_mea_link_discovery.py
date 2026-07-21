from backend import mea_tariff_hotfix19 as h19
from backend import mea_tariff_hotfix19_filter as h19_filter  # noqa: F401
from backend import mea_tariff_hotfix14 as h14

INDEX_URL = "https://www.mea.or.th/our-services/tariff-calculation/other/evlowpriority"


def _select(html: str):
    h14._SAFE_DEBUG.clear()
    return h19.select_residential_detail_link(html.encode("utf-8"), INDEX_URL)


def test_nested_card_layout():
    html = '''<main><div class="grid"><article class="card"><div><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2></div><div><p>อัตราค่าไฟฟ้า</p><a href="/our-services/tariff-calculation/other/AbCd1234">ดูเนื้อหา</a></div></article></div></main>'''
    assert _select(html)["url"].endswith("/our-services/tariff-calculation/other/AbCd1234")


def test_section_layout_and_absolute_url():
    html = '''<section><header><h3>ประเภท 1 บ้านอยู่อาศัย</h3></header><div><a href="https://www.mea.or.th/our-services/tariff-calculation/other/Residential01">รายละเอียด</a></div></section>'''
    assert _select(html)["url"].endswith("/Residential01")


def test_list_layout():
    html = '''<ul><li><span>ประเภทที่ 1 บ้านอยู่อาศัย</span><div><a href="/our-services/tariff-calculation/other/HomeRate01">More</a></div></li></ul>'''
    assert _select(html)["url"].endswith("/HomeRate01")


def test_accordion_layout():
    html = '''<div class="accordion-item"><button>ประเภทที่ 1 บ้านอยู่อาศัย</button><div class="accordion-body"><a href="/our-services/tariff-calculation/other/HomeAccordion">Read more</a></div></div>'''
    assert _select(html)["url"].endswith("/HomeAccordion")


def test_duplicated_mobile_desktop_layout_is_deduplicated():
    html = '''<div class="desktop card"><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/tariff-calculation/other/HomeDup#desktop">ดูเนื้อหา</a></div><div class="mobile card"><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/tariff-calculation/other/HomeDup#mobile">รายละเอียด</a></div>'''
    result = _select(html)
    assert result["url"].endswith("/HomeDup")
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_link_outside_immediate_parent_uses_previous_heading():
    html = '''<section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><div class="description">อัตราค่าไฟฟ้าสำหรับบ้าน</div><div class="actions"><span><a href="/our-services/tariff-calculation/other/HomeFar">ดูรายละเอียด</a></span></div></section>'''
    assert _select(html)["url"].endswith("/HomeFar")


def test_relative_url_resolution():
    html = '''<article><h2>Residential Type 1</h2><a href="../other/HomeRelative">Read more</a></article>'''
    assert _select(html)["url"] == "https://www.mea.or.th/our-services/tariff-calculation/other/HomeRelative"


def test_navigation_and_unrelated_links_are_filtered_after_scoring():
    html = '''<nav><a href="/">Home</a><a href="/our-services">Services</a></nav><section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/payment">รายละเอียด</a><a href="/our-services/electric-vehicle">More</a><a href="/our-services/tariff-calculation/other/HomeGood">ดูเนื้อหา</a></section>'''
    result = _select(html)
    assert result["url"].endswith("/HomeGood")
    assert h14._SAFE_DEBUG["anchor_count"] == 5
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_multiple_generic_labels_inside_same_residential_section():
    html = '''<section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/payment">รายละเอียด</a><a href="/our-services/electric-vehicle">Read more</a><a href="/our-services/service-rates/other/D5xEaEwgU">More</a></section>'''
    assert _select(html)["url"].endswith("/our-services/service-rates/other/D5xEaEwgU")
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_both_production_tariff_path_families_are_supported():
    html = '''<main><section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/tariff-calculation/other/HomeLegacy">รายละเอียด</a></section><section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/service-rates/other/D5xEaEwgU">ดูเนื้อหา</a></section></main>'''
    result = _select(html)
    assert result["url"].startswith("https://www.mea.or.th/our-services/service-rates/other/")
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 2


def test_scans_all_anchors_before_not_found():
    html = '''<html><body><a href="/">Home</a><a href="/our-services/payment">Payment</a><a href="https://example.com/x">External</a></body></html>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)
    assert h14._SAFE_DEBUG["anchor_count"] == 3
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 0


def test_safe_diagnostics_exist():
    html = '''<section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><a href="/our-services/tariff-calculation/other/HomeDiag">รายละเอียด</a></section>'''
    _select(html)
    for key in ("anchor_count", "candidate_before_filter", "candidate_after_filter", "top_candidate_context", "top_candidate_href", "context_tokens"):
        assert key in h14._SAFE_DEBUG
    assert "<html" not in str(h14._SAFE_DEBUG).lower()
