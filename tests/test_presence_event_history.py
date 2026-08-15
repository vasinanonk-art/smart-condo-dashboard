import os
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.household_presence_automation import HOME, PENDING_AWAY, SHADOW, HouseholdPresenceAutomation
from backend.presence_event_store import PresenceEventStore


class Clock:
    def __init__(self, value=None):
        if value is None:
            value = datetime(2026, 8, 12, 16, 30, tzinfo=timezone(timedelta(hours=7))).timestamp()
        self.value = int(value)

    def __call__(self):
        return self.value


class NoPhysicalCommand:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("physical command boundary reached")


def present():
    return {"home": True, "state": "Home", "source": "Router:REACHABLE", "ip": "192.0.2.1"}


def away():
    return {"home": False, "state": "Away", "source": "Expired", "ip": "192.0.2.1"}


def people(value):
    return {"beer": value, "seem": value}


def test_history_survives_restart_and_records_shadow_decision_and_return(tmp_path):
    clock = Clock()
    action = NoPhysicalCommand()
    state_path = tmp_path / "presence_automation_state.json"
    event_path = tmp_path / "presence_events.sqlite3"
    automation = HouseholdPresenceAutomation(
        state_path,
        action,
        clock=clock,
        transition_id_factory=lambda: "transition-1",
        mode=SHADOW,
        event_db_path=event_path,
    )

    assert automation.evaluate(people(away()))["state"] == PENDING_AWAY
    clock.value += 1800
    confirmed = automation.evaluate(people(away()))
    assert confirmed["departure_action"]["would_execute"] is True
    assert confirmed["departure_action"]["actual_command_executed"] is False
    clock.value += 60
    assert automation.evaluate(people(present()))["state"] == HOME
    assert action.calls == 0

    restarted = HouseholdPresenceAutomation(
        state_path,
        action,
        clock=clock,
        mode=SHADOW,
        event_db_path=event_path,
    )
    events = restarted.recent_events(100)
    transitions = [event for event in events if event["event_type"] == "household_transition"]
    decisions = [event for event in events if event["event_type"] == "action_decision"]
    assert [(event["from_state"], event["to_state"]) for event in reversed(transitions)] == [
        ("HOME", "PENDING_AWAY"),
        ("PENDING_AWAY", "CONFIRMED_AWAY"),
        ("CONFIRMED_AWAY", "HOME"),
    ]
    assert decisions[0]["reason"] == "shadow_would_turn_off"
    assert decisions[0]["details"]["actual_command_executed"] is False
    assert [event["event_type"] for event in events].count("startup") == 2
    assert action.calls == 0


def test_pending_restart_is_auditable_but_requires_fresh_dwell(tmp_path):
    clock = Clock()
    action = NoPhysicalCommand()
    state_path = tmp_path / "presence_automation_state.json"
    event_path = tmp_path / "presence_events.sqlite3"
    automation = HouseholdPresenceAutomation(
        state_path, action, clock=clock, mode=SHADOW, event_db_path=event_path
    )
    assert automation.evaluate(people(away()))["state"] == PENDING_AWAY

    clock.value += 1200
    restarted = HouseholdPresenceAutomation(
        state_path, action, clock=clock, mode=SHADOW, event_db_path=event_path
    )
    assert restarted.snapshot()["state"] == HOME
    startup = restarted.recent_events(1)[0]
    assert startup["event_type"] == "startup"
    assert startup["from_state"] == PENDING_AWAY
    assert startup["to_state"] == HOME
    assert startup["reason"] == "pending_cancelled_on_restart"
    assert restarted.evaluate(people(away()))["state"] == PENDING_AWAY
    assert action.calls == 0


def test_person_classification_events_are_transition_only(tmp_path):
    clock = Clock()
    automation = HouseholdPresenceAutomation(
        tmp_path / "state.json",
        NoPhysicalCommand(),
        clock=clock,
        mode=SHADOW,
        event_db_path=tmp_path / "events.sqlite3",
    )
    automation.evaluate(people(present()))
    clock.value += 10
    automation.evaluate(people(present()))
    events = [event for event in automation.recent_events() if event["event_type"] == "person_classification"]
    assert len(events) == 2
    assert {event["person"] for event in events} == {"beer", "seem"}


