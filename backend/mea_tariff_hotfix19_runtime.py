"""HOTFIX PACK 19 runtime transport hardening for official MEA fetches.

The production residential-detail endpoint can exceed the original 20-second
response window. Keep the existing HTTPS allowlist and redirect validator, use a
bounded browser-compatible request profile, and expose only safe diagnostics.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape
from typing import Any, Dict, Iterable, Optional

from backend import mea_tariff_hotfix14 as h14
from backend import mea_tariff_hotfix16 as h16
from backend import mea_tariff_hotfix18 as h18
from backend import mea_tariff_hotfix19 as h19
from backend import mea_tariff_provider as mea

FETCH_TIMEOUT_SEC = 45
FETCH_ATTEMPTS = 3
_RETRY_DELAYS = (1.0, 2.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux armv7l) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "Smart-Condo-Dashboard/1.0"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,text/csv,text/plain,application/pdf,*/*;q=0.5",
    "Accept-Language": "th-TH,th;q=0.9,en;q=0.7",
    "Accept-Encoding": "identity",
    "Connection": "close",
}
_SAFE_FIELDS = (
    "fetch_stage", "fetch_failure_kind", "fetch_attempts", "fetch_http_status",
    "fetch_redirect_count", "fetch_final_host", "fetch_content_type",
    "fetch_timeout_sec", "fetch_last_success_url",
)
_INDEX_DEBUG_FIELDS = (
    "raw_html_first_5000_chars", "raw_html_sha256", "page_title",
    "all_anchor_texts_first_100", "all_anchor_hrefs_first_100",
    "all_anchor_pairs_first_100",
)
_DETAIL_FIXTURE_URL = "https://www.mea.or.th/our-services/service-rates/other/D5xEaEwgU"
_DETAIL_FIXTURE_PATH = "/tmp/mea_residential_type_1_2_production.html"


def _debug_object_snapshot(location: str) -> Dict[str, Any]:
    debug = h14._SAFE_DEBUG
    snapshot = {
        "location": location,
        "object_id": id(debug),
        "module": getattr(debug, "__module__", type(debug).__module__),
        "type": type(debug).__name__,
        "keys": sorted(str(key) for key in debug.keys()),
        "key_count": len(debug),
    }
    print(f"HOTFIX19.2 debug object {snapshot}", flush=True)
    return snapshot


_RUNTIME_DEBUG_OBJECT = _debug_object_snapshot("backend.mea_tariff_hotfix19_runtime")


def _stage(url: str) -> str:
    if url == mea.MEA_TARIFF_PAGE:
        return "index"
    if url == mea.MEA_FT_DATASET_API:
        return "ft_metadata"
    if (urllib.parse.urlsplit(url).hostname or "").lower() == "opendata.mea.or.th":
        return "ft_resource"
    return "residential_detail"


def _classify(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(exc, urllib.error.HTTPError):
        if 300 <= int(exc.code) < 400:
            return "http_redirect"
        if int(exc.code) in {401, 403, 429}:
            return "http_blocked"
        return "http_error"
    if isinstance(exc, ssl.SSLCertVerificationError) or isinstance(reason, ssl.SSLCertVerificationError):
        return "tls_ca_certificate"
    if isinstance(exc, (socket.timeout, TimeoutError)) or isinstance(reason, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, socket.gaierror) or isinstance(reason, socket.gaierror):
        return "dns"
    text = str(exc)
    if "source_url_not_allowed" in text:
        return "url_allowlist_rejection"
    if "invalid_content_type" in text:
        return "content_type_rejection"
    if "redirect_limit_exceeded" in text:
        return "http_redirect"
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return "network"
    return "fetch_error"


def _clean_html_text(value: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _capture_index_html_debug(body: bytes) -> None:
    text = body.decode("utf-8", errors="replace")
    title_match = re.search(r"<title\b[^>]*>(.*?)</title>", text, re.I | re.S)
    anchor_pattern = re.compile(r"<a\b([^>]*)>(.*?)</a\s*>", re.I | re.S)
    href_pattern = re.compile(r"\bhref\s*=\s*([\"'])(.*?)\1", re.I | re.S)
    texts = []
    hrefs = []
    pairs = []
    for attrs, inner in anchor_pattern.findall(text)[:100]:
        href_match = href_pattern.search(attrs)
        href = unescape(href_match.group(2)).strip() if href_match else ""
        anchor_text = _clean_html_text(inner)
        texts.append(anchor_text[:500])
        hrefs.append(href[:1000])
        pairs.append({"text": anchor_text[:500], "href": href[:1000]})
    h14._SAFE_DEBUG.update({
        "raw_html_first_5000_chars": text[:5000],
        "raw_html_sha256": hashlib.sha256(body).hexdigest(),
        "page_title": _clean_html_text(title_match.group(1))[:500] if title_match else "",
        "all_anchor_texts_first_100": texts,
        "all_anchor_hrefs_first_100": hrefs,
        "all_anchor_pairs_first_100": pairs,
    })


def _capture_detail_fixture(body: bytes) -> None:
    sha256 = hashlib.sha256(body).hexdigest()
    try:
        with open(_DETAIL_FIXTURE_PATH, "wb") as fixture:
            fixture.write(body)
            fixture.flush()
            os.fsync(fixture.fileno())
        h14._SAFE_DEBUG.update({
            "detail_fixture_capture_status": "captured",
            "detail_fixture_capture_path": _DETAIL_FIXTURE_PATH,
            "detail_fixture_capture_bytes": len(body),
            "detail_fixture_capture_sha256": sha256,
        })
    except OSError:
        h14._SAFE_DEBUG.update({
            "detail_fixture_capture_status": "write_failed",
            "detail_fixture_capture_path": _DETAIL_FIXTURE_PATH,
            "detail_fixture_capture_bytes": 0,
            "detail_fixture_capture_sha256": sha256,
        })


def fetch_official(url: str, allowed_types: Iterable[str]) -> Dict[str, Any]:
    allowed = set(allowed_types)
    url = mea._safe_url(url)
    now = time.monotonic()
    if mea._LAST_REMOTE_FETCH and now - mea._LAST_REMOTE_FETCH < mea.MIN_FETCH_INTERVAL_SEC:
        raise RuntimeError("provider_rate_limited")
    mea._LAST_REMOTE_FETCH = now

    h14._SAFE_DEBUG.update({
        "fetch_stage": _stage(url), "fetch_failure_kind": None,
        "fetch_attempts": 0, "fetch_http_status": None,
        "fetch_redirect_count": 0,
        "fetch_final_host": (urllib.parse.urlsplit(url).hostname or "").lower(),
        "fetch_content_type": None, "fetch_timeout_sec": FETCH_TIMEOUT_SEC,
    })
    context = ssl.create_default_context()
    last_error: Optional[BaseException] = None

    for attempt in range(1, FETCH_ATTEMPTS + 1):
        redirect = mea._LimitedRedirect()
        h14._SAFE_DEBUG["fetch_attempts"] = attempt
        try:
            opener = urllib.request.build_opener(
                redirect, urllib.request.HTTPSHandler(context=context)
            )
            request = urllib.request.Request(url, headers=_HEADERS, method="GET")
            with opener.open(request, timeout=FETCH_TIMEOUT_SEC) as response:
                final_url = mea._safe_url(response.geturl())
                status = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get_content_type() or "").lower()
                if content_type == "application/xhtml+xml" and "text/html" in allowed:
                    content_type = "text/html"
                final_host = (urllib.parse.urlsplit(final_url).hostname or "").lower()
                h14._SAFE_DEBUG.update({
                    "fetch_http_status": status,
                    "fetch_redirect_count": int(getattr(redirect, "count", 0)),
                    "fetch_final_host": final_host,
                    "fetch_content_type": content_type,
                })
                if content_type not in allowed:
                    raise ValueError("invalid_content_type")
                length = response.headers.get("Content-Length")
                if length and int(length) > mea.MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                body = response.read(mea.MAX_RESPONSE_BYTES + 1)
                if len(body) > mea.MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                if url == mea.MEA_TARIFF_PAGE and content_type == "text/html":
                    _capture_index_html_debug(body)
                if (
                    final_url == _DETAIL_FIXTURE_URL
                    and final_host == "www.mea.or.th"
                    and status == 200
                    and content_type == "text/html"
                ):
                    _capture_detail_fixture(body)
                h14._SAFE_DEBUG.update({
                    "fetch_failure_kind": None,
                    "fetch_last_success_url": final_url,
                })
                return {
                    "url": final_url, "content_type": content_type, "body": body,
                    "title": "", "fetched_at": int(time.time()),
                    "checksum": hashlib.sha256(body).hexdigest(),
                    "http_status": status,
                }
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            h14._SAFE_DEBUG.update({
                "fetch_failure_kind": _classify(exc),
                "fetch_http_status": int(exc.code) if isinstance(exc, urllib.error.HTTPError) else h14._SAFE_DEBUG.get("fetch_http_status"),
                "fetch_redirect_count": int(getattr(redirect, "count", 0)),
            })
            if attempt < FETCH_ATTEMPTS:
                time.sleep(_RETRY_DELAYS[min(attempt - 1, len(_RETRY_DELAYS) - 1)])

    raise RuntimeError("source_fetch_failed") from last_error


mea._fetch = fetch_official
_original_debug = h19.provider_debug


def provider_debug() -> Dict[str, Any]:
    endpoint_snapshot = _debug_object_snapshot("backend.mea_tariff_hotfix19_runtime.provider_debug")
    payload = _original_debug()
    for key in _SAFE_FIELDS:
        if key in h14._SAFE_DEBUG:
            payload[key] = h14._SAFE_DEBUG[key]
    if not h14._SAFE_DEBUG.get("residential_link_candidates"):
        for key in _INDEX_DEBUG_FIELDS:
            if key in h14._SAFE_DEBUG:
                payload[key] = h14._SAFE_DEBUG[key]
    payload.update({
        "debug_object_identity": endpoint_snapshot["object_id"],
        "debug_module": endpoint_snapshot["module"],
        "debug_key_count": endpoint_snapshot["key_count"],
        "debug_object_snapshots": {
            "runtime_module": _RUNTIME_DEBUG_OBJECT,
            "provider_debug_endpoint": endpoint_snapshot,
        },
    })
    if _RUNTIME_DEBUG_OBJECT["object_id"] != endpoint_snapshot["object_id"]:
        payload["debug_object_id_mismatch"] = {
            "runtime_module": _RUNTIME_DEBUG_OBJECT["object_id"],
            "provider_debug_endpoint": endpoint_snapshot["object_id"],
        }
    return payload


h19.provider_debug = provider_debug
h18.provider_debug = provider_debug
h16.provider_debug = provider_debug
for route in h14.app.routes:
    if getattr(route, "path", None) == "/api/tariff/provider/debug" and "GET" in set(getattr(route, "methods", set()) or set()):
        route.endpoint = provider_debug
        if getattr(route, "dependant", None) is not None:
            route.dependant.call = provider_debug
