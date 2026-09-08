#!/usr/bin/env python3
"""Update tests that intentionally encode the current Zero G field anchor."""

from pathlib import Path

PATH = Path(__file__).resolve().parents[1] / "tests" / "test_event_accumulation.py"
text = PATH.read_text(encoding="utf-8")

old_anchor = '''    def test_zero_g_field_anchor_uses_provisional_numeric_decay(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        evaluation_time = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        tracker.cumulative_refill_evidence(
            status, canyon, self.config, now_utc=evaluation_time
        )
        condition = status["condition_estimate"]
        self.assertEqual(condition["percent"], 95)
        self.assertEqual(condition["basis"], "Field verified")
        self.assertEqual(condition["last_verified"]["observed_utc"], "2026-08-01T12:00:00Z")
        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")
        self.assertAlmostEqual(condition["decay_percentage_points_per_day"], 1.07, delta=0.05)
        self.assertAlmostEqual(condition["navajo_seepage_inches_per_day"], 1.28, places=2)
'''
new_anchor = '''    def test_zero_g_field_anchor_uses_provisional_numeric_decay(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        evaluation_time = datetime(2026, 9, 8, 20, 43, tzinfo=timezone.utc)
        tracker.cumulative_refill_evidence(
            status, canyon, self.config, now_utc=evaluation_time
        )
        condition = status["condition_estimate"]
        self.assertEqual(condition["percent"], 72)
        self.assertEqual(condition["basis"], "Field verified")
        self.assertEqual(condition["last_verified"]["observed_utc"], "2026-09-07T20:43:00Z")
        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")
        self.assertAlmostEqual(condition["decay_percentage_points_per_day"], 0.943, delta=0.01)
        self.assertAlmostEqual(condition["navajo_seepage_inches_per_day"], 1.28, places=2)
'''

old_topoff = '''    def test_event_after_field_anchor_can_only_top_off_condition(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        evaluation_time = datetime(2026, 8, 4, 12, tzinfo=timezone.utc)
        event_time = evaluation_time - timedelta(hours=1)
        status["events"] = [{
            "start_utc": tracker.utc_text(event_time),
            "end_utc": tracker.utc_text(event_time),
            "direct_runoff_ft3": 10,
            "fill_ratio": 0.1,
        }]
        tracker.cumulative_refill_evidence(
            status, canyon, self.config, now_utc=evaluation_time
        )
        self.assertEqual(status["condition_estimate"]["percent"], 100)
'''
new_topoff = '''    def test_event_after_field_anchor_can_only_top_off_condition(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        evaluation_time = datetime(2026, 9, 8, 20, 43, tzinfo=timezone.utc)
        event_time = evaluation_time - timedelta(hours=1)
        status["events"] = [{
            "start_utc": tracker.utc_text(event_time),
            "end_utc": tracker.utc_text(event_time),
            "direct_runoff_ft3": 40,
            "fill_ratio": 0.4,
        }]
        tracker.cumulative_refill_evidence(
            status, canyon, self.config, now_utc=evaluation_time
        )
        self.assertEqual(status["condition_estimate"]["percent"], 100)
'''

for old, new, label in (
    (old_anchor, new_anchor, "field-anchor decay test"),
    (old_topoff, new_topoff, "post-anchor top-off test"),
):
    if new in text:
        continue
    if text.count(old) != 1:
        raise RuntimeError(f"Could not uniquely update {label}")
    text = text.replace(old, new, 1)

PATH.write_text(text, encoding="utf-8")
print("Updated Zero G field-anchor tests for Sept. 7 calibration")
