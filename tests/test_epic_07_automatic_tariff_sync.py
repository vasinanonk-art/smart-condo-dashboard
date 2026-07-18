import copy
import json
from pathlib import Path

import pytest

from backend import automatic_tariff_sync as sync
from backend import automatic_tariff_sync_runtime  # noqa: F401


def tariff(version="2", effective_date="2026-07-01", rate=4.0):
    return {
        "tariff_name": "Test tariff",
        "source": "local_candidate",
        "effective_date": effective_date,
        "version": version,
        "tiers": [
            {"up_to_kwh": 150, "rate": rate},
            {"up_to_kwh": None, "rate": rate + 1},
        ],
        "ft_rate": 0.1,
        "service_charge": 24.62,
        "vat_percent": 7.0,
        "minimum_charge": 0.0,
    }


def test_provider_interface_and_current_providers():
    assert isinstance(sync.PROVIDERS["manual"], sync.TariffProvider)
    assert isinstance(sync.PROVIDERS["local_candidate"], sync.TariffProvider)
    assert sync.PROVIDERS["mea"].remote is True
    assert sync.PROVIDERS["pea"].remote is True


def test_same_tariff_is_equal():
    active = tariff()
    assert sync.compare_version(copy.deepcopy(active), active) == 0


def test_newer_effective_date_wins_before_version():
    active = tariff(version="99", effective_date="2026-06-01")
    candidate = tariff(version="1", effective_date="2026-07-01")
    assert sync.compare_version(candidate, active) == 1


def test_newer_version_wins_when_date_matches():
    assert sync.compare_version(tariff(version="3"), tariff(version="2")) == 1


def test_older_tariff_is_ignored():
    assert sync.compare_version(tariff(version="1"), tariff(version="2")) == -1
    assert sync.compare_version(tariff(version="9", effective_date="2026-06-01"), tariff(version="1")) == -1


def test_same_version_with_changed_values_is_not_newer():
    assert sync.compare_version(tariff(rate=5.0), tariff(rate=4.0)) == -1


def test_invalid_json_is_reported_safely(tmp_path, monkeypatch):
    path = tmp_path / "tariff_candidate.json"
    path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(sync, "CANDIDATE_PATH", path)
    provider = sync.LocalCandidateTariffProvider()
    with pytest.raises(json.JSONDecodeError):
        provider.fetch_latest()


def test_invalid_tiers_are_rejected():
    provider = sync.LocalCandidateTariffProvider()
    invalid = tariff()
    invalid["tiers"] = [
        {"up_to_kwh": None, "rate": 4.0},
        {"up_to_kwh": None, "rate": 5.0},
    ]
    with pytest.raises(ValueError):
        provider.validate(invalid)


def test_missing_vat_and_ft_are_rejected():
    provider = sync.LocalCandidateTariffProvider()
    for field in ("vat_percent", "ft_rate"):
        invalid = tariff()
        invalid.pop(field)
        with pytest.raises(ValueError):
            provider.validate(invalid)


def test_notification_deduplicates_by_tariff_fingerprint():
    state = {"notifications": []}
    candidate = tariff()
    key = sync._tariff_fingerprint(candidate)
    sync._upsert_notification(state, "new_tariff", key, "New tariff available", "First")
    sync._upsert_notification(state, "new_tariff", key, "New tariff available", "Updated")
    active = [item for item in state["notifications"] if not item.get("dismissed")]
    assert len(active) == 1
    assert active[0]["detail"] == "Updated"


def test_apply_uses_atomic_settings_and_runtime_reload(monkeypatch):
    candidate = tariff()
    fingerprint = sync._tariff_fingerprint(candidate)
    state = {"tariff_sync": {"provider": "local_candidate", "candidate": candidate, "fingerprint": fingerprint}, "notifications": []}
    config = {
        "version": 1,
        "electricity": {"billing_cycle_day": 2, "timezone": "Asia/Bangkok", "tariff": tariff(version="1", effective_date="2026-06-01")},
        "dashboard": {"timezone": "Asia/Bangkok"},
        "maintenance": {"daily_hour": 3, "history_retention_days": 400, "tariff_sync_enabled": True, "tariff_sync_interval_days": 1, "tariff_provider": "local_candidate"},
    }
    saved = {}
    monkeypatch.setattr(sync.settings, "_load_maintenance", lambda: copy.deepcopy(state))
    monkeypatch.setattr(sync.settings, "_save_maintenance", lambda payload: saved.update({"state": copy.deepcopy(payload)}))
    monkeypatch.setattr(sync.settings, "load_settings", lambda: copy.deepcopy(config))
    monkeypatch.setattr(sync.settings, "save_settings", lambda payload: saved.update({"settings": copy.deepcopy(payload)}) or payload)
    monkeypatch.setattr(sync, "_audit", lambda *args, **kwargs: None)
    result = sync.tariff_apply({})
    assert result["applied"] is True
    assert result["restart_required"] is False
    assert saved["settings"]["electricity"]["tariff"]["version"] == candidate["version"]
    assert saved["state"]["tariff_sync"]["candidate"] is None


def test_reject_persists_fingerprint_and_dismisses_notification(monkeypatch):
    candidate = tariff()
    fingerprint = sync._tariff_fingerprint(candidate)
    state = {
        "tariff_sync": {"candidate": candidate, "fingerprint": fingerprint},
        "notifications": [{"id": f"new_tariff-{fingerprint}", "kind": "new_tariff", "dismissed": False}],
    }
    saved = {}
    monkeypatch.setattr(sync.settings, "_load_maintenance", lambda: copy.deepcopy(state))
    monkeypatch.setattr(sync.settings, "_save_maintenance", lambda payload: saved.update(copy.deepcopy(payload)))
    monkeypatch.setattr(sync, "_audit", lambda *args, **kwargs: None)
    result = sync.tariff_reject({})
    assert result["rejected"] is True
    assert saved["tariff_sync"]["rejected_fingerprint"] == fingerprint
    assert saved["notifications"][0]["dismissed"] is True


def test_settings_validation_preserves_provider():
    raw = copy.deepcopy(sync.settings._DEFAULTS)
    raw["maintenance"]["tariff_provider"] = "local_candidate"
    assert sync.settings.validate_settings(raw)["maintenance"]["tariff_provider"] == "local_candidate"


def test_no_new_scheduler_or_request_path_scraping():
    source = Path(sync.__file__).read_text(encoding="utf-8")
    assert "threading.Thread" not in source
    assert "urlopen" not in source
    assert "requests.get" not in source
    assert "auto_apply" in source
    assert '"auto_apply": False' in source
