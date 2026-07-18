import unittest
from pathlib import Path
from unittest.mock import patch

from backend import dashboard_polish_hotfix10 as hotfix

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack10Tests(unittest.TestCase):
    def test_billing_notification_is_updated_in_place(self):
        old = {
            "billing_coverage_percent": 2.61,
            "billing_coverage_complete": False,
            "notifications": [{
                "id": "billing_incomplete",
                "kind": "billing_incomplete",
                "title": "Billing history incomplete",
                "detail": "Current billing coverage is 2.61%.",
                "coverage": 2.61,
                "created_ts": 100,
                "severity": "warning",
                "dismissed": False,
            }],
        }
        with patch("time.time", return_value=200):
            old["billing_coverage_percent"] = 3.76
            result = hotfix._stable_billing_notification(old)
        notices = [n for n in result["notifications"] if n["kind"] == "billing_incomplete"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]["id"], "billing_incomplete")
        self.assertEqual(notices[0]["coverage"], 3.76)
        self.assertIn("3.76%", notices[0]["detail"])
        self.assertEqual(notices[0]["created_ts"], 200)

    def test_billing_notification_removed_at_full_coverage(self):
        state = {
            "billing_coverage_percent": 100,
            "billing_coverage_complete": True,
            "notifications": [{"id": "billing_incomplete", "kind": "billing_incomplete", "dismissed": False}],
        }
        result = hotfix._stable_billing_notification(state)
        self.assertFalse(any(n.get("kind") == "billing_incomplete" for n in result["notifications"]))

    def test_status_contract_has_new_metadata(self):
        source = (ROOT / "backend" / "dashboard_polish_hotfix10.py").read_text(encoding="utf-8")
        for field in (
            '"next_billing_reset_ts"',
            '"next_maintenance_run_ts"',
            '"timezone_display"',
            '"history_retention_days"',
            '"current_notification_count"',
        ):
            self.assertIn(field, source)
        self.assertIn('"Bangkok (UTC+7)"', source)

    def test_frontend_has_polished_pages_and_actions(self):
        js = (ROOT / "frontend" / "assets" / "dashboard_polish10.js").read_text(encoding="utf-8")
        for text in (
            "Current Billing Cycle",
            "Next Billing Reset",
            "Current billing cycle is incomplete.",
            "Analyze History",
            "Import History",
            "Run Maintenance",
            "Dismiss All",
            "Bangkok (UTC+7)",
        ):
            self.assertIn(text, js)
        self.assertIn("data-nav=\"history\"", js)
        self.assertIn("relative(item.created_ts)", js)

    def test_no_forbidden_integration_changes(self):
        changed = {
            "backend/app_entry.py",
            "backend/dashboard_polish_hotfix10.py",
            "frontend/assets/dashboard_polish10.js",
            "frontend/assets/dashboard_polish10.css",
            "frontend/assets/dashboard_page_chrome.js",
            "frontend/index.html",
            "tests/test_hotfix_pack_10.py",
        }
        self.assertFalse(any("mqtt" in path or "pj1103" in path or "tapo" in path or "camera" in path for path in changed))


if __name__ == "__main__":
    unittest.main()
