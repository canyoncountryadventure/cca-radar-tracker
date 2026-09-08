#!/usr/bin/env python3
"""Install the Zero G logger-calibrated ET + Navajo loss model into tracker/UI."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "tracker.py"
APP = ROOT / "docs" / "app.js"
TEST = ROOT / "tests" / "test_tracker.py"
EVENT_TEST = ROOT / "tests" / "test_event_accumulation.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


tracker = TRACKER.read_text(encoding="utf-8")
tracker = replace_once(
    tracker,
    "from PIL import Image, ImageDraw\n\nROOT = Path(__file__).resolve().parent\n",
    "from PIL import Image, ImageDraw\n\nfrom loss_model import (\n"
    "    ZERO_G_NAVAJO_SEEPAGE_INCHES_PER_DAY,\n"
    "    ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT,\n"
    "    zero_g_integrated_loss_ratio,\n"
    "    zero_g_loss_components,\n"
    ")\n\nROOT = Path(__file__).resolve().parent\n",
    "tracker loss-model import",
)

old_metadata = '''            "cumulative_refill_explanation": (\n                f"The current condition applies a provisional linear loss of "\n                f"{config.get('condition_decay_percentage_points_per_day', 0.8):g} percentage "\n                "point per day. New modeled runoff is added to the decayed balance and capped "\n                "at 100%. Detailed events and peak radar maps are retained for 90 days."\n            ),\n            "pool_loss_explanation": (\n                f"The temporary {config.get('condition_decay_percentage_points_per_day', 0.8):g}-point daily loss is based on "\n                "Zero G being full on July 29 and field-verified at 98% on August 1. It applies "\n                "to every canyon until logger and field measurements support canyon-specific "\n                "and seasonal recession curves. Confidence also declines as observations age."\n            ),'''
new_metadata = '''            "cumulative_refill_explanation": (\n                "Current conditions lose modeled storage between storms. Zero G now uses an "\n                "MX2001 field-calibrated seasonal loss model: 1.28 inches/day of empirical "\n                "Navajo sandstone/seepage-equivalent recession plus monthly Moab reference "\n                "ETo. Other canyons retain the provisional fixed percentage-point decay until "\n                "their own geology or field data support a canyon-specific loss model. New "\n                "modeled runoff is added to the decayed balance and capped at 100%."\n            ),\n            "pool_loss_explanation": (\n                "Zero G's central loss calibration uses the stable lower MX2001 logger from "\n                "August 1 through September 7, 2026. The upper logger is excluded from the "\n                "central rate because it physically relocated on August 8 and remained a "\n                "shallower, more exposed high-loss comparison. The 1.28 in/day residual is an "\n                "empirical stage-equivalent term after subtracting USU Moab monthly reference "\n                "ETo; it includes Navajo sandstone/fracture seepage and any inseparable quiet-"\n                "pool drainage. The percent conversion uses the lower logger's initial 11.9288-"\n                "ft water column and is therefore a field-calibrated stage-equivalent condition "\n                "model, not a surveyed stage-volume curve."\n            ),'''
tracker = replace_once(tracker, old_metadata, new_metadata, "method metadata")

old_decay = '''    decay_points_per_day = max(\n        0.0, float(config.get("condition_decay_percentage_points_per_day", 0.8))\n    )\n    decay_ratio_per_day = decay_points_per_day / 100.0\n\n    def apply_decay(value: float, start: datetime, end: datetime) -> float:\n        elapsed_days = max(0.0, (end - start).total_seconds() / 86400.0)\n        return max(0.0, value - decay_ratio_per_day * elapsed_days)\n'''
new_decay = '''    default_decay_points_per_day = max(\n        0.0, float(config.get("condition_decay_percentage_points_per_day", 0.8))\n    )\n    if canyon.canyon_id == "zerog":\n        current_loss_components = zero_g_loss_components(now)\n        decay_points_per_day = float(\n            current_loss_components["percentage_points_per_day"]\n        )\n    else:\n        current_loss_components = None\n        decay_points_per_day = default_decay_points_per_day\n\n    def apply_decay(value: float, start: datetime, end: datetime) -> float:\n        if canyon.canyon_id == "zerog":\n            loss_ratio = zero_g_integrated_loss_ratio(start, end)\n        else:\n            elapsed_days = max(0.0, (end - start).total_seconds() / 86400.0)\n            loss_ratio = default_decay_points_per_day / 100.0 * elapsed_days\n        return max(0.0, value - loss_ratio)\n'''
tracker = replace_once(tracker, old_decay, new_decay, "condition decay implementation")

old_condition_tail = '''    canyon_status["condition_estimate"] = {\n        "percent": (\n            min(100, round(condition_ratio * 100))\n            if condition_ratio is not None\n            else None\n        ),\n        "current_condition": current_condition,\n        "confidence": confidence,\n        "confidence_age_days": round(age_days, 1),\n        "confidence_basis_utc": utc_text(confidence_time),\n        "basis": basis,\n        "basis_utc": utc_text(basis_time),\n        "last_verified": last_verified,\n        "last_meaningful_refill_utc": last_meaningful_utc,\n        "loss_model": "provisional_linear_decay",\n        "decay_percentage_points_per_day": decay_points_per_day,\n        "retention_class": "provisional reference-canyon field calibration",\n    }\n'''
new_condition_tail = '''    if canyon.canyon_id == "zerog" and current_loss_components is not None:\n        loss_fields = {\n            "loss_model": "zero_g_mx2001_et_plus_navajo",\n            "decay_percentage_points_per_day": round(decay_points_per_day, 3),\n            "eto_inches_per_day": round(\n                float(current_loss_components["eto_inches_per_day"]), 3\n            ),\n            "navajo_seepage_inches_per_day": round(\n                float(current_loss_components["navajo_seepage_inches_per_day"]), 3\n            ),\n            "total_loss_inches_per_day": round(\n                float(current_loss_components["total_loss_inches_per_day"]), 3\n            ),\n            "reference_pool_depth_ft": ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT,\n            "retention_class": "Zero G stable-lower-MX2001 field calibration",\n            "loss_calibration_note": (\n                "Central rate uses the unmoved lower logger. Upper logger moved on "\n                "2026-08-08 and is retained only as an exposed/high-loss bound."\n            ),\n        }\n    else:\n        loss_fields = {\n            "loss_model": "provisional_linear_decay",\n            "decay_percentage_points_per_day": decay_points_per_day,\n            "retention_class": "provisional reference-canyon field calibration",\n        }\n\n    canyon_status["condition_estimate"] = {\n        "percent": (\n            min(100, round(condition_ratio * 100))\n            if condition_ratio is not None\n            else None\n        ),\n        "current_condition": current_condition,\n        "confidence": confidence,\n        "confidence_age_days": round(age_days, 1),\n        "confidence_basis_utc": utc_text(confidence_time),\n        "basis": basis,\n        "basis_utc": utc_text(basis_time),\n        "last_verified": last_verified,\n        "last_meaningful_refill_utc": last_meaningful_utc,\n        **loss_fields,\n    }\n'''
tracker = replace_once(tracker, old_condition_tail, new_condition_tail, "condition metadata")
TRACKER.write_text(tracker, encoding="utf-8")

app = APP.read_text(encoding="utf-8")
old_app = '''    <p class="event-summary">\n      The percentage decreases ${number(condition.decay_percentage_points_per_day || 0.8, 1)} point per day. New modeled runoff adds to the current balance, capped at 100%; confidence also decreases as the supporting observation ages.\n    </p>'''
new_app = '''    <p class="event-summary">\n      ${condition.loss_model === "zero_g_mx2001_et_plus_navajo"\n        ? `Reference-canyon loss is field-calibrated from the stable lower MX2001 logger: ${number(condition.navajo_seepage_inches_per_day, 2)} in/day Navajo/seepage-equivalent loss + ${number(condition.eto_inches_per_day, 2)} in/day seasonal ETo = ${number(condition.total_loss_inches_per_day, 2)} in/day currently (${number(condition.decay_percentage_points_per_day, 2)} stage-equivalent percentage points/day).`\n        : `The provisional condition percentage decreases ${number(condition.decay_percentage_points_per_day || 0.8, 1)} point per day.`}\n      New modeled runoff adds to the current balance, capped at 100%; confidence also decreases as the supporting observation ages.\n    </p>'''
app = replace_once(app, old_app, new_app, "frontend condition explanation")
APP.write_text(app, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    '        self.assertIn("0.8 percentage point per day", method["cumulative_refill_explanation"])\n        self.assertIn("July 29", method["pool_loss_explanation"])\n',
    '        self.assertIn("MX2001", method["cumulative_refill_explanation"])\n        self.assertIn("1.28 in/day", method["pool_loss_explanation"])\n        self.assertIn("August 8", method["pool_loss_explanation"])\n',
    "metadata tests",
)
TEST.write_text(test, encoding="utf-8")

event_test = EVENT_TEST.read_text(encoding="utf-8")
event_test = replace_once(
    event_test,
    '        self.assertEqual(condition["percent"], 96)\n',
    '        self.assertEqual(condition["percent"], 95)\n',
    "Zero G anchor percent test",
)
event_test = replace_once(
    event_test,
    '        self.assertEqual(condition["loss_model"], "provisional_linear_decay")\n        self.assertEqual(condition["decay_percentage_points_per_day"], 0.8)\n',
    '        self.assertEqual(condition["loss_model"], "zero_g_mx2001_et_plus_navajo")\n        self.assertAlmostEqual(condition["decay_percentage_points_per_day"], 1.07, delta=0.05)\n        self.assertAlmostEqual(condition["navajo_seepage_inches_per_day"], 1.28, places=2)\n',
    "Zero G anchor loss-model test",
)
EVENT_TEST.write_text(event_test, encoding="utf-8")

print("Installed Zero G ET + Navajo loss model into tracker.py and docs/app.js")
