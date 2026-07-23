"""HOTFIX PACK 19 FT parser diagnostics without parser behavior changes."""
from __future__ import annotations

import copy
import csv
import io
from datetime import datetime
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_provider as mea

_original_parse_ft_csv = mea.parse_ft_csv


def _safe_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}


def _diagnose_ft_csv(body: bytes, source_url: str, now: Optional[datetime]) -> None:
    current = now or datetime.now(ZoneInfo("Asia/Bangkok"))
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    columns = [str(item or "").strip() for item in (reader.fieldnames or [])]
    header = text.splitlines()[0] if text.splitlines() else ""

    candidate_rows = []
    rejected_rows = []
    detected_dates = []
    detected_value_column = None
    detected_ft_column = None
    seen_periods: set[tuple[str, Optional[str]]] = set()

    for index, row in enumerate(rows):
        safe = _safe_row(row)
        normalized = {key.lower(): value for key, value in safe.items()}
        category = next((value for key, value in normalized.items() if "type" in key or "ประเภท" in key), "")
        rate_key = next((key for key in normalized if key in {"ft", "ft_rate", "rate"} or "อัตรา" in key), None)
        from_key = next((key for key in normalized if "from" in key or "start" in key or "เริ่ม" in key), None)
        to_key = next((key for key in normalized if "to" in key or "end" in key or "สิ้น" in key), None)
        rate_text = normalized.get(rate_key or "", "")
        from_text = normalized.get(from_key or "", "")
        to_text = normalized.get(to_key or "", "")
        detected_value_column = detected_value_column or rate_key
        detected_ft_column = detected_ft_column or rate_key

        reason = None
        if not columns or rate_key is None or from_key is None:
            reason = "unknown_schema"
        elif category and not ("บ้าน" in category or "residential" in category.lower() or "1" in category):
            reason = "date_mismatch"
        elif not rate_text:
            reason = "missing_ft"
        else:
            try:
                effective_from = mea._date_iso(from_text)
                effective_to = mea._date_iso(to_text) if to_text else None
                rate = mea._number(rate_text)
            except Exception:
                reason = "invalid_number"
            else:
                detected_dates.append({"from": effective_from, "to": effective_to})
                period = (effective_from, effective_to)
                if period in seen_periods:
                    reason = "duplicate_period"
                else:
                    seen_periods.add(period)
                    start_dt = datetime.strptime(effective_from, "%Y-%m-%d").replace(tzinfo=current.tzinfo)
                    end_dt = datetime.strptime(effective_to, "%Y-%m-%d").replace(tzinfo=current.tzinfo) if effective_to else None
                    status = "future" if current < start_dt else "expired" if end_dt and current > end_dt.replace(hour=23, minute=59, second=59) else "currently_effective"
                    candidate = {
                        "row_index": index,
                        "ft_rate": rate,
                        "effective_from": effective_from,
                        "effective_to": effective_to,
                        "status": status,
                    }
                    candidate_rows.append(candidate)
                    if status == "future":
                        reason = "future_effective_date"
        if reason:
            rejected_rows.append({"row_index": index, "reason": reason})

    selected = None
    current_rows = [item for item in candidate_rows if item["status"] == "currently_effective"]
    future_rows = [item for item in candidate_rows if item["status"] == "future"]
    if current_rows:
        selected = max(current_rows, key=lambda item: item["effective_from"])
    elif future_rows:
        selected = min(future_rows, key=lambda item: item["effective_from"])

    h14._SAFE_DEBUG.update({
        "ft_csv_header": header,
        "ft_csv_column_names": columns,
        "ft_csv_row_count": len(rows),
        "ft_candidate_rows": copy.deepcopy(candidate_rows),
        "ft_selected_row": copy.deepcopy(selected),
        "ft_rejected_rows": copy.deepcopy(rejected_rows),
        "ft_detected_effective_dates": copy.deepcopy(detected_dates),
        "ft_detected_value_column": detected_value_column,
        "ft_detected_ft_column": detected_ft_column,
        "ft_rejection_reason": rejected_rows[-1]["reason"] if rejected_rows else None,
    })


def parse_ft_csv_diagnostic(body: bytes, source_url: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    _diagnose_ft_csv(body, source_url, now)
    return _original_parse_ft_csv(body, source_url, now)


mea.parse_ft_csv = parse_ft_csv_diagnostic
