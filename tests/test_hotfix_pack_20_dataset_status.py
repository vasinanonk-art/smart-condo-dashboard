from datetime import datetime
from zoneinfo import ZoneInfo

from backend import mea_tariff_hotfix20_status as hotfix20


NOW = datetime(2026, 7, 25, 10, 0, tzinfo=ZoneInfo("Asia/Bangkok"))
LATEST = {
    "ft_rate": 0.1572,
    "effective_from": "2025-12-01",
    "effective_to": "2025-12-31",
    "publish_date": "2026-02-05",
}


def test_expired_ft_period_is_presented_as_outdated_official_dataset():
    result = hotfix20.project_status(
        {
            "status": "provider_unavailable",
            "candidate_status": "provider_unavailable",
            "last_error": "ft_period_expired",
            "provider_available": False,
            "diagnostics": {"parser_error_code": "ft_period_expired"},
        },
        now=NOW,
        latest=LATEST,
    )

    assert result["status"] == hotfix20.DATASET_OUTDATED
    assert result["candidate_status"] == hotfix20.DATASET_OUTDATED
    assert result["dataset_status"] == hotfix20.DATASET_OUTDATED
    assert result["provider_available"] is True
    assert result["waiting_for_official_update"] is True
    assert result["system_health"] == "healthy"
    assert result["data_health"] == hotfix20.DATASET_OUTDATED
    assert result["latest_official_period"] == {
        "from": "2025-12-01", "to": "2025-12-31", "ft_rate": 0.1572,
    }
    assert result["current_runtime_date"] == "2026-07-25"
    assert result["dataset_age_days"] == 206
    assert result["latest_dataset_publish_date"] == "2026-02-05"


def test_http_failure_remains_provider_unavailable():
    result = hotfix20.project_status(
        {
            "diagnostics": {
                "parser_error_code": "source_fetch_failed",
                "fetch_failure_kind": "http_error",
                "fetch_http_status": 503,
            },
        },
        now=NOW,
        latest=LATEST,
    )
    assert result["candidate_status"] == "provider_unavailable"
    assert result["dataset_status"] == "provider_unavailable"


def test_parser_failure_remains_source_fetch_failed():
    result = hotfix20.project_status(
        {
            "diagnostics": {
                "parser_error_code": "source_fetch_failed",
                "fetch_failure_kind": None,
                "fetch_return_http_status": 200,
            },
        },
        now=NOW,
        latest=LATEST,
    )
    assert result["candidate_status"] == "source_fetch_failed"
    assert result["dataset_status"] == "source_fetch_failed"


def test_fresh_dataset_preserves_normal_candidate():
    result = hotfix20.project_status(
        {
            "status": "candidate_available",
            "candidate_status": "currently_effective",
            "provider_available": True,
            "diagnostics": {"parser_error_code": None},
        },
        now=NOW,
        latest={
            "ft_rate": 0.2,
            "effective_from": "2026-07-01",
            "effective_to": "2026-07-31",
            "publish_date": "2026-07-01",
        },
    )
    assert result["status"] == "candidate_available"
    assert result["candidate_status"] == "currently_effective"
    assert result["dataset_status"] == "current"
    assert result["waiting_for_official_update"] is False
    assert result["system_health"] == "healthy"
    assert result["data_health"] == "healthy"
