import json
import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
