"""HOTFIX PACK 18: deterministic MEA Type 1.2 section selection.

This layer narrows residential link discovery to the exact tariff card, canonicalizes
links, deduplicates repeated desktop/mobile DOM sections by normalized values, and
keeps every public error field aligned with the terminal parser error.
"""
from __future__ import annotations

import copy
import hashlib
import re
import urllib.parse
from typing import Any, Dict, Iterable, Mapping, Optional

from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_hotfix16_runtime as h16runtime
from backend import mea_tariff_hotfix17 as h17
from backend import mea_tariff_provider as mea
from backend import mea_tariff_runtime as runtime

PARSER_VERSION = "mea-1.4-deterministic-section"
_BLOCK_TAGS = {"article", "section", "div", "li", "tr", "td", "main"}
_IGNORED_TAGS = {"script", "style", "nav", "aside", "template", "noscript"}
_REJECT_PATH_PARTS = {
    "electric-vehicle", "ev", "payment", "service", "producer", "meter",
    "deposit", "bill", "contact", "faq", "news", "download", "calculator",
}


def _norm(value: Any) -> str:
    return h17._norm(value)


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    return urllib.parse.urlunsplit((scheme, host + port, path, query, ""))


def _is_exact_residential_card(text: str) -> bool:
    value = _norm(text)
    return bool(
        re.search(r"(?:^|\s)ประเภท\s*1\s*บ้านอยู่อาศัย(?:\s|$)", value)
        or re.search(r"(?:^|\s)บ้านอยู่อาศัย\s*ประเภท\s*1(?:\s|$)", value)
        or re.search(r"(?:^|\s)residential(?:\s+customer)?\s*(?:type)?\s*1(?:\s|$)", value)
    )


