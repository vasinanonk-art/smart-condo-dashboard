from pathlib import Path
import unittest

from frontend_runtime import chart_behavior, topology_behavior

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack05Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chart = (ROOT / "frontend/assets/dashboard_pm25_hotfix.js").read_text(encoding="utf-8")
        cls.topology = (ROOT / "frontend/assets/dashboard_topology.js").read_text(encoding="utf-8")

    def test_shared_chart_engine_covers_temperature_and_pm25(self):
        self.assertIn("overviewChart", self.chart)
        self.assertIn("overviewPmChart", self.chart)
        self.assertIn("airChart", self.chart)
        self.assertEqual(self.chart.count("function selectSampleIndex"), 1)
        self.assertIn("DashboardChartInteraction", self.chart)

    def test_first_last_and_single_sample_boundaries_are_explicit(self):
        result = chart_behavior()
        self.assertEqual(result["single"], [55])
        self.assertEqual((result["before"], result["after"]), (0, 3))

    def test_pointer_and_selected_sample_positions_are_separate(self):
        result = chart_behavior()
        self.assertEqual(result["positions"], [10, 40, 70, 100])
        self.assertEqual(result["middle"], 1)

    def test_topology_uses_deduplicated_operational_edges(self):
        result = topology_behavior()
        self.assertTrue(result["unique"])
        categories = {key.rsplit(":", 1)[1] for key in result["required"]}
        self.assertEqual(categories, {"primary_dependency", "data_source", "network_tunnel"})

    def test_required_topology_dependencies_are_preserved(self):
        self.assertIn("['tinkerboard','electricity','primary_dependency']", self.topology)
        self.assertIn("['tinkerboard','tapo_ir','primary_dependency']", self.topology)
        self.assertIn("['home_assistant','tuya','data_source']", self.topology)
        self.assertIn("['home_assistant','pm25','data_source']", self.topology)

    def test_layout_is_deterministic_and_grouped(self):
        result = topology_behavior()
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["diagnosticCount"], 0)


if __name__ == "__main__":
    unittest.main()
