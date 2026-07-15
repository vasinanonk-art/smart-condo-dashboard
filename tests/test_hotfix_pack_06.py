from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack06Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_lg_remote_is_scoped_and_single_bound(self):
        js = self.read("frontend/assets/dashboard_lg_remote.js")
        css = self.read("frontend/assets/dashboard_lg_remote.css")
        self.assertIn("__dashboardLgRemoteInstalled", js)
        self.assertIn("dataset.lgRemoteBound", js)
        self.assertIn("host.onclick = event =>", js)
        self.assertNotIn("querySelectorAll('[data-lg-command]').forEach", js)
        self.assertIn(".lg-remote-panel .lg-remote-grid", css)
        self.assertIn(".lg-remote-panel .lg-remote-key", css)
        self.assertNotIn(".remote button", css)
        for command in ("power_on", "power_off", "home_key", "back", "up", "down", "left", "right", "ok", "volume_up", "volume_down", "mute", "hdmi1", "netflix"):
            self.assertIn(command, js)

    def test_shared_chart_engine_uses_rendered_svg_geometry(self):
        js = self.read("frontend/assets/dashboard_pm25_hotfix.js")
        self.assertIn("getScreenCTM", js)
        self.assertIn("matrix.inverse", js)
        self.assertIn("firstMid", js)
        self.assertIn("lastMid", js)
        self.assertIn("overviewChart", js)
        self.assertIn("overviewPmChart", js)
        self.assertIn("airChart", js)
        self.assertIn("pointerX=Math.max(plot.left,Math.min(plot.right", js)
        self.assertIn("sampleX=positions[index]", js)
        self.assertIn("line.setAttribute('x1',sampleX)", js)
        self.assertIn("svg.appendChild(hit)", js)
        self.assertIn("DASHBOARD_CHART_DEBUG", js)
        self.assertNotIn("pixel tolerance", js.lower())

    def test_topology_uses_fixed_operational_edges_and_buses(self):
        js = self.read("frontend/assets/dashboard_topology.js")
        for edge in (
            "['internet','cloudflare_wan','primary_dependency']",
            "['tinkerboard','dashboard','primary_dependency']",
            "['tinkerboard','mqtt','primary_dependency']",
            "['tinkerboard','electricity','primary_dependency']",
            "['tinkerboard','tapo_ir','primary_dependency']",
            "['mqtt','presence','primary_dependency']",
            "['mqtt','lg_tv','primary_dependency']",
            "['home_assistant','pm25','data_source']",
            "['home_assistant','tuya','data_source']",
            "['tinkerboard','zerotier_condo','network_tunnel']",
            "['truenas','home_assistant','network_tunnel']",
        ):
            self.assertIn(edge, js)
        self.assertIn("service_bus", js)
        self.assertIn("mqtt_bus", js)
        self.assertIn("validateGeometry", js)
        self.assertIn("edge_node_intersection", js)
        self.assertIn("group_overlap", js)
        self.assertIn("duplicate_edge", js)
        self.assertIn("orphan_dependency", js)
        routing = js[js.index("function buildRoutes"):js.index("function groupBox")]
        self.assertNotIn("node.data_source", routing)
        self.assertIn("__dashboardTopologyInstalled", js)

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
