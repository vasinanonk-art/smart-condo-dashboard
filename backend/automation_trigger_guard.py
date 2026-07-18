"""Compatibility guard for STORY 6.1 rules that stored empty trigger metadata."""
from __future__ import annotations

from typing import Any, Mapping, Tuple

from backend import automation_trigger_engine as engine

_original_detect_trigger = engine._detect_trigger


def detect_trigger_guarded(automation: Mapping[str, Any], context: Mapping[str, Any], now_ts: int) -> Tuple[bool, str]:
    trigger = automation.get("trigger") if isinstance(automation, Mapping) else None
    kind = trigger.get("type") if isinstance(trigger, Mapping) else None
    if not kind:
        return False, "trigger_not_configured"
    if kind not in engine.SUPPORTED_TRIGGER_TYPES:
        return False, "invalid_trigger"
    return _original_detect_trigger(automation, context, now_ts)


engine._detect_trigger = detect_trigger_guarded
