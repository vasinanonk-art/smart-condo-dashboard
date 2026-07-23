from backend import mea_tariff_hotfix19 as h19
from backend import mea_tariff_hotfix19_filter as h19_filter
from backend import mea_tariff_hotfix14 as h14

INDEX_URL = "https://www.mea.or.th/our-services/tariff-calculation/other/evlowpriority"


def _select(html: str):
    h14._SAFE_DEBUG.clear()
    return h19.select_residential_detail_link(html.encode("utf-8"), INDEX_URL)


def test_nested_card_layout():
    html = '''<main><div class="grid"><article class="card"><div><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2></div><div><p>อัตราค่าไฟฟ้า</p><a href="/our-services/tariff-calculation/other/AbCd1234">ดูเนื้อหา</a></div></article></div></main>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_section_layout_and_absolute_url():
    html = '''<section><header><h3>ประเภท 1 บ้านอยู่อาศัย</h3></header><div><a href="https://www.mea.or.th/our-services/tariff-calculation/other/Residential01">รายละเอียด</a></div></section>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_list_layout():
    html = '''<ul><li><span>ประเภทที่ 1 บ้านอยู่อาศัย</span><div><a href="/our-services/tariff-calculation/other/HomeRate01">More</a></div></li></ul>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_accordion_layout():
    html = '''<div class="accordion-item"><button>ประเภทที่ 1 บ้านอยู่อาศัย</button><div class="accordion-body"><a href="/our-services/tariff-calculation/other/HomeAccordion">Read more</a></div></div>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_duplicated_mobile_desktop_layout_is_deduplicated():
    html = '''<div class="desktop card"><a href="/our-services/tariff-calculation/other/HomeDup#desktop">ประเภทที่ 1 บ้านอยู่อาศัย</a></div><div class="mobile card"><a href="/our-services/tariff-calculation/other/HomeDup#mobile">ประเภทที่ 1 บ้านอยู่อาศัย</a></div>'''
    result = _select(html)
    assert result["url"].endswith("/HomeDup")
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_link_outside_immediate_parent_uses_previous_heading():
    html = '''<section><h2>ประเภทที่ 1 บ้านอยู่อาศัย</h2><div class="description">อัตราค่าไฟฟ้าสำหรับบ้าน</div><div class="actions"><span><a href="/our-services/tariff-calculation/other/HomeFar">ดูรายละเอียด</a></span></div></section>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_relative_url_resolution():
    html = '''<article><a href="../other/HomeRelative">ประเภทที่ 1 บ้านอยู่อาศัย</a></article>'''
    assert _select(html)["url"] == "https://www.mea.or.th/our-services/tariff-calculation/other/HomeRelative"


def test_navigation_and_unrelated_links_are_filtered_after_scoring():
    html = '''<nav><a href="/">Home</a><a href="/our-services">Services</a></nav><section><a href="/our-services/payment">ประเภทที่ 1 บ้านอยู่อาศัย</a><a href="/our-services/electric-vehicle">ประเภทที่ 1 บ้านอยู่อาศัย</a><a href="/our-services/tariff-calculation/other/HomeGood">ประเภทที่ 1 บ้านอยู่อาศัย</a></section>'''
    assert not h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services/electric-vehicle")
    assert not h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services/payment")
    assert not h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services")
    assert h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services/tariff-calculation/other/HomeGood")
    result = _select(html)
    assert result["url"].endswith("/HomeGood")
    assert h14._SAFE_DEBUG["anchor_count"] == 5
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_multiple_generic_labels_inside_same_residential_section():
    html = '''<section><a href="/our-services/payment">ประเภทที่ 1 บ้านอยู่อาศัย</a><a href="/our-services/electric-vehicle">ประเภทที่ 1 บ้านอยู่อาศัย</a><a href="/our-services/service-rates/other/D5xEaEwgU">ประเภทที่ 1 บ้านอยู่อาศัย</a></section>'''
    assert _select(html)["url"].endswith("/our-services/service-rates/other/D5xEaEwgU")
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1


def test_both_production_tariff_path_families_are_supported():
    html = '''<main><section><a href="/our-services/tariff-calculation/other/HomeLegacy">ประเภทที่ 1 บ้านอยู่อาศัย</a></section><section><a href="/our-services/service-rates/other/D5xEaEwgU">ประเภทที่ 1 บ้านอยู่อาศัย</a></section></main>'''
    assert h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services/tariff-calculation/other/HomeLegacy")
    assert h19_filter.is_valid_tariff_detail_path("https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU")
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)


def test_exact_production_table_row_selects_d5xeaewgu():
    html = '''
