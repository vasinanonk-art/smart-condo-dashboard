import threading
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import electricity_history


class Perf02ElectricitySummaryTests(unittest.TestCase):
    def setUp(self):
        electricity_history._invalidate_summary_cache()

    def tearDown(self):
        electricity_history._invalidate_summary_cache()

    @staticmethod
    def _periods():
        return {
            "today": 1.25,
            "yesterday": 2.5,
            "month": 30.0,
            "last_month": 28.0,
        }

    def test_overlapping_requests_calculate_once(self):
        entered = threading.Event()
        release = threading.Event()
        calls = 0

        def calculate(_now):
            nonlocal calls
            calls += 1
            entered.set()
            self.assertTrue(release.wait(timeout=2))
            return self._periods()

        results = []
        with patch.object(electricity_history, "_calculate_period_summary", side_effect=calculate):
            first = threading.Thread(target=lambda: results.append(electricity_history.usage_summary(100)))
            second = threading.Thread(target=lambda: results.append(electricity_history.usage_summary(200)))
            first.start()
            self.assertTrue(entered.wait(timeout=1))
            second.start()
            release.set()
            first.join(timeout=2)
            second.join(timeout=2)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(calls, 1)
        self.assertEqual({item["current_power_w"] for item in results}, {100.0, 200.0})

    def test_cache_expires_after_ttl(self):
        with patch.object(
            electricity_history,
            "_calculate_period_summary",
            side_effect=[self._periods(), self._periods()],
        ) as calculate:
            electricity_history.usage_summary()
            electricity_history.usage_summary()
            with electricity_history._summary_condition:
                electricity_history._summary_cache_at -= electricity_history.SUMMARY_CACHE_TTL_SEC + 1
            electricity_history.usage_summary()

        self.assertEqual(calculate.call_count, 2)

    def test_failed_calculation_is_not_cached_and_releases_single_flight(self):
        with patch.object(
            electricity_history,
            "_calculate_period_summary",
            side_effect=[RuntimeError("temporary"), self._periods()],
        ) as calculate:
            with self.assertRaises(RuntimeError):
                electricity_history.usage_summary()
            payload = electricity_history.usage_summary()

        self.assertEqual(calculate.call_count, 2)
        self.assertEqual(payload["today_kwh"], 1.25)

    def test_history_write_invalidates_cached_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "electricity.jsonl"
            with patch.object(electricity_history, "HISTORY_PATH", path), patch.object(
                electricity_history,
                "_calculate_period_summary",
                side_effect=[self._periods(), self._periods()],
            ) as calculate:
                electricity_history.usage_summary()
                electricity_history.usage_summary()
                self.assertTrue(electricity_history.append_success({
                    "online": True,
                    "ts": 1780000000,
                    "power": 100.0,
                    "total_energy": 10.0,
                }))
                electricity_history.usage_summary()

        self.assertEqual(calculate.call_count, 2)

    def test_cached_values_match_uncached_output_contract(self):
        periods = self._periods()
        with patch.object(electricity_history, "_calculate_period_summary", return_value=periods):
            first = electricity_history.usage_summary(321.0)
            second = electricity_history.usage_summary(654.0)

        for field, value in (
            ("today_kwh", periods["today"]),
            ("yesterday_kwh", periods["yesterday"]),
            ("month_kwh", periods["month"]),
            ("last_month_kwh", periods["last_month"]),
        ):
            self.assertEqual(first[field], value)
            self.assertEqual(second[field], value)
        self.assertEqual(first["current_power_w"], 321.0)
        self.assertEqual(second["current_power_w"], 654.0)


if __name__ == "__main__":
    unittest.main()
