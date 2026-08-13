import ast
import inspect
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.household_presence_automation import (
    AWAY_CANDIDATE,
    CONFIRMED_AWAY,
    HOME,
    PENDING_AWAY,
    PRESENT,
    UNKNOWN,
    ACTIVE,
    SHADOW,
    HouseholdPresenceAutomation,
    classify_presence,
)


BANGKOK = ZoneInfo("Asia/Bangkok")


class Clock:
    def __init__(self, value):
        self.value = int(value)

    def __call__(self):
        return self.value


class FakeAllOff:
    def __init__(self, result=None):
        self.calls = 0
        self.result = {"ok": True} if result is None else result

    def __call__(self):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def epoch(hour=12, minute=0, second=0):
    return int(datetime(2026, 8, 12, hour, minute, second, tzinfo=BANGKOK).timestamp())


def present():
    return {"home": True, "state": "Home", "source": "Router:REACHABLE", "ip": "192.0.2.1"}


def away():
    return {"home": False, "state": "Away", "source": "Expired", "ip": "192.0.2.1"}


def unknown():
    return {"home": True, "state": "Recently Seen", "source": "Cached", "ip": "192.0.2.1"}


def people(beer, seem):
    return {"beer": beer, "seem": seem}


def machine(tmp_path, start=None, result=None, mode=ACTIVE):
    clock = Clock(epoch() if start is None else start)
    action = FakeAllOff(result)
    automation = HouseholdPresenceAutomation(
        tmp_path / "presence_automation_state.json",
        action,
        clock=clock,
        transition_id_factory=lambda: "transition-1",
        mode=mode,
        intended_channels=[{"deviceid": "fake", "channel": 1, "action": "off"}],
    )
    return automation, clock, action


def confirm(automation, clock, presence, seconds=1800):
    assert automation.evaluate(presence)["state"] == PENDING_AWAY
    clock.value += seconds
    return automation.evaluate(presence)


def test_classification_contract_is_fail_safe():
    assert classify_presence(present())["classification"] == PRESENT
    assert classify_presence(away())["classification"] == AWAY_CANDIDATE
    assert classify_presence(unknown())["classification"] == UNKNOWN
    assert classify_presence({"home": False, "state": "Away", "source": "Expired"}) == {
        "classification": UNKNOWN,
        "reason": "missing_identity",
    }
    assert classify_presence({**away(), "evidence_conflict": True})["classification"] == UNKNOWN
    assert classify_presence({**away(), "evaluation_error": "TimeoutError"})["classification"] == UNKNOWN


def test_conflicting_mqtt_away_and_router_present_is_unknown(monkeypatch):
    from backend import presence_stabilizer as stabilizer

    monkeypatch.setattr(stabilizer, "_now", lambda: 1000)
    monkeypatch.setattr(stabilizer, "_neighbor_state", lambda _ip: "REACHABLE")
    item = stabilizer.resolve_person(
        "beer",
        {"name": "Beer", "state": "away", "ts": 1000, "ip": "192.0.2.1"},
    )
    assert item["classification"] == UNKNOWN
    assert item["reason"] == "conflicting_evidence"


def test_one_person_away_for_hours_stays_home(tmp_path):
    automation, clock, action = machine(tmp_path)
    assert automation.evaluate(people(present(), away()))["state"] == HOME
    clock.value += 8 * 3600
    assert automation.evaluate(people(present(), away()))["state"] == HOME
    assert action.calls == 0


def test_both_away_2959_remains_pending(tmp_path):
    automation, clock, action = machine(tmp_path)
    automation.evaluate(people(away(), away()))
    clock.value += 1799
    state = automation.evaluate(people(away(), away()))
    assert state["state"] == PENDING_AWAY
    assert action.calls == 0


def test_present_at_29_minutes_cancels_timer(tmp_path):
    automation, clock, action = machine(tmp_path)
    automation.evaluate(people(away(), away()))
    clock.value += 1740
    state = automation.evaluate(people(present(), away()))
    assert state["state"] == HOME
    assert state["pending_since"] is None
    assert action.calls == 0


def test_daytime_confirmation_executes_exactly_one_all_off(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30))
    state = confirm(automation, clock, people(away(), away()))
    assert datetime.fromtimestamp(clock.value, BANGKOK).hour == 17
    assert state["state"] == CONFIRMED_AWAY
    assert state["transition_id"] == "transition-1"
    assert state["departure_action"]["completed"] is True
    assert action.calls == 1
    for _ in range(5):
        automation.evaluate(people(away(), away()))
    assert action.calls == 1


@pytest.mark.parametrize(
    ("confirmation", "eligible"),
    [
        ((5, 59, 59), False),
        ((6, 0, 0), True),
        ((18, 29, 59), True),
        ((18, 30, 0), False),
    ],
)
def test_bangkok_confirmation_boundaries(tmp_path, confirmation, eligible):
    confirmation_ts = epoch(*confirmation)
    automation, clock, action = machine(tmp_path, confirmation_ts - 1800)
    state = confirm(automation, clock, people(away(), away()))
    assert state["departure_action"]["eligible"] is eligible
    assert action.calls == int(eligible)


