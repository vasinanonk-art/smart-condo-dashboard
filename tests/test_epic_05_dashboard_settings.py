import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import dashboard_settings as settings
from backend import dashboard_settings_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


class Epic05DashboardSettingsTests(unittest.TestCase):
    def valid_payload(self):
        return {
            "version": 1,
            "electricity": {
                "billing_cycle_day": 2,
                "timezone": "Asia/Bangkok",
                "tariff": {
                    "tariff_name": "Configured tariff",
                    "source": "manual",
                    "effective_date": "2026-07-01",
                    "version": "manual-2026-07",
                    "tiers": [
                        {"up_to_kwh": 100, "rate": 3.0},
                        {"up_to_kwh": None, "rate": 4.0},
                    ],
                    "ft_rate": 0.1,
                    "service_charge": 20,
                    "vat_percent": 7,
                    "minimum_charge": 0,
                },
            },
            "dashboard": {"timezone": "Asia/Bangkok"},
            "maintenance": {
                "daily_hour": 3,
                "history_retention_days": 400,
                "tariff_sync_enabled": False,
                "tariff_sync_interval_days": 1,
            },
        }

    def test_validation_accepts_expected_non_secret_configuration(self):
        result = settings.validate_settings(self.valid_payload())
        self.assertEqual(result["electricity"]["billing_cycle_day"], 2)
        self.assertEqual(result["electricity"]["tariff"]["tiers"][-1]["up_to_kwh"], None)
        self.assertEqual(result["maintenance"]["daily_hour"], 3)

    def test_validation_rejects_secret_fields(self):
        payload = self.valid_payload()
        payload["dashboard"]["session_secret"] = "not-allowed"
        with self.assertRaisesRegex(ValueError, "invalid_or_forbidden_settings"):
            settings.validate_settings(payload)

    def test_validation_rejects_invalid_ranges_and_tiers(self):
        payload = self.valid_payload()
        payload["electricity"]["billing_cycle_day"] = 32
        with self.assertRaisesRegex(ValueError, "invalid_billing_cycle_day"):
            settings.validate_settings(payload)
        payload = self.valid_payload()
        payload["electricity"]["tariff"]["vat_percent"] = 101
        with self.assertRaisesRegex(ValueError, "invalid_vat_percent"):
            settings.validate_settings(payload)
        payload = self.valid_payload()
        payload["electricity"]["tariff"]["tiers"] = [{"up_to_kwh": 100, "rate": 3}]
        with self.assertRaisesRegex(ValueError, "final_tier_must_be_unlimited"):
            settings.validate_settings(payload)

    def test_atomic_save_creates_backup_and_never_stores_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            maintenance = Path(directory) / "maintenance_state.json"
            with patch.object(settings, "SETTINGS_PATH", path), patch.object(settings, "MAINTENANCE_PATH", maintenance):
                first = settings.save_settings(self.valid_payload())
                second = self.valid_payload()
                second["electricity"]["billing_cycle_day"] = 5
                saved = settings.save_settings(second)
                self.assertEqual(saved["electricity"]["billing_cycle_day"], 5)
                self.assertTrue(list(Path(directory).glob("settings.json.backup-*")))
                text = path.read_text(encoding="utf-8")
                for term in ("password", "session_secret", "token", "cookie"):
                    self.assertNotIn(term, text)
                self.assertEqual(json.loads(text)["electricity"]["billing_cycle_day"], 5)
                self.assertEqual(first["electricity"]["billing_cycle_day"], 2)

    def test_tariff_status_uses_saved_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with patch.object(settings, "SETTINGS_PATH", path):
                settings.save_settings(self.valid_payload())
                status = runtime.tariff_status_from_settings()
                self.assertTrue(status["configured"])
                self.assertTrue(status["valid"])
                self.assertEqual(status["version"], "manual-2026-07")
                self.assertEqual(status["diagnostics"]["source"], "settings_json")

    def test_import_api_requires_explicit_confirmation(self):
        response = settings.import_history({"confirm": False})
        self.assertEqual(response.status_code, 409)
        payload = json.loads(response.body)
        self.assertEqual(payload["detail"], "confirmation_required")

    def test_one_daily_scheduler_contract(self):
        source = (ROOT / "backend/dashboard_settings.py").read_text(encoding="utf-8")
        self.assertIn('name="dashboard-daily-maintenance"', source)
        self.assertIn("_seconds_until_next_run", source)
        self.assertIn("if _THREAD and _THREAD.is_alive()", source)
        self.assertNotIn("while True", source)
        self.assertIn("_STOP.wait", source)

    def test_frontend_contains_settings_sections_and_no_horizontal_scroll(self):
        js = (ROOT / "frontend/assets/dashboard_settings.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/assets/dashboard_settings.css").read_text(encoding="utf-8")
        index = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
        for label in ("Electricity", "Dashboard", "Maintenance", "Analyze History", "Import History", "Export settings.json"):
            self.assertIn(label, js)
        self.assertIn("dashboard_settings.js?v=__ASSET_VERSION__", index)
        self.assertIn("dashboard_settings.css?v=__ASSET_VERSION__", index)
        self.assertIn("overflow-x:hidden", css)

    def test_required_api_paths_are_registered(self):
        source = (ROOT / "backend/dashboard_settings.py").read_text(encoding="utf-8")
        for path in (
            "/api/settings", "/api/settings/electricity", "/api/settings/export",
            "/api/settings/import", "/api/electricity/history/analyze",
            "/api/electricity/history/import", "/api/electricity/history/import/status",
            "/api/notifications", "/api/maintenance/status",
        ):
            self.assertIn(path, source)


if __name__ == "__main__":
    unittest.main()
