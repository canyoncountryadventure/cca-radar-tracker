import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402


def atlas_fixture():
    return {
        f"{minutes}-min": {"1": 0.1, "2": 0.2, "5": 0.4, "10": 0.6}
        for minutes in (5, 10, 15, 30, 60)
    }


def canyon_fixture(fill_target=52_442):
    grid = tracker.Grid(
        left=-110.0,
        bottom=38.0,
        right=-109.995,
        top=38.005,
        width=1,
        height=1,
    )
    return tracker.Canyon(
        canyon_id="zerog",
        name="Zero G",
        area_sq_mi=1.0,
        geometry={"type": "Polygon", "coordinates": []},
        outlet=[-109.99, 38.0],
        grid=grid,
        weights=np.ones((1, 1), dtype=np.float32),
        atlas14=atlas_fixture(),
        model={
            "fill_target_ft3": fill_target,
            "storage_target_ft3": fill_target,
            "flush_target_ft3": fill_target * 2,
            "spatial_rules": [
                {"dbz": 50.0, "minimum_coverage_percent": 50.0},
                {"dbz": 55.0, "minimum_coverage_percent": 25.0},
                {"dbz": 60.0, "minimum_coverage_percent": 10.0},
            ],
            "hydrology": {
                "lag_hours": 0.5,
                "curve_number": {"dry": 80.0, "normal": 88.6, "wet": 94.0},
            },
        },
    )


def spatial_canyon_fixture():
    canyon = canyon_fixture()
    canyon.grid = tracker.Grid(
        left=-110.0,
        bottom=38.0,
        right=-109.99,
        top=38.01,
        width=2,
        height=2,
    )
    canyon.weights = np.ones((2, 2), dtype=np.float32)
    return canyon


def summary(maximum_dbz, rain_inches, wet):
    return {
        "maximum_dbz": maximum_dbz,
        "frame_basin_rain_inches": rain_inches,
        "frame_rain_volume_ft3": round(rain_inches * 2_323_200),
        "spatial_gate": False,
        "unknown_watershed_percent": 0.0,
        "wet": wet,
        "rain_detected": rain_inches >= 0.0001,
    }


def record(timestamp, maximum_dbz=10.0, rain_inches=0.0, wet=False):
    values = summary(maximum_dbz, rain_inches, wet)
    result = {
        "frame_utc": timestamp,
        "source": "historical",
        "confirmed": True,
        "processed_utc": timestamp,
        "summary": {"zerog": values},
        "wet_canyons": {},
    }
    if wet:
        analysis = {
            "frame_utc": timestamp,
            "maximum_dbz": maximum_dbz,
            "coverage_percent": {"50": 0.0, "55": 0.0, "60": 0.0},
            "spatial_rules": [
                {
                    "dbz": threshold,
                    "minimum_coverage_percent": required,
                    "coverage_percent": 0.0,
                    "covered_area_sq_mi": 0.0,
                    "qualified": False,
                }
                for threshold, required in ((50.0, 50.0), (55.0, 25.0), (60.0, 10.0))
            ],
            "spatial_gate": False,
            "frame_basin_rain_inches": rain_inches,
            "frame_rain_volume_ft3": values["frame_rain_volume_ft3"],
            "wet": True,
            "rain_detected": True,
            "unknown_watershed_percent": 0.0,
            "grid_bbox": [-110.0, 38.0, -109.995, 38.005],
        }
        result["wet_canyons"]["zerog"] = {
            "analysis": analysis,
            "grid_dbz_zlib": tracker.encode_grid([[maximum_dbz]]),
            "max_pixel_frame_inches": rain_inches,
        }
    return result


class EventAccumulationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.json").read_text())
        self.canyon = canyon_fixture()

    def rebuild(self, records):
        status = tracker.empty_status([self.canyon])
        for item in records:
            tracker.upsert_frame_record(status, item)
        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        return status["canyons"]["zerog"]

    def test_25_dbz_trigger_includes_lower_rain_before_and_after(self):
        records = [
            record("2026-07-26T23:00:00Z", 20.0, 0.010, False),
            record("2026-07-26T23:05:00Z", 30.0, 0.050, True),
            record("2026-07-26T23:10:00Z", 15.0, 0.020, False),
        ]
        records.extend(
            record(f"2026-07-26T23:{minute:02d}:00Z")
            for minute in (15, 20, 25, 30, 35, 40)
        )
        canyon_status = self.rebuild(records)
        event = canyon_status["last_rain_event"]
        self.assertEqual(event["start_utc"], "2026-07-26T23:00:00Z")
        self.assertEqual(event["end_utc"], "2026-07-26T23:10:00Z")
        self.assertEqual(event["frames"], 3)
        self.assertEqual(event["wet_frames"], 1)
        self.assertAlmostEqual(event["basin_rain_inches"], 0.080, places=3)
        self.assertEqual(len(canyon_status["events"]), 1)

    def test_event_closes_after_thirty_dry_minutes(self):
        records = [record("2026-07-26T23:00:00Z", 30.0, 0.050, True)]
        records.extend(
            record(f"2026-07-26T23:{minute:02d}:00Z")
            for minute in (5, 10, 15, 20, 25, 30)
        )
        records.append(record("2026-07-26T23:35:00Z", 32.0, 0.060, True))
        canyon_status = self.rebuild(records)
        self.assertEqual(len(canyon_status["events"]), 1)
        self.assertEqual(
            canyon_status["events"][0]["start_utc"],
            "2026-07-26T23:00:00Z",
        )
        self.assertEqual(
            canyon_status["last_rain_event"]["start_utc"],
            "2026-07-26T23:35:00Z",
        )
        self.assertEqual(len(canyon_status["refill_history"]), 2)

    def test_cumulative_no_loss_evidence_preserves_multiple_storms(self):
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
        tracker.cumulative_refill_evidence(status, canyon, self.config)
        evidence = status["cumulative_refill_evidence"]
        self.assertEqual(evidence["event_count"], 2)
        self.assertEqual(evidence["balance_ft3"], 100)
        self.assertEqual(evidence["percent"], 100)
        self.assertEqual(evidence["overflow_ft3"], 20)
        self.assertEqual(
            evidence["milestones_utc"]["25"],
            "2026-07-27T18:10:00Z",
        )
        self.assertEqual(
            evidence["milestones_utc"]["100"],
            "2026-07-28T18:10:00Z",
        )
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
        tracker.cumulative_refill_evidence(status, canyon, self.config)
        evidence = status["recent_refill_evidence"]
        self.assertGreaterEqual(evidence["percent"], 68)
        self.assertEqual(
            status["historical_records"]["peak_individual_event"]["percent"],
            80,
        )

    def test_historical_event_keeps_peak_grid_for_clickable_map(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        event_time = datetime.now(timezone.utc) - timedelta(days=10)
        status["events"] = [
            {
                "start_utc": tracker.utc_text(event_time),
                "end_utc": tracker.utc_text(event_time),
                "peak_frame_utc": tracker.utc_text(event_time),
                "direct_runoff_ft3": 50,
                "fill_ratio": 0.5,
                "peak_grid_dbz": [[50.0, 40.0]],
                "grid_bbox": [-110.0, 38.0, -109.9, 38.1],
            }
        ]
        tracker.cumulative_refill_evidence(status, canyon, self.config)
        self.assertEqual(status["events"][0]["peak_grid_dbz"], [[50.0, 40.0]])
        self.assertEqual(
            status["historical_records"]["peak_individual_event"]["percent"],
            50,
        )

    def test_zero_g_field_anchor_persists_without_numeric_decay(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        tracker.cumulative_refill_evidence(status, canyon, self.config)
        condition = status["condition_estimate"]
        self.assertEqual(condition["percent"], 98)
        self.assertEqual(condition["basis"], "Field verified")
        self.assertEqual(condition["last_verified"]["observed_utc"], "2026-08-01T12:00:00Z")
        self.assertEqual(condition["loss_model"], "not_applied_pending_logger_calibration")

    def test_event_after_field_anchor_can_only_top_off_condition(self):
        canyon = canyon_fixture(fill_target=100)
        status = tracker.empty_canyon_status(canyon)
        status["events"] = [{
            "start_utc": "2026-08-01T18:00:00Z",
            "end_utc": "2026-08-01T18:00:00Z",
            "direct_runoff_ft3": 10,
            "fill_ratio": 0.1,
        }]
        tracker.cumulative_refill_evidence(status, canyon, self.config)
        self.assertEqual(status["condition_estimate"]["percent"], 100)

    def test_moving_storm_core_accumulates_at_its_actual_pixels(self):
        canyon = spatial_canyon_fixture()
        status = tracker.empty_status([canyon])
        rain_grids = (
            np.array([[0.20, 0.0], [0.0, 0.0]], dtype=np.float32),
            np.array([[0.0, 0.20], [0.0, 0.0]], dtype=np.float32),
        )
        for minute, rain_grid in zip((0, 5), rain_grids):
            timestamp = f"2026-07-26T23:{minute:02d}:00Z"
            analysis = {
                "frame_utc": timestamp,
                "maximum_dbz": 50.0,
                "coverage_percent": {"50": 25.0, "55": 0.0, "60": 0.0},
                "spatial_rules": [],
                "spatial_gate": False,
                "frame_basin_rain_inches": 0.05,
                "frame_rain_volume_ft3": 116_160,
                "wet": True,
                "rain_detected": True,
                "unknown_watershed_percent": 0.0,
                "radar_data_quality": "valid",
                "radar_data_sufficient": True,
                "grid_bbox": canyon.grid.bbox,
            }
            item = {
                "frame_utc": timestamp,
                "source": "historical",
                "confirmed": True,
                "processed_utc": timestamp,
                "summary": {"zerog": tracker.frame_summary(analysis)},
                "wet_canyons": {
                    "zerog": {
                        "analysis": analysis,
                        "grid_dbz_zlib": tracker.encode_grid(
                            [[50.0, 0.0], [0.0, 0.0]]
                        ),
                        "rain_grid_zlib": tracker.encode_grid(
                            tracker.grid_list(rain_grid, 4)
                        ),
                    }
                },
            }
            tracker.upsert_frame_record(status, item)
        tracker.rebuild_events_from_ledger(status, [canyon], self.config)
        event = status["canyons"]["zerog"]["last_rain_event"]
        self.assertAlmostEqual(event["basin_rain_inches"], 0.10, places=3)
        self.assertAlmostEqual(event["max_pixel_storm_inches"], 0.20, places=3)

    def test_pretrigger_spatial_rain_is_included_in_accumulated_grid(self):
        canyon = spatial_canyon_fixture()
        event = tracker.start_event(
            tracker.parse_utc("2026-07-26T23:05:00Z"),
            {
                "maximum_dbz": 30.0,
                "coverage_percent": {},
                "spatial_rules": [],
                "spatial_gate": False,
                "frame_basin_rain_inches": 0.025,
                "frame_rain_volume_ft3": 58_080,
                "grid_dbz": [[0.0, 30.0], [0.0, 0.0]],
                "grid_bbox": canyon.grid.bbox,
            },
            np.array([[0.0, 0.10], [0.0, 0.0]], dtype=np.float32),
        )
        tracker.prepend_rain_frame(
            event,
            tracker.parse_utc("2026-07-26T23:00:00Z"),
            {
                "maximum_dbz": 20.0,
                "frame_basin_rain_inches": 0.025,
                "frame_rain_volume_ft3": 58_080,
            },
            np.array([[0.10, 0.0], [0.0, 0.0]], dtype=np.float32),
        )
        self.assertAlmostEqual(event["max_pixel_storm_inches"], 0.10, places=3)
        self.assertEqual(
            event["accumulated_rain_grid_inches"],
            [[0.1, 0.1], [0.0, 0.0]],
        )


if __name__ == "__main__":
    unittest.main()
