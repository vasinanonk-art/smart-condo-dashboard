"""Small runtime refinements for HOTFIX PACK 14."""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import mea_tariff_hotfix14 as hotfix
from backend import mea_tariff_provider as mea

for key, default in {
    "base_source_http_status": None,
    "base_source_content_type": None,
    "base_source_bytes": None,
    "category_candidates": [],
    "category_match_method": None,
    "category_match_score": 0,
    "expected_category": hotfix.EXPECTED,
    "detected_category": None,
    "ft_source_http_status": None,
    "ft_latest_period": None,
    "parser_stage": "not_started",
    "parser_error_code": None,
    "normalized_sections": [],
}.items():
    hotfix._SAFE_DEBUG.setdefault(key, default)


def _positive_other_category_safe(text: str) -> Optional[str]:
    # The live official page is an index containing Types 1-8.  A Residential Type 1
    # parent heading therefore neutralizes other navigation labels.  Mismatch is only
    # positive when the bounded content identifies another category without Type 1.
    parent = bool(re.search(r"ประเภท\s*(?:ที่\s*)?1\s*บ้านอยู่อาศัย|residential\s+type\s+1", text, re.I))
    target = bool(re.search(r"(?:1\.2|เกิน\s*150\s*หน่วย|residential\s*(?:type)?\s*1\.2)", text, re.I))
    if parent or target:
        return None
    matches = re.findall(r"ประเภท\s*(?:ที่\s*)?([2-8])\s*([^\d]{0,60})", text, re.I)
    if len(matches) == 1:
        number, label = matches[0]
        return f"MEA Type {number} {hotfix._safe_section(label, 60)}".strip()
    english = re.search(r"(?:business|small business|medium business|large business|temporary)\s+type", text, re.I)
    return hotfix._safe_section(english.group(0), 80) if english else None


hotfix._positive_other_category = _positive_other_category_safe

_original_parse_ft = mea.parse_ft_csv


def parse_ft_with_distinct_status(body: bytes, source_url: str, now: Optional[datetime] = None):
    now = now or datetime.now(ZoneInfo("Asia/Bangkok"))
    try:
        result = _original_parse_ft(body, source_url, now)
        hotfix._SAFE_DEBUG["ft_latest_period"] = {
            "from": result.get("effective_from"), "to": result.get("effective_to"),
            "status": result.get("status"),
        }
        return result
    except ValueError as exc:
        if str(exc) != "no_current_official_ft_period":
            raise
        # Inspect dates only; never reuse an expired rate.  This determines whether
        # the official dataset is historical-only or simply lacks parseable periods.
        rows = list(csv.DictReader(io.StringIO(body.decode("utf-8-sig", errors="replace"))))
        latest_to = None
        for row in rows:
            normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
            to_text = next((v for k, v in normalized.items() if "to" in k or "end" in k or "สิ้น" in k), "")
            try:
                value = mea._date_iso(to_text) if to_text else None
            except Exception:
                value = None
            if value and (latest_to is None or value > latest_to):
                latest_to = value
        hotfix._SAFE_DEBUG["ft_latest_period"] = {"to": latest_to, "status": "expired" if latest_to else "not_found"}
        if latest_to and datetime.strptime(latest_to, "%Y-%m-%d").date() < now.date():
            hotfix._SAFE_DEBUG.update({"parser_stage": "ft", "parser_error_code": "ft_period_expired"})
            raise ValueError("ft_period_expired")
        hotfix._SAFE_DEBUG.update({"parser_stage": "ft", "parser_error_code": "ft_not_found"})
        raise ValueError("ft_not_found")


mea.parse_ft_csv = parse_ft_with_distinct_status
