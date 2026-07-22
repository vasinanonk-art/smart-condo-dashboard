from backend import automatic_tariff_sync as sync
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_hotfix19 as h19
from backend import mea_tariff_hotfix19_filter as h19_filter  # noqa: F401
from backend import mea_tariff_hotfix19_selector_runtime as runtime


def test_runtime_and_tests_use_identical_selector_callable():
    assert h17.select_residential_detail_link is h19.select_residential_detail_link
    assert h18.select_residential_detail_link is h19.select_residential_detail_link
    assert runtime.AUTHORITATIVE_SELECTOR is h19.select_residential_detail_link


def test_registered_mea_provider_uses_hotfix17_runtime_chain():
    provider = sync.PROVIDERS["mea"]
    assert type(provider).__module__ == "backend.mea_tariff_hotfix17"
    assert type(provider).__name__ == "MEATariffProviderHotfix17"


def test_selector_identity_diagnostics_are_safe_and_complete():
    diagnostics = runtime.provider_debug()
    assert diagnostics["selector_module"] == "backend.mea_tariff_hotfix19"
    assert diagnostics["selector_function"] == "select_residential_detail_link"
    assert diagnostics["selector_version"] == runtime.SELECTOR_VERSION
    assert diagnostics["selector_commit"]
    assert "<html" not in str(diagnostics).lower()


def test_exact_production_fragment_through_bound_runtime_selector():
    html = '''<tr class="pwot_date-list">
      <td><a class="doc-item-link" href="/our-services/service-rates/other/D5xEaEwgU">
        <div class="pt-1">ประเภทที่ 1 บ้านอยู่อาศัย</div>
      </a></td>
      <td class="text-right"><a class="btn btn-sm btn-danger" href="/our-services/service-rates/other/D5xEaEwgU">ดูเนื้อหา</a></td>
    </tr>'''.encode("utf-8")
    selected = h17.select_residential_detail_link(
        html, "https://www.mea.or.th/our-services/service-rates/other"
    )
    assert selected["url"].endswith("/our-services/service-rates/other/D5xEaEwgU")
