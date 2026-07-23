import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import tracker


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertAlmostEqual(self.by_id["black-hole-white-canyon"].area_sq_mi, 262.582, places=3)

    def test_zerog_model_reproduces_field_reference(self):
        model = self.by_id["zerog"].model
        self.assertAlmostEqual(model["fill_target_ft3"], 18000, delta=20)
        self.assertAlmostEqual(model["spatial_rules"][0]["minimum_coverage_percent"], 50, delta=.1)
        self.assertAlmostEqual(model["spatial_rules"][1]["minimum_coverage_percent"], 25, delta=.1)
        self.assertAlmostEqual(model["spatial_rules"][2]["minimum_coverage_percent"], 10, delta=.1)

    def test_larger_watershed_uses_declining_percent_not_fixed_percent(self):
        eardley = self.by_id["eardley"].model
        self.assertGreater(eardley["fill_target_ft3"], 18000)
        self.assertLess(eardley["spatial_rules"][0]["minimum_coverage_percent"], 5)

    def test_nws_zr_rain_depth_at_50_dbz(self):
        dbz = np.array([[50.0]], dtype=np.float32)
        depth = tracker.rain_depth_inches(dbz, self.config["model"])
        self.assertAlmostEqual(float(depth[0, 0]), 0.208, delta=.003)

    def test_nrcs_initial_abstraction_prevents_weak_event_runoff(self):
        cn = self.by_id["zerog"].model["hydrology"]["curve_number"]["normal"]
        self.assertEqual(tracker.nrcs_runoff_depth(.1, cn), 0)

    def test_two_frame_target_and_spatial_gate_can_classify_likely_full(self):
        canyon = self.by_id["zerog"]
        event = {"estimated_runoff_ft3": 20000, "wet_frames": 2, "spatial_gate_seen": True}
        classification, _ = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "likely_full")

    def test_near_target_without_heavy_rain_is_little_change(self):
        canyon = self.by_id["angel-cove"]
        event = {"estimated_runoff_ft3": canyon.model["fill_target_ft3"] * .96, "wet_frames": 4, "spatial_gate_seen": False}
        classification, label = tracker.classify_event(event, canyon, self.config)
        self.assertEqual(classification, "minor")
        self.assertIn("Little to no", label)

    def test_atlas_context_uses_basin_average_not_wettest_pixel(self):
        canyon = self.by_id["angel-cove"]
        event = {"frames": 4, "basin_rain_inches": .107, "max_pixel_storm_inches": .484}
        recurrence = tracker.atlas_return_period(event, canyon, 5)
        self.assertLess(recurrence, 1)

    def test_event_public_adds_peak_cfs_and_exact_iem_archive_link(self):
        canyon = self.by_id["zerog"]
        event = {
            "start_utc": "2024-06-21T22:10:00Z", "end_utc": "2024-06-21T22:20:00Z",
            "peak_frame_utc": "2024-06-21T22:15:00Z", "frames": 3, "wet_frames": 3,
            "estimated_runoff_ft3": 18000, "basin_rain_inches": .2, "spatial_gate_seen": True,
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
            "last_qualifying_event": {"start_utc": "2024-06-21T22:25:00Z", "end_utc": "2024-06-21T22:30:00Z"},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text(json.dumps(legacy))
            migrated = tracker.load_status(path, self.canyons)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["canyons"]["zerog"]["last_qualifying_event"]["start_utc"], "2024-06-21T22:25:00Z")


if __name__ == "__main__":
    unittest.main()
