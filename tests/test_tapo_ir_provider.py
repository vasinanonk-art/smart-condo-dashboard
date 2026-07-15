import unittest
from unittest.mock import patch

from backend import tapo_ir_provider as provider


class TapoIRProviderTests(unittest.TestCase):
    def tearDown(self):
        provider.invalidate_cache()

    def test_discovers_tapo_remote_without_guessing_entity_id(self):
        states = [
            {
                "entity_id": "remote.living_room_hub",
                "state": "on",
                "last_updated": "2026-07-15T00:00:00+00:00",
                "attributes": {
                    "friendly_name": "Tapo IR Living Room",
                    "platform": "tapo",
                    "supported_features": 1,
                    "supported_commands": ["send_command", "learn_command"],
                },
            },
            {
                "entity_id": "remote.unrelated_remote",
                "state": "on",
                "attributes": {"friendly_name": "Other Remote"},
            },
        ]
        discovered = provider._discover(states)
        self.assertEqual([item["entity_id"] for item in discovered], ["remote.living_room_hub"])
        capabilities = provider._entity_capabilities(discovered[0])
        self.assertIn("remote.send_command", capabilities)
        self.assertIn("remote.learn_command", capabilities)
        self.assertNotIn("remote.delete_command", capabilities)

    def test_missing_entities_return_unknown_not_error(self):
        with patch.object(provider, "_ha_states", return_value=([], None, 12.5, True)):
            payload = provider._snapshot_uncached()
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["online"])
        self.assertEqual(payload["health"], "unknown")
        self.assertEqual(payload["entities"], [])
        self.assertEqual(payload["diagnostics"]["available_entity_count"], 0)

    def test_registry_device_is_read_only_and_separate_from_camera(self):
        fixture = {
            "configured": True,
            "online": True,
            "health": "healthy",
            "last_update": 1780000000,
            "entities": [{"entity_id": "remote.tapo_ir"}],
            "capabilities": ["remote.send_command"],
            "diagnostics": {"source": "home_assistant", "latency_ms": 8.0},
        }
        with patch.object(provider, "tapo_ir_status", return_value=fixture):
            device = list(provider.tapo_ir_provider())[0]
        self.assertEqual(device.type, "tapo_ir")
        self.assertEqual(device.room, "condo")
        self.assertEqual(device.actions, ())
        self.assertNotEqual(device.type, "camera")


if __name__ == "__main__":
    unittest.main()
