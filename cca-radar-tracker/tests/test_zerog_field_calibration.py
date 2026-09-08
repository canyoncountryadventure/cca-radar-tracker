import unittest
from types import SimpleNamespace
import numpy as np
import tracker

class ZeroGFieldCalibrationTests(unittest.TestCase):
    def canyon(self):
        return SimpleNamespace(
            canyon_id='zerog',
            weights=np.ones((1, 1), dtype=float),
        )

    def evidence(self, inches):
        return tracker.zero_g_field_core_evidence(
            {'accumulated_rain_grid_inches': [[inches]]}, self.canyon()
        )

    def test_small_anchor_stays_below_major(self):
        self.assertFalse(self.evidence(0.0779)['major_threshold_met'])

    def test_aug12_anchor_meets_major(self):
        evidence = self.evidence(0.2123)
        self.assertTrue(evidence['major_threshold_met'])
        self.assertFalse(evidence['flush_threshold_met'])

    def test_aug29_anchor_major_not_flush(self):
        evidence = self.evidence(0.5101)
        self.assertTrue(evidence['major_threshold_met'])
        self.assertFalse(evidence['flush_threshold_met'])

    def test_aug30_anchor_meets_flush(self):
        self.assertTrue(self.evidence(1.1051)['flush_threshold_met'])

    def test_aug31_secondary_rise_does_not_force_refill(self):
        self.assertFalse(self.evidence(0.0574)['major_threshold_met'])

    def test_spatial_runoff_preserves_local_core(self):
        canyon = SimpleNamespace(
            canyon_id='zerog',
            weights=np.array([[0.5, 0.5]], dtype=float),
        )
        event = {'accumulated_rain_grid_inches': [[0.0, 0.20]]}
        spatial = tracker.spatial_nrcs_runoff_depth(event, canyon, 88.6)
        lumped = tracker.nrcs_runoff_depth(0.10, 88.6)
        self.assertIsNotNone(spatial)
        self.assertGreater(spatial, lumped)

if __name__ == '__main__':
    unittest.main()
