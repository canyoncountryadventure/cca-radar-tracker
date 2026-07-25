import unittest

import send_alert

BASE_EVENT = {
    "start_utc": "2026-07-22T22:15:00Z",
    "end_utc": "2026-07-22T22:30:00Z",
    "classification": "moderate",
    "classification_label": "Substantial partial refill possible",
    "classification_explanation": "Estimated watershed runoff reached 72% of storage.",
    "basin_rain_inches": 0.24,
    "direct_runoff_ft3": 22_000,
    "fill_ratio": 0.72,
    "peak_dbz": 49.0,
    "atlas14_return_period_years": 2.4,
    "decision_tests": {
        "heavy_rain_footprint_met": False,
        "minimum_wet_duration_met": True,
    },
}


class EmailAlertTests(unittest.TestCase):
    def test_substantial_partial_event_is_pending(self):
        canyon = {"name": "Leprechaun", "last_rain_event": BASE_EVENT, "notification": {}}
        alerts = send_alert.pending_alerts({"canyons": {"leprechaun": canyon}})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][2], send_alert.ALERT_TIER_SUBSTANTIAL)

    def test_event_below_half_storage_is_not_pending(self):
        event = {**BASE_EVENT, "fill_ratio": 0.49}
        canyon = {"name": "Leprechaun", "last_rain_event": event, "notification": {}}
        self.assertFalse(
            send_alert.pending_alerts({"canyons": {"leprechaun": canyon}})
        )

    def test_same_substantial_event_is_not_repeated(self):
        canyon = {
            "name": "Leprechaun",
            "last_rain_event": BASE_EVENT,
            "notification": {
                "last_emailed_event_start_utc": BASE_EVENT["start_utc"],
                "last_emailed_alert_tier": send_alert.ALERT_TIER_SUBSTANTIAL,
            },
        }
        self.assertFalse(
            send_alert.pending_alerts({"canyons": {"leprechaun": canyon}})
        )

    def test_same_event_can_escalate_to_likely_full(self):
        event = {
            **BASE_EVENT,
            "classification": "likely_full",
            "classification_label": "Major refill likely — pools may be full",
            "fill_ratio": 1.15,
        }
        canyon = {
            "name": "Leprechaun",
            "last_rain_event": event,
            "notification": {
                "last_emailed_event_start_utc": event["start_utc"],
                "last_emailed_alert_tier": send_alert.ALERT_TIER_SUBSTANTIAL,
            },
        }
        alerts = send_alert.pending_alerts({"canyons": {"leprechaun": canyon}})
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][2], send_alert.ALERT_TIER_LIKELY_FULL)

    def test_message_contains_revised_calculation_language(self):
        canyon = {"name": "Leprechaun"}
        message = send_alert.alert_message(
            [(canyon, BASE_EVENT, send_alert.ALERT_TIER_SUBSTANTIAL)],
            "canyoncountryadventure@gmail.com",
            "canyoncountryadventure@gmail.com",
        )
        content = message.get_content()
        self.assertIn("Leprechaun", message["Subject"])
        self.assertIn("Substantial partial refill", message["Subject"])
        self.assertIn("Estimated NRCS direct runoff: 22,000 ft³", content)
        self.assertIn("Storage-target ratio: 0.72×", content)
        self.assertIn("Alert threshold: Substantial partial refill", content)
        self.assertNotIn("delivered runoff", content.lower())


if __name__ == "__main__":
    unittest.main()
