import json
import sys
import unittest
from unittest import mock
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

UTC = timezone.utc


def dt(value: str) -> datetime:
    return tracker.parse_utc(value)


def atlas_fixture():
    return {
        f"{minutes}-min": {"1": 0.1, "2": 0.2, "5": 0.4, "10": 0.6}
        for minutes in (5, 10, 15, 30, 60)
    }


def canyon_fixture():
    grid = tracker.Grid(left=-110.0, bottom=38.0, right=-109.995, top=38.005, width=1, height=1)
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
            "fill_target_ft3": 52442,
            "storage_target_ft3": 52442,
            "flush_target_ft3": 104884,
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


def wet_record(timestamp: str, rain_inches: float = 0.25, dbz: float = 45.0):
    analysis = {
        "frame_utc": timestamp,
        "maximum_dbz": dbz,
        "coverage_percent": {"50": 0.0, "55": 0.0, "60": 0.0},
        "spatial_rules": [
            {
                "dbz": 50.0,
                "minimum_coverage_percent": 50.0,
                "coverage_percent": 0.0,
                "covered_area_sq_mi": 0.0,
                "qualified": False,
            },
            {
                "dbz": 55.0,
                "minimum_coverage_percent": 25.0,
                "coverage_percent": 0.0,
                "covered_area_sq_mi": 0.0,
                "qualified": False,
            },
            {
                "dbz": 60.0,
                "minimum_coverage_percent": 10.0,
                "coverage_percent": 0.0,
                "covered_area_sq_mi": 0.0,
                "qualified": False,
            },
        ],
        "spatial_gate": False,
        "frame_basin_rain_inches": rain_inches,
        "frame_rain_volume_ft3": 580800,
        "wet": True,
        "unknown_watershed_percent": 0.0,
        "grid_dbz": [[dbz]],
        "grid_bbox": [-110.0, 38.0, -109.995, 38.005],
    }
    return {
        "frame_utc": timestamp,
        "source": "historical",
        "confirmed": True,
        "processed_utc": timestamp,
        "summary": {"zerog": tracker.frame_summary(analysis)},
        "wet_canyons": {
            "zerog": {
                "analysis": {key: value for key, value in analysis.items() if key != "grid_dbz"},
                "grid_dbz_zlib": tracker.encode_grid(analysis["grid_dbz"]),
                "max_pixel_frame_inches": rain_inches,
            }
        },
    }


def dry_record(timestamp: str):
    return {
        "frame_utc": timestamp,
        "source": "historical",
        "confirmed": True,
        "processed_utc": timestamp,
        "summary": {
            "zerog": {
                "maximum_dbz": 10.0,
                "frame_basin_rain_inches": 0.0,
                "frame_rain_volume_ft3": 0,
                "spatial_gate": False,
                "unknown_watershed_percent": 0.0,
                "wet": False,
            }
        },
        "wet_canyons": {},
    }


class FrameReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / "config.json").read_text())
        self.canyon = canyon_fixture()

    def test_compressed_radar_grid_round_trip(self):
        grid = [[None, 35.0, 50.0], [10.0, None, 60.0]]
        self.assertEqual(tracker.decode_grid(tracker.encode_grid(grid)), grid)

    def test_confirmed_archive_record_cannot_be_replaced_by_provisional(self):
        status = tracker.empty_status([self.canyon])
        confirmed = wet_record("2026-07-26T23:20:00Z")
        provisional = {**confirmed, "source": "provisional", "confirmed": False}
        self.assertTrue(tracker.upsert_frame_record(status, confirmed))
        self.assertFalse(tracker.upsert_frame_record(status, provisional))
        self.assertTrue(status["frame_ledger"][confirmed["frame_utc"]]["confirmed"])

    def test_scheduler_includes_overlap_and_old_missing_frame(self):
        status = tracker.empty_status([self.canyon])
        status["ledger_started_utc"] = "2026-07-26T20:00:00Z"
        status["latest_archive_confirmed_frame_utc"] = "2026-07-26T21:50:00Z"
        status["missing_archive_frames_utc"] = ["2026-07-26T21:55:00Z"]
        latest = dt("2026-07-26T23:30:00Z")
        values = tracker.scheduled_timestamps(status, self.config, latest)
        self.assertIn(dt("2026-07-26T21:55:00Z"), values)
        self.assertIn(dt("2026-07-26T22:00:00Z"), values)
        self.assertIn(latest, values)

    def test_planned_first_frame_is_reported_missing_when_fetch_never_succeeds(self):
        status = tracker.empty_status([self.canyon])
        tracker.note_planned_ledger_start(status, dt("2026-07-26T23:00:00Z"))
        tracker.upsert_frame_record(status, dry_record("2026-07-26T23:05:00Z"))
        tracker.update_frame_health(
            status, dt("2026-07-26T23:20:00Z"), self.config
        )
        self.assertIn(
            "2026-07-26T23:00:00Z", status["missing_archive_frames_utc"]
        )

    def test_health_lists_gap_and_keeps_later_frames_for_retry(self):
        status = tracker.empty_status([self.canyon])
        for timestamp in (
            "2026-07-26T23:00:00Z",
            "2026-07-26T23:10:00Z",
            "2026-07-26T23:15:00Z",
        ):
            tracker.upsert_frame_record(status, dry_record(timestamp))
        tracker.update_frame_health(
            status, dt("2026-07-26T23:30:00Z"), self.config
        )
        self.assertEqual(
            status["missing_archive_frames_utc"],
            ["2026-07-26T23:05:00Z", "2026-07-26T23:20:00Z"],
        )
        self.assertEqual(
            status["stale_missing_archive_frames_utc"],
            ["2026-07-26T23:05:00Z"],
        )
        self.assertEqual(
            status["latest_archive_confirmed_frame_utc"],
            "2026-07-26T23:00:00Z",
        )

    def test_rebuild_is_idempotent_and_retains_radar_grids(self):
        status = tracker.empty_status([self.canyon])
        old_event = {
            "start_utc": "2026-07-25T20:00:00Z",
            "end_utc": "2026-07-25T20:05:00Z",
            "frames": 2,
            "wet_frames": 2,
            "classification": "moderate",
            "peak_grid_dbz": [[41.0]],
            "grid_bbox": [-110.0, 38.0, -109.995, 38.005],
        }
        canyon_status = status["canyons"]["zerog"]
        canyon_status["events"] = [old_event]
        canyon_status["last_rain_event"] = old_event
        tracker.upsert_frame_record(status, wet_record("2026-07-26T23:20:00Z"))
        tracker.upsert_frame_record(status, dry_record("2026-07-26T23:35:00Z"))

        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        canyon_status = status["canyons"]["zerog"]
        first = canyon_status["last_rain_event"]
        first_rain = first["basin_rain_inches"]
        self.assertEqual(first["peak_grid_dbz"], [[45.0]])
        self.assertNotIn("peak_grid_dbz", canyon_status["events"][0])

        tracker.rebuild_events_from_ledger(status, [self.canyon], self.config)
        canyon_status = status["canyons"]["zerog"]
        second = canyon_status["last_rain_event"]
        self.assertEqual(second["basin_rain_inches"], first_rain)
        self.assertEqual(second["peak_grid_dbz"], [[45.0]])
        self.assertEqual(len(canyon_status["events"]), 2)

    def test_missing_retained_event_grid_is_restored_from_peak_frame(self):
        status = tracker.empty_status([self.canyon])
        event = {
            "start_utc": "2026-07-25T20:00:00Z",
            "end_utc": "2026-07-25T20:05:00Z",
            "peak_frame_utc": "2026-07-25T20:05:00Z",
            "frames": 2,
            "wet_frames": 2,
            "classification": "moderate",
        }
        status["canyons"]["zerog"]["last_rain_event"] = event
        analysis = {
            "maximum_dbz": 45.0,
            "grid_dbz": [[45.0]],
            "grid_bbox": [-110.0, 38.0, -109.995, 38.005],
        }
        with (
            mock.patch.object(tracker, "fetch_radar_image", return_value=object()),
            mock.patch.object(tracker, "crop_for_grid", return_value=object()),
            mock.patch.object(
                tracker, "analyze_canyon_image",
                return_value=(analysis, np.zeros((1, 1), dtype=np.float32)),
            ),
        ):
            restored = tracker.restore_missing_event_grids(
                status,
                [self.canyon],
                self.canyon.grid,
                {},
                self.config,
                dt("2026-07-26T23:30:00Z"),
            )
        self.assertEqual(restored, 1)
        self.assertEqual(event["peak_grid_dbz"], [[45.0]])
        self.assertEqual(event["grid_bbox"], analysis["grid_bbox"])

    def test_rewind_preserves_pre_cutoff_grid(self):
        status = tracker.empty_status([self.canyon])
        old_event = {
            "start_utc": "2026-07-25T20:00:00Z",
            "end_utc": "2026-07-25T20:05:00Z",
            "frames": 2,
            "wet_frames": 2,
            "classification": "moderate",
            "peak_grid_dbz": [[42.0]],
            "grid_bbox": [-110.0, 38.0, -109.995, 38.005],
        }
        canyon_status = status["canyons"]["zerog"]
        canyon_status["events"] = [old_event]
        canyon_status["last_rain_event"] = old_event
        tracker.rewind_status(status, [self.canyon], dt("2026-07-26T00:00:00Z"))
        self.assertEqual(
            status["canyons"]["zerog"]["last_rain_event"]["peak_grid_dbz"],
            [[42.0]],
        )


if __name__ == "__main__":
    unittest.main()
