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
_EXPLICITLY_REJECTED_PREFIXES = (
    "/our-services/electric-vehicle",
    "/our-services/payment",
    "/our-services/producer",
    "/our-services/meter",
    "/our-services/deposit",
)
_GENERIC_PATHS = {"", "/", "/our-services", "/our-services/"}


def strict_path_quality(url: str) -> int:
    parsed = urllib.parse.urlsplit(url)
    path = re.sub(r"/{2,}", "/", parsed.path or "/").lower()
    if path in _GENERIC_PATHS or any(path.startswith(prefix) for prefix in _EXPLICITLY_REJECTED_PREFIXES):
        return -100
    if not any(path.startswith(prefix) for prefix in _ALLOWED_DETAIL_PREFIXES):
        return -100
    suffix = path.rsplit("/", 1)[-1]
    if not suffix:
        return -100
    # Keep EV and other unrelated detail slugs out even under an otherwise valid family.
    if any(token in suffix for token in ("electric-vehicle", "ev-station", "payment", "producer", "meter", "deposit")):
        return -100
    score = 70
    if path.startswith("/our-services/service-rates/other/"):
        score += 10
    if "residential" in suffix or "home" in suffix:
        score += 10
    return min(score, 100)


# select_residential_detail_link resolves this module global at call time.
h19._path_quality = strict_path_quality
