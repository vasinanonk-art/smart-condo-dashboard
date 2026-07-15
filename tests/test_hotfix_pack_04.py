import unittest
from pathlib import Path
from unittest.mock import patch

from backend import frontend_asset_version
from backend import topology_hotfix


class HotfixPack04Tests(unittest.TestCase):
    def test_malformed_optional_nodes_do_not_break_topology(self):
        base = {
            node_id: {"health": "healthy", "online": True, "diagnostics": {"source": "test"}}
            for node_id in topology_hotfix.topology_runtime.NODE_ORDER
        }
        base["electricity"] = None
        base["pm25"] = {"health": None, "online": None, "metadata": None, "diagnostics": None}
        base["camera"] = {"health": "unexpected", "online": "yes"}

        with patch.object(topology_hotfix.topology_runtime, "_base_nodes", return_value=base), patch.object(
            topology_hotfix, "_enrich_electricity", side_effect=lambda nodes, errors: errors.append({"provider": "electricity", "error": "RuntimeError"})
        ), patch.object(topology_hotfix, "_enrich_tapo", return_value=None), patch.object(
            topology_hotfix.topology_runtime, "_capture_events", return_value=None
        ), patch.object(topology_hotfix.topology_runtime, "_apply_dependency_health", return_value=[]), patch.object(
            topology_hotfix.topology_runtime, "_overall_health", return_value=88
        ), patch.object(topology_hotfix.topology_runtime, "_tv_payload", return_value={"health": "unknown", "online": None}):
            payload = topology_hotfix.topology_response()

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["overall_health"], 88)
        self.assertGreater(len(payload["nodes"]), 0)
        electricity = next(node for node in payload["nodes"] if node["id"] == "electricity")
        self.assertEqual(electricity["health"], "unknown")
        self.assertEqual(electricity["dependencies"], ["tinkerboard"])
        self.assertIn({"provider": "electricity", "error": "RuntimeError"}, payload["topology_provider_errors"])

    def test_unknown_dependency_is_removed_safely(self):
        order = topology_hotfix._dedupe_order()
        with patch.dict(topology_hotfix.topology_runtime.DEPENDENCIES, {"pm25": ["missing_node"]}, clear=False):
            dependents = topology_hotfix._safe_dependents(order)
        self.assertNotIn("pm25", dependents.get("missing_node", []))

    def test_asset_version_is_stable_and_html_tokens_are_replaced(self):
        first = frontend_asset_version.build_version()
        second = frontend_asset_version.build_version()
        self.assertEqual(first, second)
        response = frontend_asset_version.render_html("index.html")
        body = bytes(response.body).decode("utf-8")
        self.assertNotIn("__ASSET_VERSION__", body)
        self.assertIn("?v=", body)
        self.assertIn("dashboard_pm25_hotfix.js", body)

    def test_pm25_hotfix_contains_edge_clamping_and_nearest_sample(self):
        source = Path("frontend/assets/dashboard_pm25_hotfix.js").read_text(encoding="utf-8")
        self.assertIn("Math.round(ratio * (valid.length - 1))", source)
        self.assertIn("Math.max(8, Math.min(rect.width - tipWidth - 8, left))", source)
        self.assertIn("line.setAttribute('y1'", source)
        self.assertIn("hideInteraction(id)", source)


if __name__ == "__main__":
    unittest.main()
