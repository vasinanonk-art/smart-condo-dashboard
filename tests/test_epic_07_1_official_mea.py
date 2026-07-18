from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from backend import mea_tariff_provider as mea
from backend import tariff_segment_billing as segmented

FIXTURES = Path(__file__).parent / "fixtures"


def test_official_mea_html_parsing():
    body = (FIXTURES / "mea_type_1_2_official_excerpt.html").read_bytes()
    result = mea.parse_mea_base_document(body, "text/html", mea.MEA_TARIFF_PAGE)
    assert result["tariff_type"] == mea.EXPECTED_TARIFF_TYPE
    assert result["tiers"][-1]["up_to_kwh"] is None
    assert result["parser_confidence"] == "high"


def test_official_mea_pdf_parsing_uses_same_normalized_contract():
    body = (FIXTURES / "mea_type_1_2_official_excerpt.html").read_bytes()
    with patch.object(mea, "_pdf_text", return_value=body.decode("utf-8")):
        result = mea.parse_mea_base_document(b"fixture-pdf", "application/pdf", mea.MEA_TARIFF_PAGE)
    assert result["tariff_type"] == mea.EXPECTED_TARIFF_TYPE
    assert "tiers" in result["matched_fields"]


def test_base_tariff_and_ft_merge_contract():
    csv_body = (FIXTURES / "mea_ft_official_excerpt.csv").read_bytes()
    now = datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Bangkok"))
    ft = mea.parse_ft_csv(csv_body, "https://opendata.mea.or.th/test.csv", now)
    assert ft["status"] == "currently_effective"
    assert ft["effective_from"] == "2026-05-01"


def test_future_ft_is_selected_when_no_current_period():
    csv_body = b"type,ft_rate,effective_from,effective_to\nResidential,0.45,2027-01-01,2027-04-30\n"
    ft = mea.parse_ft_csv(csv_body, "https://opendata.mea.or.th/test.csv", datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert ft["status"] == "future"


def test_category_mismatch_is_rejected():
    bad = b"<html><body>Type 2 Small Business effective 2026-01-01 up to 100 kWh 3.0 over 100 kWh 4.0 service charge 20</body></html>"
    with pytest.raises(ValueError, match="tariff_category_mismatch"):
        mea.parse_mea_base_document(bad, "text/html", mea.MEA_TARIFF_PAGE)


def test_low_confidence_rejected():
    provider = mea.MEATariffProvider()
    with pytest.raises(ValueError):
        provider.validate({"tariff_type": mea.EXPECTED_TARIFF_TYPE, "parser_confidence": "low"})


def test_https_and_host_allowlist():
    assert mea._safe_url(mea.MEA_TARIFF_PAGE).startswith("https://")
    with pytest.raises(ValueError):
        mea._safe_url("http://mea.or.th/test")
    with pytest.raises(ValueError):
        mea._safe_url("https://example.com/test")


def test_bounded_fetch_constants_and_retry_contract():
    assert mea.MAX_RESPONSE_BYTES <= 4 * 1024 * 1024
    assert mea.MAX_REDIRECTS == 3
    assert mea.FETCH_TIMEOUT_SEC > 0


def test_segment_calculation_combines_totals():
    tariff = {"tiers":[{"up_to_kwh":100,"rate":2},{"up_to_kwh":None,"rate":3}],"ft_rate":0.4,"service_charge":20,"vat_percent":7,"minimum_charge":0}
    first = segmented.calculate_with_tariff(50, tariff, include_service=False)
    second = segmented.calculate_with_tariff(50, tariff, include_service=True)
    assert first["service_charge"] == 0
    assert second["service_charge"] == 20
    assert first["total"] > 0 and second["total"] > first["total"]


def test_no_live_internet_dependency_in_tests():
    source = Path(mea.__file__).read_text(encoding="utf-8")
    assert "ALLOWED_HOSTS" in source
    assert "tariff_candidate.json" not in source or "LocalCandidate" not in source


def test_no_new_scheduler_thread():
    source = Path(mea.__file__).read_text(encoding="utf-8")
    assert "threading.Thread" not in source
    assert "dashboard-daily-maintenance" not in source


def test_audit_retention_180_days_and_execution_safety():
    runtime = (Path(mea.__file__).parent / "mea_tariff_runtime.py").read_text(encoding="utf-8")
    assert "AUDIT_RETENTION_DAYS = 180" in runtime
    assert "mqtt.publish" not in runtime
    assert "subprocess" not in runtime
