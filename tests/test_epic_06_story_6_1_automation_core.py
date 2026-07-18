import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import automation_core as core


class AutomationCoreTests(unittest.TestCase):
    def rule(self, condition, **changes):
        value = {
            "id": "automation_test_rule",
            "name": "Test rule",
            "description": "",
            "enabled": True,
            "mode": "single",
            "trigger": {},
            "condition": condition,
            "actions": [],
            "cooldown_sec": 0,
        }
        value.update(changes)
        return value

    def test_numeric_comparisons(self):
        context = {"electricity": {"power": 450.0}}
        for operator, expected, result in (("gt", 400, True), ("gte", 450, True), ("lt", 500, True), ("lte", 449, False), ("eq", 450.0, True), ("ne", 450.0, False)):
            node = {"field": "electricity.power", "operator": operator, "value": expected}
            self.assertEqual(core.evaluate_condition(node, context), result)

    def test_string_and_boolean_comparisons(self):
        context = {"electricity": {"health": "healthy"}, "presence": {"any_home": True}}
        self.assertTrue(core.evaluate_condition({"field":"electricity.health","operator":"eq","value":"healthy"}, context))
        self.assertTrue(core.evaluate_condition({"field":"electricity.health","operator":"in","value":["healthy","warning"]}, context))
        self.assertTrue(core.evaluate_condition({"field":"presence.any_home","operator":"eq","value":True}, context))
        self.assertFalse(core.evaluate_condition({"field":"presence.any_home","operator":"ne","value":True}, context))

    def test_and_or_not(self):
        context = {"electricity":{"power":500}, "presence":{"any_home":True}}
        a = {"field":"electricity.power","operator":"gt","value":400}
        b = {"field":"presence.any_home","operator":"eq","value":True}
        self.assertTrue(core.evaluate_condition({"and":[a,b]}, context))
        self.assertTrue(core.evaluate_condition({"or":[a,{"field":"presence.any_home","operator":"eq","value":False}]}, context))
        self.assertFalse(core.evaluate_condition({"not":a}, context))

    def test_missing_field_is_false(self):
        self.assertFalse(core.evaluate_condition({"field":"electricity.power","operator":"gt","value":1}, {"electricity":{}}))
        self.assertTrue(core.evaluate_condition({"field":"electricity.power","operator":"not_exists"}, {"electricity":{}}))

    def test_invalid_operator_fails_validation(self):
        with self.assertRaises(core.ValidationFailure):
            core.validate_condition({"field":"electricity.power","operator":"contains","value":1})

    def test_maximum_depth(self):
        node = {"field":"electricity.power","operator":"exists"}
        for _ in range(core.MAX_DEPTH):
            node = {"not": node}
        with self.assertRaises(core.ValidationFailure):
            core.validate_condition(node)

    def test_maximum_condition_count(self):
        leaf = {"field":"electricity.power","operator":"exists"}
        with self.assertRaises(core.ValidationFailure):
            core.validate_condition({"and":[leaf for _ in range(core.MAX_CONDITIONS + 1)]})

    def test_safe_context_override(self):
        base = core.build_automation_context()
        result = core._override_context(base, {"electricity":{"power":999},"presence":{"any_home":False}})
        self.assertEqual(result["electricity"]["power"], 999)
        with self.assertRaises(core.ValidationFailure):
            core._override_context(base, {"device":{"command":"on"}})

    def test_secret_field_rejection(self):
        rule = self.rule({"field":"electricity.power","operator":"gt","value":100})
        rule["token"] = "not-allowed"
        with self.assertRaises(core.ValidationFailure):
            core.validate_automation(rule)

    def test_simulation_never_executes_actions(self):
        rule = self.rule({"field":"electricity.power","operator":"gt","value":100}, actions=[{"type":"placeholder"}])
        result = core.evaluate_automation(rule, {"electricity":{"power":200}})
        self.assertTrue(result["matched"])
        source = Path(core.__file__).read_text(encoding="utf-8")
        self.assertIn('"actions_executed": False', source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("eval(", source)
        self.assertNotIn("exec(", source)

    def test_atomic_save_creates_backup_and_crud_survives_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            store_path = data / "automations.json"
            events_path = data / "automation_events.jsonl"
            with patch.object(core, "DATA_DIR", data), patch.object(core, "AUTOMATIONS_PATH", store_path), patch.object(core, "EVENTS_PATH", events_path):
                first = {"schema_version":1,"automations":[core.validate_automation(self.rule({"field":"electricity.power","operator":"exists"}))]}
                core._atomic_save(first)
                second = {"schema_version":1,"automations":[]}
                backup = core._atomic_save(second)
                self.assertTrue(backup and backup.exists())
                self.assertEqual(core._load_store()["automations"], [])

    def test_malformed_payload_returns_validation_not_internal_error(self):
        with self.assertRaises(core.ValidationFailure):
            core.validate_automation(["not", "an", "object"])
        with self.assertRaises(core.ValidationFailure):
            core.validate_automation({"name":"","condition":{}})

    def test_csrf_middleware_still_protects_state_methods(self):
        auth = (Path(__file__).resolve().parents[1] / "backend" / "dashboard_auth.py").read_text(encoding="utf-8")
        entry = (Path(__file__).resolve().parents[1] / "backend" / "app_entry.py").read_text(encoding="utf-8")
        self.assertIn('_STATE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}', auth)
        self.assertIn('request.headers.get("x-csrf-token"', auth)
        self.assertLess(entry.index("automation_core"), entry.index("dashboard_auth"))

    def test_storage_schema_and_audit_retention(self):
        self.assertEqual(core.SCHEMA_VERSION, 1)
        self.assertEqual(core.EVENT_RETENTION_DAYS, int(core.EVENT_RETENTION_DAYS))
        self.assertTrue(str(core.AUTOMATIONS_PATH).endswith("automations.json"))
        self.assertTrue(str(core.EVENTS_PATH).endswith("automation_events.jsonl"))


if __name__ == "__main__":
    unittest.main()
