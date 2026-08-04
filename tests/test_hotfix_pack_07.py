from pathlib import Path
import unittest

from frontend_runtime import topology_behavior

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack07Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_topology_uses_only_operational_edges(self):
        result = topology_behavior()
        required = set(result["required"])
        for edge in (
            "tinkerboard>dashboard:primary_dependency",
            "tinkerboard>mqtt:primary_dependency",
            "tinkerboard>electricity:primary_dependency",
            "tinkerboard>tapo_ir:primary_dependency",
            "mqtt>presence:primary_dependency",
            "mqtt>lg_tv:primary_dependency",
            "home_assistant>tuya:data_source",
            "home_assistant>pm25:data_source",
            "zerotier_home>truenas:network_tunnel",
            "truenas>home_assistant:network_tunnel",
        ):
            self.assertIn(edge, required)

    def test_presence_timestamp_fallbacks_and_epoch_safety(self):
        js = self.read("frontend/assets/dashboard_presence_ui.js")
        for field in ("last_seen", "last_seen_ts", "latest_ts", "updated_ts", "last_update", "timestamp", "ts"):
            self.assertIn(field, js)
        self.assertIn("numeric > 1e12", js)
        self.assertIn("Not available", js)
        self.assertIn("Beer", js)
        self.assertIn("Seem", js)
        self.assertIn("ICT", js)

    def test_electricity_removes_unsupported_cards(self):
        js = self.read("frontend/assets/dashboard_electricity.js")
        for removed in ("Energy Today", "Energy Month", "Frequency", "Power Factor"):
            self.assertNotIn(removed, js)
        for kept in ("Voltage", "Current", "Active Power", "Total Energy", "Runtime IP", "Poll Latency", "Advanced Diagnostics"):
            self.assertIn(kept, js)
        self.assertIn("diagnostics.runtime_ip || diagnostics.configured_ip", js)
        self.assertIn("diagnostics.poll_latency_ms ?? diagnostics.latency_ms", js)
        self.assertIn("Tuya Local", js)
        self.assertIn("Home Assistant", js)
        self.assertIn("poller_started", js)
        self.assertIn("poller_alive", js)

    def test_page_subtitles_are_specific(self):
        js = self.read("frontend/assets/dashboard_page_chrome.js")
        for subtitle in (
            "Live dependency graph", "Lighting control", "System health and services",
            "Real-time electricity monitoring", "Indoor air quality",
            "Live camera monitoring", "Presence and last-seen status", "TV and remote control",
        ):
            self.assertIn(subtitle, js)
        self.assertNotIn("Live PJ-1103 meter data from the condo", js)
        self.assertIn("pageSubtitle", js)

    def test_presence_assets_are_versioned(self):
        html = self.read("frontend/index.html")
        self.assertIn("dashboard_presence_ui.css?v=__ASSET_VERSION__", html)
        self.assertIn("dashboard_presence_ui.js?v=__ASSET_VERSION__", html)


if __name__ == "__main__":
    unittest.main()
