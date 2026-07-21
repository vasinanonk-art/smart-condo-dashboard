"""HOTFIX PACK 19 path gate for official residential tariff details.

Context is useful for ranking anchors, but it must never turn an unrelated service
link into a tariff candidate. Only the two official MEA tariff-detail path families
are eligible after HTTPS/host validation has succeeded.
"""
from __future__ import annotations

import re
import urllib.parse

from backend import mea_tariff_hotfix19 as h19

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


def strict_tariff_detail_path_quality(url: str) -> int:
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/").lower()
    prefix = next((item for item in _ALLOWED_DETAIL_PREFIXES if path.startswith(item)), None)
    if prefix is None:
        return -100

    slug = path[len(prefix):].strip("/")
    if not slug:
        return -100
    tokens = {token for token in re.split(r"[/_.-]+", slug) if token}
    if tokens & _REJECTED_SLUG_TOKENS:
        return -100

    # Both current production path families are first-class. A specific slug is
    # required, so landing/navigation pages cannot compete on nearby residential text.
    score = 60
    if prefix == "/our-services/service-rates/other/":
        score += 10
    if "residential" in slug or "home" in slug:
        score += 10
    return min(score, 80)


h19._path_quality = strict_tariff_detail_path_quality
