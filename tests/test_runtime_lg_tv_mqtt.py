from types import SimpleNamespace

from backend import app as app_module
from backend import runtime_lg_tv_mqtt as runtime


def _clear_tv_state():
    for key in (
        "lg_tv_last_state",
        "lg_tv_last_state_ts",
        "lg_tv_last_full_state_ts",
        "lg_tv_last_heartbeat_ts",
        "lg_tv_bridge_online",
    ):
        app_module.state.pop(key, None)


def test_heartbeat_updates_bridge_only_and_preserves_full_state():
    _clear_tv_state()
    app_module.state["lg_tv_last_state"] = {
        "power": "on",
        "app": "YouTube",
        "input": "HDMI 1",
        "volume": 18,
        "mute": False,
    }
    app_module.state["lg_tv_last_full_state_ts"] = 100

    handled = runtime.ingest_lg_tv_payload(
        {"status": "online", "heartbeat": True, "ts": 1783873071},
        runtime.MQTT_HEARTBEAT_TOPIC,
        now=200,
    )

    assert handled is True
    assert app_module.state["lg_tv_last_heartbeat_ts"] == 200
    assert app_module.state["lg_tv_bridge_online"] is True
    assert app_module.state["lg_tv_last_full_state_ts"] == 100
    assert app_module.state["lg_tv_last_state"]["app"] == "YouTube"
    assert app_module.state["lg_tv_last_state"]["volume"] == 18


def test_full_state_merges_without_erasing_missing_values():
    _clear_tv_state()
    app_module.state["lg_tv_last_state"] = {
        "power": "on",
        "app": "Netflix",
        "input": "HDMI 2",
        "volume": 11,
        "mute": False,
    }

    runtime.ingest_lg_tv_payload(
        {"power": "on", "volume": 22},
        runtime.MQTT_STATE_TOPIC,
        now=300,
    )

    assert app_module.state["lg_tv_last_state"] == {
        "power": "on",
        "app": "Netflix",
        "input": "HDMI 2",
        "volume": 22,
        "mute": False,
    }
    assert app_module.state["lg_tv_last_state_ts"] == 300
    assert app_module.state["lg_tv_last_full_state_ts"] == 300
    assert app_module.state["lg_tv_last_heartbeat_ts"] == 300


def test_primary_callback_does_not_forward_lg_tv_message(monkeypatch):
    _clear_tv_state()
    calls = []

    def previous_message(*args):
        calls.append(args)

    monkeypatch.setattr(app_module.mqttc, "on_message", previous_message)
    monkeypatch.setattr(app_module.mqttc, "on_connect", lambda *args: None)
    monkeypatch.setattr(app_module, "_primary_lg_tv_mqtt_callback_installed", False, raising=False)

    runtime.install_primary_lg_tv_callbacks()
    msg = SimpleNamespace(
        topic=runtime.MQTT_HEARTBEAT_TOPIC,
        payload=b'{"status":"online","heartbeat":true,"ts":1783873071}',
    )
    app_module.mqttc.on_message(None, None, msg)

    assert calls == []
    assert app_module.state["lg_tv_bridge_online"] is True
    assert app_module.state["lg_tv_last_heartbeat_ts"] is not None
