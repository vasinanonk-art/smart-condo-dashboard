import unittest

from backend.device_framework import UnifiedDevice
from backend.device_registry import DeviceRegistry


class UnifiedDeviceTests(unittest.TestCase):
    def test_defaults_and_secret_filtering(self):
        device = UnifiedDevice(
            id="demo",
            type="sonoff",
            name="Demo",
            online=True,
            capabilities=("power", "power"),
            diagnostics={"source": "test", "api_token": "must-not-leak"},
            metadata={"local_key": "must-not-leak", "model": "x"},
        )
        payload = device.to_dict()
        self.assertEqual(payload["health"], "healthy")
        self.assertEqual(payload["capabilities"], ["power"])
        self.assertNotIn("api_token", payload["diagnostics"])
        self.assertNotIn("local_key", payload["metadata"])
        self.assertEqual(payload["metadata"]["model"], "x")

    def test_registry_provider_failure_is_isolated(self):
        registry = DeviceRegistry()
        registry.register_provider(
            "ok",
            lambda: [UnifiedDevice(id="one", type="pm25", name="One")],
        )
        registry.register_provider("broken", lambda: 1 / 0)
        devices = registry.snapshot()
        self.assertEqual([item.id for item in devices], ["one"])
        self.assertEqual(registry.provider_errors(), {"broken": "ZeroDivisionError"})

    def test_registry_filters(self):
        registry = DeviceRegistry()
        registry.register(UnifiedDevice(id="a", type="camera", name="A", room="living_room"))
        registry.register(UnifiedDevice(id="b", type="pm25", name="B", room="bedroom"))
        self.assertEqual([item.id for item in registry.snapshot(device_type="camera")], ["a"])
        self.assertEqual([item.id for item in registry.snapshot(room="bedroom")], ["b"])


if __name__ == "__main__":
    unittest.main()
