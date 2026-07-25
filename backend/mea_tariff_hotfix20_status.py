"""HOTFIX PACK 20 status projection for an outdated official MEA dataset.

This module performs no fetching, parsing, validation, state mutation, or tariff
application. It only presents canonical status and existing archived FT metadata.
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from zoneinfo import ZoneInfo

from backend import mea_tariff_provider as mea

DATASET_OUTDATED = "official_dataset_outdated"
_BANGKOK = ZoneInfo("Asia/Bangkok")


def _latest_official_metadata(source_dir: Optional[Path] = None) -> Dict[str, Any]:
    directory = source_dir or mea.SOURCE_DIR
    latest: Dict[str, Any] = {}
    latest_period = ""
    for path in directory.glob("ft-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            normalized = payload.get("normalized")
            if not isinstance(normalized, Mapping):
                continue
            period = str(normalized.get("effective_to") or normalized.get("effective_from") or "")
            if not period or period <= latest_period:
                continue
            latest_period = period
            latest = {
                "ft_rate": normalized.get("ft_rate"),
                "effective_from": normalized.get("effective_from"),
                "effective_to": normalized.get("effective_to"),
                "publish_date": payload.get("latest_dataset_publish_date")
                or payload.get("last_modified"),
            }
        except (OSError, TypeError, ValueError):
            continue
    return latest


def _error_code(payload: Mapping[str, Any]) -> str:
    diagnostics = payload.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    return str(
        diagnostics.get("parser_error_code")
        or diagnostics.get("error")
        or payload.get("last_error")
        or ""
    )


def _is_transport_failure(payload: Mapping[str, Any]) -> bool:
    diagnostics = payload.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    status = diagnostics.get("fetch_http_status") or diagnostics.get("fetch_return_http_status")
    try:
        http_failure = int(status) >= 400
    except (TypeError, ValueError):
        http_failure = False
    return bool(diagnostics.get("fetch_failure_kind") or http_failure)


def project_status(
    payload: Mapping[str, Any],
    *,
    now: Optional[datetime] = None,
    latest: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    projected = copy.deepcopy(dict(payload))
    current = now or datetime.now(_BANGKOK)
    metadata = dict(latest) if latest is not None else _latest_official_metadata()
    effective_from = metadata.get("effective_from")
    effective_to = metadata.get("effective_to")
    ft_rate = metadata.get("ft_rate")
    age_days = None
    if effective_to:
        try:
            age_days = max(0, (current.date() - date.fromisoformat(str(effective_to))).days)
        except ValueError:
            age_days = None

    projected.update({
        "latest_official_period": {
            "from": effective_from,
            "to": effective_to,
            "ft_rate": ft_rate,
        },
        "latest_official_ft_rate": ft_rate,
        "latest_official_effective_from": effective_from,
        "latest_official_effective_to": effective_to,
        "current_runtime_date": current.date().isoformat(),
        "dataset_age_days": age_days,
        "latest_dataset_publish_date": metadata.get("publish_date"),
    })

    error = _error_code(projected)
    if error == "ft_period_expired":
        projected.update({
            "status": DATASET_OUTDATED,
            "candidate_status": DATASET_OUTDATED,
            "dataset_status": DATASET_OUTDATED,
            "waiting_for_official_update": True,
            "provider_available": True,
            "system_health": "healthy",
            "data_health": DATASET_OUTDATED,
        })
    elif error == "source_fetch_failed":
        status = "provider_unavailable" if _is_transport_failure(projected) else "source_fetch_failed"
        projected.update({
            "status": status,
            "candidate_status": status,
            "dataset_status": status,
            "waiting_for_official_update": False,
            "provider_available": status != "provider_unavailable",
            "system_health": "degraded",
            "data_health": "unavailable" if status == "provider_unavailable" else "invalid",
        })
    else:
        projected.update({
            "dataset_status": projected.get("dataset_status") or "current",
            "waiting_for_official_update": False,
            "system_health": projected.get("system_health") or "healthy",
            "data_health": projected.get("data_health") or "healthy",
        })
    return projected
