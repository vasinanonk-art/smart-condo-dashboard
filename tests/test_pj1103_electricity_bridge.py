import unittest
from unittest.mock import patch

from backend import pj1103_electricity_bridge as bridge


class PJ1103ElectricityBridgeTests(unittest.TestCase):
    def setUp(self):
        bridge._stop_event.set()
        bridge.app_module._pj1103_bridge_started = False
        bridge.app_module._pj1103_bridge_thread = None
        bridge._runtime_snapshot = {}
        bridge._runtime_ip = None
        bridge._scan_state.update({"last_scan_ts": None, "last_scan_result": "not_run", "scan_count": 0})
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

    def test_scan_matches_device_id_variants(self):
        variants = [
            {"192.168.1.35": {"id": "meter-id", "ip": "192.168.1.35"}},
            {"192.168.1.35": {"gwId": "meter-id"}},
            [{"device_id": "meter-id", "ip": "192.168.1.35"}],
        ]
        for result in variants:
            bridge._scan_state.update({"last_scan_ts": None, "last_scan_result": "not_run", "scan_count": 0})
            with patch.object(bridge, "_tiny_scan", return_value=result):
                self.assertEqual(bridge._discover_runtime_ip("meter-id"), "192.168.1.35")
                self.assertEqual(bridge._scan_state["last_scan_result"], "matched")

    def test_scan_cooldown_prevents_repeated_scan(self):
        with patch.object(bridge, "_tiny_scan", return_value={}) as scan:
            bridge._discover_runtime_ip("meter-id")
            bridge._discover_runtime_ip("meter-id")
        self.assertEqual(scan.call_count, 1)
        self.assertEqual(bridge._scan_state["scan_count"], 1)

    def test_wrong_ip_rediscovery_retries_immediately(self):
        environment = {
            "TUYA_METER_DEVICE_ID": "meter-id",
            "TUYA_METER_IP": "192.168.1.34",
            "TUYA_METER_LOCAL_KEY": "test-only-key",
            "TUYA_METER_VERSION": "3.5",
        }
        success = {"voltage": 234.4, "current": 2.22, "power": 381.7, "total_energy": 1.4, "poll_latency_ms": 20.0}
        with patch.dict(bridge.os.environ, environment, clear=False), \
             patch.object(bridge, "_read_once", side_effect=[TimeoutError(), success]) as read_once, \
             patch.object(bridge, "_discover_runtime_ip", return_value="192.168.1.35"), \
             patch.object(bridge, "_publish_current_state"), \
             patch.object(bridge, "publish_discovery"):
            payload = bridge.poll_once()
        snapshot = bridge.local_state()
        self.assertEqual(read_once.call_count, 2)
        self.assertEqual(read_once.call_args_list[1].args[0]["ip"], "192.168.1.35")
        self.assertEqual(payload["power"], 381.7)
        self.assertEqual(snapshot["runtime_ip"], "192.168.1.35")
        self.assertNotIn("local_key", snapshot)

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
