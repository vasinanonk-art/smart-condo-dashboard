from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HotfixPack07Tests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_topology_uses_only_operational_edges(self):
        js = self.read("frontend/assets/dashboard_topology.js")
        for edge in (
            "['tinkerboard','dashboard'", "['tinkerboard','mqtt'",
            "['tinkerboard','electricity'", "['tinkerboard','tapo_ir'",
            "['mqtt','presence'", "['mqtt','lg_tv'",
            "['home_assistant','tuya'", "['home_assistant','pm25'",
            "['zerotier_home','truenas'", "['truenas','home_assistant'",
        ):
            self.assertIn(edge, js)
        self.assertIn("branch('tinkerboard',['dashboard','mqtt']", js)
        self.assertIn("branch('tinkerboard',['sonoff','camera','electricity','tapo_ir']", js)
        self.assertIn("branch('mqtt',['presence','lg_tv']", js)
        self.assertNotIn("node.data_source", js)
        self.assertIn("network_tunnel", js)
        self.assertIn("data_source", js)

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
        for removed in ("Energy Today", "Energy Month", "Frequency", "Power Factor", "Unavailable from current meter"):
            self.assertNotIn(removed, js)
        for kept in ("Voltage", "Current", "Active Power", "Total Energy", "Runtime IP", "Poll Latency", "Advanced Diagnostics"):
            self.assertIn(kept, js)
        self.assertIn("d.runtime_ip||d.configured_ip", js)
        self.assertIn("d.poll_latency_ms??d.latency_ms", js)
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
