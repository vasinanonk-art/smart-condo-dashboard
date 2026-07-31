import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend import electricity_history
from backend import topology_hotfix


class Perf01ElectricityHistoryTests(unittest.TestCase):
    def setUp(self):
        electricity_history._invalidate_summary_cache()
        self.now = int(datetime(2026, 7, 31, 12, 0, tzinfo=electricity_history.BANGKOK).timestamp())

    def tearDown(self):
        electricity_history._invalidate_summary_cache()

    def _sample(self, ts, total):
        return {
            "ts": ts,
            "voltage": 230.0,
            "current": 1.0,
            "power": 1000.0,
            "total_energy": total,
            "source": "test",
            "health": "healthy",
        }

    def test_usage_summary_reads_history_once_for_all_periods(self):
        rows = [
            self._sample(self.now - 40 * 86400, 1.0),
            self._sample(self.now - 35 * 86400, 2.0),
            self._sample(self.now - 86400, 3.0),
            self._sample(self.now - 86340, 3.1),
            self._sample(self.now - 60, 4.0),
            self._sample(self.now, 4.1),
        ]
        with patch.object(electricity_history.time, "time", return_value=self.now), patch.object(
            electricity_history, "read_samples", return_value=rows
        ) as read_samples:
            payload = electricity_history.usage_summary(250.0)

        self.assertEqual(read_samples.call_count, 1)
        self.assertEqual(payload["today_kwh"], 0.1)
        self.assertEqual(payload["yesterday_kwh"], 0.1)
        self.assertEqual(payload["month_kwh"], 1.1)
        self.assertEqual(payload["last_month_kwh"], 1.0)

    def test_usage_summary_decodes_each_jsonl_row_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "electricity.jsonl"
            rows = [
                self._sample(self.now - 86400 + offset * 60, 10.0 + offset / 10)
                for offset in range(8)
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            original_loads = json.loads
            decode_count = 0

            def counted_loads(value):
                nonlocal decode_count
                decode_count += 1
                return original_loads(value)

            with patch.object(electricity_history, "HISTORY_PATH", path), patch.object(
                electricity_history.time, "time", return_value=self.now
            ), patch.object(electricity_history.json, "loads", side_effect=counted_loads):
                electricity_history.usage_summary()

        self.assertEqual(decode_count, len(rows))

    def test_topology_enrichment_reuses_registry_electricity_status(self):
        nodes = {
            "electricity": {
                "health": "healthy",
                "online": True,
                "devices": [{
                    "status": {"voltage": 231.5, "power": 420.0},
                    "diagnostics": {
                        "source": "tuya_local",
                        "runtime_ip": "192.0.2.10",
                    },
                }],
            },
        }
        errors = []

        with patch("backend.electricity_provider.electricity_status") as electricity_status:
            topology_hotfix._enrich_electricity(nodes, errors)

        electricity_status.assert_not_called()
        self.assertEqual(errors, [])
        self.assertEqual(nodes["electricity"]["voltage"], 231.5)
        self.assertEqual(nodes["electricity"]["power"], 420.0)
        self.assertEqual(nodes["electricity"]["data_source"], "tuya_local")


if __name__ == "__main__":
    unittest.main()
