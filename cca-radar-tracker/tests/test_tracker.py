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
    "eardley": 87_403,
    "north-fork-iron-wash": 52_442,
    "poe": 90_899,
    "the-squeeze": 152_956,
    "upper-greasewood": 83_907,
    "woody": 17_481,
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
        cls.palette_by_rgb = tracker.load_palette(ROOT / "n0q_palette.json")
        cls.palette = json.loads((ROOT / "n0q_palette.json").read_text())

    def test_all_seventeen_canyons_are_loaded(self):
        self.assertEqual(len(self.canyons), 17)
        self.assertAlmostEqual(self.by_id["eardley"].area_sq_mi, 77.36, places=2)
        self.assertAlmostEqual(
            self.by_id["black-hole-white-canyon"].area_sq_mi,
            262.582,
            places=3,
        )

    def test_all_pool_targets_match_approved_table(self):
        actual = {
            canyon_id: self.by_id[canyon_id].model["fill_target_ft3"]
            for canyon_id in EXPECTED_POOL_TARGETS
        }
        self.assertEqual(actual, EXPECTED_POOL_TARGETS)

    def test_zerog_model_uses_measured_depression_volume(self):
        model = self.by_id["zerog"].model
        self.assertEqual(model["fill_target_ft3"], 52_442)
        self.assertAlmostEqual(model["technical_length_miles"], 0.75)
        self.assertAlmostEqual(model["pothole_modifier"], 0.0)

    def test_fixed_spatial_percentages_apply_to_large_and_small_watersheds(self):
        for canyon_id in ("zerog", "pool-arch", "eardley", "black-hole-white-canyon"):
            rules = self.by_id[canyon_id].model["spatial_rules"]
            self.assertEqual(
                [rule["minimum_coverage_percent"] for rule in rules],
                [50.0, 25.0, 10.0],
            )

    def test_nws_zr_rain_depth_at_50_dbz(self):
        dbz = np.array([[50.0]], dtype=np.float32)
        depth = tracker.rain_depth_inches(dbz, self.config["model"])
        self.assertAlmostEqual(float(depth[0, 0]), 0.208, delta=0.003)

    def test_nrcs_initial_abstraction_prevents_weak_event_runoff(self):
        cn = self.by_id["zerog"].model["hydrology"]["curve_number"]["normal"]
        self.assertEqual(tracker.nrcs_runoff_depth(0.1, cn), 0)

    def test_two_frame_target_and_spatial_gate_can_classify_likely_full(self):
        canyon = self.by_id["zerog"]
        event = {
            "estimated_runoff_ft3": canyon.model["fill_target_ft3"],
            "wet_frames": 2,
            "spatial_gate_seen": True,
        }
        classification, _ = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "likely_full")

    def test_near_target_without_heavy_rain_is_little_change(self):
        canyon = self.by_id["angel-cove"]
        event = {
            "estimated_runoff_ft3": canyon.model["fill_target_ft3"] * 0.96,
            "wet_frames": 4,
            "spatial_gate_seen": False,
        }
        classification, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "minor")
        self.assertIn("Little to no", label)

    def test_atlas_context_uses_basin_average_not_wettest_pixel(self):
        canyon = self.by_id["angel-cove"]
        event = {
            "frames": 4,
            "basin_rain_inches": 0.107,
            "max_pixel_storm_inches": 0.484,
        }
        recurrence = tracker.atlas_return_period(event, canyon, 5)
        self.assertLess(recurrence, 1)

    def test_event_public_adds_peak_cfs_and_exact_iem_archive_link(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2024-06-21T22:10:00Z",
            "end_utc": "2024-06-21T22:20:00Z",
            "peak_frame_utc": "2024-06-21T22:15:00Z",
            "frames": 3,
            "wet_frames": 3,
            "estimated_runoff_ft3": 52_442,
            "basin_rain_inches": 0.2,
            "spatial_gate_seen": True,
        }
        public = tracker.event_public(event, canyon, self.config)
        self.assertIn("estimated_peak_cfs", public)
        self.assertIn("dry", public["estimated_peak_cfs_range"])
        self.assertIn("mode=archive", public["iem_archive_url"])
        self.assertIn("prod=usrad", public["iem_archive_url"])

    def test_hail_values_are_capped_for_rain_volume(self):
        values = np.array([[55.0, 60.0, 70.0]], dtype=np.float32)
        depth = tracker.rain_depth_inches(values, self.config["model"])
        self.assertAlmostEqual(float(depth[0, 0]), float(depth[0, 1]), places=6)
        self.assertAlmostEqual(float(depth[0, 1]), float(depth[0, 2]), places=6)

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
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(
            migrated["canyons"]["zerog"]["last_qualifying_event"]["start_utc"],
            "2024-06-21T22:25:00Z",
        )


if __name__ == "__main__":
    unittest.main()
