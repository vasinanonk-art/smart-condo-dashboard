import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import electricity_history as history
from backend import electricity_history_coverage as coverage

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack08Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_shared_interaction_engine_is_used_by_electricity(self):
        shared = self.read("frontend/assets/dashboard_pm25_hotfix.js")
        electricity = self.read("frontend/assets/dashboard_electricity.js")
        self.assertIn("function selectSampleIndex", shared)
        self.assertIn("function attach(config)", shared)
        self.assertIn("DashboardChartInteraction", electricity)
        self.assertIn("engine.attach", electricity)
        self.assertNotIn("const pick=", electricity)
        self.assertNotIn("nearest sample exceeds", electricity)

    def test_first_last_one_and_two_sample_selection(self):
        # Mirrors the exported shared midpoint contract.
        def select(pointer, positions):
            if not positions:
                return -1
            if len(positions) == 1:
                return 0
            first_mid = (positions[0] + positions[1]) / 2
            last_mid = (positions[-2] + positions[-1]) / 2
            if pointer <= first_mid:
                return 0
            if pointer >= last_mid:
                return len(positions) - 1
            return min(range(len(positions)), key=lambda index: abs(positions[index] - pointer))

        self.assertEqual(select(0, [20]), 0)
        self.assertEqual(select(-100, [10, 90]), 0)
        self.assertEqual(select(1000, [10, 90]), 1)
        self.assertEqual(select(10, [10, 30, 70, 90]), 0)
        self.assertEqual(select(90, [10, 30, 70, 90]), 3)
        # Zoomed/panned visible positions still select visible boundaries.
        self.assertEqual(select(250, [250, 300, 350]), 0)
        self.assertEqual(select(350, [250, 300, 350]), 2)

    def test_history_coverage_metadata_is_real_and_partial(self):
        payload = {
            "range": "7d",
            "from": 1_000,
            "to": 605_800,
            "samples": [{"ts": 605_700}, {"ts": 605_760}],
            "summary": {},
        }
        result = coverage._coverage(payload)
        self.assertEqual(result["first_sample_ts"], 605_700)
        self.assertEqual(result["last_sample_ts"], 605_760)
        self.assertEqual(result["available_duration_sec"], 60)
        self.assertEqual(result["requested_duration_sec"], 604_800)
        self.assertFalse(result["complete"])
        self.assertGreaterEqual(result["coverage_percent"], 0)
        self.assertLess(result["coverage_percent"], 1)

    def test_history_payload_summary_contains_coverage_fields(self):
        base = {
            "range": "24h",
            "from": 100,
            "to": 200,
            "samples": [{"ts": 150, "power": 10}],
            "summary": {"sample_count": 1},
        }
        with patch.object(coverage, "_original_history_payload", return_value=base):
            result = coverage.history_payload_with_coverage("24h")
        self.assertIn("coverage", result)
        self.assertIn("first_sample_ts", result["summary"])
        self.assertIn("last_sample_ts", result["summary"])
        self.assertIn("coverage_complete", result["summary"])
        self.assertIn("coverage_percent", result["summary"])

    def test_tariff_status_missing_and_valid(self):
        with patch.dict(os.environ, {}, clear=True):
            status = coverage.get_tariff_status()
            self.assertFalse(status["configured"])
            self.assertFalse(status["valid"])
            self.assertEqual(status["diagnostics"]["reason"], "tariff_not_configured")

        config = {
            "tariff_name": "Configured residential tariff",
            "effective_date": "2026-07-01",
            "tiers": [{"up_to_kwh": 100, "rate": 3.0}, {"up_to_kwh": None, "rate": 4.0}],
            "ft_rate": 0.1,
            "service_charge": 20,
            "vat_percent": 7,
        }
        with patch.dict(os.environ, {"ELECTRICITY_TARIFF_CONFIG_JSON": json.dumps(config)}, clear=True):
            status = coverage.get_tariff_status()
            self.assertTrue(status["configured"])
            self.assertTrue(status["valid"])
            self.assertEqual(status["tier_count"], 2)
            self.assertEqual(status["tariff_name"], config["tariff_name"])

    def test_tariff_helper_outputs_compact_environment_line(self):
        script = self.read("scripts/generate_electricity_tariff_config.py")
        self.assertIn("ELECTRICITY_TARIFF_CONFIG_JSON='", script)
        self.assertIn('separators=(",", ":")', script)
        self.assertIn('datetime.strptime(value, "%Y-%m-%d")', script)
        self.assertIn("maximum=100.0", script)
        self.assertIn("blank = unlimited final tier", script)

    def test_backfill_is_read_only_by_default_and_deduplicates(self):
        script = self.read("scripts/import_electricity_history.py")
        self.assertIn('--apply', script)
        self.assertIn('dry_run', script)
        self.assertIn('home_assistant_import', script)
        self.assertIn('no_backfill_source_available', script)
        self.assertIn('if not ts or ts in existing', script)

    def test_real_timestamp_axis_and_gap_breaks(self):
        js = self.read("frontend/assets/dashboard_electricity.js")
        self.assertIn("xTs=ts=>", js)
        self.assertIn("splitSegments", js)
        self.assertIn("max_gap_sec", js)
        self.assertIn("Partial history", js)
        self.assertIn("Empty time is not interpolated", js)


if __name__ == "__main__":
    unittest.main()
