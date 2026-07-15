import os
import unittest
from unittest.mock import patch

from backend import tapo_ir_local_bridge as bridge


class FakeConfig:
    host = "192.168.1.50"


class FakeDevice:
    alias = "Condo IR Hub"
    model = "TEST-IR"
    device_id = "device-123"
    mac = "AA:BB:CC:DD:EE:FF"
    config = FakeConfig()
    sys_info = {
        "sw_ver": "1.2.3",
        "device_type": "ir_hub",
    }
    features = {
        "ir_remote": object(),
        "learn_command": object(),
    }

    async def update(self):
        return None

    async def disconnect(self):
        return None

    async def send_command(self):
        return None


class TapoIRLocalBridgeTests(unittest.TestCase):
    def setUp(self):
        bridge.invalidate_cache()

    def tearDown(self):
        bridge.invalidate_cache()

    def test_unconfigured_returns_safe_unknown(self):
        with patch.dict(os.environ, {}, clear=True):
            payload = bridge._snapshot_uncached()
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["online"])
        self.assertEqual(payload["health"], "unknown")
        self.assertEqual(payload["supported_actions"], [])
        self.assertEqual(payload["diagnostics"]["source"], "tapo_local")

    def test_identity_and_capabilities_are_derived_from_device(self):
        device = FakeDevice()
        identity = bridge._device_identity(device, "192.168.1.50")
        capabilities = bridge._capability_snapshot(device, identity)
        self.assertEqual(identity["model"], "TEST-IR")
        self.assertEqual(identity["firmware"], "1.2.3")
        self.assertEqual(identity["device_type"], "ir_hub")
        self.assertTrue(capabilities["exposes_ir"])
        self.assertTrue(capabilities["local_control_supported"])
        self.assertEqual(capabilities["supported_actions"], [])

    def test_configured_discovery_returns_real_metadata_without_actions(self):
        async def fake_discover(config):
            return {
                "device": FakeDevice(),
                "method": "python_kasa_targeted",
                "library": "python-kasa",
                "error": None,
            }

        env = {
            "TAPO_IR_HOST": "192.168.1.50",
            "TAPO_IR_USERNAME": "user@example.com",
            "TAPO_IR_PASSWORD": "not-returned",
            "TAPO_IR_DEVICE_ID": "device-123",
            "TAPO_IR_MODEL": "TEST-IR",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(bridge, "_discover_async", fake_discover):
            payload = bridge._snapshot_uncached()
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["online"])
        self.assertEqual(payload["host"], "192.168.1.50")
        self.assertEqual(payload["model"], "TEST-IR")
        self.assertEqual(payload["supported_actions"], [])
        self.assertNotIn("password", str(payload).lower())

    def test_registry_device_is_local_and_read_only(self):
        fixture = {
            "configured": True,
            "online": True,
            "health": "healthy",
            "host": "192.168.1.50",
            "model": "TEST-IR",
            "firmware": "1.2.3",
            "device_type": "ir_hub",
            "last_update": 1780000000,
            "capabilities": ["ir_remote"],
            "supported_actions": [],
            "diagnostics": {"source": "tapo_local", "latency_ms": 20.0},
        }
        with patch.object(bridge, "local_tapo_ir_status", return_value=fixture):
            device = list(bridge.local_tapo_ir_provider())[0]
        self.assertEqual(device.id, "tapo_ir:condo")
        self.assertEqual(device.type, "tapo_ir")
        self.assertEqual(device.room, "condo")
        self.assertEqual(device.actions, ())
        self.assertEqual(device.metadata["source"], "tapo_local")


if __name__ == "__main__":
    unittest.main()
