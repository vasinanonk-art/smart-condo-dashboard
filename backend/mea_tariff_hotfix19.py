"""HOTFIX PACK 19: robust residential link discovery from the official MEA index.

Every anchor is scanned. URL eligibility is decided from the canonical allow-listed
MEA URL before residential context is scored, so unrelated service/navigation links
cannot become candidates through inherited nearby text.
"""
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

PARSER_VERSION = "mea-1.6-production-row-link-discovery"
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


def _norm(value: Any) -> str:
    return h17._norm(value)


def _canonical_url(url: str) -> str:
    return h18._canonical_url(url)


def _normalized_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return re.sub(r"/{2,}", "/", parsed.path or "/").lower()


def _tariff_detail_path(url: str) -> tuple[bool, str]:
    """Return eligibility and a safe rejection reason for a canonical MEA URL."""
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
    # Include the anchor's own text first. The live MEA row puts the residential label
    # inside one anchor and the generic action label inside a sibling anchor.
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


def select_residential_detail_link(index_body: bytes, index_url: str) -> Dict[str, Any]:
    parser = h17._DomParser()
    parser.feed(index_body.decode("utf-8", errors="replace"))
    anchors = [node for node in h17._walk(parser.root) if node.tag == "a" and node.attrs.get("href")]
    allowed_path_anchor_count = 0
    residential_text_anchor_count = 0
    rejected_reasons: Dict[str, int] = {}
    before: list[Dict[str, Any]] = []

    def reject(reason: str) -> None:
        rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1

    for node in anchors:
        href = node.attrs.get("href", "")
        link_text = _norm(node.text())
        context = _context(node)
        if _has_residential_text(link_text) or _has_residential_text(context["text"]):
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

        context_score = _context_score(context, link_text)
        if context_score <= 0:
            reject("no_residential_context")
            continue
        path_score = _path_quality(canonical)
        if path_score < 0:
            reject("path_quality_rejection")
            continue
        before.append({
            "url": canonical,
            "href": href,
            "link_text": link_text,
            "context_score": context_score,
            "context": context,
            "path_score": path_score,
        })

    by_url: Dict[str, Dict[str, Any]] = {}
    for item in before:
        score = min(100, item["context_score"] + item["path_score"])
        candidate = {
            "url": item["url"],
            "score": score,
            "evidence": h14._safe_section(f"{item['context']['text']} {item['link_text']}", 220),
            "context_tokens": list(item["context"]["tokens"]),
        }
        previous = by_url.get(candidate["url"])
        if previous is None or candidate["score"] > previous["score"]:
            by_url[candidate["url"]] = candidate

    candidates = sorted(by_url.values(), key=lambda item: (-item["score"], item["url"]))
    best = candidates[0] if candidates else None
    diagnostics = {
        "parser_version": PARSER_VERSION,
        "anchor_count": len(anchors),
        "total_anchor_count": len(anchors),
        "allowed_path_anchor_count": allowed_path_anchor_count,
        "residential_text_anchor_count": residential_text_anchor_count,
        "candidate_before_filter": len(before),
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
    return candidates[0]


# HOTFIX 17 provider resolves this function dynamically at request time.
h17.select_residential_detail_link = select_residential_detail_link
h18.select_residential_detail_link = select_residential_detail_link
h17.PARSER_VERSION = PARSER_VERSION
h18.PARSER_VERSION = PARSER_VERSION
h16.PARSER_VERSION = PARSER_VERSION


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

# Replace only the debug endpoint; status/check consistency remains owned by HOTFIX 18.
for route in h14.app.routes:
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = provider_debug
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = provider_debug
