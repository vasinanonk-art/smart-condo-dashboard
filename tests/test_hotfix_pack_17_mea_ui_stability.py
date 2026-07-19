"""HOTFIX PACK 17 regression contracts.

Fixtures remain offline; these tests verify parser structure and the stable frontend
ownership/polling contracts without a live MEA dependency.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = (ROOT / "backend" / "mea_tariff_hotfix17.py").read_text(encoding="utf-8")
UI = (ROOT / "frontend" / "assets" / "dashboard_electricity_settings_hotfix17.js").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "assets" / "dashboard_electricity_settings_hotfix17.css").read_text(encoding="utf-8")
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "backend" / "app_entry.py").read_text(encoding="utf-8")


def test_dom_parser_uses_structural_residential_card_and_official_url_resolution():
    assert "class _DomParser" in PARSER
    assert "select_residential_detail_link" in PARSER
    assert "urllib.parse.urljoin" in PARSER
    assert "mea._safe_url(resolved)" in PARSER
    assert "บ้านอยู่อาศัย" in PARSER


def test_type_1_2_section_is_bounded_before_sibling_or_next_category():
    assert "extract_type_1_2_dom_section" in PARSER
    assert "_is_type_1_2" in PARSER
    assert "_is_boundary" in PARSER
    assert "type_1_2_section_not_found" in PARSER
    assert "type_1_2_section_ambiguous" in PARSER
    assert "tier_parse_failed" in PARSER


def test_ft_fetch_starts_only_after_base_parse():
    provider = PARSER.split("class MEATariffProviderHotfix17", 1)[1]
    assert provider.index("base = parse_type_1_2_dom") < provider.index("ft_metadata_fetch")
    assert provider.index("ft_metadata_fetch") < provider.index("MEA_FT_DATASET_API")


def test_hotfix_loaded_after_hotfix16_and_before_auth():
    assert APP.index("mea_tariff_hotfix16") < APP.index("mea_tariff_hotfix17")
    assert APP.index("mea_tariff_hotfix17") < APP.index("dashboard_auth")


def test_stable_frontend_owns_legacy_settings_and_tariff_modules():
    assert "window.__dashboardSettingsInstalled = true" in UI
    assert "window.__dashboardTariffSyncInstalled = true" in UI
    assert "window.__dashboardMeaTariffInstalled = true" in UI
    assert INDEX.index("dashboard_electricity_settings_hotfix17.js") < INDEX.index("dashboard_settings.js")
    assert INDEX.index("dashboard_electricity_settings_hotfix17.js") < INDEX.index("dashboard_tariff_sync.js")


def test_initial_loading_is_separate_from_background_refresh():
    assert "initialLoading:true" in UI
    assert "Refreshing…" in UI
    assert "state.refreshing&&!state.initialLoading" in UI
    assert "Never replace a dirty draft" in UI


def test_dirty_form_not_overwritten_and_reload_is_explicit():
    assert "settingsDirty:false" in UI
    assert "if(form && !state.settingsDirty)" in UI
    assert "markDirty" in UI
    assert "discardElectricityDraft" in UI
    assert "state.settingsDirty=false;hydrateFormsFromSettings()" in UI


def test_single_poller_and_cleanup_contract():
    assert "if(state.pollTimer)return" in UI
    assert "state.pollTimer=setInterval" in UI
    assert "clearInterval(state.pollTimer)" in UI
    assert "beforeunload" in UI
    assert "active_pollers" in UI


def test_stale_responses_are_ignored():
    assert "requestSequence" in UI
    assert "appliedSequence" in UI
    assert "ignoredStaleResponses" in UI
    assert "sequence < state.appliedSequence" in UI


def test_check_now_and_save_keep_stable_dom():
    assert "tariffCheckInProgress=true" in UI
    assert "check.disabled=state.tariffCheckInProgress" in UI
    assert "saveElectricitySettings" in UI
    assert "button.disabled=true" in UI
    assert "window.scrollTo(0,scrollY)" in UI
    assert ".remove()" not in UI
    assert "host.innerHTML" in UI  # one initial mount only


def test_transient_error_keeps_previous_data_and_layout_height_is_reserved():
    assert "Previous successful data remains visible" in UI
    assert "stable-warning-placeholder" in UI
    assert "min-height" in CSS
    assert "settings-stable-error" in CSS
    assert "stable-message" in CSS


def test_no_changing_keys_or_timestamp_based_identity():
    lowered = UI.lower()
    assert "key=" not in lowered
    assert "data-key" not in lowered
    assert "json.stringify" in lowered  # comparison display only, never identity
    assert "requestcounter" not in lowered


def test_safe_frontend_diagnostics_are_present():
    for field in (
        "active_pollers", "last_refresh_started", "last_refresh_completed",
        "ignored_stale_responses", "settings_dirty", "tariff_check_in_progress",
    ):
        assert field in UI
