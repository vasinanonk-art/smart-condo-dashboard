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

from backend.presence_event_store import PresenceEventStore


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
SHADOW = "shadow"
ACTIVE = "active"


def execution_mode(value: Any = None) -> str:
    mode = str(value if value is not None else os.getenv("PRESENCE_AUTOMATION_MODE", SHADOW)).strip().lower()
    return ACTIVE if mode == ACTIVE else SHADOW


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


def _departure_action(intended_channels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "eligible": False,
        "attempted": False,
        "completed": False,
        "attempted_at": None,
        "completed_at": None,
        "status": None,
        "error": None,
        "intended_channels": copy.deepcopy(intended_channels or []),
        "would_execute": False,
        "actual_command_executed": False,
    }


def initial_state(now: int = 0, *, mode: str = SHADOW, intended_channels: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": execution_mode(mode),
        "state": HOME,
        "pending_since": None,
        "confirmed_away_at": None,
        "transition_id": None,
        "departure_action": _departure_action(intended_channels),
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
        mode: str | None = None,
        intended_channels: list[dict[str, Any]] | None = None,
        event_store: PresenceEventStore | None = None,
        event_db_path: Path | None = None,
        event_backup_dir: Path | None = None,
    ) -> None:
        self.state_path = Path(state_path)
        self.all_off = all_off
        self.clock = clock
        self.transition_id_factory = transition_id_factory
        self.mode = execution_mode(mode)
        self.intended_channels = copy.deepcopy(intended_channels or [])
        self._lock = threading.RLock()
        self._startup_prior_state: str | None = None
        self.state = self._load()
        self.event_store = event_store or PresenceEventStore(
            event_db_path or self.state_path.with_name("presence_events.sqlite3"),
            clock=self.clock,
            backup_dir=event_backup_dir,
        )
        self._record_event(
            "startup",
            ts=int(self.clock()),
            from_state=self._startup_prior_state,
            to_state=self.state.get("state"),
            reason=self.state.get("reason"),
        )

    def _load(self) -> dict[str, Any]:
        now = int(self.clock())
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
            _atomic_write(self.state_path, payload)
            return payload
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            payload = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
            _atomic_write(self.state_path, payload)
            return payload
        if payload.get("mode") not in {SHADOW, ACTIVE} or payload.get("mode") != self.mode:
            self._startup_prior_state = payload.get("state")
            payload = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
            payload["reason"] = "execution_mode_changed_reset_home"
            _atomic_write(self.state_path, payload)
            return payload
        if payload.get("state") == CONFIRMED_AWAY:
            self._startup_prior_state = CONFIRMED_AWAY
            restored = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
            restored.update(payload)
            restored["mode"] = self.mode
            restored["departure_action"] = {**_departure_action(self.intended_channels), **dict(payload.get("departure_action") or {})}
            restored["departure_action"]["actual_command_executed"] = bool(restored["departure_action"].get("actual_command_executed"))
            restored["reason"] = "confirmed_away_restored"
            restored["updated_at"] = now
            _atomic_write(self.state_path, restored)
            return restored
        prior_state = payload.get("state")
        self._startup_prior_state = prior_state
        # HOME and PENDING_AWAY both restart conservatively from HOME. A pending
        # timer can never be shortened by process downtime.
        payload = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
        payload["reason"] = "pending_cancelled_on_restart" if prior_state == PENDING_AWAY else "startup_home"
        _atomic_write(self.state_path, payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = copy.deepcopy(self.state)
            pending_since = snapshot.get("pending_since")
            elapsed = max(0, int(self.clock()) - int(pending_since)) if pending_since else 0
            snapshot["pending_elapsed_seconds"] = elapsed
            snapshot["pending_remaining_seconds"] = max(0, AWAY_DWELL_SECONDS - elapsed) if pending_since else 0
            return snapshot

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.event_store.recent(limit)

    def event_history_status(self) -> dict[str, Any]:
        return self.event_store.status()

    def _record_event(self, event_type: str, **values: Any) -> None:
        self.event_store.record(event_type, mode=self.mode, **values)

    def _record_people_changes(
        self,
        previous_people: Mapping[str, Any],
        people: Mapping[str, Any],
        now: int,
    ) -> None:
        for person in PEOPLE:
            before = dict(previous_people.get(person) or {})
            after = dict(people.get(person) or {})
            if before.get("classification") == after.get("classification") and before.get("reason") == after.get("reason"):
                continue
            self._record_event(
                "person_classification",
                ts=now,
                person=person,
                from_state=before.get("classification"),
                to_state=after.get("classification"),
                reason=after.get("reason"),
            )

    def _record_household_transition(
        self,
        previous: str | None,
        current: str,
        now: int,
        reason: str,
        transition_id: str | None = None,
    ) -> None:
        self._record_event(
            "household_transition",
            ts=now,
            from_state=previous,
            to_state=current,
            reason=reason,
            transition_id=transition_id,
        )

    def reset_home(self, reason: str = "presence_identity_changed") -> dict[str, Any]:
        with self._lock:
            now = int(self.clock())
            self.state = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
            self.state["reason"] = reason
            self.state["last_transition"] = {"from": "CONFIG_CHANGE", "to": HOME, "at": now}
            self._save()
            self._record_household_transition("CONFIG_CHANGE", HOME, now, reason)
            return self.snapshot()

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
        self.state = initial_state(now, mode=self.mode, intended_channels=self.intended_channels)
        self.state["people"] = people
        self.state["last_transition"] = {"from": previous, "to": HOME, "at": now}
        self.state["reason"] = reason
        self._save()
        self._record_household_transition(previous, HOME, now, reason)

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
        self._record_people_changes(previous.get("people") or {}, people, now)
        self.event_store.maintenance(now=now)
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
            self.state["departure_action"] = _departure_action(self.intended_channels)
            self.state["departure_action"]["eligible"] = eligible
            if not eligible:
                self.state["departure_action"]["status"] = "outside_time_window"
                self._save()
                self._record_household_transition(PENDING_AWAY, CONFIRMED_AWAY, now, "continuous_away_confirmed", transition_id)
                self._record_event(
                    "action_decision",
                    ts=now,
                    reason="outside_time_window",
                    transition_id=transition_id,
                    details={"eligible": False, "would_execute": False, "actual_command_executed": False},
                )
                return self.snapshot()

            if self.mode != ACTIVE:
                self.state["departure_action"].update({
                    "would_execute": True,
                    "actual_command_executed": False,
                    "status": "would_turn_off",
                })
                self.state["reason"] = "shadow_would_turn_off"
                self._save()
                self._record_household_transition(PENDING_AWAY, CONFIRMED_AWAY, now, "continuous_away_confirmed", transition_id)
                self._record_event(
                    "action_decision",
                    ts=now,
                    reason="shadow_would_turn_off",
                    transition_id=transition_id,
                    details={"eligible": True, "would_execute": True, "actual_command_executed": False},
                )
                print(
                    f"presence automation shadow: WOULD TURN OFF transition_id={transition_id}",
                    flush=True,
                )
                return self.snapshot()

            # Persist attempted before issuing the command. A crash cannot cause
            # an uncontrolled retry; missing one off action is safer than repeats.
            self.state["departure_action"].update({"attempted": True, "attempted_at": now, "status": "attempting"})
            self._save()
            self._record_household_transition(PENDING_AWAY, CONFIRMED_AWAY, now, "continuous_away_confirmed", transition_id)
            self._record_event(
                "action_attempt",
                ts=now,
                reason="active_departure_action",
                transition_id=transition_id,
                details={"eligible": True, "actual_command_executed": False},
            )
            # Hard physical-command boundary: shadow mode returned above and can
            # never reach the Sonoff callable.
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
                "actual_command_executed": True,
            })
            self.state["updated_at"] = int(self.clock())
            self._save()
            self._record_event(
                "action_result",
                ts=self.state["updated_at"],
                reason=self.state["departure_action"]["status"],
                transition_id=transition_id,
                details={
                    "completed": ok,
                    "error": error,
                    "actual_command_executed": True,
                },
            )
            return self.snapshot()

        if both_away:
            self.state.update({
                "state": PENDING_AWAY,
                "pending_since": now,
                "confirmed_away_at": None,
                "transition_id": None,
                "departure_action": _departure_action(self.intended_channels),
                "last_transition": {"from": HOME, "to": PENDING_AWAY, "at": now},
                "reason": "both_away_candidate",
                "updated_at": now,
            })
            self._save()
            self._record_household_transition(HOME, PENDING_AWAY, now, "both_away_candidate")
        else:
            self.state["pending_since"] = None
            self.state["reason"] = "home_present" if any_present else "home_unknown"
            self._save_if_changed(previous, now)
        return self.snapshot()
