from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_page_chrome_isolated_per_page():
    source = read("frontend/assets/dashboard_page_chrome.js")
    for page in ("topology", "lighting", "system", "electricity", "climate", "camera"):
        assert f"{page}:" in source
    assert "pageSubtitle" in read("frontend/index.html")
    assert "dashboard_page_chrome.js" in read("frontend/index.html")


def test_electricity_has_no_duplicate_page_heading_and_has_fallbacks():
    source = read("frontend/assets/dashboard_electricity.js")
    assert "electricity-page-head" not in source
    assert "d.runtime_ip || d.configured_ip" in source
    assert "d.poll_latency_ms ?? d.latency_ms" in source
    assert "Unavailable from current meter" in source
    assert "Available only when supported by source meter." in source
    assert "source !== 'tuya_local'" in source


def test_topology_renderer_has_safe_normalization_and_visible_diagnostics():
    source = read("frontend/assets/dashboard_topology.js")
    assert "normalizeNode" in source
    assert "normalizeTopology" in source
    assert "console.error('Topology refresh failed'" in source
    assert "console.error('Topology render failed'" in source
    assert "fitToView" in source
    assert "groupBounds" in source
    assert "orthogonalPath" in source


def test_topology_backend_enrichment_is_type_safe():
    source = read("backend/topology_location_model.py")
    assert "def _safe_mapping" in source
    assert "if not isinstance(nodes, list)" in source
    assert "if not isinstance(raw_node, dict)" in source
    assert 'diagnostics.get("runtime_ip") or diagnostics.get("configured_ip")' in source
