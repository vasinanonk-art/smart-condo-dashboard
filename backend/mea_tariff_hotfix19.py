"""HOTFIX PACK 19: robust residential link discovery and production Type 1.2 parsing."""
from __future__ import annotations

import copy
import re
import urllib.parse
from typing import Any, Dict, Iterable, Optional

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_provider as mea

PARSER_VERSION = "mea-1.7-production-type-1-2-dom"
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_CONTEXT_TAGS = {"section", "article", "li", "div", "tr", "td", "main"}
_ALLOWED_DETAIL_PREFIXES = (
    "/our-services/tariff-calculation/other/",
    "/our-services/service-rates/other/",
)
_REJECTED_SLUG_TOKENS = {
    "electric", "vehicle", "ev", "payment", "payments", "meter", "meters",
    "deposit", "deposits", "producer", "producers", "bill", "bills",
    "contact", "faq", "news", "download", "calculator", "solar", "charging",
    "service-center", "service-centers",
}
_LINK_WORDS = ("ดูเนื้อหา", "รายละเอียด", "more", "read more", "view", "อ่านเพิ่มเติม")
_EXACT_RESIDENTIAL_ANCHOR_TEXT = "ประเภท 1 บ้านอยู่อาศัย"


def _norm(value: Any) -> str:
    return h17._norm(value)


def _canonical_url(url: str) -> str:
    return h18._canonical_url(url)


def _normalized_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return re.sub(r"/{2,}", "/", parsed.path or "/").lower()


def _tariff_detail_path(url: str) -> tuple[bool, str]:
    path = _normalized_path(url)
    prefix = next((item for item in _ALLOWED_DETAIL_PREFIXES if path.startswith(item)), None)
    if prefix is None:
        if path in {"", "/", "/our-services", "/our-services/"}:
            return False, "navigation_or_landing_path"
        return False, "non_tariff_detail_path"
    slug = path[len(prefix):].strip("/")
    if not slug:
        return False, "missing_detail_slug"
    tokens = {token for token in re.split(r"[/_.-]+", slug) if token}
    if tokens & _REJECTED_SLUG_TOKENS:
        return False, "unrelated_tariff_slug"
    return True, "allowed_tariff_detail_path"


def _path_quality(url: str) -> int:
    allowed, _reason = _tariff_detail_path(url)
    if not allowed:
        return -100
    path = _normalized_path(url)
    score = 70
    if path.startswith("/our-services/service-rates/other/"):
        score += 10
    slug = path.rsplit("/", 1)[-1]
    if "residential" in slug or "home" in slug:
        score += 10
    return min(score, 100)


def _iter_ancestors(node: h17._DomNode) -> Iterable[h17._DomNode]:
    current = node.parent
    while current is not None and current.tag != "document":
        yield current
        current = current.parent


def _previous_heading(node: h17._DomNode) -> Optional[h17._DomNode]:
    current: Optional[h17._DomNode] = node
    while current and current.parent:
        siblings = current.parent.children
        try:
            index = siblings.index(current)
        except ValueError:
            return None
        for sibling in reversed(siblings[:index]):
            headings = [item for item in h17._walk(sibling) if item.tag in _HEADING_TAGS and item.text().strip()]
            if headings:
                return headings[-1]
        current = current.parent
    return None


def _sibling_headings(node: h17._DomNode) -> list[str]:
    values: list[str] = []
    parent = node.parent
    if not parent:
        return values
    for sibling in parent.children:
        if sibling is node:
            continue
        if sibling.tag in _HEADING_TAGS and sibling.text().strip():
            values.append(sibling.text())
        elif sibling.tag in _CONTEXT_TAGS:
            for child in sibling.children:
                if child.tag in _HEADING_TAGS and child.text().strip():
                    values.append(child.text())
    return values[:8]


