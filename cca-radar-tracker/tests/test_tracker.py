import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import tracker

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_POOL_TARGETS = {
    "zerog": 52_442,
    "angel-cove": 34_087,
    "black-hole-white-canyon": 262_210,
    "entrajo": 17_830,
    "hog-canyons": 34_087,
    "leprechaun": 6_992,
    "no-kidding": 28_528,
    "pool-arch": 1_748,
    "alcatraz": 54_540,
    "cable-canyon": 262_210,
    "constrychnine": 20_977,
    "eardley": 132_853,
    "north-fork-iron-wash": 52_442,
    "poe": 90_899,
    "the-squeeze": 152_956,
    "upper-greasewood": 83_907,
    "woody": 17_481,
    "wonderland-canyon": 19_404,
    "hogwarts": 3_846,
    "neon": 111_876,
    "quandary": 111_177,
    "yankee-doodle": 27_969,
}


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = json.loads((ROOT / "config.json").read_text())
        cls.collection = json.loads((ROOT / "watersheds.geojson").read_text())
        cls.atlas = json.loads((ROOT / "atlas14.json").read_text())
        cls.hydrology = json.loads((ROOT / "hydrology.json").read_text())
        cls.canyons, cls.global_grid = tracker.build_canyons(
            cls.collection, cls.atlas, cls.config, cls.hydrology
        )
        cls.by_id = {c.canyon_id: c for c in cls.canyons}

    def test_all_twenty_two_canyons_are_loaded(self):
        self.assertEqual(len(self.canyons), 22)

    def test_all_pool_targets_match_approved_table(self):
        actual = {
            canyon_id: self.by_id[canyon_id].model["fill_target_ft3"]
            for canyon_id in EXPECTED_POOL_TARGETS
        }
        self.assertEqual(actual, EXPECTED_POOL_TARGETS)

    def test_zerog_model_uses_measured_depression_volume(self):
        model = self.by_id["zerog"].model
        self.assertEqual(model["fill_target_ft3"], 52_442)
        self.assertEqual(model["storage_target_ft3"], 52_442)
        self.assertAlmostEqual(model["technical_length_miles"], 0.75)
        self.assertAlmostEqual(model["pothole_modifier"], 0.0)

    def test_area_scaling_and_fixed_runoff_coefficient_are_not_in_canyon_model(self):
        model = self.by_id["angel-cove"].model
        self.assertNotIn("scale_factor", model)
        self.assertNotIn("runoff_coefficient", model)
        self.assertEqual(model["storage_rate_percent_of_zerog"], 75.0)

    def test_historical_spatial_comparisons_remain_available_as_context(self):
        for canyon_id in (
            "zerog",
            "pool-arch",
            "eardley",
            "black-hole-white-canyon",
        ):
            rules = self.by_id[canyon_id].model["spatial_rules"]
            self.assertEqual(
                [rule["minimum_coverage_percent"] for rule in rules],
                [50.0, 25.0, 10.0],
            )

    def test_nws_zr_rain_depth_at_50_dbz(self):
        dbz = np.array([[50.0]], dtype=np.float32)
        depth = tracker.rain_depth_inches(dbz, self.config["model"])
        self.assertAlmostEqual(float(depth[0, 0]), 0.208, delta=0.003)

    def test_adjusted_nrcs_initial_abstraction(self):
        cn = self.by_id["zerog"].model["hydrology"]["curve_number"]["normal"]
        self.assertAlmostEqual(tracker.nrcs_initial_abstraction(cn), 0.089, delta=0.002)
        self.assertEqual(tracker.nrcs_runoff_depth(0.05, cn), 0)
        self.assertGreater(tracker.nrcs_runoff_depth(0.10, cn), 0)

    def test_storage_and_duration_classify_likely_full_without_dbz_gate(self):
        canyon = self.by_id["zerog"]
        event = {
            "direct_runoff_ft3": canyon.model["fill_target_ft3"],
            "direct_runoff_ft3_range": {
                "dry": 40_000,
                "normal": canyon.model["fill_target_ft3"],
                "wet": 70_000,
            },
            "wet_frames": 2,
            "spatial_gate_seen": False,
        }
        classification, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "likely_full")
        self.assertIn("may be full", label)
        self.assertTrue(event["decision_tests"]["storage_target_met"])
        self.assertFalse(event["decision_tests"]["heavy_rain_footprint_observed"])

    def test_near_target_without_heavy_rain_is_partial_not_little_change(self):
        canyon = self.by_id["angel-cove"]
        event = {
            "direct_runoff_ft3": canyon.model["fill_target_ft3"] * 0.96,
            "direct_runoff_ft3_range": {},
            "wet_frames": 4,
            "spatial_gate_seen": False,
        }
        classification, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "moderate")
        self.assertIn("Large partial refill", label)

    def test_truly_small_event_is_no_meaningful_refill(self):
        canyon = self.by_id["angel-cove"]
        event = {
            "direct_runoff_ft3": canyon.model["fill_target_ft3"] * 0.20,
            "direct_runoff_ft3_range": {},
            "wet_frames": 2,
            "spatial_gate_seen": False,
        }
        classification, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "minor")
        self.assertIn("No meaningful", label)

    def test_refill_ratio_bands_follow_storage_method(self):
        canyon = self.by_id["zerog"]
        cases = (
            (0.30, "Some pool refill"),
            (0.60, "Substantial partial refill"),
            (0.85, "Large partial refill"),
        )
        for ratio, expected_label in cases:
            with self.subTest(ratio=ratio):
                event = {
                    "direct_runoff_ft3": canyon.model["fill_target_ft3"] * ratio,
                    "direct_runoff_ft3_range": {},
                    "wet_frames": 2,
                    "spatial_gate_seen": False,
                }
                classification, label = tracker.classify_event(event, canyon, self.config)
                self.assertEqual(classification, "moderate")
                self.assertIn(expected_label, label)

    def test_atlas_context_uses_basin_average_not_wettest_pixel(self):
        canyon = self.by_id["angel-cove"]
        event = {
            "frames": 4,
            "basin_rain_inches": 0.107,
            "max_pixel_storm_inches": 0.484,
        }
        recurrence = tracker.atlas_return_period(event, canyon, 5)
        self.assertLess(recurrence, 1)

    def test_event_public_exposes_direct_runoff_decision_and_atlas_fields(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2024-06-21T22:10:00Z",
            "end_utc": "2024-06-21T22:20:00Z",
            "peak_frame_utc": "2024-06-21T22:15:00Z",
            "frames": 3,
            "wet_frames": 3,
            "basin_rain_inches": 0.2,
            "accumulated_rain_grid_inches": [[0.1, 0.2]],
            "spatial_gate_seen": True,
            "peak_dbz": 55.0,
        }
        public = tracker.event_public(event, canyon, self.config)
        self.assertIn("direct_runoff_ft3", public)
        self.assertIn("routed_peak_cfs_range", public)
        self.assertEqual(public["accumulated_rain_grid_inches"], [[0.1, 0.2]])

    def test_event_public_reports_watershed_grid_max_and_weighted_mean_check(self):
        canyon = self.by_id["zerog"]
        accumulated = np.full(canyon.weights.shape, 0.3, dtype=np.float32)
        first_inside = np.argwhere(canyon.weights > 0)[0]
        accumulated[tuple(first_inside)] = 0.6
        weighted_mean = float((accumulated * canyon.weights).sum() / canyon.weights.sum())
        event = {
            "start_utc": "2026-08-09T21:00:00Z",
            "end_utc": "2026-08-09T22:00:00Z",
            "peak_frame_utc": "2026-08-09T21:30:00Z",
            "frames": 13,
            "wet_frames": 13,
            "basin_rain_inches": round(weighted_mean, 4),
            "accumulated_rain_grid_inches": tracker.grid_list(accumulated, 4),
            "spatial_gate_seen": False,
            "peak_dbz": 50.0,
        }
        public = tracker.event_public(event, canyon, self.config)
        self.assertEqual(public["maximum_watershed_cell_storm_inches"], 0.6)
        self.assertAlmostEqual(
            public["accumulation_grid_area_weighted_mean_inches"],
            weighted_mean,
            places=3,
        )
        self.assertTrue(public["accumulation_grid_mean_consistent"])

    def test_zero_g_peak_is_calibrated_without_changing_runoff_volume(self):
        event = {
            "start_utc": "2026-07-21T00:00:00Z",
            "end_utc": "2026-07-21T01:00:00Z",
            "peak_frame_utc": "2026-07-21T00:30:00Z",
            "frames": 13,
            "wet_frames": 13,
            "basin_rain_inches": 1.0,
        }
        public = tracker.event_public(event, self.by_id["zerog"], self.config)
        self.assertEqual(public["peak_flow_factor"], 0.14)
        self.assertEqual(public["peak_flow_status"], "provisional_field_calibration")
        self.assertEqual(
            public["routed_peak_cfs"],
            round(public["uncalibrated_routed_peak_cfs"] * 0.14, 2),
        )
        self.assertEqual(public["direct_runoff_ft3"], public["generated_runoff_ft3"])
        self.assertNotIn("estimated_runoff_ft3", public)
        self.assertNotIn("estimated_peak_cfs", public)
        self.assertIn("decision_tests", public)
        self.assertIn("atlas14_return_period_years", public)
        self.assertNotIn("fill_target_one_hour_cfs", public)
        self.assertIn("mode=archive", public["iem_archive_url"])

    def test_hail_values_are_capped_for_rain_volume(self):
        values = np.array([[55.0, 60.0, 70.0]], dtype=np.float32)
        depth = tracker.rain_depth_inches(values, self.config["model"])
        self.assertAlmostEqual(float(depth[0, 0]), float(depth[0, 1]), places=6)
        self.assertAlmostEqual(float(depth[0, 1]), float(depth[0, 2]), places=6)

    def test_metadata_describes_new_method_and_honest_condition_language(self):
        metadata = tracker.model_metadata(self.canyons, self.config)
        method = metadata["method"]
        self.assertIn("52,442 ft³", method["target_formula"])
        self.assertIn("No fixed runoff coefficient", method["direct_runoff_explanation"])
        self.assertIn("timestamp-keyed ledger", method["frame_reconciliation_explanation"])
        self.assertIn("25 dBZ", method["rain_event_explanation"])
        self.assertIn("30 consecutive minutes", method["rain_event_explanation"])
        self.assertIn("0.8 percentage point per day", method["cumulative_refill_explanation"])
        self.assertIn("July 29", method["pool_loss_explanation"])
        self.assertIn("0.14 factor", method["peak_flow_explanation"])
        self.assertEqual(self.config["model"]["storm_dbz_threshold"], 25)
        self.assertIn("S0.05", method["runoff_formula"])
        self.assertIn("HSG D", method["direct_runoff_explanation"])
        self.assertNotIn("runoff_coefficient_explanation", method)
        self.assertIn("not a measured pool-depth percentage", method["fill_ratio_explanation"])
        self.assertIn("pools may be full", method["classification"]["likely_full"])

    def test_schema_one_status_preserves_zerog_qualifying_event(self):
        legacy = {
            "schema_version": 1,
            "monitoring_started_utc": "2026-07-22T00:00:00Z",
            "last_qualifying_event": {
                "start_utc": "2024-06-21T22:25:00Z",
                "end_utc": "2024-06-21T22:30:00Z",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps(legacy))
            migrated = tracker.load_status(path, self.canyons)
        self.assertEqual(migrated["schema_version"], 5)
        self.assertEqual(
            migrated["canyons"]["zerog"]["last_qualifying_event"]["start_utc"],
            "2024-06-21T22:25:00Z",
        )

    def test_long_event_atlas_comparison_uses_multi_hour_duration_data(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2026-07-01T00:00:00Z",
            "end_utc": "2026-07-01T02:00:00Z",
            "frames": 25,
            "basin_rain_inches": 1.0,
        }
        self.assertIsNotNone(tracker.atlas_return_period(event, canyon, 5))

    def test_atlas_comparison_is_suppressed_beyond_24_hours(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2026-07-01T00:00:00Z",
            "end_utc": "2026-07-02T01:00:00Z",
            "frames": 301,
            "basin_rain_inches": 2.0,
        }
        self.assertIsNone(tracker.atlas_return_period(event, canyon, 5))

    def test_current_direct_runoff_event_is_refreshed_with_atlas_context(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2026-07-01T00:00:00Z",
            "end_utc": "2026-07-01T01:59:00Z",
            "peak_frame_utc": "2026-07-01T01:00:00Z",
            "frames": 24,
            "wet_frames": 24,
            "basin_rain_inches": 1.0,
            "direct_runoff_ft3": 1,
            "atlas14_return_period_years": None,
        }
        status = {
            "canyons": {
                canyon.canyon_id: {
                    "last_rain_event": dict(event),
                    "last_qualifying_event": None,
                    "events": [dict(event)],
                }
            }
        }
        tracker.refresh_status_events(status, [canyon], self.config)
        refreshed = status["canyons"][canyon.canyon_id]
        self.assertIsNotNone(
            refreshed["last_rain_event"]["atlas14_return_period_years"]
        )
        self.assertIsNotNone(refreshed["events"][0]["atlas14_return_period_years"])

    def test_insufficient_radar_data_is_not_classified_as_zero_rain(self):
        canyon = self.by_id["zerog"]
        event = {"radar_data_sufficient": False}
        code, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(code, "insufficient_data")
        self.assertEqual(label, "Insufficient radar data")

    def test_required_state_fails_closed_when_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaises(ValueError):
                tracker.load_status(missing, self.canyons, require_existing=True)


if __name__ == "__main__":
    unittest.main()
