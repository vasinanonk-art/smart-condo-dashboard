"""Final HOTFIX PACK 19 link-path guard.

Residential context is used only for ranking. Eligibility is decided first from the
canonical MEA URL so unrelated service/navigation links can never become tariff
candidates through inherited nearby text.
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


def _normalized_path(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return re.sub(r"/{2,}", "/", parsed.path or "/").lower()


def is_valid_tariff_detail_path(url: str) -> bool:
    """Return True only for a specific page in an official tariff-detail family."""
    path = _normalized_path(url)
    prefix = next((item for item in _ALLOWED_DETAIL_PREFIXES if path.startswith(item)), None)
    if prefix is None:
        return False
    slug = path[len(prefix):].strip("/")
    if not slug:
        return False
    tokens = {token for token in re.split(r"[/_.-]+", slug) if token}
    return not bool(tokens & _REJECTED_SLUG_TOKENS)


def strict_path_quality(url: str) -> int:
    """Reject non-tariff links before any residential context score is applied."""
    if not is_valid_tariff_detail_path(url):
        return -100
    path = _normalized_path(url)
    score = 70
    if path.startswith("/our-services/service-rates/other/"):
        score += 10
    slug = path.rsplit("/", 1)[-1]
    if "residential" in slug or "home" in slug:
        score += 10
    return min(score, 100)


# select_residential_detail_link resolves this module global at call time. Runtime
# imports this guard after HOTFIX 19; tests import it explicitly to verify the exact
# production registration path without weakening HTTPS/host/redirect validation.
h19._path_quality = strict_path_quality
