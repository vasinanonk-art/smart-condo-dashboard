"""HOTFIX PACK 17: robust MEA residential DOM parsing.

This layer keeps the official-source and bounded-fetch policy from EPIC 07.1/HOTFIX 16,
but parses the index and residential detail HTML structurally.  It never exposes raw
source bodies and never starts Ft retrieval until the bounded Type 1.2 base section is
validated.
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
from typing import Any, Dict, Mapping, Optional

from backend import automatic_tariff_sync as sync
from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_provider as mea

PARSER_VERSION = "mea-1.3-dom-detail"


def _norm(value: Any) -> str:
    text = unescape(str(value or "")).lower().replace("ประเภทที่", "ประเภท ")
    return " ".join(re.sub(r"[^a-z0-9ก-๙.]+", " ", text).split())


class _DomNode:
    def __init__(self, tag: str, attrs: Mapping[str, str], parent: Optional["_DomNode"] = None) -> None:
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list[_DomNode] = []
        self.parts: list[str] = []

    def text(self) -> str:
        values = list(self.parts)
        for child in self.children:
            values.append(child.text())
        return " ".join(" ".join(values).split())


class _DomParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _DomNode("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        node = _DomNode(tag.lower(), {k: v or "" for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag.lower() not in {"br", "img", "meta", "link", "input", "hr"}:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.stack[-1].parts.append(text)


def _walk(node: _DomNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _container(node: _DomNode) -> _DomNode:
    current = node
    while current.parent and current.parent.tag not in {"document", "body"}:
        if current.tag in {"article", "section", "li", "div", "tr"} and len(current.text()) >= 20:
            return current
        current = current.parent
    return current


def select_residential_detail_link(index_body: bytes, index_url: str) -> Dict[str, Any]:
    parser = _DomParser()
    parser.feed(index_body.decode("utf-8", errors="replace"))
    candidates: list[Dict[str, Any]] = []
    for node in _walk(parser.root):
        if node.tag != "a" or not node.attrs.get("href"):
            continue
        context_node = _container(node)
        evidence = _norm(context_node.text())
        link_text = _norm(node.text())
        score = 0
        if "บ้านอยู่อาศัย" in evidence or "residential" in evidence:
            score += 60
        if re.search(r"(?:^|\s)ประเภท\s*1(?:\s|$)", evidence):
            score += 30
        if "ดูเนื้อหา" in link_text or "รายละเอียด" in link_text or "view" in link_text:
            score += 5
        if re.search(r"(?:^|\s)ประเภท\s*[2-8](?:\s|$)", evidence):
            score -= 120
        try:
            resolved = urllib.parse.urljoin(index_url, node.attrs["href"])
            mea._safe_url(resolved)
        except Exception:
            continue
        if score > 0:
            candidates.append({"url": resolved, "score": score, "evidence": h14._safe_section(evidence, 180)})
    candidates.sort(key=lambda item: (-item["score"], item["url"]))
    h14._SAFE_DEBUG["residential_link_candidates"] = copy.deepcopy(candidates[:8])
    if not candidates or candidates[0]["score"] < 60:
        raise ValueError("residential_detail_link_not_found")
    top_urls = {item["url"] for item in candidates if item["score"] == candidates[0]["score"]}
    if len(top_urls) != 1:
        raise ValueError("residential_detail_link_not_found")
    return candidates[0]


def _block_nodes(body: bytes) -> list[Dict[str, str]]:
    parser = _DomParser()
    parser.feed(body.decode("utf-8", errors="replace"))
    blocks: list[Dict[str, str]] = []
    for node in _walk(parser.root):
        if node.tag not in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "section", "article"}:
            continue
        text = node.text()
        if text:
            blocks.append({"tag": node.tag, "text": " ".join(text.split())})
    return blocks


def _is_type_1_2(text: str) -> bool:
    value = _norm(text)
    return bool(
        re.search(r"(?:^|\s)1\.2(?:\s|$)", value)
        or "บ้านอยู่อาศัยประเภท 1.2" in value
        or "บ้านอยู่อาศัย ประเภท 1.2" in value
        or "residential type 1.2" in value
        or ("อัตราปกติ" in value and "เกิน 150 หน่วย" in value)
    )


def _is_boundary(text: str) -> bool:
    value = _norm(text)
    return bool(
        re.search(r"(?:^|\s)1\.(?:3|4|5|6|7|8|9)(?:\s|$)", value)
        or re.search(r"(?:^|\s)ประเภท\s*[2-8](?:\s|$)", value)
        or re.search(r"(?:^|\s)(?:2|3|4|5|6|7|8)\.(?:1|2)(?:\s|$)", value)
    )


def extract_type_1_2_dom_section(detail_body: bytes, content_type: str) -> Dict[str, Any]:
    if content_type == "application/pdf":
        return h16._extract_type_1_2_section(detail_body, content_type)
    blocks = _block_nodes(detail_body)
    starts = [index for index, item in enumerate(blocks) if _is_type_1_2(item["text"])]
    groups: list[list[int]] = []
    for index in starts:
        if not groups or index - groups[-1][-1] > 2:
            groups.append([index])
        else:
            groups[-1].append(index)
    if not groups:
        raise ValueError("type_1_2_section_not_found")
    if len(groups) != 1:
        raise ValueError("type_1_2_section_ambiguous")
    start = groups[0][0]
    end = len(blocks)
    for index in range(groups[0][-1] + 1, len(blocks)):
        if _is_boundary(blocks[index]["text"]):
            end = index
            break
    selected = blocks[start:end]
    section = "\n".join(item["text"] for item in selected)
    if len(section) < 40:
        raise ValueError("type_1_2_section_not_found")
    return {"text": section, "heading": h14._safe_section(blocks[start]["text"], 180), "length": len(section)}


def parse_type_1_2_dom(detail_body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    bounded = extract_type_1_2_dom_section(detail_body, content_type)
    original = h16._extract_type_1_2_section
    try:
        h16._extract_type_1_2_section = lambda *_args, **_kwargs: bounded
        result = h16._parse_type_1_2(detail_body, content_type, source_url)
    finally:
        h16._extract_type_1_2_section = original
    result["parser_version"] = PARSER_VERSION
    result["category_match_method"] = "dom_residential_card+official_detail_link+bounded_dom_type_1_2+tier_structure+service_charge"
    h14._SAFE_DEBUG.update({
        "parser_version": PARSER_VERSION,
        "type_1_2_heading": bounded["heading"],
        "type_1_2_section_length": bounded["length"],
        "category_match_method": result["category_match_method"],
        "category_match_score": 100,
    })
    return result


class MEATariffProviderHotfix17(mea.MEATariffProvider):
    name = "mea"
    remote = True

    def fetch_latest(self) -> Dict[str, Any]:
        h14._SAFE_DEBUG.update({
            "parser_version": PARSER_VERSION,
            "parser_stage": "index_fetch",
            "parser_error_code": None,
            "checked_ts": int(time.time()),
            "ft_source_http_status": None,
            "ft_latest_period": None,
        })
        sync._audit("remote_check_started", "started", "provider=mea")
        try:
            index_source = mea._fetch(mea.MEA_TARIFF_PAGE, {"text/html", "application/pdf"})
            h14._SAFE_DEBUG.update({
                "index_source_http_status": int(index_source.get("http_status") or 200),
                "index_source_url": index_source.get("url"),
                "parser_stage": "residential_link",
            })
            if index_source.get("content_type") != "text/html":
                raise ValueError("residential_detail_link_not_found")
            selected = select_residential_detail_link(index_source["body"], index_source["url"])
            h14._SAFE_DEBUG["residential_detail_url"] = selected["url"]
            mea._LAST_REMOTE_FETCH = 0.0
            h14._SAFE_DEBUG["parser_stage"] = "residential_detail_fetch"
            try:
                detail = mea._fetch(selected["url"], {"text/html", "application/pdf"})
            except Exception as exc:
                raise ValueError("residential_detail_fetch_failed") from exc
            h14._SAFE_DEBUG.update({
                "detail_source_http_status": int(detail.get("http_status") or 200),
                "detail_source_content_type": detail.get("content_type"),
                "detail_source_bytes": len(detail.get("body") or b""),
                "parser_stage": "type_1_2_section",
            })
            base = parse_type_1_2_dom(detail["body"], detail["content_type"], detail["url"])

            # Ft begins only after the complete bounded base section has parsed.
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
            ft = mea.parse_ft_csv(ft_source["body"], ft_source["url"])
            h14._SAFE_DEBUG["ft_latest_period"] = {"from": ft.get("effective_from"), "to": ft.get("effective_to")}

            base_archive = mea._archive_source("base", detail, base)
            ft_archive = mea._archive_source("ft", ft_source, ft)
            result = {
                **base,
                "ft_rate": ft["ft_rate"], "vat_percent": 7.0, "provider": "mea", "source": "mea",
                "effective_from": max(base.get("effective_date") or "", ft.get("effective_from") or ""),
                "effective_to": ft.get("effective_to"),
                "base_tariff_source": {k: base_archive.get(k) for k in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
                "ft_source": {k: ft_archive.get(k) for k in ("source_id", "source_url", "source_title", "checksum", "fetched_at")},
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
            code = h16._map_error(exc)
            h14._SAFE_DEBUG.update({"parser_error_code": code, "parser_stage": h14._SAFE_DEBUG.get("parser_stage") or "provider"})
            sync._audit("remote_check_failed", "error", code)
            raise ValueError(code)


sync.PROVIDERS["mea"] = MEATariffProviderHotfix17()
h16.PARSER_VERSION = PARSER_VERSION
