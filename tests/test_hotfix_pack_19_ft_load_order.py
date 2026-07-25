from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_provider as mea
from backend.app_entry import app
from backend import mea_tariff_hotfix19_ft_debug as ft_debug
from backend import mea_tariff_hotfix19_ft_parser as ft_parser


def test_app_entry_imports_and_public_binding_api_exists():
    assert app is not None
    assert callable(ft_debug.bind_runtime_parser)
    assert mea.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic
    assert h17.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic


def test_ft_binding_is_idempotent_and_preserves_production_delegate():
    assert ft_debug._wrapped_ft_parser is ft_parser.parse_ft_with_distinct_status
    first = ft_debug.bind_runtime_parser()
    second = ft_debug.bind_runtime_parser()
    assert first is ft_debug.parse_ft_csv_diagnostic
    assert second is ft_debug.parse_ft_csv_diagnostic
    assert mea.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic
    assert h17.parse_ft_csv is ft_debug.parse_ft_csv_diagnostic
    assert ft_debug._wrapped_ft_parser is ft_parser.parse_ft_with_distinct_status


def test_ft_diagnostic_wrapper_remains_active_over_production_parser():
    body = (
        "year,month,type,type_name,ft_rate\n"
        "2026,7,1,ประเภทที่ 1 บ้านอยู่อาศัย,0.3972\n"
    ).encode("utf-8")
    result = mea.parse_ft_csv(body, "https://opendata.mea.or.th/ft.csv")
    assert result["ft_rate"] == 0.3972
