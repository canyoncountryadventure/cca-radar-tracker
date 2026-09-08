import unittest
from datetime import datetime, timezone

import loss_model


class ZeroGLossModelTests(unittest.TestCase):
    def test_august_field_calibrated_loss(self):
        values = loss_model.zero_g_loss_components(
            datetime(2026, 8, 15, tzinfo=timezone.utc)
        )
        self.assertAlmostEqual(values["eto_inches_per_day"], 7.78 / 31, places=6)
        self.assertAlmostEqual(values["navajo_seepage_inches_per_day"], 1.28, places=6)
        self.assertAlmostEqual(values["total_loss_inches_per_day"], 1.28 + 7.78 / 31, places=6)
        self.assertAlmostEqual(values["percentage_points_per_day"], 0.98139, places=5)
        self.assertEqual(values["reference_pool_depth_ft"], 13.0)

    def test_september_loss_is_lower_than_august(self):
        august = loss_model.zero_g_loss_components(
            datetime(2026, 8, 15, tzinfo=timezone.utc)
        )
        september = loss_model.zero_g_loss_components(
            datetime(2026, 9, 15, tzinfo=timezone.utc)
        )
        self.assertLess(
            september["percentage_points_per_day"],
            august["percentage_points_per_day"],
        )


    def test_monthly_recession_reference_has_all_twelve_months(self):
        rows = loss_model.zero_g_monthly_recession_table(2026)
        self.assertEqual(len(rows), 12)
        by_month = {row["month"]: row for row in rows}
        self.assertAlmostEqual(by_month[1]["percentage_points_per_day"], 0.8445, places=4)
        self.assertAlmostEqual(by_month[7]["percentage_points_per_day"], 1.0072, places=4)
        self.assertAlmostEqual(by_month[9]["percentage_points_per_day"], 0.9427, places=4)
        self.assertGreater(by_month[7]["percentage_points_per_day"], by_month[1]["percentage_points_per_day"])

    def test_integrated_loss_crosses_month_boundary(self):
        start = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        end = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        ratio = loss_model.zero_g_integrated_loss_ratio(start, end)
        aug = loss_model.zero_g_loss_components(start)["percentage_points_per_day"]
        sep = loss_model.zero_g_loss_components(end)["percentage_points_per_day"]
        expected = (aug * 0.5 + sep * 0.5) / 100.0
        self.assertAlmostEqual(ratio, expected, places=5)


if __name__ == "__main__":
    unittest.main()
