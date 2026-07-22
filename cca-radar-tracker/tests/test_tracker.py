import json
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

import tracker


ROOT = Path(__file__).resolve().parents[1]


class TrackerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        geojson = json.loads((ROOT / "watershed.geojson").read_text(encoding="utf-8"))
        cls.geometry = geojson["features"][0]["geometry"]
        cls.config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        cls.palette_by_rgb = tracker.load_palette(ROOT / "n0q_palette.json")
        cls.palette = json.loads((ROOT / "n0q_palette.json").read_text(encoding="utf-8"))

    def test_grid_is_aligned_and_contains_watershed(self):
        grid = tracker.aligned_grid(self.geometry, 2)
        self.assertGreater(grid.width, 9)
        self.assertGreater(grid.height, 5)
        self.assertAlmostEqual((grid.left - tracker.GRID_LEFT_EDGE) / tracker.GRID_RESOLUTION, round((grid.left - tracker.GRID_LEFT_EDGE) / tracker.GRID_RESOLUTION))

    def test_mask_has_fractional_boundary_cells(self):
        grid = tracker.aligned_grid(self.geometry, 2)
        weights = tracker.watershed_weights(self.geometry, grid, 100)
        self.assertGreater(weights.sum(), 10)
        self.assertLess(weights.sum(), 20)
        self.assertTrue(np.any((weights > 0) & (weights < 1)))

    def test_any_independent_rule_can_trigger(self):
        grid = tracker.aligned_grid(self.geometry, 2)
        weights = tracker.watershed_weights(self.geometry, grid, 50)
        image = Image.new("RGB", (grid.width, grid.height), tuple(self.palette[0]))
        pixels = image.load()
        covered = np.argwhere(weights > 0)
        for row, column in covered[: max(1, len(covered) // 3)]:
            pixels[int(column), int(row)] = tuple(self.palette[185])  # 60 dBZ
        analysis = tracker.analyze_image(image, weights, self.palette_by_rgb, self.config["thresholds"])
        self.assertTrue(analysis["qualified"])

    def test_no_rule_trigger(self):
        grid = tracker.aligned_grid(self.geometry, 2)
        weights = tracker.watershed_weights(self.geometry, grid, 50)
        image = Image.new("RGB", (grid.width, grid.height), tuple(self.palette[164]))  # 49.5 dBZ
        analysis = tracker.analyze_image(image, weights, self.palette_by_rgb, self.config["thresholds"])
        self.assertFalse(analysis["qualified"])


if __name__ == "__main__":
    unittest.main()
