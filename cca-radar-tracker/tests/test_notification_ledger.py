import unittest

import send_alert
from tools import notification_ledger

EVENT_START = "2026-11-14T06:40:00Z"
MARKER = {
    "last_emailed_event_start_utc": EVENT_START,
    "last_email_sent_utc": "2026-11-14T15:10:55Z",
    "last_emailed_alert_tier": 2,
    "last_emailed_alert_label": "Major refill likely",
}
LEDGER = {
    "schema_version": 1,
    "canyons": {"yankee-doodle": MARKER},
    "health_notification": {},
}


def status_for_event(classification="likely_full", start=EVENT_START):
    return {
        "canyons": {
            "yankee-doodle": {
                "name": "Yankee Doodle",
                "notification": {},
                "last_rain_event": {
                    "start_utc": start,
                    "end_utc": "2026-11-14T14:50:00Z",
                    "classification": classification,
                    "fill_ratio": 1.85,
                },
            }
        },
        "health_notification": {},
    }


class DurableNotificationLedgerTests(unittest.TestCase):
    def test_restore_prevents_repeat_of_same_storm_on_stale_pages_state(self):
        status = status_for_event()
        self.assertEqual(len(send_alert.pending_alerts(status)), 1)
        self.assertTrue(notification_ledger.restore_notifications(status, LEDGER))
        self.assertFalse(send_alert.pending_alerts(status))
        self.assertEqual(
            status["canyons"]["yankee-doodle"]["notification"],
            MARKER,
        )

    def test_new_event_after_prior_email_remains_alertable(self):
        status = status_for_event(start="2026-11-15T01:00:00Z")
        notification_ledger.restore_notifications(status, LEDGER)
        self.assertEqual(len(send_alert.pending_alerts(status)), 1)

    def test_same_event_can_escalate_to_full_flush(self):
        status = status_for_event(classification="full_flush")
        notification_ledger.restore_notifications(status, LEDGER)
        alerts = send_alert.pending_alerts(status)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][2], send_alert.ALERT_TIER_FULL_FLUSH)

    def test_old_ledger_does_not_replace_newer_sent_marker(self):
        status = status_for_event()
        newer = {**MARKER, "last_email_sent_utc": "2026-11-14T16:10:00Z"}
        status["canyons"]["yankee-doodle"]["notification"] = newer
        self.assertFalse(notification_ledger.restore_notifications(status, LEDGER))
        self.assertEqual(
            status["canyons"]["yankee-doodle"]["notification"], newer
        )

    def test_persist_keeps_latest_marker_and_health_cooldown(self):
        status = status_for_event()
        status["canyons"]["yankee-doodle"]["notification"] = {
            **MARKER, "last_email_sent_utc": "2026-11-14T16:10:00Z"
        }
        status["health_notification"] = {
            "last_email_sent_utc": "2026-11-14T16:10:00Z",
            "last_missing_signature": "test-missing-frame",
        }
        persisted = notification_ledger.extract_notifications(status, LEDGER)
        self.assertEqual(
            persisted["canyons"]["yankee-doodle"]["last_email_sent_utc"],
            "2026-11-14T16:10:00Z",
        )
        self.assertEqual(
            persisted["health_notification"]["last_missing_signature"],
            "test-missing-frame",
        )
        self.assertFalse(notification_ledger.restore_notifications(status, persisted))

    def test_invalid_or_missing_ledger_cannot_silently_trigger_duplicates(self):
        with self.assertRaises(ValueError):
            notification_ledger.restore_notifications(
                status_for_event(), {"canyons": {}}
            )


if __name__ == "__main__":
    unittest.main()
