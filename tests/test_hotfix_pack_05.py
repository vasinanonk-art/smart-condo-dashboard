from pathlib import Path
import unittest


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
        self.assertIn("positions.length === 1", self.chart)
        self.assertIn("clamped <= firstMidpoint", self.chart)
        self.assertIn("clamped >= lastMidpoint", self.chart)
        self.assertIn("selectedX = positions[index]", self.chart)
        self.assertNotIn("nearest-point distance", self.chart)

    def test_pointer_and_selected_sample_positions_are_separate(self):
        self.assertIn("pointerGraphX", self.chart)
        self.assertIn("selectedPx", self.chart)
        self.assertIn("line.setAttribute('x1', String(selectedX))", self.chart)
        self.assertIn("tooltip.innerHTML", self.chart)

    def test_topology_uses_deduplicated_operational_edges(self):
        self.assertIn("const EDGES", self.topology)
        self.assertIn("primary_dependency", self.topology)
        self.assertIn("data_source", self.topology)
        self.assertIn("network_tunnel", self.topology)
        self.assertIn("const seen = new Set()", self.topology)
        self.assertIn("if (seen.has(key)) return", self.topology)

    def test_required_topology_dependencies_are_preserved(self):
        self.assertIn("['tinkerboard','electricity','primary_dependency']", self.topology)
        self.assertIn("['tinkerboard','tapo_ir','primary_dependency']", self.topology)
        self.assertIn("['home_assistant','tuya','data_source']", self.topology)
        self.assertIn("['home_assistant','pm25','data_source']", self.topology)

    def test_layout_is_deterministic_and_grouped(self):
        self.assertIn("const x = {", self.topology)
        self.assertIn("cloud:", self.topology)
        self.assertIn("condoInfra:", self.topology)
        self.assertIn("zeroTier:", self.topology)
        self.assertIn("home:", self.topology)
        self.assertIn("groupBounds(GROUPS.cloud", self.topology)
        self.assertIn("groupBounds(GROUPS.condo", self.topology)
        self.assertIn("groupBounds(GROUPS.zerotier", self.topology)
        self.assertIn("groupBounds(GROUPS.home", self.topology)


if __name__ == "__main__":
    unittest.main()
