"""HOTFIX PACK 14: live MEA category parsing and safe provider diagnostics.

The official MEA tariff URL is a generic category page.  This module identifies
Residential Type 1.2 from bounded section evidence instead of requiring one exact
English label.  It exposes metadata only; raw source bodies are never retained in
runtime diagnostics or returned by an API.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import datetime
from html import unescape
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import app as app_module
from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_provider as mea

app = app_module.app
PARSER_VERSION = "mea-1.1-live-category"
EXPECTED = mea.EXPECTED_TARIFF_TYPE
_SAFE_DEBUG: Dict[str, Any] = {
    "expected_category": EXPECTED,
    "parser_version": PARSER_VERSION,
    "parser_stage": "not_started",
    "parser_error_code": None,
    "category_candidates": [],
}

ERROR_CODES = {
    "source_fetch_failed", "category_not_found", "category_ambiguous",
    "category_mismatch", "tier_parse_failed", "ft_not_found",
    "ft_period_expired",
}


def _clean(value: Any) -> str:
    text = unescape(str(value or "")).lower()
    text = text.replace("ประเภทที่", "ประเภท ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _safe_section(value: str, limit: int = 220) -> str:
    """Return normalized short evidence, never the full page."""
    return " ".join(value.split())[:limit]


def _section_candidates(text: str) -> list[str]:
    # HTML has already been reduced to text by the base provider.  Bound candidates
    # around category/subcategory markers so a generic parent page is safe.
    patterns = (
        r"ประเภท\s*1\s*บ้านอยู่อาศัย.{0,180}",
        r"ประเภท\s*ที่?\s*1\s*บ้านอยู่อาศัย.{0,180}",
        r"บ้านอยู่อาศัย.{0,100}(?:1\.2|ประเภท\s*1\.2).{0,140}",
        r"(?:ประเภท\s*)?1\.2.{0,220}",
        r"residential\s*(?:type)?\s*1\.2.{0,180}",
        r"อัตราปกติ\s*ใช้พลังงานไฟฟ้าเกิน\s*150\s*หน่วยต่อเดือน.{0,180}",
    )
    found: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            item = _safe_section(match.group(0))
            if item and item not in found:
                found.append(item)
    return found[:12]


def _positive_other_category(text: str) -> Optional[str]:
    # Reject only positive evidence for another top-level category near a tariff
    # heading.  A page listing all categories is not by itself a mismatch.
    target_markers = bool(re.search(r"(?:1\.2|เกิน\s*150\s*หน่วย|residential\s*(?:type)?\s*1\.2)", text, re.I))
    if target_markers:
        return None
    matches = re.findall(r"ประเภท\s*(?:ที่\s*)?([2-8])\s*([^\d]{0,60})", text, re.I)
    if len(matches) == 1:
        number, label = matches[0]
        return f"MEA Type {number} {_safe_section(label, 60)}".strip()
    english = re.search(r"(?:business|small business|medium business|large business|temporary)\s+type", text, re.I)
    return _safe_section(english.group(0), 80) if english else None


def _category_evidence(text: str, tiers: list[Dict[str, Any]], service_charge: Optional[float]) -> Dict[str, Any]:
    normalized = _clean(text)
    candidates = _section_candidates(normalized)
    parent = bool(re.search(r"ประเภท\s*1\s*บ้านอยู่อาศัย|ประเภท\s*ที่?\s*1\s*บ้านอยู่อาศัย", normalized))
    subcategory = bool(re.search(r"(?:^|\D)1\.2(?:\D|$)", normalized))
    over_150 = bool(re.search(r"เกิน\s*150\s*หน่วย|over\s*150\s*kwh", normalized, re.I))
    residential = "บ้านอยู่อาศัย" in normalized or "residential" in normalized
    progressive = len(tiers) >= 2 and bool(tiers and tiers[-1].get("up_to_kwh") is None)

    score = 0
    methods: list[str] = []
    if parent: score += 20; methods.append("parent_heading")
    if residential: score += 20; methods.append("residential_label")
    if subcategory: score += 35; methods.append("subcategory_1_2")
    if over_150: score += 30; methods.append("over_150_description")
    if progressive: score += 15; methods.append("progressive_tiers")
    if service_charge is not None: score += 5; methods.append("service_charge")

    other = _positive_other_category(normalized)
    if other:
        return {"matched": False, "error": "category_mismatch", "score": 0,
                "method": "positive_other_category", "detected": other,
                "candidates": candidates}
    if subcategory and (residential or parent or over_150):
        detected = EXPECTED
    elif parent and over_150 and progressive:
        detected = EXPECTED
    elif parent and not subcategory:
        # Generic parent page is neutral, not a mismatch.  Continue only when table
        # evidence isolates the >150-unit residential subsection.
        if over_150 and progressive:
            detected = EXPECTED
        else:
            return {"matched": False, "error": "category_ambiguous", "score": score,
                    "method": "+".join(methods) or "parent_only", "detected": "MEA Residential Type 1",
                    "candidates": candidates}
    else:
        return {"matched": False, "error": "category_not_found", "score": score,
                "method": "+".join(methods) or "no_category_evidence", "detected": None,
                "candidates": candidates}
    return {"matched": True, "error": None, "score": min(100, score),
            "method": "+".join(methods), "detected": detected, "candidates": candidates}


def parse_live_base(body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    _SAFE_DEBUG.update({"parser_stage": "base_parse", "parser_error_code": None})
    text = mea._pdf_text(body) if content_type == "application/pdf" else mea._html_text(body)

    tiers: list[Dict[str, Any]] = []
    # Both compact official table text and Thai prose are supported.
    for match in re.finditer(r"(?:ไม่เกิน|ตั้งแต่|up\s*to)\s*([0-9,]+(?:\.\d+)?)\s*(?:หน่วย|kwh)?[^0-9]{0,90}([0-9]+(?:\.\d{2,6})?)", text, re.I):
        limit, rate = mea._number(match.group(1)), mea._number(match.group(2))
        if rate >= 0 and (not tiers or limit > float(tiers[-1]["up_to_kwh"] or 0)):
            tiers.append({"up_to_kwh": limit, "rate": rate})
    unlimited = re.search(r"(?:เกิน|over)\s*([0-9,]+(?:\.\d+)?)\s*(?:หน่วย|kwh)?[^0-9]{0,90}([0-9]+(?:\.\d{2,6})?)", text, re.I)
    if unlimited:
        tiers.append({"up_to_kwh": None, "rate": mea._number(unlimited.group(2))})

    service_match = re.search(r"(?:ค่าบริการ|service\s*charge)[^0-9]{0,50}([0-9]+(?:\.\d+)?)", text, re.I)
    service = mea._number(service_match.group(1)) if service_match else None
    evidence = _category_evidence(text, tiers, service)
    _SAFE_DEBUG.update({
        "category_candidates": evidence["candidates"],
        "category_match_method": evidence["method"],
        "category_match_score": evidence["score"],
        "expected_category": EXPECTED,
        "detected_category": evidence["detected"],
    })
    if not evidence["matched"]:
        _SAFE_DEBUG.update({"parser_stage": "category", "parser_error_code": evidence["error"]})
        raise ValueError(evidence["error"])
    if len(tiers) < 2 or tiers[-1].get("up_to_kwh") is not None:
        _SAFE_DEBUG.update({"parser_stage": "tiers", "parser_error_code": "tier_parse_failed"})
        raise ValueError("tier_parse_failed")

    effective_match = re.search(r"(?:effective|มีผล(?:ตั้งแต่)?)\s*[: ]*([^\n]{4,80})", text, re.I)
    version_match = re.search(r"(?:version|ฉบับ|ประกาศ)\s*[:# ]*([A-Za-z0-9._/-]{2,40})", text, re.I)
    minimum_match = re.search(r"(?:minimum\s*charge|ค่าไฟฟ้าต่ำสุด)[^0-9]{0,40}([0-9]+(?:\.\d+)?)", text, re.I)
    title_match = re.search(r"(?:อัตราค่าไฟฟ้าประเภทต่าง ๆ|อัตราค่าไฟฟ้า|Electricity Tariff)[^\n]{0,100}", text, re.I)
    effective = ""
    if effective_match:
        try: effective = mea._date_iso(effective_match.group(1))
        except Exception: effective = ""
    matched = ["tariff_type", "tiers"] + (["service_charge"] if service is not None else []) + (["effective_date"] if effective else [])
    missing = [name for name in ("service_charge", "effective_date") if name not in matched]
    confidence = "high" if not missing and evidence["score"] >= 70 else "medium"
    _SAFE_DEBUG.update({"parser_stage": "base_complete", "parser_error_code": None,
                        "normalized_sections": evidence["candidates"][:6]})
    return {
        "tariff_name": EXPECTED, "tariff_type": EXPECTED,
        "effective_date": effective, "version": version_match.group(1) if version_match else "",
        "tiers": tiers, "service_charge": service,
        "minimum_charge": mea._number(minimum_match.group(1)) if minimum_match else 0.0,
        "source_url": source_url,
        "source_title": title_match.group(0).strip() if title_match else "Official MEA tariff categories",
        "document_date": effective or None, "parser_confidence": confidence,
        "matched_fields": matched, "missing_fields": missing,
        "parser_version": PARSER_VERSION,
        "category_match_method": evidence["method"], "category_match_score": evidence["score"],
    }


_original_fetch = mea._fetch


def _fetch_with_diagnostics(url: str, allowed_types: Iterable[str]) -> Dict[str, Any]:
    try:
        result = _original_fetch(url, allowed_types)
    except Exception:
        _SAFE_DEBUG.update({"parser_stage": "fetch", "parser_error_code": "source_fetch_failed"})
        raise ValueError("source_fetch_failed")
    meta = {
        "content_type": result.get("content_type"),
        "bytes": len(result.get("body") or b""),
        # urllib's original helper did not retain status; a successful response is 2xx.
        "http_status": int(result.get("http_status") or 200),
    }
    if url == mea.MEA_TARIFF_PAGE:
        _SAFE_DEBUG.update({"base_source_http_status": meta["http_status"],
                            "base_source_content_type": meta["content_type"],
                            "base_source_bytes": meta["bytes"]})
    elif "opendata.mea.or.th" in url:
        _SAFE_DEBUG.update({"ft_source_http_status": meta["http_status"]})
    return result


mea._fetch = _fetch_with_diagnostics
mea.parse_mea_base_document = parse_live_base


class MEATariffProviderHotfix14(mea.MEATariffProvider):
    def fetch_latest(self) -> Dict[str, Any]:
        _SAFE_DEBUG.update({"parser_stage": "fetch_start", "parser_error_code": None,
                            "checked_ts": int(time.time())})
        try:
            result = super().fetch_latest()
            ft_source = result.get("ft_source") or {}
            _SAFE_DEBUG.update({
                "parser_stage": "complete", "parser_error_code": None,
                "ft_latest_period": {"from": result.get("effective_from"), "to": result.get("effective_to")},
                "detected_category": result.get("tariff_type"),
                "source_checksum": result.get("checksum"),
            })
            return result
        except Exception as exc:
            code = str(exc).strip()
            mapping = {
                "official_ft_resource_not_found": "ft_not_found",
                "no_current_official_ft_period": "ft_not_found",
                "ft_period_expired": "ft_period_expired",
                "tariff_category_mismatch": "category_mismatch",
            }
            code = mapping.get(code, code if code in ERROR_CODES else "source_fetch_failed")
            _SAFE_DEBUG.update({"parser_error_code": code,
                                "parser_stage": _SAFE_DEBUG.get("parser_stage") or "provider"})
            raise ValueError(code)


sync.PROVIDERS["mea"] = MEATariffProviderHotfix14()


def provider_debug() -> Dict[str, Any]:
    return {
        "provider": "mea", "official_source_only": True,
        **copy.deepcopy(_SAFE_DEBUG),
    }


@app.get("/api/tariff/provider/debug")
def get_provider_debug() -> Dict[str, Any]:
    return provider_debug()


# Patch status route after EPIC 07.1 runtime installed. Preserve its payload and add
# the same safe parser diagnostics plus a specific candidate/provider status.
from backend import mea_tariff_runtime as runtime  # noqa: E402
_original_status = runtime.tariff_status_071


def tariff_status_hotfix14() -> Dict[str, Any]:
    payload = _original_status()
    diagnostics = copy.deepcopy(_SAFE_DEBUG)
    saved = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
    diagnostics = {**saved, **diagnostics}
    code = diagnostics.get("parser_error_code")
    payload["diagnostics"] = diagnostics
    payload["last_error"] = code or payload.get("last_error")
    if code:
        payload["provider_available"] = code not in {"source_fetch_failed"}
        payload["candidate_status"] = code
    return payload


for route in app.routes:
    if getattr(route, "path", None) == "/api/tariff/status" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = tariff_status_hotfix14
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = tariff_status_hotfix14
