import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import app_clock


class BeijingClockTests(unittest.TestCase):
    def test_utc_server_wall_time_becomes_beijing_time(self):
        with patch.object(app_clock, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 11, 13, 49, tzinfo=timezone.utc).astimezone(app_clock.BEIJING_TIMEZONE)
            result = app_clock.now_beijing()
        clock.now.assert_called_once_with(app_clock.BEIJING_TIMEZONE)
        self.assertEqual(result.strftime("%Y-%m-%d %H:%M"), "2026-10-11 21:49")
        self.assertIsNone(result.tzinfo)

    def test_utc_evening_rolls_into_next_beijing_date(self):
        with patch.object(app_clock, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 11, 20, 5, tzinfo=timezone.utc).astimezone(app_clock.BEIJING_TIMEZONE)
            result = app_clock.now_beijing()
        self.assertEqual(result.strftime("%Y-%m-%d %H:%M"), "2026-10-12 04:05")


if __name__ == "__main__":
    unittest.main()
