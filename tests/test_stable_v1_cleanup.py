import threading
import time
from pathlib import Path

import sonoff_client
from backend.version import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_presence_startup_initialization_runs_once_under_concurrency(monkeypatch):
    calls = []
    monkeypatch.setattr(sonoff_client, "_presence_initialization_complete", False)

    def initialize(label):
        time.sleep(0.01)
        calls.append(label)

    monkeypatch.setattr(sonoff_client, "_initialize_presence_state", initialize)
    workers = [
        threading.Thread(target=sonoff_client._schedule_presence_initialization)
        for _ in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()

    assert calls == ["startup"]


def test_electricity_startup_reuses_dashboard_refresh():
    source = (ROOT / "frontend/assets/dashboard_electricity.js").read_text()
    assert "document.readyState === 'loading'" in source
    assert "const initialData" in source


def test_settings_polling_only_runs_while_settings_page_is_open():
    settings_source = (
        ROOT / "frontend/assets/dashboard_electricity_settings_hotfix17.js"
    ).read_text()
    navigation_source = (ROOT / "frontend/assets/dashboard_v3.js").read_text()

    assert "function activate()" in settings_source
    assert "function deactivate()" in settings_source
    assert "state.active=true" in settings_source
    assert "state.active=false" in settings_source
    assert "stopPolling();" in settings_source
    assert "settings?.activate?.()" in navigation_source
    assert "settings?.deactivate?.()" in navigation_source


def test_temporary_hotfix_object_diagnostics_are_not_printed():
    for relative in (
        "backend/mea_tariff_hotfix19_runtime.py",
        "backend/mea_tariff_hotfix19_debug_runtime.py",
        "backend/mea_tariff_hotfix19_state_runtime.py",
    ):
        assert "HOTFIX19.2 debug object" not in (ROOT / relative).read_text()


def test_release_version_has_one_canonical_source():
    assert __version__ == "1.0.1"
