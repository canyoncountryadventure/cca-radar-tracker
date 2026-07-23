import unittest

import send_alert


EVENT = {
    "start_utc": "2026-07-22T22:15:00Z",
    "end_utc": "2026-07-22T22:30:00Z",
    "classification_label": "High likelihood — pools likely full",
    "basin_rain_inches": 0.24,
    "estimated_runoff_ft3": 22000,
    "fill_ratio": 1.22,
    "peak_dbz": 63.0,
}


class EmailAlertTests(unittest.TestCase):
    def test_new_canyon_event_is_pending(self):
        canyon = {"name": "ZeroG", "last_qualifying_event": EVENT, "notification": {}}
        self.assertEqual(len(send_alert.pending_alerts({"canyons": {"zerog": canyon}})), 1)

    def test_already_emailed_event_is_not_pending(self):
        canyon = {
            "name": "ZeroG",
            "last_qualifying_event": EVENT,
            "notification": {"last_emailed_event_start_utc": EVENT["start_utc"]},
        }
        self.assertFalse(send_alert.pending_alerts({"canyons": {"zerog": canyon}}))

    def test_message_contains_canyon_and_calculation(self):
        canyon = {"name": "ZeroG"}
        message = send_alert.alert_message([(canyon, EVENT)], "canyoncountryadventure@gmail.com", "canyoncountryadventure@gmail.com")
        self.assertIn("ZeroG", message["Subject"])
        self.assertIn("22,000 ft³", message.get_content())
        self.assertIn("1.22×", message.get_content())


if __name__ == "__main__":
    unittest.main()
