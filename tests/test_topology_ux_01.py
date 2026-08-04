from frontend_runtime import topology_behavior


def test_layered_layout_has_no_node_overlap_at_supported_widths():
    result = topology_behavior()
    assert [item["width"] for item in result["responsive"]] == [1366, 1024, 390]
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


def test_links_have_warning_broken_and_unknown_states():
    result = topology_behavior()
    assert result["linkStates"] == ["warning", "broken", "broken"]
    assert result["midpoint"] == {"x": 15, "y": 10}
