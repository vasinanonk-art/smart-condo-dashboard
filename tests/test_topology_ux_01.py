from pathlib import Path

from frontend_runtime import topology_behavior


ROOT = Path(__file__).resolve().parents[1]


def test_layered_layout_has_no_node_overlap_at_supported_widths():
    result = topology_behavior()
    assert [item["width"] for item in result["responsive"]] == [1366, 1024, 768, 390]
    assert all(item["overlaps"] == 0 for item in result["responsive"])
    assert all(item["missingEdges"] == 0 for item in result["responsive"])
    assert all(item["nodeCount"] == 19 for item in result["responsive"])


def test_summary_normalizes_health_into_user_facing_counts():
    result = topology_behavior()
    assert result["summary"] == {
        "counts": {"healthy": 1, "warning": 1, "critical": 1, "unknown": 1},
        "state": "Critical",
        "className": "critical",
    }
    assert result["statusLabels"] == ["Healthy", "Attention", "Offline", "Unknown"]


def test_links_cover_all_semantic_states():
    result = topology_behavior()
    assert result["linkStates"] == ["healthy", "warning", "critical", "unknown"]
    assert result["midpoint"] == {"x": 15, "y": 10}


def test_heartbeat_is_css_only_and_limited_to_healthy_links():
    css = (ROOT / "frontend/assets/dashboard_topology.css").read_text(encoding="utf-8")
    js = (ROOT / "frontend/assets/dashboard_topology.js").read_text(encoding="utf-8")

    assert ".topology-edge.healthy{stroke:#35d07f;stroke-dasharray:4 10;animation:topology-heartbeat 1.4s linear infinite}" in css
    for state in ("warning", "critical", "unknown"):
        selector = css.split(f".topology-edge.{state}", 1)[1].split("}", 1)[0]
        assert "animation:none" in selector
    assert "@keyframes topology-heartbeat{to{stroke-dashoffset:-28}}" in css
    assert "@media(prefers-reduced-motion:reduce){.topology-edge.healthy{animation:none}}" in css
    assert "@media@keyframes" not in css
    assert css.count("{") == css.count("}")
    assert "setInterval" not in js
    assert "topology-heartbeat" not in js


def test_critical_link_keeps_static_broken_marker():
    js = (ROOT / "frontend/assets/dashboard_topology.js").read_text(encoding="utf-8")

    assert "edge.health !== 'critical'" in js
    assert "topology-link-break" in js
