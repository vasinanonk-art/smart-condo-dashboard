import unittest

from backend import pj1103_electricity_bridge as bridge


class PJ1103ElectricityBridgeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