def test_store_prunes_old_events_and_is_owner_only(tmp_path):
    clock = Clock()
    store = PresenceEventStore(tmp_path / "events.sqlite3", clock=clock, retention_days=30)
    store.record("old", ts=clock.value - 31 * 86400)
    store.record("current", ts=clock.value)
    assert [event["event_type"] for event in store.recent()] == ["current"]
    assert os.stat(store.path).st_mode & 0o777 == 0o600


def test_sqlite_backup_is_consistent_and_nas_failure_does_not_block_history(tmp_path):
    clock = Clock()
    backup_dir = tmp_path / "backup"
    store = PresenceEventStore(tmp_path / "events.sqlite3", clock=clock, backup_dir=backup_dir)
    assert store.record("startup", mode=SHADOW) is True
    stamp = datetime.fromtimestamp(clock.value, timezone.utc).strftime("%Y%m%d")
    backup = backup_dir / f"presence_events-{stamp}.sqlite3"
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT event_type FROM presence_events").fetchall() == [("startup",)]
    assert os.stat(backup).st_mode & 0o777 == 0o600

    broken_backup = tmp_path / "not-a-directory"
    broken_backup.write_text("blocked")
    second = PresenceEventStore(
        tmp_path / "second.sqlite3", clock=clock, backup_dir=broken_backup
    )
    assert second.record("startup", mode=SHADOW) is True
    assert second.recent(1)[0]["event_type"] == "startup"
    assert second.status()["last_backup_error"] in {"FileExistsError", "NotADirectoryError"}


def test_event_database_failure_cannot_reach_physical_command(tmp_path):
    clock = Clock()
    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a directory")
    failed_store = PresenceEventStore(blocked_parent / "events.sqlite3", clock=clock)
    action = NoPhysicalCommand()
    automation = HouseholdPresenceAutomation(
        tmp_path / "state.json",
        action,
        clock=clock,
        mode=SHADOW,
        event_store=failed_store,
    )
    assert automation.evaluate(people(away()))["state"] == PENDING_AWAY
    clock.value += 1800
    state = automation.evaluate(people(away()))
    assert state["departure_action"]["would_execute"] is True
    assert state["departure_action"]["actual_command_executed"] is False
    assert failed_store.status()["available"] is False
    assert action.calls == 0


def test_status_api_exposes_bounded_recent_events_and_store_health(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_CONDO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PRESENCE_EVENT_DB_PATH", str(tmp_path / "api-events.sqlite3"))
    import sonoff_client
    from backend import app_runtime

    clock = Clock()
    automation = HouseholdPresenceAutomation(
        tmp_path / "state.json",
        NoPhysicalCommand(),
        clock=clock,
        mode=SHADOW,
        event_db_path=tmp_path / "events.sqlite3",
    )
    automation.evaluate(people(present()))
    monkeypatch.setattr(sonoff_client, "_household_automation", automation)

    status = sonoff_client.household_automation_status()
    assert 1 <= len(status["recent_events"]) <= 100
    assert status["event_history"] == {
        "available": True,
        "retention_days": 30,
        "backup_enabled": False,
        "last_error": None,
        "last_backup_error": None,
    }
    payload = app_runtime._automation_payload()
    assert payload["recent_events"] == status["recent_events"]
    assert payload["event_history"]["available"] is True


def test_backup_rejects_destination_that_cannot_enforce_private_mode(monkeypatch, tmp_path):
    clock = Clock()
    backup_dir = tmp_path / "backup"
    store = PresenceEventStore(tmp_path / "events.sqlite3", clock=clock, backup_dir=backup_dir)
    real_chmod = os.chmod

    def ignore_backup_chmod(path, mode):
        if str(path).startswith(str(backup_dir)):
            return None
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", ignore_backup_chmod)
    assert store.record("startup", mode=SHADOW) is True
    assert store.status()["last_backup_error"] == "PermissionError"
    assert list(backup_dir.glob("presence_events-*.sqlite3")) == []


def test_presence_paths_are_immutable_after_process_start():
    import sonoff_client

    paths = (
        sonoff_client._presence_state_path(),
        sonoff_client._presence_event_db_path(),
        sonoff_client._presence_event_backup_dir(),
    )
    with patch.dict(os.environ, {}, clear=True):
        assert sonoff_client._presence_state_path() == paths[0]
        assert sonoff_client._presence_event_db_path() == paths[1]
        assert sonoff_client._presence_event_backup_dir() == paths[2]
