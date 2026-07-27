from pathlib import Path
import unittest

from frontend_runtime import chart_behavior, topology_behavior
ROOT = Path(__file__).resolve().parents[1]


class HotfixPack06Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_lg_remote_is_scoped_and_single_bound(self):
        js = self.read("frontend/assets/dashboard_lg_remote.js")
        css = self.read("frontend/assets/dashboard_lg_remote.css")
        self.assertIn("__lgCompactRemoteInstalled", js)
        self.assertIn("dataset.lgPending", js)
        self.assertIn("querySelectorAll('[data-lg-command]').forEach", js)
        self.assertIn(".household-lg-controls", css)
        self.assertIn(".household-lg-navigation", css)
        self.assertNotIn(".remote button", css)
        for command in ("power_on", "power_off", "home", "back", "up", "down", "left", "right", "ok", "volume_up", "volume_down", "mute", "set_input", "launch_app"):
            self.assertIn(command, js)

    def test_shared_chart_engine_uses_rendered_svg_geometry(self):
        result = chart_behavior()
        self.assertEqual(result["positions"], [10, 40, 70, 100])
        self.assertEqual((result["before"], result["after"]), (0, 3))

    def test_topology_uses_fixed_operational_edges_and_buses(self):
        result = topology_behavior()
        required = set(result["required"])
        for edge in (
            "internet>cloudflare_wan:primary_dependency",
            "tinkerboard>dashboard:primary_dependency",
            "tinkerboard>mqtt:primary_dependency",
            "tinkerboard>electricity:primary_dependency",
            "tinkerboard>tapo_ir:primary_dependency",
            "mqtt>presence:primary_dependency",
            "mqtt>lg_tv:primary_dependency",
            "home_assistant>pm25:data_source",
            "home_assistant>tuya:data_source",
            "tinkerboard>zerotier_condo:network_tunnel",
            "truenas>home_assistant:network_tunnel",
        ):
            self.assertIn(edge, required)
        self.assertTrue(result["unique"])
        self.assertEqual(result["diagnosticCount"], 0)

    def test_topology_css_does_not_leak_into_tv_remote(self):
        css = self.read("frontend/assets/dashboard_topology.css")
        self.assertNotIn(".tv-", css)
        self.assertNotIn(".remote", css)
        self.assertIn("topology-edge-data_source", css)
        self.assertIn("topology-edge-network_tunnel", css)
        self.assertIn("topology-bus", css)

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

    def test_debug_flag_defaults_false(self):
        py = self.read("backend/frontend_asset_version.py")
        self.assertIn('DASHBOARD_CHART_DEBUG", "false"', py)
        self.assertIn("CHART_DEBUG_TOKEN", py)


if __name__ == "__main__":
    unittest.main()
