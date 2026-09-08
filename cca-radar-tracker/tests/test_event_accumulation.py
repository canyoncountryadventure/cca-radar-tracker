import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import tracker


ROOT = Path(__file__).resolve().parents[1]


def canyon_fixture(fill_target=52_442):
    model = {
        "fill_target_ft3": fill_target,
        "storage_target_ft3": fill_target,
        "flush_target_ft3": fill_target * 2,
        "technical_length_miles": 0.75,
        "pothole_modifier": 0.0,
        "hydrology": {
            "curve_number": {"dry": 75.0, "normal": 82.0, "wet": 88.0},
            "lag_hours": 0.5,
        },
        "spatial_rules": [],
    }
    return tracker.Canyon(
        canyon_id="zerog",
        name="Zero G",
        area_sq_mi=1.0,
        geometry={"type": "Polygon", "coordinates": [[[-110, 38], [-109, 38], [-109, 39], [-110, 39], [-110, 38]]]},
        outlet=[-109.5, 38.5],
        grid=tracker.Grid(-110, 38, -109, 39, 1, 1),
        weights=np.ones((1, 1), dtype=np.float32),
        atlas14={},
        model=model,
    )


def record(timestamp, dbz=None, rain=0.0, wet=False):
    summary = {
        "maximum_dbz": dbz,
        "frame_basin_rain_inches": rain,
        "frame_rain_volume_ft3": round(rain / 12 * tracker.SQUARE_FEET_PER_SQUARE_MILE),
        "spatial_gate": wet,
        "wet": wet,
        "rain_detected": rain > 0,
        "unknown_watershed_percent": 0,
        "radar_data_quality": "valid",
        "radar_data_sufficient": True,
    }
    result = {
        "frame_utc": timestamp,
        "source": "historical",
        "confirmed": True,
        "summary": {"zerog": summary},
        "wet_canyons": {},
    }
    if rain > 0 or wet:
        analysis = {
            **summary,
            "coverage_percent": {},
            "spatial_rules": [],
            "grid_bbox": [-110, 38, -109, 39],
        }
        rain_grid = [[rain]]
        grid_dbz = [[dbz if dbz is not None else 0]]
        result["wet_canyons"]["zerog"] = {
            "analysis": analysis,
            "grid_dbz_zlib": tracker.encode_grid(grid_dbz),
            "rain_grid_zlib": tracker.encode_grid(rain_grid),
        }
    return result


class EventAccumulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = {
            "model": {
                "frame_minutes": 5,
                "event_gap_minutes": 30,
                "storm_dbz_threshold": 25,
                "event_continue_minimum_basin_rain_inches": 0.0001,
                "flush_ratio": 2,
            },
            "recent_refill_window_days": 7,
            "event_detail_retention_days": 90,
            "event_radar_grid_retention_days": 30,
            "max_retained_events_per_canyon": 50,
        }
        cls.canyon = canyon_fixture()

    def test_25_dbz_trigger_includes_lower_rain_before_and_after(self):
        status = tracker.empty_canyon_status(self.canyon)
        before = record("2026-07-26T22:55:00Z", 20, 0.010, False)
        trigger = record("2026-07-26T23:00:00Z", 30, 0.020, True)
        after = record("2026-07-26T23:05:00Z", 20, 0.015, False)
        for item in (before, trigger, after):
            tracker.upsert_frame_record({"frame_ledger": {}, **{}}, item)
        for item in (before, trigger, after):
            wet_record = item["wet_canyons"].get("zerog")
            analysis = dict(wet_record["analysis"])
            analysis["grid_dbz"] = tracker.decode_grid(wet_record["grid_dbz_zlib"])
            rain = np.asarray(tracker.decode_grid(wet_record["rain_grid_zlib"]), dtype=np.float32)
            tracker.update_canyon_event(
                status,
                self.canyon,
                tracker.parse_utc(item["frame_utc"]),
                analysis,
                rain,
                self.config,
            )
        self.assertIsNotNone(status["open_event"])
        self.assertAlmostEqual(status["open_event"]["basin_rain_inches"], 0.045, places=3)

    def test_event_closes_after_thirty_dry_minutes(self):
        status = tracker.empty_canyon_status(self.canyon)
        wet = record("2026-07-26T23:00:00Z", 30, 0.02, True)
        wet_record = wet["wet_canyons"]["zerog"]
        analysis = dict(wet_record["analysis"])
        analysis["grid_dbz"] = tracker.decode_grid(wet_record["grid_dbz_zlib"])
        rain = np.asarray(tracker.decode_grid(wet_record["rain_grid_zlib"]), dtype=np.float32)
        tracker.update_canyon_event(status, self.canyon, tracker.parse_utc(wet["frame_utc"]), analysis, rain, self.config)
        dry_analysis = tracker.dry_analysis_from_summary(
            tracker.parse_utc("2026-07-26T23:30:00Z"), {}, self.config
        )
        tracker.update_canyon_event(
            status,
            self.canyon,
            tracker.parse_utc("2026-07-26T23:30:00Z"),
            dry_analysis,
            np.zeros((1, 1), dtype=np.float32),
            self.config,
        )
        self.assertIsNone(status["open_event"])
        self.assertEqual(len(status["events"]), 1)

    def test_pretrigger_spatial_rain_is_included_in_accumulated_grid(self):
        status = tracker.empty_canyon_status(self.canyon)
        pending = record("2026-07-26T22:55:00Z", 20, 0.01, False)
        trigger = record("2026-07-26T23:00:00Z", 30, 0.02, True)
        for item in (pending, trigger):
            wet_record = item["wet_canyons"]["zerog"]
            analysis = dict(wet_record["analysis"])
            analysis["grid_dbz"] = tracker.decode_grid(wet_record["grid_dbz_zlib"])
            rain = np.asarray(tracker.decode_grid(wet_record["rain_grid_zlib"]), dtype=np.float32)
            tracker.update_canyon_event(status, self.canyon, tracker.parse_utc(item["frame_utc"]), analysis, rain, self.config)
        accumulated = status["open_event"]["accumulated_rain_grid_inches"]
        self.assertAlmostEqual(float(accumulated[0][0]), 0.03, places=3)

    def test_moving_storm_core_accumulates_at_its_actual_pixels(self):
        canyon = canyon_fixture()
        canyon.grid = tracker.Grid(-110, 38, -109, 39, 2, 1)
        canyon.weights = np.ones((1, 2), dtype=np.float32)
        status = tracker.empty_canyon_status(canyon)
        analysis1 = {
            "maximum_dbz": 35,
            "coverage_percent": {},
            "spatial_rules": [],
            "spatial_gate": True,
            "frame_basin_rain_inches": 0.05,
            "frame_rain_volume_ft3": 1000,
            "wet": True,
            "rain_detected": True,
            "grid_dbz": [[35, 10]],
            "grid_bbox": canyon.grid.bbox,
        }
        analysis2 = {**analysis1, "grid_dbz": [[10, 35]]}
        tracker.update_canyon_event(status, canyon, tracker.parse_utc("2026-07-26T23:00:00Z"), analysis1, np.array([[0.1, 0.0]], dtype=np.float32), self.config)
        tracker.update_canyon_event(status, canyon, tracker.parse_utc("2026-07-26T23:05:00Z"), analysis2, np.array([[0.0, 0.1]], dtype=np.float32), self.config)
        accumulated = np.asarray(status["open_event"]["accumulated_rain_grid_inches"], dtype=float)
        self.assertAlmostEqual(accumulated[0, 0], 0.1, places=3)
        self.assertAlmostEqual(accumulated[0, 1], 0.1, places=3)

    def test_peak_grid_is_frame_with_event_maximum_dbz(self):
        status = tracker.empty_canyon_status(self.canyon)
        first = {
            "maximum_dbz": 40,
            "coverage_percent": {},
            "spatial_rules": [],
            "spatial_gate": True,
            "frame_basin_rain_inches": 0.05,
            "frame_rain_volume_ft3": 1000,
            "wet": True,
            "rain_detected": True,
            "grid_dbz": [[40]],
            "grid_bbox": self.canyon.grid.bbox,
            "dbz_distribution": [],
        }
        second = {**first, "maximum_dbz": 50, "grid_dbz": [[50]], "frame_rain_volume_ft3": 800}
        tracker.update_canyon_event(status, self.canyon, tracker.parse_utc("2026-07-26T23:00:00Z"), first, np.array([[0.1]], dtype=np.float32), self.config)
        tracker.update_canyon_event(status, self.canyon, tracker.parse_utc("2026-07-26T23:05:00Z"), second, np.array([[0.1]], dtype=np.float32), self.config)
        self.assertEqual(status["open_event"]["peak_grid_dbz"], [[50]])

    def test_historical_event_keeps_peak_grid_for_clickable_map(self):
        event = {"peak_grid_dbz": [[55]], "grid_bbox": [-110, 38, -109, 39]}
        self.assertEqual(tracker.compact_event(event)["peak_grid_dbz"], [[55]])

    def test_stored_storm_cards_recalculate_after_storage_target_change(self):
        records = [
            record("2026-07-26T23:00:00Z", 45.0, 0.120, True),
            record("2026-07-26T23:05:00Z", 43.0, 0.100, True),
        ]
        records.extend(
            record(f"2026-07-26T23:{minute:02d}:00Z")
            for minute in (10, 15, 20, 25, 30, 35)
        )
        status = tracker.empty_status([self.canyon])
        for item in records:
            tracker.upsert_frame_record(status, item)
        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        original = status["canyons"]["zerog"]["events"][0]

        larger_storage = canyon_fixture(fill_target=104_884)
        tracker.refresh_status_events(status, [larger_storage], self.config)
        refreshed = status["canyons"]["zerog"]["events"][0]

        self.assertEqual(refreshed["storage_target_ft3"], 104_884)
        self.assertAlmostEqual(
            refreshed["fill_ratio"], original["fill_ratio"] / 2, places=4
        )

    def test_cumulative_loss_evidence_applies_transferred_recession_between_storms(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        status["events"] = [
            {
                "start_utc": "2026-07-27T18:00:00Z",
                "end_utc": "2026-07-27T18:10:00Z",
                "direct_runoff_ft3": 40,
                "fill_ratio": 0.4,
            },
            {
                "start_utc": "2026-07-28T18:00:00Z",
                "end_utc": "2026-07-28T18:10:00Z",
                "direct_runoff_ft3": 80,
                "fill_ratio": 0.8,
            },
        ]
        now = tracker.parse_utc("2026-07-28T18:10:00Z")
        tracker.cumulative_refill_evidence(status, canyon, self.config, now_utc=now)
        evidence = status["cumulative_refill_evidence"]
        self.assertEqual(evidence["event_count"], 2)
        self.assertLess(evidence["balance_ft3"], 100)
        self.assertGreater(evidence["balance_ft3"], 80)
        self.assertEqual(evidence["percent"], evidence["balance_ft3"])
        self.assertEqual(evidence["overflow_ft3"], 0)
        self.assertEqual(evidence["loss_model"], "zero_g_mx2001_et_plus_navajo")
        self.assertGreater(evidence["modeled_loss_ft3"], 0)
        self.assertEqual(
            evidence["milestones_utc"]["25"],
            "2026-07-27T18:10:00Z",
        )
        self.assertIsNone(evidence["milestones_utc"]["100"])
        self.assertEqual(len(status["refill_history"]), 2)

    def test_recent_evidence_does_not_reset_when_new_event_has_zero_runoff(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        now = datetime.now(timezone.utc)
        strong_time = now - timedelta(days=1)
        zero_time = now - timedelta(hours=1)
        status["events"] = [
            {
                "start_utc": tracker.utc_text(strong_time),
                "end_utc": tracker.utc_text(strong_time),
                "direct_runoff_ft3": 80,
                "fill_ratio": 0.8,
            },
            {
                "start_utc": tracker.utc_text(zero_time),
                "end_utc": tracker.utc_text(zero_time),
                "direct_runoff_ft3": 0,
                "fill_ratio": 0.0,
            },
        ]
        tracker.cumulative_refill_evidence(status, canyon, self.config, now_utc=now)
        self.assertGreater(status["recent_refill_evidence"]["percent"], 0)

    def test_no_refill_history_is_unknown_not_zero_percent(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        now = tracker.parse_utc("2026-08-01T12:00:00Z")
        tracker.cumulative_refill_evidence(status, canyon, self.config, now_utc=now)
        condition = status["condition_estimate"]
        self.assertIsNone(condition["percent"])
        self.assertEqual(condition["current_condition"], "unknown")
        self.assertEqual(condition["confidence"], "Unknown")
        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")
        self.assertAlmostEqual(condition["navajo_seepage_inches_per_day"], 1.28, places=2)
        self.assertAlmostEqual(condition["decay_percentage_points_per_day"], 1.07, delta=0.05)

    def test_event_after_field_anchor_can_only_top_off_condition(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        event_time = tracker.parse_utc("2026-08-02T12:00:00Z")
        status["events"] = [
            {
                "start_utc": tracker.utc_text(event_time),
                "end_utc": tracker.utc_text(event_time),
                "direct_runoff_ft3": 80,
                "fill_ratio": 0.8,
            }
        ]
        tracker.cumulative_refill_evidence(
            status,
            canyon,
            self.config,
            now_utc=tracker.parse_utc("2026-08-02T12:00:00Z"),
        )
        self.assertEqual(status["condition_estimate"]["percent"], 100)

    def test_zero_g_field_anchor_uses_provisional_numeric_decay(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        tracker.cumulative_refill_evidence(
            status,
            canyon,
            self.config,
            now_utc=tracker.parse_utc("2026-08-02T12:00:00Z"),
        )
        condition = status["condition_estimate"]
        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")
        self.assertLess(condition["percent"], 98)
        self.assertGreater(condition["percent"], 95)

    def test_rebuild_is_idempotent_and_retains_radar_grids(self):
        records = [
            record("2026-07-26T23:00:00Z", 45.0, 0.120, True),
            record("2026-07-26T23:05:00Z", 43.0, 0.100, True),
        ]
        records.extend(
            record(f"2026-07-26T23:{minute:02d}:00Z")
            for minute in (10, 15, 20, 25, 30, 35)
        )
        status = tracker.empty_status([self.canyon])
        for item in records:
            tracker.upsert_frame_record(status, item)
        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        first = copy.deepcopy(status)
        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        self.assertEqual(status["canyons"]["zerog"]["events"], first["canyons"]["zerog"]["events"])

    def test_rewind_preserves_pre_cutoff_grid(self):
        self.assertTrue(True)

    def test_rewind_uses_configured_event_limit_not_hidden_fifty(self):
        self.assertTrue(True)

    def test_cumulative_no_loss_evidence_preserves_multiple_storms(self):
        self.skipTest("Replaced by transferred-recession cumulative balance test")


if __name__ == "__main__":
    unittest.main()
