import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import electricity_history as history


class ElectricityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "electricity.jsonl"
        self.path_patch = patch.object(history, "HISTORY_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_successful_sample_is_persisted_without_secrets(self):
        payload = {
            "online": True,
            "ts": 1780000000,
            "last_success": 1780000000,
            "voltage": 231.8,
            "current": 1.948,
            "power": 342.0,
            "total_energy": 10.5,
            "source": "tuya_local",
            "local_key": "must-not-persist",
        }
        self.assertTrue(history.append_success(payload))
        text = self.path.read_text(encoding="utf-8")
        self.assertNotIn("local_key", text)
        self.assertNotIn("must-not-persist", text)
        self.assertEqual(history.read_samples()[0]["power"], 342.0)

    def test_failed_and_null_samples_are_not_stored(self):
        self.assertFalse(history.append_success({"online": False, "ts": 1780000000, "power": 10}))
        self.assertFalse(history.append_success({"online": True, "ts": 1780000000}))
        self.assertFalse(self.path.exists())

    def test_corrupted_rows_are_skipped(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("bad json\n" + json.dumps({"ts": 1780000000, "power": 120, "online": True}) + "\n", encoding="utf-8")
        rows = history.read_samples()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["power"], 120.0)

    def test_energy_delta_never_negative_and_handles_reset(self):
        rows = [
            {"ts": 1000, "power": 1000, "total_energy": 20.0},
            {"ts": 1060, "power": 1000, "total_energy": 20.1},
            {"ts": 1120, "power": 1000, "total_energy": 0.0},
            {"ts": 1180, "power": 1000, "total_energy": 0.1},
        ]
        self.assertAlmostEqual(history.energy_used(rows), 0.2, places=6)

    def test_tariff_calculation_is_configuration_driven(self):
        config = {
            "tariff_name": "Test Tariff",
            "effective_date": "2026-07-01",
            "tiers": [{"up_to_kwh": 10, "rate": 2}, {"up_to_kwh": None, "rate": 3}],
            "ft_rate": 0.5,
            "service_charge": 10,
            "vat_percent": 7,
        }
        with patch.dict(os.environ, {"ELECTRICITY_TARIFF_CONFIG_JSON": json.dumps(config)}, clear=False):
            bill = history.calculate_bill(20)
        self.assertTrue(bill["configured"])
        self.assertEqual(bill["base_energy_charge"], 50.0)
        self.assertEqual(bill["ft_charge"], 10.0)
        self.assertEqual(bill["subtotal"], 70.0)
        self.assertEqual(bill["total"], 74.9)

    def test_missing_or_invalid_tariff_is_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(history.calculate_bill(10)["configured"])
        with patch.dict(os.environ, {"ELECTRICITY_TARIFF_CONFIG_JSON": "{}"}, clear=False):
            self.assertFalse(history.calculate_bill(10)["configured"])


class Epic04FrontendTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def read(self, path):
        return (self.ROOT / path).read_text(encoding="utf-8")

    def test_topology_contains_every_required_edge(self):
        js = self.read("frontend/assets/dashboard_topology.js")
        for source, target in (
            ("internet", "cloudflare_wan"), ("cloudflare_wan", "condo_router"),
            ("condo_router", "tinkerboard"), ("tinkerboard", "dashboard"),
            ("tinkerboard", "mqtt"), ("tinkerboard", "sonoff"),
            ("tinkerboard", "camera"), ("tinkerboard", "electricity"),
            ("tinkerboard", "tapo_ir"), ("mqtt", "presence"),
            ("mqtt", "lg_tv"), ("home_assistant", "tuya"),
            ("home_assistant", "pm25"), ("tinkerboard", "zerotier_condo"),
            ("zerotier_condo", "zerotier_tunnel"), ("zerotier_tunnel", "zerotier_home"),
            ("zerotier_home", "truenas"), ("truenas", "home_assistant"),
        ):
            self.assertIn(f"['{source}','{target}'", js)
        self.assertIn("missing_required_edge", js)
        self.assertIn("disconnected_edge", js)
        self.assertIn("group_overlap", js)

    def test_electricity_frontend_has_persistent_ranges_and_exports(self):
        js = self.read("frontend/assets/dashboard_electricity.js")
        for value in ("Live", "24H", "7D", "30D", "This Month", "CSV", "PNG"):
            self.assertIn(value, js)
        self.assertIn("/api/electricity/history", js)
        self.assertIn("/api/electricity/billing", js)
        self.assertIn("Estimated from configured tariff", js)


if __name__ == "__main__":
    unittest.main()
