"""HOTFIX PACK 19 production FT CSV parser.

Supports the current MEA year/month/type/type_name/ft_rate schema while preserving
legacy date-column schemas. This module changes only FT parsing behavior.
"""
from __future__ import annotations

import csv
import io
import math
from calendar import monthrange
from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_provider as mea

_PRODUCTION_COLUMNS = {"year", "month", "type", "type_name", "ft_rate"}


def _normalized_row(row: Mapping[str, Any]) -> Dict[str, str]:
    return {str(key or "").strip().lower(): str(value or "").strip() for key, value in row.items()}


def _is_residential(type_value: str, type_name: str) -> bool:
    type_text = " ".join(type_value.lower().split())
    name_text = " ".join(type_name.lower().split())
    combined = f"{type_text} {name_text}"
    return bool(
        "บ้านอยู่อาศัย" in type_name
        or "residential" in combined
        or type_text in {"1", "01", "type 1", "ประเภท 1", "ประเภทที่ 1"}
        or name_text.startswith("ประเภท 1 ")
        or name_text.startswith("ประเภทที่ 1 ")
    )


def _parse_year_month(row: Mapping[str, str]) -> tuple[int, int]:
    try:
        year = int(row.get("year", ""))
        month = int(row.get("month", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_ft_period") from exc
    if year >= 2500:
        year -= 543
    if year < 2000 or year > 2100 or month < 1 or month > 12:
        raise ValueError("invalid_ft_period")
    return year, month


def _parse_production_schema(rows: list[Dict[str, str]], source_url: str, now: datetime) -> Dict[str, Any]:
    applicable: Dict[tuple[int, int], Dict[str, Any]] = {}
    saw_residential = False

    for row in rows:
        type_value = row.get("type", "")
        type_name = row.get("type_name", "")
        if not type_value or not type_name:
            raise ValueError("ft_row_incomplete")
        if not _is_residential(type_value, type_name):
            continue
        saw_residential = True
        year, month = _parse_year_month(row)
        rate_text = row.get("ft_rate", "")
        if not rate_text:
            raise ValueError("ft_row_incomplete")
        try:
            rate = mea._number(rate_text)
        except Exception as exc:
            raise ValueError("invalid_ft_rate") from exc
        if not math.isfinite(rate):
            raise ValueError("invalid_ft_rate")

        period = (year, month)
        if period in applicable:
            raise ValueError("duplicate_ft_period")

        effective_from = f"{year:04d}-{month:02d}-01"
        last_day = monthrange(year, month)[1]
        effective_to = f"{year:04d}-{month:02d}-{last_day:02d}"
        applicable[period] = {
            "ft_rate": rate,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "status": "future" if (year, month) > (now.year, now.month) else "currently_effective",
            "source_url": source_url,
        }

    if not saw_residential or not applicable:
        raise ValueError("no_current_official_ft_period")

    non_future = [item for period, item in applicable.items() if period <= (now.year, now.month)]
    if not non_future:
        raise ValueError("no_current_official_ft_period")
    selected = max(non_future, key=lambda item: item["effective_from"])
    selected.update({
        "source_title": "MEA Ft rate by customer type",
        "parser_confidence": "high",
        "parser_version": mea.PARSER_VERSION,
    })
    return selected


def parse_ft_with_distinct_status(body: bytes, source_url: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(ZoneInfo("Asia/Bangkok"))
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = [_normalized_row(row) for row in reader]
    columns = {str(item or "").strip().lower() for item in (reader.fieldnames or [])}
    if _PRODUCTION_COLUMNS.issubset(columns):
        return _parse_production_schema(rows, source_url, now)
    return _legacy_parse_ft_csv(body, source_url, now)


_legacy_parse_ft_csv = mea.parse_ft_csv
mea.parse_ft_csv = parse_ft_with_distinct_status
setattr(h17, "parse_ft_csv", parse_ft_with_distinct_status)
