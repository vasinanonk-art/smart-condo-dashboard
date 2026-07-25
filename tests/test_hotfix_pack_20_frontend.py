from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "frontend" / "assets" / "dashboard_mea_tariff.js").read_text(encoding="utf-8")
STABLE_UI = (
    ROOT / "frontend" / "assets" / "dashboard_electricity_settings_hotfix17.js"
).read_text(encoding="utf-8")


def test_outdated_dataset_copy_and_health_labels_are_present():
    for text in (
        "System Health",
        "Healthy",
        "Official Dataset",
        "Waiting for Official Update",
        "Official MEA has not yet published a newer dataset.",
        "Latest official FT:",
        "Latest official period:",
        "The dashboard is operating normally.",
        "Waiting for the next official MEA dataset.",
        "Waiting for official dataset update",
    ):
        assert text in UI


def test_stable_settings_panel_does_not_show_outdated_data_as_degraded():
    assert "official_dataset_outdated" in STABLE_UI
    assert "Waiting for official dataset update" in STABLE_UI
    assert "provider_available===false&&!waiting" in STABLE_UI
