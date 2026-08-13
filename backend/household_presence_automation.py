"""Persisted household presence state machine with one bounded departure action.

AWAY_CANDIDATE is an operational inference from expired network reachability. It
is not proof that a person physically left the home.
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
HOME = "HOME"
PENDING_AWAY = "PENDING_AWAY"
CONFIRMED_AWAY = "CONFIRMED_AWAY"
PRESENT = "PRESENT"
AWAY_CANDIDATE = "AWAY_CANDIDATE"
UNKNOWN = "UNKNOWN"
PEOPLE = ("beer", "seem")
AWAY_DWELL_SECONDS = 1800
BANGKOK = ZoneInfo("Asia/Bangkok")


def classify_presence(item: Any) -> dict[str, str]:
    """Classify resolved network evidence without overstating physical absence."""
    if not isinstance(item, Mapping):
        return {"classification": UNKNOWN, "reason": "presence_unavailable"}
    if item.get("evaluation_error"):
        return {"classification": UNKNOWN, "reason": "evaluation_error"}
    if item.get("evidence_conflict"):
        return {"classification": UNKNOWN, "reason": "conflicting_evidence"}

    source = str(item.get("source") or "")
    state = str(item.get("state") or item.get("status") or "").lower()
    if item.get("home") is True and state == "home" and source.startswith(("MQTT", "Router", "Ping")):
        return {"classification": PRESENT, "reason": "positive_presence"}
    if (
        item.get("home") is False
        and state == "away"
        and source == "Expired"
        and bool(item.get("ip"))
    ):
        return {"classification": AWAY_CANDIDATE, "reason": "reachability_expired"}
    if not item.get("ip") and not source.startswith("MQTT"):
        return {"classification": UNKNOWN, "reason": "missing_identity"}
    return {"classification": UNKNOWN, "reason": "insufficient_evidence"}


def _departure_action() -> dict[str, Any]:
    return {
        "eligible": False,
        "attempted": False,
        "completed": False,
        "attempted_at": None,
        "completed_at": None,
        "status": None,
        "error": None,
    }


def initial_state(now: int = 0) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "state": HOME,
        "pending_since": None,
        "confirmed_away_at": None,
        "transition_id": None,
        "departure_action": _departure_action(),
        "people": {
            person: {"classification": UNKNOWN, "reason": "not_evaluated"}
            for person in PEOPLE
        },
        "last_transition": None,
        "reason": "initialized_home",
        "updated_at": int(now),
    }


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class HouseholdPresenceAutomation:
    def __init__(
        self,
        state_path: Path,
        all_off: Callable[[], Any],
        *,
        clock: Callable[[], float] = time.time,
        transition_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        self.state_path = Path(state_path)
        self.all_off = all_off
        self.clock = clock
        self.transition_id_factory = transition_id_factory
        self._lock = threading.RLock()
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        now = int(self.clock())
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = initial_state(now)
            _atomic_write(self.state_path, payload)
            return payload
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            payload = initial_state(now)
            _atomic_write(self.state_path, payload)
            return payload
        if payload.get("state") == CONFIRMED_AWAY:
            restored = initial_state(now)
            restored.update(payload)
            restored["departure_action"] = {**_departure_action(), **dict(payload.get("departure_action") or {})}
            restored["reason"] = "confirmed_away_restored"
            restored["updated_at"] = now
            _atomic_write(self.state_path, restored)
            return restored
        prior_state = payload.get("state")
        # HOME and PENDING_AWAY both restart conservatively from HOME. A pending
        # timer can never be shortened by process downtime.
        payload = initial_state(now)
        payload["reason"] = "pending_cancelled_on_restart" if prior_state == PENDING_AWAY else "startup_home"
        _atomic_write(self.state_path, payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self.state)

    def _save(self) -> None:
        _atomic_write(self.state_path, self.state)

    def _save_if_changed(self, previous: Mapping[str, Any], now: int) -> None:
        before = copy.deepcopy(dict(previous))
        after = copy.deepcopy(self.state)
        before.pop("updated_at", None)
        after.pop("updated_at", None)
        if before == after:
            self.state["updated_at"] = previous.get("updated_at", self.state.get("updated_at", now))
            return
        self.state["updated_at"] = now
        self._save()

    def _transition_home(self, now: int, reason: str) -> None:
        previous = self.state.get("state")
        people = copy.deepcopy(self.state.get("people") or {})
        self.state = initial_state(now)
        self.state["people"] = people
        self.state["last_transition"] = {"from": previous, "to": HOME, "at": now}
        self.state["reason"] = reason
        self._save()

    @staticmethod
    def _eligible_at(now: int) -> bool:
        local = datetime.fromtimestamp(now, BANGKOK)
        seconds = local.hour * 3600 + local.minute * 60 + local.second
        return 6 * 3600 <= seconds < 18 * 3600 + 30 * 60

    def evaluate(self, presence: Mapping[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            return self._evaluate_locked(presence)

    def _evaluate_locked(self, presence: Mapping[str, Any] | None) -> dict[str, Any]:
        now = int(self.clock())
        previous = copy.deepcopy(self.state)
        presence = presence if isinstance(presence, Mapping) else {}
        people = {person: classify_presence(presence.get(person)) for person in PEOPLE}
        self.state["people"] = people
        classes = {item["classification"] for item in people.values()}
        both_away = all(people[p]["classification"] == AWAY_CANDIDATE for p in PEOPLE)
        any_present = PRESENT in classes
        any_unknown = UNKNOWN in classes
        current = self.state.get("state")

        if current == CONFIRMED_AWAY:
            if any_present:
                self._transition_home(now, "positive_presence_after_confirmed_away")
            else:
                self.state["reason"] = "confirmed_away_unknown_hold" if any_unknown else "confirmed_away_unchanged"
                self._save_if_changed(previous, now)
            return self.snapshot()

        if current == PENDING_AWAY:
            if any_present or any_unknown or not both_away:
                self._transition_home(now, "pending_cancelled_present" if any_present else "pending_cancelled_unknown")
                return self.snapshot()
            pending_since = int(self.state.get("pending_since") or now)
            if now - pending_since < AWAY_DWELL_SECONDS:
                self.state["reason"] = "continuous_away_pending"
                self._save_if_changed(previous, now)
                return self.snapshot()
            transition_id = self.transition_id_factory()
            eligible = self._eligible_at(now)
            self.state.update({
                "state": CONFIRMED_AWAY,
                "pending_since": None,
                "confirmed_away_at": now,
                "transition_id": transition_id,
                "last_transition": {"from": PENDING_AWAY, "to": CONFIRMED_AWAY, "at": now, "id": transition_id},
                "reason": "continuous_away_confirmed",
                "updated_at": now,
            })
            self.state["departure_action"] = _departure_action()
            self.state["departure_action"]["eligible"] = eligible
            if not eligible:
                self.state["departure_action"]["status"] = "outside_time_window"
                self._save()
                return self.snapshot()

            # Persist attempted before issuing the command. A crash cannot cause
            # an uncontrolled retry; missing one off action is safer than repeats.
            self.state["departure_action"].update({"attempted": True, "attempted_at": now, "status": "attempting"})
            self._save()
            try:
                result = self.all_off()
                ok = bool(result.get("ok")) if isinstance(result, Mapping) else bool(result)
                error = None if ok else "all_off_failed"
            except Exception as exc:
                ok = False
                error = type(exc).__name__
            completed_at = int(self.clock()) if ok else None
            self.state["departure_action"].update({
                "completed": ok,
                "completed_at": completed_at,
                "status": "completed" if ok else "failed",
                "error": error,
            })
            self.state["updated_at"] = int(self.clock())
            self._save()
            return self.snapshot()

        if both_away:
            self.state.update({
                "state": PENDING_AWAY,
                "pending_since": now,
                "confirmed_away_at": None,
                "transition_id": None,
                "departure_action": _departure_action(),
                "last_transition": {"from": HOME, "to": PENDING_AWAY, "at": now},
                "reason": "both_away_candidate",
                "updated_at": now,
            })
            self._save()
        else:
            self.state["pending_since"] = None
            self.state["reason"] = "home_present" if any_present else "home_unknown"
            self._save_if_changed(previous, now)
        return self.snapshot()