def test_1810_departure_confirmed_1840_has_no_action(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(18, 10))
    state = confirm(automation, clock, people(away(), away()))
    assert state["state"] == CONFIRMED_AWAY
    assert state["departure_action"]["status"] == "outside_time_window"
    assert action.calls == 0


def test_sleep_and_reconnect_never_turns_lights_on(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(22, 0))
    for _ in range(4):
        automation.evaluate(people(away(), away()))
        clock.value += 900
        automation.evaluate(people(present(), present()))
        clock.value += 60
    assert automation.snapshot()["state"] == HOME
    assert action.calls == 0


def test_confirmed_away_return_changes_home_without_action(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30))
    confirm(automation, clock, people(away(), away()))
    assert action.calls == 1
    state = automation.evaluate(people(present(), away()))
    assert state["state"] == HOME
    assert action.calls == 1


def test_restart_during_pending_requires_new_full_dwell(tmp_path):
    automation, clock, action = machine(tmp_path)
    automation.evaluate(people(away(), away()))
    clock.value += 1700
    restarted = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, transition_id_factory=lambda: "t2", mode=ACTIVE)
    assert restarted.snapshot()["state"] == HOME
    assert restarted.evaluate(people(away(), away()))["state"] == PENDING_AWAY
    clock.value += 1799
    assert restarted.evaluate(people(away(), away()))["state"] == PENDING_AWAY
    assert action.calls == 0


def test_restart_during_confirmed_away_restores_without_repeating(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30))
    confirm(automation, clock, people(away(), away()))
    assert action.calls == 1
    restarted = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, transition_id_factory=lambda: "t2", mode=ACTIVE)
    assert restarted.snapshot()["state"] == CONFIRMED_AWAY
    restarted.evaluate(people(away(), away()))
    assert action.calls == 1


def test_unknown_during_pending_cancels(tmp_path):
    automation, clock, action = machine(tmp_path)
    automation.evaluate(people(away(), away()))
    clock.value += 1000
    state = automation.evaluate(people(unknown(), away()))
    assert state["state"] == HOME
    assert state["pending_since"] is None
    assert action.calls == 0


def test_all_off_failure_is_persisted_without_retry(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30), {"ok": False})
    state = confirm(automation, clock, people(away(), away()))
    assert state["state"] == CONFIRMED_AWAY
    assert state["departure_action"]["status"] == "failed"
    assert state["departure_action"]["attempted"] is True
    assert action.calls == 1
    for _ in range(3):
        automation.evaluate(people(away(), away()))
    assert action.calls == 1
    persisted = json.loads(automation.state_path.read_text())
    assert persisted["departure_action"]["status"] == "failed"


def test_retained_home_after_restart_cannot_turn_light_on(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30))
    confirm(automation, clock, people(away(), away()))
    restarted = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, mode=ACTIVE)
    restarted.evaluate(people(present(), present()))
    assert restarted.snapshot()["state"] == HOME
    assert action.calls == 1


def test_persistence_contract_and_permissions(tmp_path):
    automation, _, _ = machine(tmp_path)
    payload = json.loads(automation.state_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["state"] == HOME
    assert payload["pending_since"] is None
    assert payload["confirmed_away_at"] is None
    assert payload["transition_id"] is None
    assert set(payload["departure_action"]) == {
        "eligible", "attempted", "completed", "attempted_at", "completed_at", "status", "error",
        "intended_channels", "would_execute", "actual_command_executed"
    }
    assert automation.state_path.stat().st_mode & 0o777 == 0o600


def test_no_legacy_presence_triggered_light_on_execution_path():
    root = Path(__file__).resolve().parents[1]
    source = (root / "sonoff_client.py").read_text()
    runtime = (root / "backend/runtime_fixes.py").read_text()
    forbidden = (
        "ARRIVAL_DEVICEID",
        "arrival_armed",
        "_run_arrival_action",
        "_run_person_arrival_automation",
        "living_room_on",
        'set_state(ARRIVAL',
    )
    assert not any(term in source for term in forbidden)
    assert source.count("def _evaluate_household_presence") == 1
    assert source.count("_evaluate_household_presence(") == 1
    assert runtime.count("presence_automation._evaluate_household_presence(") == 1
    tree = ast.parse(source)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "HOUSEHOLD_DEPARTURE_CHANNELS" for target in node.targets)
    )
    assert ast.literal_eval(assignment.value) == (
        ("10015b0992", 1),
        ("100250f198", 1), ("100250f198", 2),
        ("10026c4143", 1), ("10026c4143", 2), ("10026c4143", 3),
        ("1002354e11", 1),
    )
    assert 'set_state(deviceid, "off", channel)' in source
    assert 'bulk_all_state("on")' not in source


