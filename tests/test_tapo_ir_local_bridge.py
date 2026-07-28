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
        "hw_ver": "1.0",
        "device_type": "ir_hub",
        "password": "must-not-leak",
    }
    hw_info = {"hw_ver": "1.0"}
    child_devices = []
    features = {"ir_remote": object(), "learn_command": object()}
    modules = {"device": object(), "ir": object()}

    async def update(self):
        return None

    async def disconnect(self):
        return None

    async def send_command(self):
        return None


class FakeIRChild:
    def __init__(self, identifier, alias, remote_type, **state):
        self.device_id = identifier
        self.alias = alias
        self.sys_info = {
            "device_id": identifier,
            "alias": alias,
            "category": "ir.remote",
            "type": "SMART.TAPOREMOTE",
            "remote_type": remote_type,
            "key_list": [{"private": "stored-reference"}],
            "key_sum": 1,
            "hexData": "opaque-ir-data",
            **state,
        }
        self.features = {"device_id": object(), "reboot": object(), "unpair": object()}
        self.modules = {"DeviceModule": object()}


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

    def test_identity_and_debug_are_safe(self):
        device = FakeDevice()
        identity = bridge._device_identity(device, "192.168.1.50")
        capabilities = bridge._capability_snapshot(device, identity)
        debug = bridge._safe_debug_snapshot(device, identity, capabilities)
        self.assertEqual(identity["model"], "TEST-IR")
        self.assertEqual(identity["firmware"], "1.2.3")
        self.assertEqual(identity["hardware_version"], "1.0")
        self.assertTrue(capabilities["exposes_ir"])
        self.assertNotIn("password", str(debug).lower())

    def test_missing_model_accepts_target_host(self):
        identity = {"host": "192.168.1.50", "model": None, "mac": None, "device_id": None}
        config = {"host": "192.168.1.50", "model": "Tapo H110", "mac": None, "device_id": None}
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["matched_by"], "host")

    def test_model_mismatch_is_diagnostic_not_target_rejection(self):
        identity = {"host": "192.168.1.50", "model": "H100", "mac": None, "device_id": None}
        config = {"host": "192.168.1.50", "model": "Tapo H110", "mac": None, "device_id": None}
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertIn("model", result["comparison_failed"])

    def test_mac_match_overrides_model_difference(self):
        identity = {"host": "192.168.1.50", "model": "H100", "mac": "50:3D:D1:54:CC:89", "device_id": None}
        config = {"host": "192.168.1.50", "model": "Tapo H110", "mac": "50-3D-D1-54-CC-89", "device_id": None}
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["mac_match"])
        self.assertEqual(result["matched_by"], "mac")

    def test_host_match_accepts_fully_connected_device(self):
        identity = {"host": "192.168.1.37", "model": "H110", "mac": "50:3D:D1:54:CC:89", "device_id": None}
        config = {"host": "192.168.1.37", "model": "Tapo H110", "mac": None, "device_id": None}
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["host_match"])
        self.assertTrue(result["model_match"])

    def test_firmware_only_identity_accepts_target_host(self):
        identity = {"host": "192.168.1.37", "model": None, "mac": None, "device_id": None, "firmware": "1.4.4 Build 251031"}
        config = {"host": "192.168.1.37", "model": "Tapo H110", "mac": None, "device_id": None}
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["matched_by"], "host")

    def test_tapo_h110_production_case(self):
        identity = {
            "host": "192.168.1.37",
            "model": "H110",
            "mac": "50:3D:D1:54:CC:89",
            "device_id": None,
            "firmware": "1.4.4 Build 251031",
            "hardware_version": "1.0",
        }
        config = {
            "host": "192.168.1.37",
            "model": "Tapo H110",
            "mac": "50:3D:D1:54:CC:89",
            "device_id": None,
        }
        result = bridge._identity_comparison(identity, config, targeted=True)
        self.assertTrue(result["accepted"])
        self.assertTrue(result["host_match"])
        self.assertTrue(result["mac_match"])
        self.assertTrue(result["model_match"])
        self.assertEqual(result["comparison_failed"], [])

    def test_configured_discovery_returns_real_metadata_without_actions(self):
        async def fake_discover(config):
            identity = bridge._device_identity(FakeDevice(), "192.168.1.50")
            return {
                "device": FakeDevice(),
                "method": "python_kasa_targeted",
                "library": "python-kasa",
                "error": None,
                "comparison": bridge._identity_comparison(identity, config, targeted=True),
            }

        env = {
            "TAPO_IR_HOST": "192.168.1.50",
            "TAPO_IR_USERNAME": "user@example.com",
            "TAPO_IR_PASSWORD": "not-returned",
            "TAPO_IR_DEVICE_ID": "device-123",
            "TAPO_IR_MODEL": "Tapo TEST-IR",
            "TAPO_IR_MAC": "AA:BB:CC:DD:EE:FF",
        }
        with patch.dict(os.environ, env, clear=True), patch.object(bridge, "_discover_async", fake_discover):
            payload = bridge._snapshot_uncached()
        self.assertTrue(payload["configured"])
        self.assertTrue(payload["online"])
        self.assertEqual(payload["host"], "192.168.1.50")
        self.assertEqual(payload["supported_actions"], [])
        self.assertNotIn("not-returned", str(payload))

    def test_registry_device_is_local_and_read_only(self):
        fixture = {
            "configured": True, "online": True, "health": "healthy",
            "host": "192.168.1.50", "model": "TEST-IR", "firmware": "1.2.3",
            "hardware_version": "1.0", "device_type": "ir_hub",
            "last_update": 1780000000, "capabilities": ["ir_remote"],
            "supported_actions": [], "diagnostics": {"source": "tapo_local", "latency_ms": 20.0},
        }
        with patch.object(bridge, "local_tapo_ir_status", return_value=fixture):
            device = list(bridge.local_tapo_ir_provider())[0]
        self.assertEqual(device.id, "tapo_ir:condo")
        self.assertEqual(device.actions, ())
        self.assertEqual(device.metadata["source"], "tapo_local")

    def test_existing_child_inventory_normalizes_private_metadata(self):
        device = FakeDevice()
        device.child_devices = [
            FakeIRChild("vendor-child-2", "Living Room Fan", 1, on=1, speed_level=2),
            FakeIRChild("vendor-child-1", "Living Room AC", 2, on=1, current_temp=24),
        ]

        remotes = bridge._existing_ir_remotes(device)

        assert [item["display_name"] for item in remotes] == ["Living Room Fan", "Living Room AC"]
        assert remotes[0]["child_id"] == "vendor-child-2"
        assert remotes[0]["reported_state"] == {"on": 1, "speed_level": 2}
        assert remotes[0]["stored_command_references_present"] is True
        assert remotes[0]["opaque_ir_metadata_present"] is True
        assert remotes[0]["verified_command_methods"] == []

    def test_public_existing_inventory_excludes_ids_codes_and_private_metadata(self):
        private = {
            "online": True,
            "configured": True,
            "diagnostics": {"last_error": None},
            "_existing_ir_remotes": [
                {
                    "child_id": "vendor-secret-child-id",
                    "display_name": "Configured Fan",
                    "category": "ir.remote",
                    "device_type": "SMART.TAPOREMOTE",
                    "remote_type": 1,
                    "reported_state": {"on": 1},
                    "stored_command_references_present": True,
                    "stored_command_reference_count": 4,
                    "opaque_ir_metadata_present": True,
                    "verified_command_methods": [],
                    "password": "must-not-leak",
                    "hexData": "must-not-leak-code",
                }
            ],
        }
        with patch.object(bridge, "local_tapo_ir_status", return_value=private):
            payload = bridge.existing_ir_remote_inventory()

        assert payload["bridge_online"] is True
        assert payload["authenticated"] is True
        assert payload["count"] == 1
        assert payload["control_available"] is False
        assert payload["remotes"][0]["verified_controls"] == []
        serialized = str(payload)
        for forbidden in (
            "vendor-secret-child-id", "must-not-leak", "must-not-leak-code",
            "child_id", "hexData", "password",
        ):
            assert forbidden not in serialized

    def test_debug_sanitizer_removes_h110_ir_payload_fields(self):
        sanitized = bridge._safe_value({
            "display_name": "Power",
            "hexData": "opaque-ac-state",
            "pwm": "opaque-waveform",
            "remote_id": "private-remote-reference",
            "nested": {"oemId": "private-oem-reference"},
        })

        assert sanitized == {"display_name": "Power", "nested": {}}

    def test_existing_inventory_reports_bridge_offline_and_authentication_failure(self):
        fixture = {
            "configured": True,
            "online": False,
            "diagnostics": {"last_error": "AuthenticationError"},
            "_existing_ir_remotes": [],
        }
        with patch.object(bridge, "local_tapo_ir_status", return_value=fixture):
            payload = bridge.existing_ir_remote_inventory()

        assert payload["bridge_online"] is False
        assert payload["authenticated"] is False
        assert payload["remotes"] == []
        assert payload["last_error"] == "AuthenticationError"


if __name__ == "__main__":
    unittest.main()