<tr class="pwot_date-list">
  <td>
    <a class="doc-item-link" href="/our-services/service-rates/other/D5xEaEwgU">
      <div class="pt-1">ประเภทที่ 1 บ้านอยู่อาศัย</div>
    </a>
  </td>
  <td class="text-right">
    <a class="btn btn-sm btn-danger" href="/our-services/service-rates/other/D5xEaEwgU">
      ดูเนื้อหา
    </a>
  </td>
</tr>
'''
    result = _select(html)
    assert result["url"] == "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"
    assert h19._norm("ประเภทที่ 1 บ้านอยู่อาศัย") == "ประเภท 1 บ้านอยู่อาศัย"
    assert h14._SAFE_DEBUG["total_anchor_count"] == 2
    assert h14._SAFE_DEBUG["allowed_path_anchor_count"] == 2
    assert h14._SAFE_DEBUG["residential_text_anchor_count"] == 1
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 1
    assert h14._SAFE_DEBUG["top_candidate_href"].endswith("/D5xEaEwgU")


def test_multiple_production_rows_selects_only_exact_residential_anchor():
    html = '''
<table>
<tr class="pwot_date-list"><td><a class="doc-item-link" href="/our-services/service-rates/other/2VDCOXNlT"><div class="pt-1">ประเภทที่ 2 กิจการขนาดเล็ก</div></a></td><td><a href="/our-services/service-rates/other/2VDCOXNlT">ดูเนื้อหา</a></td></tr>
<tr class="pwot_date-list"><td><a class="doc-item-link" href="/our-services/service-rates/other/D5xEaEwgU"><div class="pt-1">ประเภทที่ 1 บ้านอยู่อาศัย</div></a></td><td><a href="/our-services/service-rates/other/D5xEaEwgU">ดูเนื้อหา</a></td></tr>
<tr class="pwot_date-list"><td><a class="doc-item-link" href="/our-services/service-rates/other/lhKD8oIlS"><div class="pt-1">ประเภทที่ 3 กิจการขนาดกลาง</div></a></td><td><a href="/our-services/service-rates/other/lhKD8oIlS">ดูเนื้อหา</a></td></tr>
</table>
'''
    result = _select(html)
    assert result["url"] == "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"
    candidates = h14._SAFE_DEBUG["residential_link_candidates"]
    assert [item["url"] for item in candidates] == ["https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"]
    assert "2VDCOXNlT" not in str(candidates)
    assert "lhKD8oIlS" not in str(candidates)


def test_scans_all_anchors_before_not_found():
    html = '''<html><body><a href="/">Home</a><a href="/our-services/payment">Payment</a><a href="https://example.com/x">External</a></body></html>'''
    with __import__('pytest').raises(ValueError, match="residential_detail_link_not_found"):
        _select(html)
    assert h14._SAFE_DEBUG["anchor_count"] == 3
    assert h14._SAFE_DEBUG["candidate_after_filter"] == 0
    assert h14._SAFE_DEBUG["rejected_candidate_reasons"]


def test_safe_diagnostics_exist():
    html = '''<section><a href="/our-services/tariff-calculation/other/HomeDiag">ประเภทที่ 1 บ้านอยู่อาศัย</a></section>'''
    _select(html)
    for key in (
        "anchor_count", "total_anchor_count", "allowed_path_anchor_count",
        "residential_text_anchor_count", "candidate_before_filter",
        "candidate_after_filter", "rejected_candidate_reasons",
        "top_candidate_context", "top_candidate_href", "context_tokens",
    ):
        assert key in h14._SAFE_DEBUG
    assert "<html" not in str(h14._SAFE_DEBUG).lower()
