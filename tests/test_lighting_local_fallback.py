from urllib.error import URLError

from backend import dashboard_extensions
from backend import runtime_ha_lighting_stable as lighting


DEVICE = {"id": "light-1", "name": "Living 1", "ip": "192.168.1.20"}


def brightness(value=500):
    return dashboard_extensions.ZoneCommand(zone="living_room", action="brightness", value=value)


def test_available_home_assistant_never_calls_local_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(lighting.base, "_entity_for_device", lambda device: "light.living_1")
    monkeypatch.setattr(
        lighting.base,
        "_ha_request",
        lambda path, method="GET", payload=None: calls.append((path, method)) or ({"state": "on", "attributes": {}} if method == "GET" else {}),
    )
    monkeypatch.setattr(lighting.runtime_fixes, "_run_bounded", lambda *args: (_ for _ in ()).throw(AssertionError("local fallback called")))

    result = lighting._command_one(DEVICE, brightness(), None)

    assert result == {"deviceid": "light-1", "ok": True, "control_source": "home_assistant"}
    assert calls[-1] == ("/api/services/light/turn_on", "POST")


def test_unavailable_entity_uses_bounded_local_fallback(monkeypatch):
    local_calls = []
    monkeypatch.setattr(lighting.base, "_entity_for_device", lambda device: "light.living_1")
    monkeypatch.setattr(lighting.base, "_ha_request", lambda *args, **kwargs: {"state": "unavailable"})
    monkeypatch.setattr(lighting.dashboard_extensions, "_supports", lambda *args: True)
    monkeypatch.setattr(lighting.runtime_fixes, "_run_bounded", lambda *args: local_calls.append(args) or {"ok": True})
    monkeypatch.setattr(lighting.runtime_fixes, "_cache_success", lambda *args: None)
    monkeypatch.setattr(lighting.runtime_fixes, "_refresh_success_async", lambda *args: None)

    result = lighting._command_one(DEVICE, brightness(300), None)

    assert result["ok"] is True
    assert result["control_source"] == "local_tuya"
    assert result["fallback_reason"] == "entity_unavailable"
    assert len(local_calls) == 1


def test_home_assistant_preflight_error_uses_local_fallback(monkeypatch):
    monkeypatch.setattr(lighting.base, "_entity_for_device", lambda device: "light.living_1")
    monkeypatch.setattr(lighting.base, "_ha_request", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")))
    monkeypatch.setattr(lighting.dashboard_extensions, "_supports", lambda *args: True)
    monkeypatch.setattr(lighting.runtime_fixes, "_run_bounded", lambda *args: {"ok": True})
    monkeypatch.setattr(lighting.runtime_fixes, "_cache_success", lambda *args: None)
    monkeypatch.setattr(lighting.runtime_fixes, "_refresh_success_async", lambda *args: None)

    result = lighting._command_one(DEVICE, brightness(), None)

    assert result["ok"] is True
    assert result["fallback_reason"] == "home_assistant_unavailable"


def test_ambiguous_home_assistant_post_failure_is_not_retried_locally(monkeypatch):
    local_calls = []
    monkeypatch.setattr(lighting.base, "_entity_for_device", lambda device: "light.living_1")

    def ha_request(path, method="GET", payload=None):
        if method == "POST":
            raise URLError("response lost")
        return {"state": "on", "attributes": {}}

    monkeypatch.setattr(lighting.base, "_ha_request", ha_request)
    monkeypatch.setattr(lighting.runtime_fixes, "_run_bounded", lambda *args: local_calls.append(args) or {"ok": True})

    result = lighting._command_one(DEVICE, brightness(), None)

    assert result["ok"] is False
    assert result["control_source"] == "home_assistant"
    assert local_calls == []


def test_zone_state_adds_local_cache_when_home_assistant_has_no_device(monkeypatch):
    monkeypatch.setattr(
        lighting.base,
        "_zones_payload",
        lambda: {"ok": True, "zones": [{"zone": "living_room", "devices": [], "support": {}, "presets": []}]},
    )
    monkeypatch.setattr(lighting.dashboard_extensions, "_zones", lambda: {"living_room": ["living_1"]})
    monkeypatch.setattr(lighting.dashboard_extensions, "_presets", lambda: ({}, "test"))
    monkeypatch.setattr(
        lighting.dashboard_extensions,
        "_zone_payload",
        lambda name, presets: {
            "zone": name,
            "devices": [{
                "deviceid": "light-1",
                "capabilities": {"brightness": True, "temperature": True, "rgb": True},
                "values": {"brightness": 500},
            }],
            "presets": [],
        },
    )

    result = lighting._zones_payload()

    zone = result["zones"][0]
    assert zone["devices"][0]["state_source"] == "local_cache"
    assert zone["support"] == {"brightness": 1, "temperature": 1, "rgb": 1}
    assert zone["state_source"] == "home_assistant_with_local_cache"