def _tariff_detail_path(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.lower().rstrip("/")
    if path in {"", "/", "/our-services"}:
        return False
    tokens = {token for token in re.split(r"[/_\-.]+", path) if token}
    if tokens & _REJECT_PATH_PARTS:
        return False
    return any(token in path for token in ("tariff", "electricity", "rate", "residential", "home"))


def _descendants(node: h17._DomNode) -> Iterable[h17._DomNode]:
    yield node
    for child in node.children:
        yield from _descendants(child)


def _node_outer_html(node: h17._DomNode) -> str:
    attrs = "".join(
        f' {key}="{str(value).replace(chr(34), "&quot;")}"'
        for key, value in sorted(node.attrs.items())
    )
    inner = " ".join(node.text().split())
    return h14._safe_section(f"<{node.tag}{attrs}>{inner}</{node.tag}>", 500)


def _parser_diagnostics(index_body: bytes, parser: h17._DomParser) -> Dict[str, Any]:
    anchors = [
        node for node in h17._walk(parser.root)
        if node.tag == "a" and node.attrs.get("href")
    ]
    hrefs = [str(node.attrs.get("href") or "") for node in anchors]
    doc_item = [
        node for node in anchors
        if "doc-item-link" in str(node.attrs.get("class") or "").split()
    ]
    service_rates = [
        node for node in anchors
        if "/our-services/service-rates/other/" in str(node.attrs.get("href") or "")
    ]
    matching = doc_item[0] if doc_item else (service_rates[0] if service_rates else None)
    fixture = (
        '<a class="doc-item-link" href="/our-services/service-rates/other/D5xEaEwgU">'
        '<div class="pt-1">ประเภทที่ 1 บ้านอยู่อาศัย</div></a>'
    ).encode("utf-8")
    body_compact = re.sub(rb"\s+", b"", index_body)
    fixture_compact = re.sub(rb"\s+", b"", fixture)
    return {
        "raw_html_length": len(index_body),
        "first_20_anchor_hrefs": hrefs[:20],
        "total_anchor_count": len(anchors),
        "anchors_matching_doc_item_link": len(doc_item),
        "anchors_matching_service_rates_other": len(service_rates),
        "first_matching_anchor_outer_html": _node_outer_html(matching) if matching is not None else None,
        "beautifulsoup_parser_used": False,
        "html_parser_used": "html.parser.HTMLParser",
        "response_body_equals_production_fixture": body_compact == fixture_compact,
        "response_body_contains_production_fixture": fixture_compact in body_compact,
    }


def _card_candidates(root: h17._DomNode) -> list[h17._DomNode]:
    cards: list[h17._DomNode] = []
    for node in h17._walk(root):
        if node.tag not in _BLOCK_TAGS:
            continue
        text = node.text()
        if not _is_exact_residential_card(text):
            continue
        if not any(child.tag == "a" and child.attrs.get("href") for child in _descendants(node)):
            continue
        if any(_is_exact_residential_card(child.text()) for child in node.children if child.tag in _BLOCK_TAGS):
            continue
        cards.append(node)
    return cards


def select_residential_detail_link(index_body: bytes, index_url: str) -> Dict[str, Any]:
    parser = h17._DomParser()
    parser.feed(index_body.decode("utf-8", errors="replace"))
    h14._SAFE_DEBUG.update(_parser_diagnostics(index_body, parser))
    by_url: Dict[str, Dict[str, Any]] = {}
    for card in _card_candidates(parser.root):
        card_text = _norm(card.text())
        for node in _descendants(card):
            if node.tag != "a" or not node.attrs.get("href"):
                continue
            try:
                resolved = urllib.parse.urljoin(index_url, node.attrs["href"])
                mea._safe_url(resolved)
                canonical = _canonical_url(resolved)
            except Exception:
                continue
            if not _tariff_detail_path(canonical):
                continue
            link_text = _norm(node.text())
            score = 70
            if "ดูเนื้อหา" in link_text or "รายละเอียด" in link_text or "view" in link_text:
                score += 15
            if "บ้านอยู่อาศัย" in link_text or "residential" in link_text:
                score += 10
            if re.search(r"(?:^|\s)1(?:\.0)?(?:\s|$)", link_text):
                score += 5
            item = {
                "url": canonical,
                "score": min(100, score),
                "evidence": h14._safe_section(f"{card_text} {link_text}", 180),
            }
            previous = by_url.get(canonical)
            if previous is None or item["score"] > previous["score"]:
                by_url[canonical] = item
    candidates = sorted(by_url.values(), key=lambda item: (-item["score"], item["url"]))
    h14._SAFE_DEBUG["residential_link_candidates"] = copy.deepcopy(candidates[:8])
    h14._SAFE_DEBUG["deduplicated_residential_link_count"] = len(candidates)
    if not candidates:
        raise ValueError("residential_detail_link_not_found")
    best = candidates[0]
    tied = [item for item in candidates if item["score"] == best["score"]]
    if len(tied) > 1:
        raise ValueError("residential_detail_link_not_found")
    return best


def _direct_text(node: h17._DomNode) -> str:
    return " ".join(" ".join(node.parts).split())


def _heading_node(node: h17._DomNode) -> bool:
    if node.tag in _IGNORED_TAGS:
        return False
    own = _direct_text(node) or node.text()
    value = _norm(own)
    return bool(
        re.search(r"(?:^|\s)1\.2(?:\s|$)", value)
        or "บ้านอยู่อาศัยประเภท 1.2" in value
        or "บ้านอยู่อาศัย ประเภท 1.2" in value
        or "residential type 1.2" in value
        or ("อัตราปกติ" in value and "เกิน 150 หน่วย" in value)
    )


def _extract_values(text: str) -> Optional[Dict[str, float]]:
    patterns = {
        "up_to_150": r"(?:ไม่เกิน|0\s*[-–]\s*150|up\s*to)\s*150\s*(?:หน่วย|kwh)?[^0-9]{0,120}([0-9]+(?:\.[0-9]{2,6})?)",
        "up_to_400": r"(?:151\s*[-–]\s*400|เกิน\s*150\s*(?:หน่วย)?\s*(?:แต่)?ไม่เกิน\s*400|up\s*to\s*400)[^0-9]{0,120}([0-9]+(?:\.[0-9]{2,6})?)",
        "over_400": r"(?:เกิน\s*400|over\s*400)\s*(?:หน่วย|kwh)?[^0-9]{0,120}([0-9]+(?:\.[0-9]{2,6})?)",
        "service_charge": r"(?:ค่าบริการ|service\s*charge)[^0-9]{0,80}([0-9]+(?:\.[0-9]+)?)",
    }
    values: Dict[str, float] = {}
    for name, pattern in patterns.items():
        match = re.search(pattern, text, re.I)
        if not match:
            return None
        values[name] = mea._number(match.group(1))
    return values


def _candidate_ancestor(node: h17._DomNode) -> Optional[h17._DomNode]:
    current: Optional[h17._DomNode] = node
    fallback: Optional[h17._DomNode] = None
    while current and current.tag != "document":
        if current.tag in _IGNORED_TAGS:
            return None
        if current.tag in _BLOCK_TAGS:
            text = current.text()
            fallback = current
            if _extract_values(text) is not None:
                return current
        current = current.parent
    return fallback


def _content_fingerprint(text: str) -> str:
    normalized = _norm(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _value_fingerprint(values: Mapping[str, float]) -> str:
    canonical = "|".join(f"{key}={float(values[key]):.6f}" for key in sorted(values))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:20]


def _candidate_score(text: str, values: Optional[Mapping[str, float]]) -> int:
    value = _norm(text)
    score = 0
    if re.search(r"(?:^|\s)1\.2(?:\s|$)", value): score += 25
    if "บ้านอยู่อาศัย" in value or "residential" in value: score += 15
    if values:
        score += 15 * 4
    if len(text) >= 120: score += 5
    return min(100, score)


def extract_type_1_2_dom_section(detail_body: bytes, content_type: str) -> Dict[str, Any]:
    if content_type == "application/pdf":
        return h16._extract_type_1_2_section(detail_body, content_type)
    parser = h17._DomParser()
    parser.feed(detail_body.decode("utf-8", errors="replace"))
    headings = [node for node in h17._walk(parser.root) if _heading_node(node)]
    raw_count = len(headings)
    by_content: Dict[str, Dict[str, Any]] = {}
    seen_nodes: set[int] = set()
    for heading in headings:
        candidate = _candidate_ancestor(heading)
        if candidate is None or id(candidate) in seen_nodes:
            continue
        seen_nodes.add(id(candidate))
        text = candidate.text()
        values = _extract_values(text)
        score = _candidate_score(text, values)
        valid = values is not None and len(text) >= 100 and candidate.tag not in _IGNORED_TAGS
        fingerprint = _content_fingerprint(text)
        item = {
            "node": candidate, "text": text, "heading": h14._safe_section(heading.text(), 180),
            "length": len(text), "values": values, "score": score, "valid": valid,
            "fingerprint": fingerprint,
        }
        previous = by_content.get(fingerprint)
        if previous is None or item["score"] > previous["score"]:
            by_content[fingerprint] = item
    deduplicated = list(by_content.values())
    valid = [item for item in deduplicated if item["valid"]]
    value_groups: Dict[str, list[Dict[str, Any]]] = {}
    for item in valid:
        fp = _value_fingerprint(item["values"])
        value_groups.setdefault(fp, []).append(item)
    h14._SAFE_DEBUG.update({
        "raw_type_1_2_match_count": raw_count,
        "deduplicated_type_1_2_candidate_count": len(deduplicated),
        "valid_type_1_2_candidate_count": len(valid),
        "duplicate_candidate_count": max(0, raw_count - len(deduplicated)) + sum(max(0, len(items) - 1) for items in value_groups.values()),
        "candidate_value_sets": [
            {"fingerprint": fp, "values": copy.deepcopy(items[0]["values"]), "representations": len(items)}
            for fp, items in sorted(value_groups.items())
        ],
    })
    if not valid:
        raise ValueError("type_1_2_section_not_found")
    if len(value_groups) > 1:
        raise ValueError("type_1_2_section_ambiguous")
    selected = max(valid, key=lambda item: (item["score"], item["length"], item["fingerprint"]))
    h14._SAFE_DEBUG.update({
        "selected_candidate_score": selected["score"],
        "selected_candidate_fingerprint": selected["fingerprint"],
    })
    return {"text": selected["text"], "heading": selected["heading"], "length": selected["length"]}


def parse_type_1_2_dom(detail_body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    bounded = extract_type_1_2_dom_section(detail_body, content_type)
    original = h16._extract_type_1_2_section
    try:
        h16._extract_type_1_2_section = lambda *_args, **_kwargs: bounded
        result = h16._parse_type_1_2(detail_body, content_type, source_url)
    finally:
        h16._extract_type_1_2_section = original
    result["parser_version"] = PARSER_VERSION
    result["category_match_method"] = "exact_residential_card+canonical_detail_url+deduplicated_dom_values"
    h14._SAFE_DEBUG.update({
        "parser_version": PARSER_VERSION,
        "type_1_2_heading": bounded["heading"],
        "type_1_2_section_length": bounded["length"],
        "category_match_method": result["category_match_method"],
        "category_match_score": h14._SAFE_DEBUG.get("selected_candidate_score", 100),
    })
    return result


h17.select_residential_detail_link = select_residential_detail_link
h17.extract_type_1_2_dom_section = extract_type_1_2_dom_section
h17.parse_type_1_2_dom = parse_type_1_2_dom
h17.PARSER_VERSION = PARSER_VERSION
h16.PARSER_VERSION = PARSER_VERSION


def provider_debug() -> Dict[str, Any]:
    allowed = {
        "expected_category", "parser_version", "parser_stage", "parser_error_code",
        "index_source_http_status", "index_source_url", "residential_detail_url",
        "detail_source_http_status", "detail_source_content_type", "detail_source_bytes",
        "type_1_2_heading", "type_1_2_section_length", "category_match_score",
        "category_match_method", "detected_category", "ft_source_http_status",
        "ft_latest_period", "checked_ts", "source_checksum", "residential_link_candidates",
        "deduplicated_residential_link_count", "raw_type_1_2_match_count",
        "deduplicated_type_1_2_candidate_count", "valid_type_1_2_candidate_count",
        "selected_candidate_score", "selected_candidate_fingerprint",
        "duplicate_candidate_count", "candidate_value_sets",
        "raw_html_length", "first_20_anchor_hrefs", "total_anchor_count",
        "anchors_matching_doc_item_link", "anchors_matching_service_rates_other",
        "first_matching_anchor_outer_html", "beautifulsoup_parser_used",
        "html_parser_used", "response_body_equals_production_fixture",
        "response_body_contains_production_fixture",
    }
    return {"provider": "mea", "official_source_only": True, **{
        key: copy.deepcopy(value) for key, value in h14._SAFE_DEBUG.items() if key in allowed
    }}


h16.provider_debug = provider_debug


def _terminal_code() -> Optional[str]:
    value = h14._SAFE_DEBUG.get("parser_error_code")
    return str(value) if value else None


def tariff_status_hotfix18() -> Dict[str, Any]:
    payload = runtime.tariff_status_071()
    code = _terminal_code()
    debug = provider_debug()
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), Mapping) else {}
    diagnostics = {**copy.deepcopy(diagnostics), **{k: v for k, v in debug.items() if k not in {"provider", "official_source_only"}}}
    if code:
        diagnostics.update({"error": code, "parser_error_code": code})
        payload.update({"last_error": code, "status": code, "candidate_status": code})
    payload["diagnostics"] = diagnostics
    return payload


def tariff_check_hotfix18() -> Dict[str, Any]:
    result = h16runtime.tariff_check_hotfix16()
    code = _terminal_code()
    if not code:
        return result
    maintenance = settings._load_maintenance()
    sync_state = maintenance.setdefault("tariff_sync", {})
    diagnostics = sync_state.get("diagnostics") if isinstance(sync_state.get("diagnostics"), Mapping) else {}
    sync_state.update({"last_error": code, "status": code})
    sync_state["diagnostics"] = {**diagnostics, "error": code, "parser_error_code": code}
    maintenance["tariff_status"] = code
    settings._save_maintenance(maintenance)
    return {
        **result,
        "last_error": code,
        "status": code,
        "diagnostics": {**(result.get("diagnostics") or {}), "error": code, "parser_error_code": code},
    }


for route in h14.app.routes:
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", set()) or set())
    endpoint = None
    if path == "/api/tariff/provider/debug" and "GET" in methods:
        endpoint = provider_debug
    elif path == "/api/tariff/status" and "GET" in methods:
        endpoint = tariff_status_hotfix18
    elif path == "/api/tariff/check" and "POST" in methods:
        endpoint = tariff_check_hotfix18
    if endpoint is not None:
        route.endpoint = endpoint
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = endpoint
