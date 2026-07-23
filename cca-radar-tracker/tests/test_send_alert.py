import unittest

import send_alert

EVENT = {
    "start_utc": "2026-07-22T22:15:00Z",
    "end_utc": "2026-07-22T22:30:00Z",
    "classification_label": "Major refill likely — pools may be full",
    "classification_explanation": "All storage, footprint, and duration tests passed.",
    "basin_rain_inches": 0.24,
    "direct_runoff_ft3": 22_000,
    "fill_ratio": 1.22,
    "peak_dbz": 63.0,
    "atlas14_return_period_years": 2.4,
    "decision_tests": {
        "heavy_rain_footprint_met": True,
        "minimum_wet_duration_met": True,
    },
}


class EmailAlertTests(unittest.TestCase):
    def test_new_canyon_event_is_pending(self):
        canyon = {"name": "Zero G", "last_qualifying_event": EVENT, "notification": {}}
        self.assertEqual(
            len(send_alert.pending_alerts({"canyons": {"zerog": canyon}})), 1
        )

    def test_already_emailed_event_is_not_pending(self):
        canyon = {
            "name": "Zero G",
            "last_qualifying_event": EVENT,
            "notification": {
                "last_emailed_event_start_utc": EVENT["start_utc"]
            },
        }
        self.assertFalse(
            send_alert.pending_alerts({"canyons": {"zerog": canyon}})
        )

    def test_message_contains_revised_calculation_language(self):
        canyon = {"name": "Zero G"}
        message = send_alert.alert_message(
            [(canyon, EVENT)],
            "canyoncountryadventure@gmail.com",
            "canyoncountryadventure@gmail.com",
        )
        content = message.get_content()
        self.assertIn("Zero G", message["Subject"])
        self.assertIn("Estimated NRCS direct runoff: 22,000 ft³", content)
        self.assertIn("Storage-target ratio: 1.22×", content)
        self.assertIn("Atlas 14 context", content)
        self.assertNotIn("delivered runoff", content.lower())


if __name__ == "__main__":
    unittest.main()
