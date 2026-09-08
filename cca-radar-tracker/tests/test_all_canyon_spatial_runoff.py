import unittest
from types import SimpleNamespace

import numpy as np

import tracker


class AllCanyonSpatialRunoffTests(unittest.TestCase):
    def canyon(self, canyon_id="neon"):
        return SimpleNamespace(
            canyon_id=canyon_id,
            area_sq_mi=1.0,
            weights=np.array([[0.5, 0.5]], dtype=float),
            model={
                "hydrology": {
                    "lag_hours": 0.5,
                    "curve_number": {"dry": 77.3, "normal": 88.6, "wet": 94.8},
                }
            },
        )

    def config(self):
        return {"model": {"frame_minutes": 5}}

    def event(self, include_grid=True):
        event = {
            "start_utc": "2026-08-29T20:00:00Z",
            "end_utc": "2026-08-29T20:05:00Z",
            "frames": 2,
            "wet_frames": 2,
            "basin_rain_inches": 0.10,
        }
        if include_grid:
            event["accumulated_rain_grid_inches"] = [[0.0, 0.20]]
        return event

    def test_non_zero_g_uses_spatial_runoff(self):
        event = self.event()
        canyon = self.canyon("neon")
        tracker.apply_hydrologic_model(event, canyon, self.config())
        self.assertTrue(event["runoff_spatialization_applied"])
        self.assertEqual(event["runoff_method_version"], tracker.RUNOFF_METHOD_VERSION)
        self.assertGreater(
            event["direct_runoff_ft3_range"]["normal"],
            event["lumped_direct_runoff_ft3_range"]["normal"],
        )

    def test_gridless_history_is_explicit_fallback(self):
        event = self.event(include_grid=False)
        canyon = self.canyon("woody")
        tracker.apply_hydrologic_model(event, canyon, self.config())
        self.assertFalse(event["runoff_spatialization_applied"])
        self.assertIn("fallback", event["runoff_spatialization_basis"].lower())
        self.assertEqual(
            event["direct_runoff_ft3_range"],
            event["lumped_direct_runoff_ft3_range"],
        )

    def test_method_version_changes_model_signature(self):
        canyon = SimpleNamespace(
            geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]]]},
            model={
                "fill_target_ft3": 100,
                "flush_target_ft3": 200,
                "technical_length_miles": 1.0,
                "pothole_modifier": 0.0,
            },
        )
        first = tracker.canyon_model_signature(canyon)
        prior = tracker.RUNOFF_METHOD_VERSION
        try:
            tracker.RUNOFF_METHOD_VERSION = "different-test-method"
            second = tracker.canyon_model_signature(canyon)
        finally:
            tracker.RUNOFF_METHOD_VERSION = prior
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
