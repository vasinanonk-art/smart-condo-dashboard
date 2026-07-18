"""HOTFIX PACK 13 scheduler regression checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOTFIX = ROOT / "backend" / "automation_trigger_hotfix13.py"
ENTRY = ROOT / "backend" / "app_entry.py"


def _text():
    return HOTFIX.read_text(encoding="utf-8")


def test_hotfix_load_order():
    text = ENTRY.read_text(encoding="utf-8")
    assert text.index("automation_trigger_engine") < text.index("automation_trigger_hotfix13")


def test_event_wait_replaces_fixed_tick():
    text = _text()
    assert "_STOP.wait(timeout)" in text
    assert "WORKER_INTERVAL_SEC" not in text
    assert "timeout = max(1.0" in text


def test_automation_cache_uses_mtime():
    text = _text()
    assert "st_mtime_ns" in text
    assert "st_size" in text
    assert "automation_reload_count" in text


def test_worker_avoids_refreshing_provider_context():
    text = _text()
    assert "core.build_automation_context()" not in text
    assert "electricity_status()" not in text
    assert "urlopen" not in text
    assert "bridge.local_state()" in text


def test_due_scheduling_uses_interval_and_minute_boundary():
    text = _text()
    assert "_interval_due_ts" in text
    assert "_next_minute_epoch" in text
    assert "scheduler_minute_key" in text
    assert "next_wake_ts" in text


def test_state_namespaces_use_change_signatures():
    text = _text()
    assert "_LAST_SNAPSHOT_SIGNATURES" in text
    assert "_context_signature" in text
    for namespace in ("electricity", "presence", "pm25", "temperature", "system"):
        assert namespace in text


def test_runtime_diagnostics_present():
    text = _text()
    for field in (
        "cycle_count", "last_cycle_duration_ms", "max_cycle_duration_ms",
        "context_build_count", "automation_reload_count", "idle_wake_count",
        "due_rule_count", "average_cycle_duration_ms", "next_wake_ts",
    ):
        assert field in text
    assert '"execution_enabled": False' in text


def test_slow_cycle_backoff_and_rate_limit():
    text = _text()
    assert "SLOW_CYCLE_MS = 500.0" in text
    assert "WARNING_RATE_LIMIT_SEC" in text
    assert "SLOW_BACKOFF_SEC" in text
    assert "AUTOMATION_TRIGGER_SLOW_CYCLE" in text


def test_no_catchup_busy_loop():
    text = _text()
    assert "next_wake = current_wall + 1.0" in text
    assert "wait(0" not in text


def test_hundred_rule_benchmark_shape():
    text = _text()
    assert "for automation in due:" in text
    assert "for automation in core._load_store()" not in text
    assert "core.build_automation_context()" not in text