def test_departure_helper_targets_only_approved_channels_with_fake(monkeypatch):
    import sonoff_client

    calls = []
    monkeypatch.setattr(
        sonoff_client,
        "set_state",
        lambda deviceid, action, channel: calls.append((deviceid, action, channel)) or {"ok": True},
    )
    result = sonoff_client._all_sonoff_lights_off()
    assert result["ok"] is True
    assert calls == [
        ("10015b0992", "off", 1),
        ("100250f198", "off", 1), ("100250f198", "off", 2),
        ("10026c4143", "off", 1), ("10026c4143", "off", 2), ("10026c4143", "off", 3),
        ("1002354e11", "off", 1),
    ]


def test_away_candidate_contract_is_documented():
    from backend import household_presence_automation as module

    assert "operational inference" in inspect.getdoc(module)
    assert "not proof" in inspect.getdoc(module)


def test_missing_or_invalid_mode_defaults_to_shadow(monkeypatch, tmp_path):
    from backend.household_presence_automation import execution_mode

    monkeypatch.delenv("PRESENCE_AUTOMATION_MODE", raising=False)
    assert execution_mode() == SHADOW
    monkeypatch.setenv("PRESENCE_AUTOMATION_MODE", "invalid")
    assert execution_mode() == SHADOW
    action = FakeAllOff()
    automation = HouseholdPresenceAutomation(tmp_path / "default.json", action, clock=Clock(epoch(16, 30)))
    assert automation.snapshot()["mode"] == SHADOW


def test_shadow_daytime_confirmation_would_off_without_call(tmp_path, capsys):
    automation, clock, action = machine(tmp_path, epoch(16, 30), mode=SHADOW)
    state = confirm(automation, clock, people(away(), away()))
    departure = state["departure_action"]
    assert state["state"] == CONFIRMED_AWAY
    assert departure["eligible"] is True
    assert departure["would_execute"] is True
    assert departure["actual_command_executed"] is False
    assert departure["attempted"] is False
    assert departure["status"] == "would_turn_off"
    assert departure["intended_channels"] == [{"deviceid": "fake", "channel": 1, "action": "off"}]
    assert "WOULD TURN OFF" in capsys.readouterr().out
    assert action.calls == 0


def test_shadow_night_and_1810_to_1840_have_no_would_off(tmp_path):
    for start in (epoch(18, 0), epoch(18, 10)):
        path = tmp_path / str(start)
        path.mkdir()
        automation, clock, action = machine(path, start, mode=SHADOW)
        state = confirm(automation, clock, people(away(), away()))
        assert state["departure_action"]["eligible"] is False
        assert state["departure_action"]["would_execute"] is False
        assert action.calls == 0


def test_shadow_reconnect_returns_home_without_light_action(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30), mode=SHADOW)
    confirm(automation, clock, people(away(), away()))
    state = automation.evaluate(people(present(), away()))
    assert state["state"] == HOME
    assert action.calls == 0


def test_shadow_to_active_resets_home_and_requires_fresh_dwell(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30), mode=SHADOW)
    confirm(automation, clock, people(away(), away()))
    active = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, mode=ACTIVE)
    assert active.snapshot()["state"] == HOME
    assert active.snapshot()["reason"] == "execution_mode_changed_reset_home"
    assert active.evaluate(people(away(), away()))["state"] == PENDING_AWAY
    clock.value += 1799
    assert active.evaluate(people(away(), away()))["state"] == PENDING_AWAY
    assert action.calls == 0


def test_restart_shadow_pending_resets_and_confirmed_restores_without_call(tmp_path):
    automation, clock, action = machine(tmp_path, epoch(16, 30), mode=SHADOW)
    automation.evaluate(people(away(), away()))
    pending_restart = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, mode=SHADOW)
    assert pending_restart.snapshot()["state"] == HOME
    state = confirm(pending_restart, clock, people(away(), away()))
    assert state["state"] == CONFIRMED_AWAY
    confirmed_restart = HouseholdPresenceAutomation(automation.state_path, action, clock=clock, mode=SHADOW)
    assert confirmed_restart.snapshot()["state"] == CONFIRMED_AWAY
    assert confirmed_restart.snapshot()["departure_action"]["actual_command_executed"] is False
    assert action.calls == 0


def test_shadow_guard_precedes_and_blocks_physical_callable(tmp_path):
    class Unreachable:
        def __call__(self):
            raise AssertionError("physical command boundary reached in shadow")

    clock = Clock(epoch(16, 30))
    automation = HouseholdPresenceAutomation(
        tmp_path / "guard.json", Unreachable(), clock=clock, mode=SHADOW
    )
    state = confirm(automation, clock, people(away(), away()))
    assert state["departure_action"]["status"] == "would_turn_off"
    source = inspect.getsource(HouseholdPresenceAutomation._evaluate_locked)
    assert source.index("if self.mode != ACTIVE") < source.index("result = self.all_off()")
