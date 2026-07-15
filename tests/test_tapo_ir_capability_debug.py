import unittest
from unittest.mock import patch

from backend import tapo_ir_capability_debug as debug


class FakeModule:
    async def list_children(self):
        return []

    async def pair_device(self, child_id):
        return None


class FakeFeature:
    value = "enabled"


class FakeDevice:
    model = "H110"
    mac = "50:3D:D1:54:CC:89"
    firmware_version = "1.4.4 Build 251031 Rel.111523"
    hardware_version = "1.0"
    device_id = "h110-device"
    modules = {"childsetup": FakeModule()}
    features = {"alarm": FakeFeature()}
    child_devices = []
    sys_info = {
        "type": "SMART.TAPOHUB",
        "component_nego": {"child_quick_setup": 1, "device": 1},
    }

    async def update(self):
        return None

    async def disconnect(self):
        return None

    async def get_child_device_list(self):
        return []


class FakeIRDevice(FakeDevice):
    async def send_ir_command(self, command_name):
        return None

    async def learn_ir_command(self, timeout=15):
        return None


class TapoIRCapabilityDebugTests(unittest.TestCase):
    def test_safe_signatures_do_not_show_sensitive_parameter_names(self):
        def method(username, password="secret", command=None):
            return None

        signature = debug._safe_signature(method)
        self.assertNotIn("username", signature)
        self.assertNotIn("password", signature)
        self.assertNotIn("secret", signature)
        self.assertIn("command", signature)

    def test_no_ir_callable_reports_unsupported_with_reason(self):
        device = FakeDevice()
        modules, _ = debug._modules(device)
        features, _ = debug._features(device)
        components = debug._components(device)
        methods = [item["name"] for item in debug._public_methods(device)]
        result = debug._support(methods, modules, features, components)
        self.assertFalse(result["local_ir_supported"])
        self.assertEqual(result["confirmed_callable_methods"], [])
        self.assertIn("no callable", result["reason"].lower())

    def test_real_ir_callables_are_detected_without_invocation(self):
        device = FakeIRDevice()
        methods = debug._public_methods(device)
        names = [item["name"] for item in methods]
        result = debug._support(names, [], [], [])
        self.assertTrue(result["local_ir_supported"])
        self.assertIn("send_ir_command", result["confirmed_callable_methods"])
        self.assertIn("learn_ir_command", result["confirmed_callable_methods"])

    def test_capability_debug_is_read_only(self):
        async def fake_discover(config):
            return {
                "device": FakeDevice(),
                "method": "python_kasa_targeted",
                "library": "python-kasa",
                "error": None,
            }

        config = {
            "host": "192.168.1.37",
            "username": "user",
            "password": "hidden",
            "device_id": None,
            "model": "H110",
            "mac": "50:3D:D1:54:CC:89",
        }
        with patch.object(debug.bridge, "_configuration", return_value=config), patch.object(
            debug.bridge, "_configured", return_value=True
        ), patch.object(debug.bridge, "_discover_async", fake_discover), patch.object(
            debug, "_library_version", return_value="0.10.2"
        ):
            payload = debug.capability_debug()

        self.assertTrue(payload["online"])
        self.assertEqual(payload["protocol"], "SMART.TAPOHUB")
        self.assertFalse(payload["local_ir_support"]["local_ir_supported"])
        self.assertFalse(payload["diagnostics"]["commands_invoked"])
        self.assertNotIn("hidden", str(payload))
        self.assertEqual(payload["compatibility"]["upgrade_recommendation"], "do_not_upgrade_blindly")


if __name__ == "__main__":
    unittest.main()
