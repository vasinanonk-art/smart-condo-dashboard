import json
from pathlib import Path

import pytest

from backend.household_presence_automation import AWAY_CANDIDATE, HouseholdPresenceAutomation
from backend.presence_identity import DEFAULT_IDENTITIES, normalize_mac, validate_identities
from backend import presence_stabilizer
from backend import dashboard_settings


AWAY = {"home": False, "state": "away", "source": "Expired", "ip": "192.0.2.1"}


def test_default_beer_identity_replaces_old_ip():
    assert DEFAULT_IDENTITIES["beer"] == {"name": "Beer", "ip": "192.168.1.241", "mac": "E6:2C:F5:81:D1:DA"}
    assert "192.168.1.107" not in json.dumps(DEFAULT_IDENTITIES)


@pytest.mark.parametrize("ip", ["", "999.1.1.1", "example.invalid", "2001:db8::1"])
def test_invalid_ipv4_rejected(ip):
    raw = json.loads(json.dumps(DEFAULT_IDENTITIES)); raw["beer"]["ip"] = ip
    with pytest.raises(ValueError, match="invalid_presence_ipv4"):
        validate_identities(raw)


@pytest.mark.parametrize("mac", ["", "aa:bb:cc", "GG:22:33:44:55:66"])
def test_invalid_mac_rejected(mac):
    raw = json.loads(json.dumps(DEFAULT_IDENTITIES)); raw["beer"]["mac"] = mac
    with pytest.raises(ValueError, match="invalid_presence_mac"):
        validate_identities(raw)


def test_mac_normalization_and_duplicate_rejection():
    assert normalize_mac("e6-2c-f5-81-d1-da") == "E6:2C:F5:81:D1:DA"
    raw = json.loads(json.dumps(DEFAULT_IDENTITIES)); raw["seem"]["ip"] = raw["beer"]["ip"]
    with pytest.raises(ValueError, match="duplicate_presence_ip"):
        validate_identities(raw)
    raw = json.loads(json.dumps(DEFAULT_IDENTITIES)); raw["seem"]["mac"] = raw["beer"]["mac"].lower()
    with pytest.raises(ValueError, match="duplicate_presence_mac"):
        validate_identities(raw)


@pytest.mark.parametrize("prior", ["PENDING_AWAY", "CONFIRMED_AWAY"])
def test_config_change_resets_state_and_requires_fresh_dwell(tmp_path, prior):
    now = [1_700_000_000]; calls = []
    automation = HouseholdPresenceAutomation(tmp_path / "state.json", lambda: calls.append(True), clock=lambda: now[0], mode="shadow")
    automation.state["state"] = prior; automation.state["pending_since"] = now[0] - 1799; automation._save()
    reset = automation.reset_home()
    assert reset["state"] == "HOME" and reset["pending_since"] is None and reset["confirmed_away_at"] is None
    assert reset["departure_action"]["actual_command_executed"] is False and calls == []
    assert automation.evaluate({"beer": AWAY, "seem": AWAY})["state"] == "PENDING_AWAY"
    now[0] += 1799
    assert automation.evaluate({"beer": AWAY, "seem": AWAY})["state"] == "PENDING_AWAY"


def test_identity_mismatch_is_unknown_and_test_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("SMART_CONDO_DATA_DIR", str(tmp_path))
    (tmp_path / "settings.json").write_text(json.dumps({"presence": {"people": DEFAULT_IDENTITIES}}))
    monkeypatch.setattr(presence_stabilizer, "_neighbor_mac", lambda ip: "B4:55:75:3B:EF:24")
    monkeypatch.setattr(presence_stabilizer, "_ping", lambda ip: pytest.fail("ping must not run after identity mismatch"))
    result = presence_stabilizer.test_presence_identity("beer")
    assert result["classification"] == "UNKNOWN"
    assert result["reason"] == "identity_mismatch"


def test_stale_mqtt_ip_cannot_override_configured_identity(monkeypatch):
    monkeypatch.setattr(presence_stabilizer, "_now", lambda: 2_000)
    monkeypatch.setattr(presence_stabilizer, "_neighbor_mac", lambda ip: "E6:2C:F5:81:D1:DA")
    monkeypatch.setattr(presence_stabilizer, "_neighbor_state", lambda ip: "REACHABLE")
    identity = DEFAULT_IDENTITIES["beer"]
    result = presence_stabilizer.resolve_person(
        "beer",
        {"name": "Beer", "state": "away", "ip": "192.168.1.107", "ts": 1},
        identity,
    )
    assert result["ip"] == "192.168.1.241"
    assert result["classification"] == "PRESENT"
    assert result["source"] == "Router:REACHABLE"


def test_presence_probe_has_no_household_or_sonoff_execution_path():
    source = Path(presence_stabilizer.__file__).read_text()
    block = source.split("def test_presence_identity", 1)[1]
    assert "evaluate_household" not in block
    assert "all_off" not in block
    assert "sonoff" not in block.lower()


def test_identity_persists_audit_and_resets_shadow_state(monkeypatch, tmp_path):
    import sonoff_client

    settings_path = tmp_path / "settings.json"
    audit_path = tmp_path / "presence_identity_audit.jsonl"
    monkeypatch.setattr(dashboard_settings, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(dashboard_settings, "PRESENCE_AUDIT_PATH", audit_path)
    resets = []
    monkeypatch.setattr(sonoff_client, "reset_household_presence", lambda reason: resets.append(reason))
    previous = dashboard_settings.load_settings()
    previous["presence"]["people"]["beer"]["ip"] = "192.168.1.218"
    settings_path.write_text(json.dumps(previous))
    changed = json.loads(json.dumps(previous))
    changed["presence"]["people"]["beer"] = DEFAULT_IDENTITIES["beer"]
    saved = dashboard_settings.save_settings(changed)
    assert saved["presence"]["people"]["beer"]["ip"] == "192.168.1.241"
    assert dashboard_settings.load_settings()["presence"] == saved["presence"]
    assert resets == ["presence_identity_changed"]
    audit = json.loads(audit_path.read_text().splitlines()[-1])
    assert audit["person"] == "beer" and audit["old"]["ip"] == "192.168.1.218" and audit["new"]["ip"] == "192.168.1.241"
    assert audit_path.stat().st_mode & 0o777 == 0o600


def test_settings_mobile_presence_structure():
    root = Path(__file__).resolve().parents[1]
    js = (root / "frontend/assets/dashboard_settings.js").read_text()
    css = (root / "frontend/assets/dashboard_presence_settings.css").read_text()
    assert "['electricity','Electricity'],['dashboard','Dashboard'],['presence','Presence']" in js
    assert "IP Address" in js and "MAC Address" in js and "Test Presence" in js
    assert "/api/settings/presence/test/" in js
    assert "@media(max-width:640px)" in css and ".presence-settings-grid{grid-template-columns:1fr}" in css
    assert "overflow-x" not in css
