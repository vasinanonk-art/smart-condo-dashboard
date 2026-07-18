import tempfile
import time
import unittest
from collections import deque
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend import automation_core as core
from backend import automation_trigger_engine as engine


class AutomationTriggerEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.path_patch = patch.object(core, "AUTOMATIONS_PATH", root / "automations.json")
        self.events_patch = patch.object(core, "EVENTS_PATH", root / "automation_events.jsonl")
        self.path_patch.start()
        self.events_patch.start()
        engine._PENDING.clear()
        engine._STATE["last_values"] = {}
        engine._STATE["last_fire_keys"] = {}
        engine._STATE["last_triggered"] = {}
        engine._STATE["trigger_count"] = 0
        engine._STATE["cooldown_count"] = 0

    def tearDown(self):
        self.path_patch.stop()
        self.events_patch.stop()
        self.temp.cleanup()

    def automation(self, trigger, condition=None, cooldown=0):
        return core.validate_automation({
            "id": "automation_test_rule",
            "name": "Test rule",
            "enabled": True,
            "mode": "single",
            "trigger": trigger,
            "condition": condition or {"field": "system.dashboard_health", "operator": "eq", "value": "healthy"},
            "actions": [{"type": "placeholder"}],
            "cooldown_sec": cooldown,
            "created_ts": 100,
        }, current_id="automation_test_rule")

    def test_time_trigger_exact_and_once_per_minute(self):
        trigger = engine.validate_trigger({"type": "time", "at": "08:30", "weekdays": "weekday"}, [])
        automation = self.automation(trigger)
        monday = int(datetime(2026, 7, 20, 8, 30).astimezone().timestamp())
        detected, reason = engine._detect_trigger(automation, {}, monday)
        self.assertTrue(detected)
        self.assertEqual(reason, "time_match")
        self.assertFalse(engine._detect_trigger(automation, {}, monday + 20)[0])

    def test_interval_trigger_accuracy_contract(self):
        automation = self.automation({"type": "interval", "every_sec": 30})
        automation["created_ts"] = 100
        self.assertFalse(engine._detect_trigger(automation, {}, 129)[0])
        self.assertTrue(engine._detect_trigger(automation, {}, 130)[0])
        self.assertEqual(engine.WORKER_INTERVAL_SEC, 1.0)

    def test_presence_edge_only(self):
        automation = self.automation({"type": "presence", "event": "beer_arrives"})
        away = {"presence": {"beer": "away"}}
        home = {"presence": {"beer": "home"}}
        self.assertFalse(engine._detect_trigger(automation, away, 100)[0])
        self.assertTrue(engine._detect_trigger(automation, home, 101)[0])
        self.assertFalse(engine._detect_trigger(automation, home, 102)[0])

    def test_electricity_threshold_crossing(self):
        automation = self.automation({"type": "electricity", "field": "power", "operator": "gt", "value": 3000, "edge": "rising"})
        low = {"electricity": {"power": 2500}}
        high = {"electricity": {"power": 3200}}
        self.assertFalse(engine._detect_trigger(automation, low, 100)[0])
        self.assertTrue(engine._detect_trigger(automation, high, 101)[0])
        self.assertFalse(engine._detect_trigger(automation, high, 102)[0])

    def test_pm25_falling_threshold(self):
        automation = self.automation({"type": "pm25", "field": "maximum", "operator": "gt", "value": 20, "edge": "falling"})
        self.assertFalse(engine._detect_trigger(automation, {"pm25": {"maximum": 40}}, 100)[0])
        self.assertTrue(engine._detect_trigger(automation, {"pm25": {"maximum": 18}}, 101)[0])

    def test_cooldown_returns_reason(self):
        automation = self.automation({"type": "manual"}, cooldown=60)
        engine._STATE["last_triggered"][automation["id"]] = 100
        result = engine.process_detected_trigger(automation, "manual_trigger", {"system": {"dashboard_health": "healthy"}}, 120)
        self.assertEqual(result["reason"], "cooldown_active")
        self.assertFalse(result["pending_actions"])

    def test_manual_trigger_populates_pending_without_execution(self):
        automation = self.automation({"type": "manual"})
        store = {"schema_version": core.SCHEMA_VERSION, "automations": [automation]}
        core._atomic_save(store)
        result = engine.process_detected_trigger(automation, "manual_trigger", {"system": {"dashboard_health": "healthy"}}, 200, "manual_trigger")
        self.assertTrue(result["matched"])
        self.assertTrue(result["pending_actions"])
        self.assertFalse(result["actions_executed"])
        self.assertFalse(result["execution_enabled"])

    def test_pending_queue_limit_drops_oldest(self):
        engine._PENDING = deque(maxlen=100)
        automation = self.automation({"type": "manual"})
        for index in range(105):
            engine._queue_pending(automation, index, "test")
        self.assertEqual(len(engine._PENDING), 100)
        self.assertEqual(engine._PENDING[0]["queued_ts"], 5)

    def test_worker_restart_returns_single_live_thread(self):
        first = engine.start_worker()
        second = engine.start_worker()
        self.assertIs(first, second)
        self.assertTrue(second.is_alive())

    def test_malformed_trigger_fails_validation_without_server_error(self):
        errors = []
        result = engine.validate_trigger({"type": "interval", "every_sec": 0}, errors)
        self.assertEqual(result, {})
        self.assertTrue(errors)
        with self.assertRaises(core.ValidationFailure):
            core.validate_automation({"name": "bad", "trigger": {"type": "unknown"}, "condition": {"field": "time.hour", "operator": "eq", "value": 1}, "actions": []})

    def test_system_change_trigger_and_execution_disabled_status(self):
        automation = self.automation({"type": "system", "field": "mqtt_connected", "event": "change"})
        self.assertFalse(engine._detect_trigger(automation, {"system": {"mqtt_connected": True}}, 100)[0])
        self.assertTrue(engine._detect_trigger(automation, {"system": {"mqtt_connected": False}}, 101)[0])
        status = engine.runtime_status()
        self.assertFalse(status["execution_enabled"])

    def test_no_device_or_publish_calls_in_trigger_engine(self):
        source = Path(engine.__file__).read_text(encoding="utf-8")
        self.assertNotIn("mqtt.publish", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("requests.post", source)
        self.assertNotIn("sonoff", source.lower())
        self.assertNotIn("webhook", source.lower())


if __name__ == "__main__":
    unittest.main()
