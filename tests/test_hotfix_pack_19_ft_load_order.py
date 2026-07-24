import bcrypt
from fastapi.testclient import TestClient

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_provider as mea
from backend.app_entry import app
from backend import mea_tariff_hotfix19_ft_debug as ft_debug


def test_ft_wrapper_is_bound_last_and_delegates(monkeypatch):
    delegated = {"called": False}

    def parse_ft_with_distinct_status(body, source_url, now=None):
        delegated["called"] = True
        return {
            "ft_rate": 0.3972,
            "effective_from": "2026-05-01",
            "effective_to": "2026-08-31",
            "source_url": source_url,
        }

    mea.parse_ft_csv = parse_ft_with_distinct_status
    ft_debug.bind_runtime_parser()

    assert mea.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic
    assert ft_debug._wrapped_ft_parser is parse_ft_with_distinct_status

    h14._SAFE_DEBUG.clear()
    result = mea.parse_ft_csv(
        b"type,start,end,ft\nresidential,2026-05-01,2026-08-31,0.3972\n",
        "https://opendata.mea.or.th/ft.csv",
    )
    assert delegated["called"] is True
    assert result["ft_rate"] == 0.3972
    assert h14._SAFE_DEBUG["ft_csv_header"] == "type,start,end,ft"
    assert h14._SAFE_DEBUG["ft_csv_column_names"] == ["type", "start", "end", "ft"]
    assert h14._SAFE_DEBUG["ft_csv_row_count"] == 1


def test_ft_wrapper_binding_is_idempotent():
    current_delegate = ft_debug._wrapped_ft_parser
    first = ft_debug.bind_runtime_parser()
    second = ft_debug.bind_runtime_parser()
    assert first is ft_debug.parse_ft_csv_diagnostic
    assert second is ft_debug.parse_ft_csv_diagnostic
    assert mea.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic
    assert ft_debug._wrapped_ft_parser is current_delegate
