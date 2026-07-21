"""Final HOTFIX PACK 19 link-path guard.

Context identifies the residential row, but only official MEA tariff-detail path
families are eligible for selection. This prevents unrelated service links inside
the same DOM section from winning on inherited residential text.
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
    "electric", "vehicle", "ev", "station", "payment", "payments",
    "producer", "producers", "meter", "meters", "deposit", "deposits",
    "bill", "bills", "contact", "faq", "news", "download", "calculator",
    "solar", "charging", "service-center", "service-centers",
}
_GENERIC_PATHS = {"", "/", "/our-services", "/our-services/"}


def strict_path_quality(url: str) -> int:
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/").lower()
    if path in _GENERIC_PATHS:
        return -100

    prefix = next((item for item in _ALLOWED_DETAIL_PREFIXES if path.startswith(item)), None)
    if prefix is None:
        return -100

    suffix = path[len(prefix):].strip("/")
    if not suffix:
        return -100
    tokens = {token for token in re.split(r"[/_.-]+", suffix) if token}
    if tokens & _REJECTED_SLUG_TOKENS:
        return -100

    # Both current production path families are first-class candidates. A specific
    # detail slug is mandatory, so root/navigation/service landing links can never
    # win solely because they inherit residential context from a shared container.
    score = 70
    if prefix == "/our-services/service-rates/other/":
        score += 10
    if "residential" in suffix or "home" in suffix:
        score += 10
    return min(score, 100)


# select_residential_detail_link resolves this module global at call time.
h19._path_quality = strict_path_quality
