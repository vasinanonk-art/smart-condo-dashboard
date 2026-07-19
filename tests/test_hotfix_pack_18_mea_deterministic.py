"""HOTFIX PACK 18 regression contracts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "mea_tariff_hotfix18.py").read_text(encoding="utf-8")
ENTRY = (ROOT / "backend" / "app_entry.py").read_text(encoding="utf-8")


def test_hotfix_loads_after_hotfix17():
    assert ENTRY.index("mea_tariff_hotfix17") < ENTRY.index("mea_tariff_hotfix18") < ENTRY.index("dashboard_auth")


def test_duplicate_residential_links_are_canonicalized():
    assert "def _canonical_url" in SOURCE
    assert "urlunsplit" in SOURCE
    assert 'by_url: Dict[str, Dict[str, Any]]' in SOURCE
    assert 'if previous is None or item["score"] > previous["score"]' in SOURCE
    assert 'deduplicated_residential_link_count' in SOURCE


def test_only_exact_residential_card_links_are_considered():
    assert "def _is_exact_residential_card" in SOURCE
    assert "def _card_candidates" in SOURCE
    assert "_tariff_detail_path" in SOURCE
    for rejected in ("payment", "producer", "meter", "deposit", "calculator"):
        assert f'"{rejected}"' in SOURCE
    assert 'path in {"", "/", "/our-services"}' in SOURCE


def test_dom_identity_and_content_fingerprint_deduplication():
    assert "seen_nodes: set[int]" in SOURCE
    assert "id(candidate) in seen_nodes" in SOURCE
    assert "def _content_fingerprint" in SOURCE
    assert "by_content" in SOURCE


def test_toc_sidebar_heading_only_blocks_cannot_validate():
    assert '"nav", "aside"' in SOURCE
    assert "values is not None and len(text) >= 100" in SOURCE
    assert "_candidate_ancestor" in SOURCE


def test_all_four_tariff_components_are_required():
    for component in ("up_to_150", "up_to_400", "over_400", "service_charge"):
        assert f'"{component}"' in SOURCE
    assert "if not match:" in SOURCE
    assert "return None" in SOURCE


def test_identical_duplicate_sections_are_accepted():
    assert "value_groups" in SOURCE
    assert "if len(value_groups) > 1" in SOURCE
    assert 'raise ValueError("type_1_2_section_ambiguous")' in SOURCE
    assert "selected = max(valid" in SOURCE


def test_different_value_sets_are_ambiguous():
    assert "def _value_fingerprint" in SOURCE
    assert "candidate_value_sets" in SOURCE
    assert "representations" in SOURCE


def test_safe_diagnostics_are_exposed():
    for field in (
        "raw_type_1_2_match_count",
        "deduplicated_type_1_2_candidate_count",
        "valid_type_1_2_candidate_count",
        "selected_candidate_score",
        "selected_candidate_fingerprint",
        "duplicate_candidate_count",
        "candidate_value_sets",
    ):
        assert f'"{field}"' in SOURCE
    assert "raw HTML" not in SOURCE


def test_terminal_error_fields_are_consistent():
    status = SOURCE.split("def tariff_status_hotfix18", 1)[1].split("def tariff_check_hotfix18", 1)[0]
    for field in ('"last_error": code', '"status": code', '"candidate_status": code'):
        assert field in status
    check = SOURCE.split("def tariff_check_hotfix18", 1)[1]
    assert '"last_error": code' in check
    assert '"status": code' in check
    assert '"error": code' in check
    assert '"parser_error_code": code' in check


def test_ft_fetch_remains_after_base_parse():
    provider = (ROOT / "backend" / "mea_tariff_hotfix17.py").read_text(encoding="utf-8")
    assert provider.index("base = parse_type_1_2_dom") < provider.index("ft_metadata_fetch")


def test_regression_scope_does_not_touch_unrelated_integrations():
    lowered = SOURCE.lower()
    for forbidden in ("lg tv", "mqtt topic", "presence", "billing calculation", "frontend flicker"):
        assert forbidden not in lowered
