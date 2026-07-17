import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import dashboard_settings as settings
from backend import dashboard_settings_hotfix09 as hotfix


class HotfixPack09Tests(unittest.TestCase):
    def test_runtime_state_is_single_source_for_status(self):
        snapshot = {
            "billing_coverage_percent": 48.5,
            "billing_coverage_complete": False,
            "history_first_ts": 100,
            "history_last_ts": 200,
            "history_sample_count": 10,
            "tariff_version": "manual-1",
            "last_tariff_check_ts": 300,
            "last_history_prune_ts": 400,
            "projection_status": "available",
            "last_successful_run": 500,
            "last_failed_run": None,
            "history_import_duration_ms": 12.3,
            "tariff_check_duration_ms": 0.4,
            "history_prune_duration_ms": 2.1,
        }
        hotfix._save_runtime_state(snapshot)
        self.assertEqual(settings._load_maintenance(), snapshot)
        status = hotfix.electricity_status_from_runtime()
        self.assertEqual(status["coverage_percent"], 48.5)
        self.assertEqual(status["history_starts"], 100)
        self.assertEqual(status["history_ends"], 200)
        self.assertEqual(status["tariff_version"], "manual-1")
        self.assertEqual(status["projection_status"], "available")
        self.assertEqual(status["history_import_duration_ms"], 12.3)

    def test_import_invocation_matches_manual_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "scripts").mkdir()
            (source / "scripts" / "import_electricity_history.py").write_text("print('{}')\n", encoding="utf-8")
            with patch.dict(os.environ, {"SMART_CONDO_SOURCE_DIR": str(source)}):
                argv, script, cwd = hotfix._history_import_invocation(False)
            self.assertEqual(cwd, source)
            self.assertEqual(script, source / "scripts" / "import_electricity_history.py")
            self.assertEqual(argv[0], os.path.realpath(os.sys.executable) if False else os.sys.executable)
            self.assertEqual(argv[1], str(script))
            self.assertNotIn("--apply", argv)

    def test_apply_invocation_adds_only_apply_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "scripts").mkdir()
            with patch.dict(os.environ, {"SMART_CONDO_SOURCE_DIR": str(source)}):
                argv, _, _ = hotfix._history_import_invocation(True)
            self.assertEqual(argv[-1], "--apply")
            self.assertEqual(argv.count("--apply"), 1)

    def test_failure_returns_required_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "scripts").mkdir()
            with patch.dict(os.environ, {"SMART_CONDO_SOURCE_DIR": str(source)}):
                result = hotfix._run_import(False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["exit_code"], 2)
            diagnostics = result["diagnostics"]
            for key in ("python_executable", "script_path", "cwd", "argv", "stderr"):
                self.assertIn(key, diagnostics)
            self.assertTrue(diagnostics["stderr"])

    def test_maintenance_fields_and_failure_notification_are_present(self):
        source = (Path(__file__).resolve().parents[1] / "backend" / "dashboard_settings_hotfix09.py").read_text(encoding="utf-8")
        for field in (
            '"last_successful_run"',
            '"last_failed_run"',
            '"history_import_duration_ms"',
            '"tariff_check_duration_ms"',
            '"history_prune_duration_ms"',
        ):
            self.assertIn(field, source)
        self.assertIn('"History analysis failed"', source)
        self.assertIn('"history_analysis_failed"', source)


if __name__ == "__main__":
    unittest.main()
