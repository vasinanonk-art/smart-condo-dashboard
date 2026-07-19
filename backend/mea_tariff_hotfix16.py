"""HOTFIX PACK 16: follow the official MEA residential detail page.

The MEA landing page is an index containing several tariff categories.  This module
selects only the official residential link, fetches that document with the existing
bounded HTTPS client, isolates subsection 1.2, and starts Ft retrieval only after the
base subsection has parsed successfully.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import urllib.parse
from html import unescape
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, Mapping, Optional

from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_provider as mea

PARSER_VERSION = "mea-1.2-detail-page"
ERROR_CODES = {
    "residential_detail_link_not_found",
    "residential_detail_fetch_failed",
    "type_1_2_section_not_found",
    "type_1_2_section_ambiguous",
    "tier_parse_failed",
    "ft_not_found",
    "ft_period_expired",
    "source_fetch_failed",
}


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[Dict[str, str]] = []
        self._href: Optional[str] = None
        self._parts: list[str] = []
        self._context: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        self._context.append(text)
        self._context = self._context[-18:]
        if self._href is not None:
            self._parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append({
                "href": self._href,
                "text": " ".join(self._parts),
                "context": " ".join(self._context[-12:]),
            })
            self._href = None
            self._parts = []


def _normal(value: Any) -> str:
    text = unescape(str(value or "")).lower().replace("ประเภทที่", "ประเภท ")
    return " ".join(re.sub(r"[^a-z0-9ก-๙./_-]+", " ", text).split())


def _select_residential_link(index_body: bytes, index_url: str) -> Dict[str, Any]:
    parser = _AnchorCollector()
    parser.feed(index_body.decode("utf-8", errors="replace"))
    candidates: list[Dict[str, Any]] = []
    for anchor in parser.anchors:
        evidence = _normal(f"{anchor['context']} {anchor['text']}")
        score = 0
        if "บ้านอยู่อาศัย" in evidence or "residential" in evidence:
            score += 60
        if re.search(r"ประเภท\s*1(?:\D|$)", evidence):
            score += 30
        if "ดูเนื้อหา" in evidence or "รายละเอียด" in evidence or "view" in evidence:
            score += 5
        if re.search(r"ประเภท\s*[2-8](?:\D|$)", evidence):
            score -= 100
        try:
            resolved = urllib.parse.urljoin(index_url, anchor["href"])
            mea._safe_url(resolved)
        except Exception:
            continue
        if score > 0:
            candidates.append({"url": resolved, "score": score, "evidence": h14._safe_section(evidence, 180)})
    candidates.sort(key=lambda item: item["score"], reverse=True)
    h14._SAFE_DEBUG["residential_link_candidates"] = [
        {"url": item["url"], "score": item["score"], "evidence": item["evidence"]}
        for item in candidates[:6]
    ]
    if not candidates or candidates[0]["score"] < 60:
        raise ValueError("residential_detail_link_not_found")
    if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"] and candidates[0]["url"] != candidates[1]["url"]:
        raise ValueError("residential_detail_link_not_found")
    return candidates[0]


def _html_to_structured_text(body: bytes) -> str:
    html = body.decode("utf-8", errors="replace")
    html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", html)
    html = re.sub(r"(?i)</?(?:h[1-6]|section|article|div|li|tr|p|br|table|thead|tbody)[^>]*>", "\n", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    lines = [" ".join(unescape(line).split()) for line in html.splitlines()]
    return "\n".join(line for line in lines if line)


def _type_1_2_heading(line: str) -> bool:
    value = _normal(line)
    return bool(
        re.search(r"(?:^|\s)1\.2(?:\s|$)", value)
        or "บ้านอยู่อาศัย ประเภท 1.2" in value
        or "บ้านอยู่อาศัยประเภท 1.2" in value
        or "residential type 1.2" in value
        or ("อัตราปกติ" in value and "เกิน 150 หน่วย" in value)
    )


def _next_sibling(line: str) -> bool:
    value = _normal(line)
    if re.search(r"(?:^|\s)1\.(?:[3-9]|[1-9][0-9])(?:\s|$)", value):
        return True
    if re.search(r"ประเภท\s*[2-8](?:\D|$)", value):
        return True
    return False


def _extract_type_1_2_section(detail_body: bytes, content_type: str) -> Dict[str, Any]:
    text = mea._pdf_text(detail_body) if content_type == "application/pdf" else _html_to_structured_text(detail_body)
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if _type_1_2_heading(line)]
    if not starts:
        raise ValueError("type_1_2_section_not_found")
    # Adjacent matches from one wrapped heading count as one section.
    groups: list[list[int]] = []
    for index in starts:
        if not groups or index - groups[-1][-1] > 2:
            groups.append([index])
        else:
            groups[-1].append(index)
    if len(groups) != 1:
        raise ValueError("type_1_2_section_ambiguous")
    start = groups[0][0]
    end = len(lines)
    for index in range(groups[0][-1] + 1, len(lines)):
        if _next_sibling(lines[index]):
            end = index
            break
    section_lines = lines[start:end]
    section = "\n".join(section_lines)
    if len(section.strip()) < 40:
        raise ValueError("type_1_2_section_not_found")
    heading = h14._safe_section(lines[start], 180)
    return {"text": section, "heading": heading, "length": len(section)}


def _parse_type_1_2(detail_body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    bounded = _extract_type_1_2_section(detail_body, content_type)
    section = bounded["text"]
    normalized = _normal(section)
    evidence = bool(
        "1.2" in normalized
        and ("บ้านอยู่อาศัย" in normalized or "residential" in normalized or "เกิน 150 หน่วย" in normalized)
    )
    if not evidence:
        raise ValueError("type_1_2_section_not_found")

    tier_patterns = (
        (150.0, r"(?:ไม่เกิน|up\s*to)\s*150\s*(?:หน่วย|kwh)?[^0-9]{0,100}([0-9]+(?:\.[0-9]{2,6})?)"),
        (400.0, r"(?:151\s*[-–]\s*400|เกิน\s*150\s*(?:หน่วย)?\s*(?:แต่)?ไม่เกิน\s*400|up\s*to\s*400)[^0-9]{0,100}([0-9]+(?:\.[0-9]{2,6})?)"),
    )
    tiers: list[Dict[str, Any]] = []
    for limit, pattern in tier_patterns:
        match = re.search(pattern, section, re.I)
        if match:
            tiers.append({"up_to_kwh": limit, "rate": mea._number(match.group(1))})
    unlimited = re.search(r"(?:เกิน\s*400|over\s*400)\s*(?:หน่วย|kwh)?[^0-9]{0,100}([0-9]+(?:\.[0-9]{2,6})?)", section, re.I)
    if unlimited:
        tiers.append({"up_to_kwh": None, "rate": mea._number(unlimited.group(1))})
    if [item.get("up_to_kwh") for item in tiers] != [150.0, 400.0, None]:
        raise ValueError("tier_parse_failed")

    service_match = re.search(r"(?:ค่าบริการ|service\s*charge)[^0-9]{0,60}([0-9]+(?:\.[0-9]+)?)", section, re.I)
    if not service_match:
        raise ValueError("tier_parse_failed")
    effective_match = re.search(r"(?:effective|มีผล(?:ตั้งแต่)?)\s*[: ]*([^\n]{4,80})", section, re.I)
    version_match = re.search(r"(?:version|ฉบับ|ประกาศ)\s*[:# ]*([A-Za-z0-9._/-]{2,40})", section, re.I)
    effective = ""
    if effective_match:
        try:
            effective = mea._date_iso(effective_match.group(1))
        except Exception:
            effective = ""
    h14._SAFE_DEBUG.update({
        "type_1_2_heading": bounded["heading"],
        "type_1_2_section_length": bounded["length"],
        "category_match_score": 100,
        "category_match_method": "residential_detail_link+bounded_type_1_2_subsection+tier_structure+service_charge",
        "detected_category": mea.EXPECTED_TARIFF_TYPE,
        "parser_stage": "base_complete",
        "parser_error_code": None,
        "normalized_sections": [bounded["heading"]],
    })
    return {
        "tariff_name": mea.EXPECTED_TARIFF_TYPE,
        "tariff_type": mea.EXPECTED_TARIFF_TYPE,
        "effective_date": effective,
        "version": version_match.group(1) if version_match else "",
        "tiers": tiers,
        "service_charge": mea._number(service_match.group(1)),
        "minimum_charge": 0.0,
        "source_url": source_url,
        "source_title": "Official MEA Residential Type 1.2 tariff",
        "document_date": effective or None,
        "parser_confidence": "high" if effective else "medium",
        "matched_fields": ["tariff_type", "tiers", "service_charge"] + (["effective_date"] if effective else []),
        "missing_fields": [] if effective else ["effective_date"],
        "parser_version": PARSER_VERSION,
        "category_match_method": h14._SAFE_DEBUG["category_match_method"],
        "category_match_score": 100,
    }


def _map_error(exc: BaseException) -> str:
    raw = str(exc).strip()
    mapping = {
        "official_ft_resource_not_found": "ft_not_found",
        "no_current_official_ft_period": "ft_not_found",
        "no_official_ft_rows": "ft_not_found",
        "ft_period_expired": "ft_period_expired",
    }
    if raw in ERROR_CODES:
        return raw
    return mapping.get(raw, "source_fetch_failed")


class MEATariffProviderHotfix16(mea.MEATariffProvider):
    name = "mea"
    remote = True

    def fetch_latest(self) -> Dict[str, Any]:
        h14._SAFE_DEBUG.update({
            "parser_version": PARSER_VERSION,
            "parser_stage": "index_fetch",
            "parser_error_code": None,
            "checked_ts": int(time.time()),
            "expected_category": mea.EXPECTED_TARIFF_TYPE,
            "ft_source_http_status": None,
            "ft_latest_period": None,
        })
        sync._audit("remote_check_started", "started", "provider=mea")
        try:
            index_source = mea._fetch(mea.MEA_TARIFF_PAGE, {"text/html", "application/pdf"})
            h14._SAFE_DEBUG.update({
                "index_source_http_status": int(index_source.get("http_status") or 200),
                "index_source_url": index_source.get("url"),
                "base_source_http_status": int(index_source.get("http_status") or 200),
                "base_source_content_type": index_source.get("content_type"),
                "base_source_bytes": len(index_source.get("body") or b""),
                "parser_stage": "residential_link",
            })
            if index_source.get("content_type") == "application/pdf":
                raise ValueError("residential_detail_link_not_found")
            selected = _select_residential_link(index_source["body"], index_source["url"])
            detail_url = selected["url"]
            h14._SAFE_DEBUG["residential_detail_url"] = detail_url

            # Internal sequential fetch in one explicit check; retain the existing guard
            # against unrelated request-path fetching.
            mea._LAST_REMOTE_FETCH = 0.0
            h14._SAFE_DEBUG["parser_stage"] = "residential_detail_fetch"
            try:
                detail_source = mea._fetch(detail_url, {"text/html", "application/pdf"})
            except Exception as exc:
                raise ValueError("residential_detail_fetch_failed") from exc
            h14._SAFE_DEBUG.update({
                "detail_source_http_status": int(detail_source.get("http_status") or 200),
                "detail_source_content_type": detail_source.get("content_type"),
                "detail_source_bytes": len(detail_source.get("body") or b""),
                "parser_stage": "type_1_2_section",
            })
            base = _parse_type_1_2(detail_source["body"], detail_source["content_type"], detail_source["url"])

            # Ft retrieval is deliberately after successful base parsing.
            mea._LAST_REMOTE_FETCH = 0.0
            h14._SAFE_DEBUG["parser_stage"] = "ft_metadata_fetch"
            metadata_source = mea._fetch(mea.MEA_FT_DATASET_API, {"application/json", "text/json"})
            h14._SAFE_DEBUG["ft_source_http_status"] = int(metadata_source.get("http_status") or 200)
            metadata = json.loads(metadata_source["body"].decode("utf-8"))
            package = metadata.get("result") if isinstance(metadata, Mapping) else None
            if not isinstance(package, Mapping):
                raise ValueError("ft_not_found")
            ft_url = mea._pick_ft_resource(package)
            mea._LAST_REMOTE_FETCH = 0.0
            h14._SAFE_DEBUG["parser_stage"] = "ft_resource_fetch"
            ft_source = mea._fetch(ft_url, {"text/csv", "application/csv", "text/plain", "application/octet-stream"})
            h14._SAFE_DEBUG["ft_source_http_status"] = int(ft_source.get("http_status") or 200)
            ft = mea.parse_ft_csv(ft_source["body"], ft_source["url"])
            h14._SAFE_DEBUG["ft_latest_period"] = {"from": ft.get("effective_from"), "to": ft.get("effective_to")}

            base_archive = mea._archive_source("base", detail_source, base)
            ft_archive = mea._archive_source("ft", ft_source, ft)
            result = {
                **base,
                "ft_rate": ft["ft_rate"],
                "vat_percent": 7.0,
                "provider": "mea",
                "source": "mea",
                "effective_from": max(base.get("effective_date") or "", ft.get("effective_from") or ""),
                "effective_to": ft.get("effective_to"),
                "base_tariff_source": {key: base_archive.get(key) for key in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
                "ft_source": {key: ft_archive.get(key) for key in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
                "fetched_at": int(time.time()),
                "checksum": hashlib.sha256((base_archive["checksum"] + ft_archive["checksum"]).encode()).hexdigest(),
            }
            result["effective_date"] = result["effective_from"]
            result["version"] = result.get("version") or f"MEA-{result['effective_date']}-FT-{ft['effective_from']}"
            result["matched_fields"] = sorted(set(base.get("matched_fields", [])) | {"ft_rate", "vat_percent", "effective_period", "source_documents"})
            result["missing_fields"] = sorted(set(base.get("missing_fields", [])))
            result["parser_confidence"] = "high" if base.get("parser_confidence") == "high" and ft.get("parser_confidence") == "high" and not result["missing_fields"] else "medium"
            h14._SAFE_DEBUG.update({"parser_stage": "complete", "parser_error_code": None, "source_checksum": result["checksum"]})
            sync._audit("remote_check_succeeded", "ok", f"checksum={result['checksum'][:16]}", result["version"])
            return result
        except Exception as exc:
            code = _map_error(exc)
            h14._SAFE_DEBUG.update({"parser_error_code": code, "parser_stage": h14._SAFE_DEBUG.get("parser_stage") or "provider"})
            sync._audit("remote_check_failed", "error", code)
            raise ValueError(code)


sync.PROVIDERS["mea"] = MEATariffProviderHotfix16()


def provider_debug() -> Dict[str, Any]:
    allowed = {
        "expected_category", "parser_version", "parser_stage", "parser_error_code",
        "index_source_http_status", "index_source_url", "residential_detail_url",
        "detail_source_http_status", "detail_source_content_type", "detail_source_bytes",
        "base_source_http_status", "base_source_content_type", "base_source_bytes",
        "type_1_2_heading", "type_1_2_section_length", "category_match_score",
        "category_match_method", "detected_category", "ft_source_http_status",
        "ft_latest_period", "checked_ts", "source_checksum", "normalized_sections",
        "residential_link_candidates",
    }
    return {"provider": "mea", "official_source_only": True, **{key: copy.deepcopy(value) for key, value in h14._SAFE_DEBUG.items() if key in allowed}}


# Replace HOTFIX 14's debug route with the narrower HOTFIX 16 payload.
for route in h14.app.routes:
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = provider_debug
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = provider_debug
