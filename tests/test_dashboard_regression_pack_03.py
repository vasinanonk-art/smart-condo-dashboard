from pathlib import Path

from frontend_runtime import topology_behavior

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
    assert "diagnostics.runtime_ip || diagnostics.configured_ip" in source
    assert "diagnostics.poll_latency_ms ?? diagnostics.latency_ms" in source
    assert "Energy Today" not in source
    assert "Energy Month" not in source
    assert "sourceName(source)" in source


def test_topology_renderer_has_safe_normalization_and_visible_diagnostics():
    result = topology_behavior()
    assert len(result["normalized"]) == 2
    assert result["normalized"][0]["health"] == "unknown"
    assert result["normalized"][0]["dependencies"] == []
    assert result["unique"] is True
    assert result["deterministic"] is True


def test_topology_backend_enrichment_is_type_safe():
    source = read("backend/topology_location_model.py")
    assert "def _safe_mapping" in source
    assert "if not isinstance(nodes, list)" in source
    assert "if not isinstance(raw_node, dict)" in source
    assert 'diagnostics.get("runtime_ip") or diagnostics.get("configured_ip")' in source
