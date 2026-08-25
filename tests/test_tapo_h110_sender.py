import asyncio
import base64
from types import SimpleNamespace

import pytest

from backend import tapo_h110_sender as sender


CONFIG = {
    "host": "192.0.2.10",
    "username": "fixture@example.test",
    "password": "fixture-password",
    "device_id": "fixture-h110-id",
    "mac": "50:3D:D1:00:00:01",
    "model": "H110",
}


def _encoded(label):
    return base64.b64encode(label.encode()).decode()


class FakeProtocol:
    def __init__(self, response=None, delay=0):
        self.response = response or {"sendIrCmdById": {}}
        self.delay = delay
        self.calls = []
        self.closed = False

    async def query(self, request, retry_count=3):
        self.calls.append((request, retry_count))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response

    async def close(self):
        self.closed = True


def _device(*, mac=CONFIG["mac"], query_protocol=None):
    child_protocol = query_protocol or FakeProtocol()
    child = SimpleNamespace(
        alias="Sound Bar",
        sys_info={
            "category": "ir.remote",
            "key_list": [
                {"name": "private-key-reference", "display_name": _encoded("power")}
            ],
        },
        protocol=child_protocol,
    )
    parent_protocol = FakeProtocol()
    parent = SimpleNamespace(
        host=CONFIG["host"],
        config=SimpleNamespace(host=CONFIG["host"]),
        model="H110",
        device_id=CONFIG["device_id"],
        mac=mac,
        sys_info={
            "model": "H110",
            "device_id": CONFIG["device_id"],
            "mac": mac,
        },
        children=[child],
        protocol=parent_protocol,
    )
    return parent, child_protocol, parent_protocol


def test_sender_builds_exact_local_rpc_without_transport_retry():
    device, child_protocol, parent_protocol = _device()

    async def connector(_config):
        return device

    client = sender.TapoH110Sender(
        CONFIG,
        {"soundbar_power": {"remote_name": "Sound Bar", "key_label": "power"}},
        connector,
    )

    client("soundbar_power", 0.5)

    assert child_protocol.calls == [
        (
            {
                "multipleRequest": {
                    "requests": [
                        {
                            "method": "sendIrCmdById",
                            "params": {"name": "private-key-reference"},
                        }
                    ]
                }
            },
            0,
        )
    ]
    assert client.last_outbound_attempts == 1
    assert parent_protocol.closed is True


def test_sender_rejects_identity_mismatch_before_command():
    device, child_protocol, parent_protocol = _device(mac="00:00:00:00:00:00")

    async def connector(_config):
        return device

    client = sender.TapoH110Sender(
        CONFIG,
        {"soundbar_power": {"remote_name": "Sound Bar", "key_label": "power"}},
        connector,
    )

    with pytest.raises(sender.TapoH110SenderError, match="identity_mismatch"):
        client("soundbar_power", 0.5)

    assert child_protocol.calls == []
    assert client.last_outbound_attempts == 0
    assert parent_protocol.closed is True


def test_sender_accepts_strong_mac_identity_when_optional_device_id_is_empty():
    device, child_protocol, parent_protocol = _device()

    async def connector(_config):
        return device

    config = {**CONFIG, "device_id": ""}
    client = sender.TapoH110Sender(
        config,
        {"soundbar_power": {"remote_name": "Sound Bar", "key_label": "power"}},
        connector,
    )

    client("soundbar_power", 0.5)

    assert len(child_protocol.calls) == 1
    assert parent_protocol.closed is True


def test_sender_timeout_has_one_outbound_attempt_and_closes():
    device, child_protocol, parent_protocol = _device(
        query_protocol=FakeProtocol(delay=0.3)
    )

    async def connector(_config):
        return device

    client = sender.TapoH110Sender(
        CONFIG,
        {"soundbar_power": {"remote_name": "Sound Bar", "key_label": "power"}},
        connector,
    )

    with pytest.raises(TimeoutError):
        client("soundbar_power", 0.2)

    assert len(child_protocol.calls) == 1
    assert child_protocol.calls[0][1] == 0
    assert client.last_outbound_attempts == 1
    assert parent_protocol.closed is True


def test_mapping_rejects_duplicate_physical_selector(tmp_path):
    path = tmp_path / "commands.json"
    path.write_text(
        '{"schema_version":1,"commands":{'
        '"first":{"remote_name":"Sound Bar","key_label":"Power"},'
        '"second":{"remote_name":"sound bar","key_label":"power"}'
        '}}',
        encoding="utf-8",
    )

    with pytest.raises(sender.TapoH110SenderError, match="duplicate_selector"):
        sender._load_mapping(path)


def test_checked_in_mapping_and_profiles_are_guarded(monkeypatch):
    monkeypatch.delenv("TAPO_IR_SENDER_ENABLED", raising=False)

    commands = sender._load_mapping()
    status = sender.sender_status()

    assert set(commands) == {
        "soundbar_power", "soundbar_volume_up", "soundbar_volume_down",
        "soundbar_mute", "soundbar_woofer_up", "soundbar_woofer_down",
        "soundbar_sound_mode", "soundbar_setting", "soundbar_source",
        "fan_power", "fan_swing", "fan_mode",
    }
    assert status["ready"] is False
    assert sender._enabled() is False
