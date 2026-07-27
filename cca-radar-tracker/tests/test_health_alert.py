import sys
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import send_alert  # noqa: E402


class HealthAlertTests(unittest.TestCase):
    def test_stale_missing_frame_is_pending(self):
        status = {
            "stale_missing_archive_frames_utc": ["2026-07-26T23:05:00Z"],
            "health_notification": {},
        }
        self.assertEqual(
            send_alert.pending_health_alert(status),
            ["2026-07-26T23:05:00Z"],
        )

    def test_health_alert_respects_cooldown(self):
        now = send_alert.datetime.now(send_alert.UTC)
        status = {
            "stale_missing_archive_frames_utc": ["2026-07-26T23:05:00Z"],
            "health_notification": {
                "last_email_sent_utc": send_alert.utc_text(now - timedelta(minutes=10))
            },
        }
        self.assertEqual(send_alert.pending_health_alert(status, now), [])

    def test_health_message_lists_missing_timestamp(self):
        status = {"latest_archive_confirmed_frame_utc": "2026-07-26T23:00:00Z"}
        message = send_alert.health_alert_message(
            status,
            ["2026-07-26T23:05:00Z"],
            "from@example.com",
            "to@example.com",
        )
        self.assertIn("2026-07-26T23:05:00Z", message.get_content())


if __name__ == "__main__":
    unittest.main()
