import unittest
from unittest.mock import patch

from backend import pj1103_electricity_bridge as bridge


class PJ1103ElectricityBridgeTests(unittest.TestCase):
    def setUp(self):
        bridge._stop_event.set()
        bridge.app_module._pj1103_bridge_started = False
        bridge.app_module._pj1103_bridge_thread = None
        bridge._runtime_snapshot = {}
        bridge.app_module.state.pop("electricity_local_state", None)

    def tearDown(self):
        bridge._stop_event.set()
        bridge.app_module._pj1103_bridge_started = False
        bridge.app_module._pj1103_bridge_thread = None

    def test_provisional_dps_scaling(self):
        scaled = bridge.scale_dps({17: 129, "18": 2070, 19: 3715, "20": 2300})
        self.assertEqual(scaled["total_energy"], 1.29)
        self.assertEqual(scaled["current"], 2.07)
        self.assertEqual(scaled["power"], 371.5)
        self.assertEqual(scaled["voltage"], 230.0)
        self.assertFalse(bridge.MAPPING_VERIFIED)

    def test_discovery_uses_shared_device_and_correct_classes(self):
        payloads = bridge._discovery_payloads()
        self.assertEqual(len(payloads), 4)
        voltage = payloads["homeassistant/sensor/pj1103_voltage/config"]
        energy = payloads["homeassistant/sensor/pj1103_total_energy/config"]
        self.assertEqual(voltage["device_class"], "voltage")
        self.assertEqual(voltage["state_class"], "measurement")
        self.assertEqual(voltage["unit_of_measurement"], "V")
        self.assertEqual(energy["device_class"], "energy")
        self.assertEqual(energy["state_class"], "total_increasing")
        self.assertEqual(energy["unit_of_measurement"], "kWh")
        self.assertEqual(voltage["device"], energy["device"])
        self.assertEqual(voltage["state_topic"], "condo/electricity/state")
        self.assertEqual(voltage["availability_topic"], "condo/electricity/availability")

    def test_success_and_failure_payloads_include_lifecycle_fields(self):
        success = bridge._success_payload({
            "voltage": 234.4,
            "current": 2.22,
            "power": 381.7,
            "total_energy": 1.4,
            "poll_latency_ms": 42.5,
        })
        self.assertIsNotNone(success["last_success"])
        self.assertIsNotNone(success["last_attempt_ts"])
        self.assertEqual(success["consecutive_failures"], 0)
        self.assertIsNone(success["last_error"])

        failed = bridge._failure_payload("RuntimeError", success)
        self.assertEqual(failed["last_error"], "RuntimeError")
        self.assertIsNotNone(failed["last_attempt_ts"])
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertEqual(failed["voltage"], 234.4)

    def test_repeated_start_does_not_create_duplicate_live_worker(self):
        class FakeThread:
            created = 0

            def __init__(self, *args, **kwargs):
                FakeThread.created += 1
                self.alive = False

            def start(self):
                self.alive = True

            def is_alive(self):
                return self.alive

        with patch.object(bridge.threading, "Thread", FakeThread):
            bridge.start_pj1103_bridge()
            bridge.start_pj1103_bridge()
        self.assertEqual(FakeThread.created, 1)
        self.assertTrue(bridge.app_module._pj1103_bridge_started)
        self.assertTrue(bridge.app_module._pj1103_bridge_thread.is_alive())

    def test_dead_started_flag_is_recovered(self):
        class DeadThread:
            def is_alive(self):
                return False

        class LiveThread:
            def __init__(self, *args, **kwargs):
                self.alive = False

            def start(self):
                self.alive = True

            def is_alive(self):
                return self.alive

        bridge.app_module._pj1103_bridge_started = True
        bridge.app_module._pj1103_bridge_thread = DeadThread()
        with patch.object(bridge.threading, "Thread", LiveThread):
            bridge.start_pj1103_bridge()
        self.assertTrue(bridge.app_module._pj1103_bridge_started)
        self.assertTrue(bridge.app_module._pj1103_bridge_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