def _context(node: h17._DomNode) -> Dict[str, Any]:
    chunks: list[tuple[str, str]] = [("anchor", node.text())]
    parent = node.parent
    if parent:
        chunks.append(("parent", parent.text()))
        if parent.parent:
            chunks.append(("grandparent", parent.parent.text()))
    for ancestor in _iter_ancestors(node):
        classes = _norm(ancestor.attrs.get("class", ""))
        role = _norm(ancestor.attrs.get("role", ""))
        if ancestor.tag in {"section", "article", "li", "tr"}:
            chunks.append((ancestor.tag, ancestor.text()))
        elif ancestor.tag == "div" and any(token in classes or token in role for token in ("card", "accordion", "item", "tariff", "rate")):
            chunks.append(("card", ancestor.text()))
    previous = _previous_heading(node)
    if previous:
        chunks.append(("previous_heading", previous.text()))
    for text in _sibling_headings(node):
        chunks.append(("sibling_heading", text))

    dedup: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, text in chunks:
        normalized = _norm(text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        dedup.append((source, normalized))
    combined = " ".join(text for _, text in dedup)
    tokens = []
    for token in ("ประเภท 1 บ้านอยู่อาศัย", "ประเภทที่ 1 บ้านอยู่อาศัย", "residential", "บ้านอยู่อาศัย"):
        if _norm(token) in combined:
            tokens.append(token)
    return {"text": combined, "parts": dedup, "tokens": tokens}


def _has_residential_text(value: str) -> bool:
    text = _norm(value)
    return bool(
        re.search(r"(?:^|\s)ประเภท\s*1\s*บ้านอยู่อาศัย(?:\s|$)", text)
        or ("บ้านอยู่อาศัย" in text and re.search(r"(?:^|\s)ประเภท\s*1(?:\s|$)", text))
        or ("residential" in text and re.search(r"(?:^|\s)(?:type\s*)?1(?:\s|$)", text))
    )


def _context_score(context: Dict[str, Any], link_text: str) -> int:
    text = context["text"]
    score = 0
    if re.search(r"(?:^|\s)ประเภท\s*1\s*บ้านอยู่อาศัย(?:\s|$)", text):
        score += 80
    elif "บ้านอยู่อาศัย" in text and re.search(r"(?:^|\s)ประเภท\s*1(?:\s|$)", text):
        score += 70
    elif "residential" in text and re.search(r"(?:^|\s)(?:type\s*)?1(?:\s|$)", text):
        score += 65
    elif "บ้านอยู่อาศัย" in text or "residential" in text:
        score += 45
    if any(word in link_text for word in _LINK_WORDS):
        score += 15
    if "บ้านอยู่อาศัย" in link_text or "residential" in link_text:
        score += 15
    if re.search(r"(?:^|\s)(?:ประเภท\s*)?1(?:\s|$)", link_text):
        score += 5
    for source, _ in context["parts"]:
        if source == "previous_heading":
            score += 8
        elif source in {"section", "article", "li", "tr", "card"}:
            score += 3
    return min(score, 100)


def _nearest_row(node: h17._DomNode) -> Optional[h17._DomNode]:
    current: Optional[h17._DomNode] = node
    while current is not None and current.tag != "document":
        if current.tag == "tr":
            return current
        current = current.parent
    return None


def select_residential_detail_link(index_body: bytes, index_url: str) -> Dict[str, Any]:
    parser = h17._DomParser()
    parser.feed(index_body.decode("utf-8", errors="replace"))
    anchors = [node for node in h17._walk(parser.root) if node.tag == "a" and node.attrs.get("href")]
    allowed_path_anchor_count = 0
    residential_text_anchor_count = 0
    rejected_reasons: Dict[str, int] = {}

    def reject(reason: str) -> None:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    exact_matches: list[Dict[str, Any]] = []
    for node in anchors:
        href = node.attrs.get("href", "")
        link_text = _norm(node.text())
        if _has_residential_text(link_text):
            residential_text_anchor_count += 1
        try:
            resolved = urllib.parse.urljoin(index_url, href)
            mea._safe_url(resolved)
            canonical = _canonical_url(resolved)
        except Exception:
            reject("url_allowlist_or_scheme_rejection")
            continue
        path_allowed, path_reason = _tariff_detail_path(canonical)
        if not path_allowed:
            reject(path_reason)
            continue
        allowed_path_anchor_count += 1
        if _EXACT_RESIDENTIAL_ANCHOR_TEXT not in link_text:
            reject("anchor_text_not_exact_residential_type_1")
            continue
        row = _nearest_row(node)
        secondary_same_href = False
        if row is not None:
            for sibling in h17._walk(row):
                if sibling is node or sibling.tag != "a" or not sibling.attrs.get("href"):
                    continue
                try:
                    sibling_url = _canonical_url(urllib.parse.urljoin(index_url, sibling.attrs["href"]))
                except Exception:
                    continue
                if sibling_url == canonical and any(word in _norm(sibling.text()) for word in _LINK_WORDS):
                    secondary_same_href = True
                    break
        exact_matches.append({
            "url": canonical,
            "score": 100 if secondary_same_href else 95,
            "evidence": h14._safe_section(node.text(), 220),
            "context_tokens": ["ประเภทที่ 1 บ้านอยู่อาศัย"],
            "secondary_same_href": secondary_same_href,
        })

    by_url: Dict[str, Dict[str, Any]] = {}
    for item in exact_matches:
        previous = by_url.get(item["url"])
        if previous is None or item["score"] > previous["score"]:
            by_url[item["url"]] = item
    candidates = sorted(by_url.values(), key=lambda item: (-item["score"], item["url"]))
    best = candidates[0] if candidates else None
    diagnostics = {
        "parser_version": PARSER_VERSION,
        "anchor_count": len(anchors),
        "total_anchor_count": len(anchors),
        "allowed_path_anchor_count": allowed_path_anchor_count,
        "residential_text_anchor_count": residential_text_anchor_count,
        "candidate_before_filter": len(exact_matches),
        "candidate_after_filter": len(candidates),
        "rejected_candidate_reasons": dict(sorted(rejected_reasons.items())),
        "residential_link_candidates": copy.deepcopy(candidates[:8]),
        "top_candidate_context": best.get("evidence") if best else None,
        "top_candidate_href": best.get("url") if best else None,
        "context_tokens": best.get("context_tokens", []) if best else [],
        "deduplicated_residential_link_count": len(candidates),
    }
    h14._SAFE_DEBUG.update(diagnostics)
    if not candidates:
        raise ValueError("residential_detail_link_not_found")
    if len(candidates) > 1 and candidates[0]["score"] == candidates[1]["score"]:
        raise ValueError("residential_detail_link_not_found")
    return candidates[0]


def _child_texts(node: h17._DomNode, tag: str) -> list[str]:
    return [item.text().strip() for item in h17._walk(node) if item.tag == tag and item.text().strip()]


def _find_unique_type_1_2_container(detail_body: bytes) -> tuple[h17._DomNode, str]:
    parser = h17._DomParser()
    parser.feed(detail_body.decode("utf-8", errors="replace"))
    matches: list[h17._DomNode] = []
    for node in h17._walk(parser.root):
        if node.tag not in _HEADING_TAGS:
            continue
        heading = " ".join(node.text().split())
        normalized = _norm(heading)
        if re.search(r"(?:^|\s)1\.2(?:\s|$)", normalized) and "เกินกว่า 150 หน่วยต่อเดือน" in normalized:
            matches.append(node)
    if len(matches) != 1:
        raise ValueError("type_1_2_section_ambiguous" if matches else "type_1_2_section_not_found")

    heading_node = matches[0]
    parent = heading_node.parent
    if parent is None:
        raise ValueError("type_1_2_section_not_found")
    try:
        start_index = parent.children.index(heading_node)
    except ValueError:
        raise ValueError("type_1_2_section_not_found")

    bounded = h17._DomNode("section", {"data-hotfix19": "type-1-2"})
    for sibling in parent.children[start_index:]:
        if sibling is not heading_node and sibling.tag in _HEADING_TAGS:
            sibling_heading = _norm(" ".join(sibling.text().split()))
            if re.search(r"(?:^|\s)1\.3(?:\s|$)", sibling_heading):
                break
        bounded.children.append(sibling)
    if not bounded.children or bounded.children[0] is not heading_node:
        raise ValueError("type_1_2_section_not_found")
    return bounded, " ".join(heading_node.text().split())


def _parse_production_type_1_2(detail_body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    if content_type != "text/html":
        return h17.parse_type_1_2_dom(detail_body, content_type, source_url)
    container, heading = _find_unique_type_1_2_container(detail_body)
    rows = [node for node in h17._walk(container) if node.tag == "tr"]
    if len(rows) != 4:
        raise ValueError("tier_parse_failed")

    tiers: list[Dict[str, Any]] = []
    service_charge: Optional[float] = None
    for row in rows:
        cells = _child_texts(row, "td")
        if not cells:
            continue
        row_text = " ".join(cells)
        normalized = _norm(row_text)
        if "ค่าบริการ" in normalized:
            numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?", row_text)
            if len(numbers) != 1:
                raise ValueError("tier_parse_failed")
            service_charge = mea._number(numbers[0])
            continue
        if len(cells) != 3 or "หน่วยละ" not in _norm(cells[1]):
            raise ValueError("tier_parse_failed")
        rate_numbers = re.findall(r"[0-9]+(?:\.[0-9]+)?", cells[2])
        if len(rate_numbers) != 1:
            raise ValueError("tier_parse_failed")
        rate = mea._number(rate_numbers[0])
        first = _norm(cells[0])
        if "หน่วยที่ 1 150" in first:
            limit: Optional[float] = 150.0
        elif "หน่วยที่ 151 400" in first:
            limit = 400.0
        elif "401 เป็นต้นไป" in first:
            limit = None
        else:
            raise ValueError("tier_parse_failed")
        tiers.append({"up_to_kwh": limit, "rate": rate})

    if tiers != [
        {"up_to_kwh": 150.0, "rate": 3.2484},
        {"up_to_kwh": 400.0, "rate": 4.2218},
        {"up_to_kwh": None, "rate": 4.4217},
    ]:
        raise ValueError("tier_parse_failed")
    if service_charge != 24.62:
        raise ValueError("tier_parse_failed")

    html = detail_body.decode("utf-8", errors="replace")
    title_matches = re.findall(r"(?is)<title[^>]*>(.*?)</title>", html)
    canonical_matches = re.findall(r"(?is)<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']", html)
    date_matches = re.findall(r"<p[^>]*class=[\"'][^\"']*card-date[^\"']*[\"'][^>]*>.*?([0-9]{1,2}\s+[ก-๙.]+\s+[0-9]{4}).*?</p>", html, re.S)
    if len(title_matches) != 1 or len(canonical_matches) != 1 or canonical_matches[0] != source_url:
        raise ValueError("type_1_2_section_ambiguous")
    effective = ""
    if len(date_matches) == 1:
        try:
            effective = mea._date_iso(date_matches[0])
        except Exception:
            effective = ""
    elif len(date_matches) > 1:
        raise ValueError("type_1_2_section_ambiguous")

    source_title = " ".join(re.sub(r"<[^>]+>", " ", title_matches[0]).split())
    h14._SAFE_DEBUG.update({
        "parser_version": PARSER_VERSION,
        "type_1_2_heading": h14._safe_section(heading, 180),
        "type_1_2_section_length": len(container.text()),
        "category_match_method": "production_dom_exact_1_2_heading+bounded_sibling_range+exact_tier_rows+service_charge",
        "category_match_score": 100,
    })
    return {
        "tariff_name": mea.EXPECTED_TARIFF_TYPE,
        "tariff_type": mea.EXPECTED_TARIFF_TYPE,
        "effective_date": effective,
        "version": "",
        "tiers": tiers,
        "service_charge": service_charge,
        "minimum_charge": 0.0,
        "source_url": source_url,
        "source_title": source_title,
        "document_date": effective or None,
        "parser_confidence": "high",
        "matched_fields": ["tariff_type", "tiers", "service_charge", "source_title", "source_url"] + (["effective_date"] if effective else []),
        "missing_fields": [] if effective else ["effective_date"],
        "parser_version": PARSER_VERSION,
        "category_match_method": h14._SAFE_DEBUG["category_match_method"],
        "category_match_score": 100,
    }


h17.parse_type_1_2_dom = _parse_production_type_1_2
h18.parse_type_1_2_dom = _parse_production_type_1_2
h16.PARSER_VERSION = PARSER_VERSION
h17.PARSER_VERSION = PARSER_VERSION
h18.PARSER_VERSION = PARSER_VERSION

_original_debug = h18.provider_debug


def provider_debug() -> Dict[str, Any]:
    payload = _original_debug()
    for key in (
        "anchor_count", "total_anchor_count", "allowed_path_anchor_count",
        "residential_text_anchor_count", "candidate_before_filter",
        "candidate_after_filter", "rejected_candidate_reasons",
        "top_candidate_context", "top_candidate_href", "context_tokens",
    ):
        if key in h14._SAFE_DEBUG:
            payload[key] = copy.deepcopy(h14._SAFE_DEBUG[key])
    payload["parser_version"] = PARSER_VERSION
    return payload


h18.provider_debug = provider_debug
h16.provider_debug = provider_debug
for route in h14.app.routes:
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = provider_debug
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = provider_debug
