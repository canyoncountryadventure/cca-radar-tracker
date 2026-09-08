#!/usr/bin/env python3
"""Apply the Zero G MX2001 + ETo reference recession to every modeled canyon."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "tracker.py"
APP = ROOT / "docs" / "app.js"
TEST = ROOT / "tests" / "test_tracker.py"
EVENT_TEST = ROOT / "tests" / "test_event_accumulation.py"


def replace_if_present(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one old match, found {count}")
    return text.replace(old, new, 1)


tracker = TRACKER.read_text(encoding="utf-8")

old_metadata = '''            "cumulative_refill_explanation": (\n                "Current conditions lose modeled storage between storms. Zero G now uses an "\n                "MX2001 field-calibrated seasonal loss model: 1.28 inches/day of empirical "\n                "Navajo sandstone/seepage-equivalent recession plus monthly Moab reference "\n                "ETo. Other canyons retain the provisional fixed percentage-point decay until "\n                "their own geology or field data support a canyon-specific loss model. New "\n                "modeled runoff is added to the decayed balance and capped at 100%."\n            ),\n            "pool_loss_explanation": (\n                "Zero G's central loss calibration uses the stable lower MX2001 logger from "\n                "August 1 through September 7, 2026. The upper logger is excluded from the "\n                "central rate because it physically relocated on August 8 and remained a "\n                "shallower, more exposed high-loss comparison. The 1.28 in/day residual is an "\n                "empirical stage-equivalent term after subtracting USU Moab monthly reference "\n                "ETo; it includes Navajo sandstone/fracture seepage and any inseparable quiet-"\n                "pool drainage. The percent conversion uses the lower logger's initial 11.9288-"\n                "ft water column and is therefore a field-calibrated stage-equivalent condition "\n                "model, not a surveyed stage-volume curve."\n            ),'''
new_metadata = '''            "cumulative_refill_explanation": (\n                "All canyon condition balances now use the same transferred Zero G reference "\n                "recession between storms. The reference combines 1.28 inches/day of empirical "\n                "Navajo sandstone/seepage-equivalent loss with monthly Moab reference ETo. "\n                "New modeled runoff is added after time-integrated loss and the condition is "\n                "capped at 100%."\n            ),\n            "pool_loss_explanation": (\n                "The loss reference comes from the stable lower HOBO MX2001 logger in Zero G, "\n                "deployed August 1 through September 7, 2026. The upper logger is excluded from "\n                "the central rate because it physically relocated on August 8 and remained a "\n                "shallower, more exposed high-loss comparison. After subtracting USU Moab "\n                "monthly reference ETo normals (2000-2022), the stable lower logger supports a "\n                "1.28 in/day empirical Navajo/seepage-equivalent residual. The percentage "\n                "conversion uses the lower logger's initial 11.9288-ft water column. That same "\n                "seasonal stage-equivalent percentage-point loss is transferred to all 22 "\n                "modeled canyons until canyon-specific recession data are available. This is an "\n                "explicit transfer assumption, not a claim that every canyon has identical pool "\n                "geometry, evaporation, fractures, or seepage."\n            ),'''
tracker = replace_if_present(tracker, old_metadata, new_metadata, "method metadata")

old_decay = '''    default_decay_points_per_day = max(\n        0.0, float(config.get("condition_decay_percentage_points_per_day", 0.8))\n    )\n    if canyon.canyon_id == "zerog":\n        current_loss_components = zero_g_loss_components(now)\n        decay_points_per_day = float(\n            current_loss_components["percentage_points_per_day"]\n        )\n    else:\n        current_loss_components = None\n        decay_points_per_day = default_decay_points_per_day\n\n    def apply_decay(value: float, start: datetime, end: datetime) -> float:\n        if canyon.canyon_id == "zerog":\n            loss_ratio = zero_g_integrated_loss_ratio(start, end)\n        else:\n            elapsed_days = max(0.0, (end - start).total_seconds() / 86400.0)\n            loss_ratio = default_decay_points_per_day / 100.0 * elapsed_days\n        return max(0.0, value - loss_ratio)\n'''
new_decay = '''    current_loss_components = zero_g_loss_components(now)\n    decay_points_per_day = float(\n        current_loss_components["percentage_points_per_day"]\n    )\n\n    def apply_decay(value: float, start: datetime, end: datetime) -> float:\n        loss_ratio = zero_g_integrated_loss_ratio(start, end)\n        return max(0.0, value - loss_ratio)\n'''
tracker = replace_if_present(tracker, old_decay, new_decay, "all-canyon condition decay")

old_condition = '''    if canyon.canyon_id == "zerog" and current_loss_components is not None:\n        loss_fields = {\n            "loss_model": "zero_g_mx2001_et_plus_navajo",\n            "decay_percentage_points_per_day": round(decay_points_per_day, 3),\n            "eto_inches_per_day": round(\n                float(current_loss_components["eto_inches_per_day"]), 3\n            ),\n            "navajo_seepage_inches_per_day": round(\n                float(current_loss_components["navajo_seepage_inches_per_day"]), 3\n            ),\n            "total_loss_inches_per_day": round(\n                float(current_loss_components["total_loss_inches_per_day"]), 3\n            ),\n            "reference_pool_depth_ft": ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT,\n            "retention_class": "Zero G stable-lower-MX2001 field calibration",\n            "loss_calibration_note": (\n                "Central rate uses the unmoved lower logger. Upper logger moved on "\n                "2026-08-08 and is retained only as an exposed/high-loss bound."\n            ),\n        }\n    else:\n        loss_fields = {\n            "loss_model": "provisional_linear_decay",\n            "decay_percentage_points_per_day": decay_points_per_day,\n            "retention_class": "provisional reference-canyon field calibration",\n        }\n'''
new_condition = '''    loss_fields = {\n        "loss_model": "zero_g_mx2001_et_plus_navajo",\n        "decay_percentage_points_per_day": round(decay_points_per_day, 3),\n        "eto_inches_per_day": round(\n            float(current_loss_components["eto_inches_per_day"]), 3\n        ),\n        "navajo_seepage_inches_per_day": round(\n            float(current_loss_components["navajo_seepage_inches_per_day"]), 3\n        ),\n        "total_loss_inches_per_day": round(\n            float(current_loss_components["total_loss_inches_per_day"]), 3\n        ),\n        "reference_pool_depth_ft": ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT,\n        "retention_class": "Transferred Zero G stable-lower-MX2001 reference calibration",\n        "loss_calibration_note": (\n            "Reference rate comes from the unmoved lower Zero G logger; the upper "\n            "logger moved on 2026-08-08 and is retained only as an exposed/high-loss "\n            "bound. This reference recession is transferred to all modeled canyons."\n        ),\n    }\n'''
tracker = replace_if_present(tracker, old_condition, new_condition, "all-canyon condition metadata")
TRACKER.write_text(tracker, encoding="utf-8")

app = APP.read_text(encoding="utf-8")
old_app = '''      ${condition.loss_model === "zero_g_mx2001_et_plus_navajo"\n        ? `Reference-canyon loss is field-calibrated from the stable lower MX2001 logger: ${number(condition.navajo_seepage_inches_per_day, 2)} in/day Navajo/seepage-equivalent loss + ${number(condition.eto_inches_per_day, 2)} in/day seasonal ETo = ${number(condition.total_loss_inches_per_day, 2)} in/day currently (${number(condition.decay_percentage_points_per_day, 2)} stage-equivalent percentage points/day).`\n        : `The provisional condition percentage decreases ${number(condition.decay_percentage_points_per_day || 0.8, 1)} point per day.`}\n'''
new_app = '''      ${condition.loss_model === "zero_g_mx2001_et_plus_navajo"\n        ? `All-canyon loss uses the transferred Zero G stable-lower-MX2001 reference: ${number(condition.navajo_seepage_inches_per_day, 2)} in/day empirical Navajo/seepage-equivalent loss + ${number(condition.eto_inches_per_day, 2)} in/day seasonal Moab ETo = ${number(condition.total_loss_inches_per_day, 2)} in/day currently (${number(condition.decay_percentage_points_per_day, 2)} stage-equivalent percentage points/day). The Zero G reference uses an 11.9288-ft lower-logger water column and is applied to every canyon until canyon-specific recession data exist.`\n        : `Pool-loss calibration unavailable.`}\n'''
app = replace_if_present(app, old_app, new_app, "frontend condition explanation")
APP.write_text(app, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
old_test = '''        self.assertIn("MX2001", method["cumulative_refill_explanation"])\n        self.assertIn("1.28 in/day", method["pool_loss_explanation"])\n        self.assertIn("August 8", method["pool_loss_explanation"])\n'''
new_test = '''        self.assertIn("All canyon", method["cumulative_refill_explanation"])\n        self.assertIn("1.28 in/day", method["pool_loss_explanation"])\n        self.assertIn("all 22 modeled canyons", method["pool_loss_explanation"])\n        self.assertIn("August 8", method["pool_loss_explanation"])\n'''
test = replace_if_present(test, old_test, new_test, "metadata tests")
TEST.write_text(test, encoding="utf-8")

event_test = EVENT_TEST.read_text(encoding="utf-8")
old_unknown = '''        self.assertIsNone(condition["percent"])\n        self.assertEqual(condition["current_condition"], "unknown")\n        self.assertEqual(condition["confidence"], "Unknown")\n'''
new_unknown = '''        self.assertIsNone(condition["percent"])\n        self.assertEqual(condition["current_condition"], "unknown")\n        self.assertEqual(condition["confidence"], "Unknown")\n        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")\n        self.assertAlmostEqual(condition["navajo_seepage_inches_per_day"], 1.28, places=2)\n        self.assertAlmostEqual(condition["decay_percentage_points_per_day"], 1.07, delta=0.05)\n'''
event_test = replace_if_present(event_test, old_unknown, new_unknown, "non-Zero-G transfer test")
EVENT_TEST.write_text(event_test, encoding="utf-8")

print("Applied transferred Zero G MX2001 + ETo loss reference to all modeled canyons")
