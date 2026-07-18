from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend import mea_tariff_hotfix14 as hotfix
from backend import mea_tariff_provider as mea


def test_current_ft_available():
    csv_data = "type,ft_rate,effective_from,effective_to\nบ้านอยู่อาศัย,0.40,2026-05-01,2026-08-31\n".encode()
    result = mea.parse_ft_csv(csv_data, "https://opendata.mea.or.th/current.csv", datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert result["status"] == "currently_effective"


def test_expired_ft_is_not_reused():
    csv_data = "type,ft_rate,effective_from,effective_to\nบ้านอยู่อาศัย,0.40,2025-09-01,2025-12-31\n".encode()
    with pytest.raises(ValueError, match="ft_period_expired"):
        mea.parse_ft_csv(csv_data, "https://opendata.mea.or.th/history.csv", datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Bangkok")))
    assert hotfix._SAFE_DEBUG["parser_error_code"] == "ft_period_expired"


def test_missing_current_ft_is_distinct():
    csv_data = "type,ft_rate,effective_from,effective_to\n".encode()
    with pytest.raises(ValueError, match="ft_not_found"):
        mea.parse_ft_csv(csv_data, "https://opendata.mea.or.th/empty.csv", datetime(2026, 7, 18, tzinfo=ZoneInfo("Asia/Bangkok")))
