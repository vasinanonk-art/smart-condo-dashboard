from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack06Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_lg_remote_is_scoped_and_single_install(self):
        js = self.read("frontend/assets/dashboard_lg_remote.js")
        css = self.read("frontend/assets/dashboard_lg_remote.css")
        self.assertIn("__dashboardLgRemoteInstalled", js)
        self.assertIn(".lg-remote-panel", css)
        self.assertIn(".lg-remote-grid", css)
        self.assertIn(".lg-remote-key", css)
        self.assertNotIn(".remote button", css)
        for command in ("power_on", "power_off", "home", "back", "up", "down", "left", "right", "ok", "volume_up", "volume_down", "mute"):
            self.assertIn(command, js)
        self.assertIn("button.onclick", js)
        self.assertNotIn("addEventListener('click'", js)

    def test_shared_chart_engine_uses_rendered_svg_geometry(self):
        js = self.read("frontend/assets/dashboard_pm25_hotfix.js")
        self.assertIn("getScreenCTM", js)
        self.assertIn("matrix.inverse", js)
        self.assertIn("firstMid", js)
        self.assertIn("lastMid", js)
        self.assertIn("overviewChart", js)
        self.assertIn("overviewPmChart", js)
        self.assertIn("airChart", js)
        self.assertIn("sampleX", js)
        self.assertIn("DASHBOARD_CHART_DEBUG", js)
        self.assertNotIn("pixel tolerance", js.lower())

    def test_topology_uses_fixed_operational_edges_and_buses(self):
        js = self.read("frontend/assets/dashboard_topology.js")
        self.assertIn("service_bus", js)
        self.assertIn("mqtt_bus", js)
        self.assertIn("primary_dependency", js)
        self.assertIn("data_source", js)
        self.assertIn("network_tunnel", js)
        self.assertIn("['tinkerboard','electricity'", js)
        self.assertIn("['tinkerboard','tapo_ir'", js)
        self.assertIn("['home_assistant','pm25'", js)
        self.assertIn("['home_assistant','tuya'", js)
        self.assertNotIn("node.data_source", js)
        self.assertIn("__dashboardTopologyInstalled", js)

    def test_asset_order_is_base_then_chart_then_remote_then_topology(self):
        html = self.read("frontend/index.html")
        base = html.index("dashboard_v3.js")
        chart = html.index("dashboard_pm25_hotfix.js")
        remote = html.index("dashboard_lg_remote.js")
        topology = html.index("dashboard_topology.js")
        self.assertLess(base, chart)
        self.assertLess(chart, remote)
        self.assertLess(remote, topology)
        self.assertIn("dashboard_lg_remote.css", html)
        self.assertIn("dashboard_topology_hotfix06.css", html)

    def test_debug_flag_defaults_false(self):
        py = self.read("backend/frontend_asset_version.py")
        self.assertIn('DASHBOARD_CHART_DEBUG", "false"', py)
        self.assertIn("CHART_DEBUG_TOKEN", py)


if __name__ == "__main__":
    unittest.main()
