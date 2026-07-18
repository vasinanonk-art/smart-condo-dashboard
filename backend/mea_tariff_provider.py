"""Official MEA tariff provider and reviewed future-tariff workflow.

Only allow-listed HTTPS resources published by MEA are fetched. Normal dashboard and
billing requests never perform remote I/O; fetching is limited to the existing daily
maintenance task or the authenticated explicit Check Now endpoint.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import Body
from fastapi.responses import JSONResponse

from backend import app as app_module
from backend import automatic_tariff_sync as sync
from backend import dashboard_settings as settings

app = app_module.app
PARSER_VERSION = "mea-1.0"
MEA_TARIFF_PAGE = "https://www.mea.or.th/our-services/tariff-calculation/other/evlowpriority"
MEA_FT_DATASET_API = "https://opendata.mea.or.th/api/3/action/package_show?id=ft-rate-by-type"
ALLOWED_HOSTS = {"www.mea.or.th", "mea.or.th", "opendata.mea.or.th"}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
FETCH_TIMEOUT_SEC = 20
MAX_REDIRECTS = 3
MIN_FETCH_INTERVAL_SEC = 300
SOURCE_DIR = settings.DATA_DIR / "tariff_sources"
APPROVAL_PATH = settings.DATA_DIR / "approved_future_tariff.json"
TARIFF_HISTORY_PATH = settings.DATA_DIR / "tariff_history.json"
EXPECTED_TARIFF_TYPE = "MEA Residential Type 1.2"
_AUDIT_RETENTION_DAYS = 180
_LAST_REMOTE_FETCH = 0.0

# EPIC 07.1 defaults. Existing explicit settings remain authoritative.
settings._DEFAULTS["maintenance"]["tariff_sync_enabled"] = True
settings._DEFAULTS["maintenance"]["tariff_provider"] = "mea"
settings._DEFAULTS["maintenance"]["tariff_sync_interval_days"] = 1
settings._DEFAULTS["maintenance"]["tariff_auto_apply_mode"] = "never"
settings._DEFAULTS["electricity"]["tariff_type"] = EXPECTED_TARIFF_TYPE

_original_validate_settings = settings.validate_settings


def _validate_settings_071(raw: Any) -> Dict[str, Any]:
    validated = _original_validate_settings(raw)
    source = raw if isinstance(raw, Mapping) else {}
    electricity = source.get("electricity") if isinstance(source.get("electricity"), Mapping) else {}
    maintenance = source.get("maintenance") if isinstance(source.get("maintenance"), Mapping) else {}
    tariff_type = str(electricity.get("tariff_type") or EXPECTED_TARIFF_TYPE).strip()
    if _normalize_category(tariff_type) != _normalize_category(EXPECTED_TARIFF_TYPE):
        raise ValueError("unsupported_tariff_type")
    mode = str(maintenance.get("tariff_auto_apply_mode") or "never").strip().lower()
    if mode not in {"never", "effective_date_only_after_approval"}:
        raise ValueError("invalid_tariff_auto_apply_mode")
    validated["electricity"]["tariff_type"] = EXPECTED_TARIFF_TYPE
    validated["maintenance"]["tariff_auto_apply_mode"] = mode
    return validated


settings.validate_settings = _validate_settings_071


def _normalize_category(value: Any) -> str:
    text = unescape(str(value or "")).lower()
    text = text.replace("ประเภทที่", "type ").replace("บ้านอยู่อาศัย", "residential")
    text = re.sub(r"[^a-z0-9ก-๙.]+", " ", text)
    return " ".join(text.split())


def _category_matches(value: Any) -> bool:
    normalized = _normalize_category(value)
    return ("1.2" in normalized and ("residential" in normalized or "บ้าน" in normalized)) or normalized == _normalize_category(EXPECTED_TARIFF_TYPE)


def _safe_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
        raise ValueError("source_url_not_allowed")
    return url


class _LimitedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        self.count += 1
        if self.count > MAX_REDIRECTS:
            raise urllib.error.HTTPError(newurl, code, "redirect_limit_exceeded", headers, fp)
        _safe_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, allowed_types: Iterable[str]) -> Dict[str, Any]:
    global _LAST_REMOTE_FETCH
    url = _safe_url(url)
    now = time.monotonic()
    if _LAST_REMOTE_FETCH and now - _LAST_REMOTE_FETCH < MIN_FETCH_INTERVAL_SEC:
        raise RuntimeError("provider_rate_limited")
    _LAST_REMOTE_FETCH = now
    headers = {"User-Agent": "Smart-Condo-Dashboard/1.0 (+official MEA tariff synchronization)"}
    context = ssl.create_default_context()
    last_error: Optional[BaseException] = None
    for attempt in range(2):
        try:
            opener = urllib.request.build_opener(_LimitedRedirect(), urllib.request.HTTPSHandler(context=context))
            request = urllib.request.Request(url, headers=headers, method="GET")
            with opener.open(request, timeout=FETCH_TIMEOUT_SEC) as response:
                final_url = _safe_url(response.geturl())
                content_type = str(response.headers.get_content_type() or "").lower()
                if content_type not in set(allowed_types):
                    raise ValueError("invalid_content_type")
                length = response.headers.get("Content-Length")
                if length and int(length) > MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ValueError("response_too_large")
                return {
                    "url": final_url,
                    "content_type": content_type,
                    "body": body,
                    "title": "",
                    "fetched_at": int(time.time()),
                    "checksum": hashlib.sha256(body).hexdigest(),
                }
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
    raise RuntimeError(str(last_error or "fetch_failed"))


def _html_text(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def _pdf_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(io.BytesIO(body))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError("pdf_text_extraction_unavailable") from exc


def _number(text: str) -> float:
    return float(text.replace(",", "").strip())


def _date_iso(text: str) -> str:
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    thai = re.search(r"(\d{1,2})\s+(มกราคม|กุมภาพันธ์|มีนาคม|เมษายน|พฤษภาคม|มิถุนายน|กรกฎาคม|สิงหาคม|กันยายน|ตุลาคม|พฤศจิกายน|ธันวาคม)\s+(25\d{2})", text)
    if not thai:
        raise ValueError("invalid_date")
    months = {name: index + 1 for index, name in enumerate("มกราคม กุมภาพันธ์ มีนาคม เมษายน พฤษภาคม มิถุนายน กรกฎาคม สิงหาคม กันยายน ตุลาคม พฤศจิกายน ธันวาคม".split())}
    return f"{int(thai.group(3)) - 543:04d}-{months[thai.group(2)]:02d}-{int(thai.group(1)):02d}"


def parse_mea_base_document(body: bytes, content_type: str, source_url: str) -> Dict[str, Any]:
    text = _pdf_text(body) if content_type == "application/pdf" else _html_text(body)
    category_patterns = [r"(?:ประเภท|Type)\s*(?:ที่)?\s*1\.2[^\n]{0,120}", r"MEA Residential Type 1\.2"]
    category = next((m.group(0).strip() for p in category_patterns if (m := re.search(p, text, re.I))), "")
    if not _category_matches(category):
        raise ValueError("tariff_category_mismatch")

    tiers: list[Dict[str, Any]] = []
    # Accept official Thai/English tables after text extraction: limit followed by rate.
    for match in re.finditer(r"(?:ไม่เกิน|up\s*to)\s*([0-9,]+(?:\.\d+)?)\s*(?:หน่วย|kwh)?[^0-9]{0,80}([0-9]+(?:\.\d{2,6})?)", text, re.I):
        limit, rate = _number(match.group(1)), _number(match.group(2))
        if rate >= 0 and (not tiers or limit > float(tiers[-1]["up_to_kwh"] or 0)):
            tiers.append({"up_to_kwh": limit, "rate": rate})
    unlimited = re.search(r"(?:เกิน|over)\s*([0-9,]+(?:\.\d+)?)\s*(?:หน่วย|kwh)?[^0-9]{0,80}([0-9]+(?:\.\d{2,6})?)", text, re.I)
    if unlimited:
        tiers.append({"up_to_kwh": None, "rate": _number(unlimited.group(2))})

    service_match = re.search(r"(?:ค่าบริการ|service\s*charge)[^0-9]{0,40}([0-9]+(?:\.\d+)?)", text, re.I)
    minimum_match = re.search(r"(?:minimum\s*charge|ค่าไฟฟ้าต่ำสุด)[^0-9]{0,40}([0-9]+(?:\.\d+)?)", text, re.I)
    effective_match = re.search(r"(?:effective|มีผล(?:ตั้งแต่)?)\s*[: ]*([^\n]{4,80})", text, re.I)
    version_match = re.search(r"(?:version|ฉบับ|ประกาศ)\s*[:# ]*([A-Za-z0-9._/-]{2,40})", text, re.I)
    title_match = re.search(r"(?:อัตราค่าไฟฟ้า|Electricity Tariff)[^\n]{0,100}", text, re.I)

    matched = ["tariff_type"]
    if tiers: matched.append("tiers")
    if service_match: matched.append("service_charge")
    if effective_match: matched.append("effective_date")
    missing = [field for field in ("tiers", "service_charge", "effective_date") if field not in matched]
    confidence = "high" if not missing and len(tiers) >= 2 and tiers[-1].get("up_to_kwh") is None else "medium" if len(missing) <= 1 else "low"
    return {
        "tariff_name": "MEA Residential Type 1.2",
        "tariff_type": EXPECTED_TARIFF_TYPE,
        "effective_date": _date_iso(effective_match.group(1)) if effective_match else "",
        "version": version_match.group(1) if version_match else "",
        "tiers": tiers,
        "service_charge": _number(service_match.group(1)) if service_match else None,
        "minimum_charge": _number(minimum_match.group(1)) if minimum_match else 0.0,
        "source_url": source_url,
        "source_title": title_match.group(0).strip() if title_match else "Official MEA tariff",
        "document_date": _date_iso(effective_match.group(1)) if effective_match else None,
        "parser_confidence": confidence,
        "matched_fields": matched,
        "missing_fields": missing,
        "parser_version": PARSER_VERSION,
    }


def _pick_ft_resource(package: Mapping[str, Any]) -> str:
    resources = package.get("resources") if isinstance(package.get("resources"), list) else []
    csv_items = [item for item in resources if isinstance(item, Mapping) and str(item.get("format") or "").upper() == "CSV"]
    if not csv_items:
        raise ValueError("official_ft_resource_not_found")
    csv_items.sort(key=lambda item: str(item.get("last_modified") or item.get("created") or ""), reverse=True)
    return _safe_url(str(csv_items[0].get("url") or ""))


def parse_ft_csv(body: bytes, source_url: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(ZoneInfo("Asia/Bangkok"))
    text = body.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    candidates = []
    for row in rows:
        normalized = {str(k or "").strip().lower(): str(v or "").strip() for k, v in row.items()}
        category = next((v for k, v in normalized.items() if "type" in k or "ประเภท" in k), "")
        if category and not ("บ้าน" in category or "residential" in category.lower() or "1" in category):
            continue
        rate_text = next((v for k, v in normalized.items() if k in {"ft", "ft_rate", "rate"} or "อัตรา" in k), "")
        from_text = next((v for k, v in normalized.items() if "from" in k or "start" in k or "เริ่ม" in k), "")
        to_text = next((v for k, v in normalized.items() if "to" in k or "end" in k or "สิ้น" in k), "")
        try:
            effective_from = _date_iso(from_text)
            effective_to = _date_iso(to_text) if to_text else None
            rate = _number(rate_text)
        except Exception:
            continue
        if rate < 0:
            continue
        start_dt = datetime.strptime(effective_from, "%Y-%m-%d").replace(tzinfo=now.tzinfo)
        end_dt = datetime.strptime(effective_to, "%Y-%m-%d").replace(tzinfo=now.tzinfo) if effective_to else None
        status = "future" if now < start_dt else "expired" if end_dt and now > end_dt.replace(hour=23, minute=59, second=59) else "currently_effective"
        candidates.append({"ft_rate": rate, "effective_from": effective_from, "effective_to": effective_to, "status": status, "source_url": source_url})
    current = [item for item in candidates if item["status"] == "currently_effective"]
    future = [item for item in candidates if item["status"] == "future"]
    selected = max(current, key=lambda item: item["effective_from"], default=None) or min(future, key=lambda item: item["effective_from"], default=None)
    if not selected:
        raise ValueError("no_current_official_ft_period")
    selected.update({"source_title": "MEA Ft rate by customer type", "parser_confidence": "high", "parser_version": PARSER_VERSION})
    return selected


def _archive_source(kind: str, source: Mapping[str, Any], normalized: Mapping[str, Any]) -> Dict[str, Any]:
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    checksum = str(source.get("checksum") or hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest())
    source_id = f"{kind}-{checksum[:16]}"
    payload = {
        "source_id": source_id,
        "kind": kind,
        "source_url": source.get("url"),
        "source_title": source.get("title") or normalized.get("source_title"),
        "fetched_at": source.get("fetched_at") or int(time.time()),
        "checksum": checksum,
        "parser_version": PARSER_VERSION,
        "normalized": copy.deepcopy(dict(normalized)),
    }
    settings._atomic_json_write(SOURCE_DIR / f"{source_id}.json", payload, backup=False)
    return payload


class MEATariffProvider(sync.TariffProvider):
    name = "mea"
    remote = True

    def fetch_latest(self) -> Dict[str, Any]:
        sync._audit("remote_check_started", "started", "provider=mea")
        try:
            base_source = _fetch(MEA_TARIFF_PAGE, {"text/html", "application/pdf"})
            base = parse_mea_base_document(base_source["body"], base_source["content_type"], base_source["url"])

            metadata_source = _fetch(MEA_FT_DATASET_API, {"application/json", "text/json"})
            metadata = json.loads(metadata_source["body"].decode("utf-8"))
            package = metadata.get("result") if isinstance(metadata, Mapping) else None
            if not isinstance(package, Mapping):
                raise ValueError("invalid_official_ft_metadata")
            ft_url = _pick_ft_resource(package)
            # The second official document is part of the same explicit check. Reset only
            # the internal per-provider guard; no other request path can supply a URL.
            global _LAST_REMOTE_FETCH
            _LAST_REMOTE_FETCH = 0.0
            ft_source = _fetch(ft_url, {"text/csv", "application/csv", "text/plain", "application/octet-stream"})
            ft = parse_ft_csv(ft_source["body"], ft_source["url"])

            base_archive = _archive_source("base", base_source, base)
            ft_archive = _archive_source("ft", ft_source, ft)
            result = {
                **base,
                "ft_rate": ft["ft_rate"],
                "vat_percent": 7.0,
                "provider": "mea",
                "source": "mea",
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
            sync._audit("remote_check_succeeded", "ok", f"checksum={result['checksum'][:16]}", result["version"])
            return result
        except Exception as exc:
            sync._audit("remote_check_failed", "error", str(exc)[:160])
            raise

    def validate(self, raw: Any) -> Dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ValueError("invalid_mea_candidate")
        if not _category_matches(raw.get("tariff_type")):
            raise ValueError("tariff_category_mismatch")
        confidence = str(raw.get("parser_confidence") or "low")
        if confidence == "low":
            sync._audit("parser_rejected", "low_confidence", str(raw.get("version") or ""))
            raise ValueError("parser_confidence_low")
        if raw.get("effective_to"):
            end = datetime.strptime(str(raw["effective_to"]), "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Bangkok"))
            if datetime.now(ZoneInfo("Asia/Bangkok")) > end.replace(hour=23, minute=59, second=59):
                raise ValueError("ft_period_expired")
        normalized = super().validate(raw)
        return {**copy.deepcopy(dict(raw)), **normalized, "source": "mea", "provider": "mea", "tariff_type": EXPECTED_TARIFF_TYPE}


sync.PROVIDERS["mea"] = MEATariffProvider()


def candidate_effective_status(candidate: Mapping[str, Any], now_ts: Optional[int] = None) -> str:
    now = datetime.fromtimestamp(now_ts or time.time(), ZoneInfo("Asia/Bangkok"))
    start = datetime.strptime(str(candidate.get("effective_from") or candidate.get("effective_date")), "%Y-%m-%d").replace(tzinfo=now.tzinfo)
    end_text = str(candidate.get("effective_to") or "")
    end = datetime.strptime(end_text, "%Y-%m-%d").replace(tzinfo=now.tzinfo) if end_text else None
    return "future" if now < start else "expired" if end and now > end.replace(hour=23, minute=59, second=59) else "currently_effective"


def _load_approval() -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(APPROVAL_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def _save_history(tariff: Mapping[str, Any], applied_ts: int) -> None:
    try:
        data = json.loads(TARIFF_HISTORY_PATH.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else []
    except Exception:
        rows = []
    record = {"applied_ts": applied_ts, "effective_ts": int(datetime.strptime(str(tariff.get("effective_date")), "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Bangkok")).timestamp()), "tariff": copy.deepcopy(dict(tariff))}
    rows = [row for row in rows if isinstance(row, dict) and (row.get("tariff") or {}).get("version") != tariff.get("version")]
    rows.append(record)
    rows.sort(key=lambda row: int(row.get("effective_ts") or 0))
    settings._atomic_json_write(TARIFF_HISTORY_PATH, {"version": 1, "tariffs": rows[-100:]}, backup=True)


def _apply_candidate(candidate: Mapping[str, Any], scheduled: bool = False) -> Dict[str, Any]:
    validated = sync.PROVIDERS["mea"].validate(candidate)
    status = candidate_effective_status(validated)
    if status == "expired":
        raise ValueError("candidate_expired")
    if status == "future" and not scheduled:
        raise ValueError("candidate_not_effective")
    current = settings.load_settings()
    active = copy.deepcopy(current["electricity"]["tariff"])
    if active.get("effective_date") and active.get("version"):
        _save_history(active, int(time.time()))
    current["electricity"]["tariff"] = {key: validated[key] for key in ("tariff_name", "source", "effective_date", "version", "tiers", "ft_rate", "service_charge", "vat_percent", "minimum_charge")}
    saved = settings.save_settings(current)
    _save_history(saved["electricity"]["tariff"], int(time.time()))
    state = settings._load_maintenance()
    state["last_tariff_update_ts"] = int(time.time())
    state.setdefault("tariff_sync", {})["candidate"] = None
    state["tariff_sync"]["comparison"] = None
    state["tariff_sync"]["status"] = "applied"
    state["tariff_sync"]["approved_future_tariff"] = None
    sync._dismiss_notifications(state)
    settings._save_maintenance(state)
    try:
        APPROVAL_PATH.unlink(missing_ok=True)
    except OSError:
        pass
    sync._audit("scheduled_tariff_applied" if scheduled else "tariff_applied", "ok", "provider=mea", str(validated.get("version") or ""))
    return {"ok": True, "applied": True, "scheduled": scheduled, "settings": saved}


def apply_approved_future_if_due() -> Optional[Dict[str, Any]]:
    approval = _load_approval()
    if not approval or not isinstance(approval.get("candidate"), Mapping):
        return None
    candidate = approval["candidate"]
    if candidate_effective_status(candidate) != "currently_effective":
        return None
    return _apply_candidate(candidate, scheduled=True)


_original_maintenance_once = settings._maintenance_once


def maintenance_with_mea() -> Dict[str, Any]:
    snapshot = _original_maintenance_once()
    try:
        apply_approved_future_if_due()
    except Exception as exc:
        snapshot = settings._load_maintenance()
        snapshot.setdefault("tariff_sync", {})["last_error"] = str(exc)[:160]
        settings._save_maintenance(snapshot)
    return settings._load_maintenance()


settings._maintenance_once = maintenance_with_mea


@app.get("/api/tariff/sources")
def list_tariff_sources() -> Dict[str, Any]:
    rows = []
    if SOURCE_DIR.exists():
        for path in sorted(SOURCE_DIR.glob("*.json"), reverse=True)[:100]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                item.pop("normalized", None)
                rows.append(item)
            except Exception:
                continue
    return {"sources": rows, "count": len(rows)}


@app.get("/api/tariff/sources/{source_id}")
def get_tariff_source(source_id: str):
    if not re.fullmatch(r"[a-z]+-[0-9a-f]{16}", source_id):
        return JSONResponse({"detail": "source_not_found"}, status_code=404)
    path = SOURCE_DIR / f"{source_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return JSONResponse({"detail": "source_not_found"}, status_code=404)


@app.post("/api/tariff/approve-future")
def approve_future(payload: Dict[str, Any] = Body(default={})):
    state = settings._load_maintenance()
    candidate = (state.get("tariff_sync") or {}).get("candidate")
    if not isinstance(candidate, Mapping):
        return JSONResponse({"detail": "candidate_not_available"}, status_code=409)
    validated = sync.PROVIDERS["mea"].validate(candidate)
    if candidate_effective_status(validated) != "future":
        return JSONResponse({"detail": "candidate_not_future"}, status_code=409)
    if str(validated.get("parser_confidence")) not in {"high", "medium"}:
        return JSONResponse({"detail": "candidate_confidence_insufficient"}, status_code=422)
    approval = {"approved_ts": int(time.time()), "approved_by": "authenticated_dashboard_user", "candidate": validated, "mode": "effective_date_only_after_approval"}
    settings._atomic_json_write(APPROVAL_PATH, approval, backup=True)
    state.setdefault("tariff_sync", {})["approved_future_tariff"] = {"version": validated.get("version"), "effective_date": validated.get("effective_date"), "approved_ts": approval["approved_ts"]}
    settings._save_maintenance(state)
    sync._audit("candidate_approved", "future", "effective_date_only_after_approval", str(validated.get("version") or ""))
    return {"ok": True, "approved": True, "effective_date": validated.get("effective_date"), "version": validated.get("version")}
