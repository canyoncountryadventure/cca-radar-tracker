#!/usr/bin/env python3
"""Finalize the field-calibrated Zero G reference used by all canyon balances.

Zero G lower-pool field observations bracket operational full stage:
- about 12 ft: nearly full
- 13.66 ft: actively spilling

Until the spill crest is surveyed, 13.0 ft is the operational 100% reference.
The latest lower MX2001 observation on 2026-09-07 is about 9.43 ft, or 72.5%
of that reference. The physical recession terms remain 1.28 in/day empirical
Navajo/seepage-equivalent loss plus monthly Moab reference ETo; only the
stage-to-percent conversion changes. That seasonal percentage recession is
transferred to all 22 modeled canyons.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, text: str) -> None:
    (ROOT / relative).write_text(text, encoding="utf-8")


def replace_required(text: str, old: str, new: str, label: str) -> str:
    """Replace exactly once, while remaining idempotent after installation."""
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one old match, found {count}")
    return text.replace(old, new, 1)


# Core recession model.
loss = read("loss_model.py")
loss = loss.replace(
    "the stable lower Zero G logger's\ninitial 11.9288-ft water column",
    "the 13.0-ft operational full-stage reference for the lower Zero G pool",
)
loss = loss.replace(
    "ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT = 11.9288",
    "ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT = 13.0",
)
loss = loss.replace(
    "Percentage-point conversion uses the stable lower logger's initial\n    11.9288-ft water column as the field reference depth.",
    "Percentage-point conversion uses the 13.0-ft operational full-stage\n    reference. Field observations bracket full stage: about 12 ft was nearly full\n    and 13.66 ft was actively spilling.",
)
if "ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT = 13.0" not in loss:
    raise RuntimeError("loss_model.py did not receive the 13.0-ft reference")
if "11.9288" in loss:
    raise RuntimeError("loss_model.py still contains the superseded 11.9288-ft reference")
write("loss_model.py", loss)


# Zero G current condition: replace the original Aug. 1 anchor with the latest
# lower logger observation. Radar/recession can evolve condition after this time.
tracker = read("tracker.py")
old_anchor = '''FIELD_CONDITION_ANCHORS: dict[str, dict[str, Any]] = {
    "zerog": {
        "observed_utc": "2026-08-01T12:00:00Z",
        "percent": 98,
        "description": "Field verified throughout the technical section",
        "notes": "Two water-level loggers installed: one persistent half-shaded pool and one fast-drying full-sun pool.",
    },
}'''
new_anchor = '''FIELD_CONDITION_ANCHORS: dict[str, dict[str, Any]] = {
    "zerog": {
        "observed_utc": "2026-09-07T20:43:00Z",
        "percent": 72.5,
        "observed_depth_ft": 9.43,
        "operational_full_depth_ft": 13.0,
        "spill_observed_depth_ft": 13.66,
        "description": "Lower MX2001 field calibration: 9.43 ft observed; 13.0 ft operational full stage",
        "notes": "Field observations bracket full stage: the logger was installed near 12 ft when the pool was nearly full, while 13.66 ft was actively spilling. Use 13.0 ft as the operational 100% reference until the spill crest is surveyed.",
    },
}'''
tracker = replace_required(tracker, old_anchor, new_anchor, "Zero G field anchor")
tracker = tracker.replace(
    "the lower logger's initial 11.9288-ft water column.",
    "the 13.0-ft operational full-stage reference, bracketed by field observations of about 12 ft nearly full and 13.66 ft actively spilling.",
)
if '"percent": 72.5' not in tracker:
    raise RuntimeError("tracker.py does not contain the Sept. 7 Zero G field anchor")
if "11.9288" in tracker:
    raise RuntimeError("tracker.py still contains the superseded 11.9288-ft reference")
write("tracker.py", tracker)


# Front-end Methods panel.
app = read("docs/app.js")
old_methods = '''    <p><strong>Operational equation:</strong> 1.28 in/day empirical Navajo/seepage-equivalent residual + Utah State University Moab monthly reference ETo normals (2000-2022). The conversion to condition loss uses the lower logger's initial 11.9288-ft water column. The resulting seasonal percentage-point loss is transferred to all 22 canyons, including cumulative balances between storms and loss after the latest storm.</p>'''
new_methods = '''    <p><strong>Operational equation:</strong> 1.28 in/day empirical Navajo/seepage-equivalent residual + Utah State University Moab monthly reference ETo normals (2000-2022). The conversion to condition loss now uses a <strong>13.0-ft operational full-stage reference</strong> for the lower Zero G pool. Field observations bracket that reference: about 12 ft was nearly full and 13.66 ft was actively spilling. The Sept. 7 lower-logger reading of about 9.43 ft is therefore 72.5% of operational full stage. The resulting seasonal percentage-point loss is transferred to all 22 canyons, including cumulative balances between storms and loss after the latest storm.</p>'''
app = replace_required(app, old_methods, new_methods, "front-end Methods calibration")
if "11.9288" in app:
    raise RuntimeError("docs/app.js still contains the superseded 11.9288-ft reference")
write("docs/app.js", app)


# Repository README.
readme = read("README.md")
readme = replace_required(
    readme,
    "The percentage-point conversion uses the stable lower Zero G logger's initial **11.9288-ft water column**:",
    "The percentage-point conversion uses a field-calibrated **13.0-ft operational full-stage reference** for the lower Zero G pool. The user observed the pool near 12 ft when it was nearly full and personally observed **13.66 ft actively spilling**, so 13.0 ft is used as the operational 100% reference until the spill crest is surveyed:",
    "README full-stage basis",
)
readme = readme.replace("÷ (11.9288 ft × 12 in/ft)", "÷ (13.0 ft × 12 in/ft)")
readme = readme.replace(
    "decay ≈ **1.07 percentage points/day**.",
    "decay ≈ **0.981 percentage points/day**.",
)
readme = readme.replace(
    "decay ≈ **1.03 percentage points/day**.",
    "decay ≈ **0.943 percentage points/day**.",
)
readme = readme.replace(
    "the 11.9288-ft reference water column, USU Moab ETo normals,",
    "the 13.0-ft operational full-stage reference (12 ft nearly full; 13.66 ft spilling), USU Moab ETo normals,",
)
readme = readme.replace(
    "- Zero G field anchor: 98% full on August 1, 2026",
    "- Zero G field anchor: lower MX2001 about 9.43 ft on September 7, 2026 = 72.5% of the 13.0-ft operational full-stage reference; 13.66 ft is recorded as observed spill stage",
)
if "**0.981 percentage points/day**" not in readme:
    raise RuntimeError("README August recession example not updated")
if "**0.943 percentage points/day**" not in readme:
    raise RuntimeError("README September recession example not updated")
if "11.9288" in readme:
    raise RuntimeError("README still contains the superseded 11.9288-ft reference")
write("README.md", readme)


# Tests lock the new reference and exact recalculated rates.
test_loss = read("tests/test_loss_model.py")
test_loss = test_loss.replace(
    'self.assertAlmostEqual(values["percentage_points_per_day"], 1.07, delta=0.05)',
    'self.assertAlmostEqual(values["percentage_points_per_day"], 0.98139, places=5)\n        self.assertEqual(values["reference_pool_depth_ft"], 13.0)',
)
if "0.98139" not in test_loss:
    raise RuntimeError("test_loss_model.py was not updated")
write("tests/test_loss_model.py", test_loss)

frontend = read("tests/test_frontend_contract.py")
frontend = frontend.replace(
    'self.assertIn("11.9288-ft", self.app)',
    'self.assertIn("13.0-ft operational full-stage reference", self.app)',
)
write("tests/test_frontend_contract.py", frontend)

tracker_test = read("tests/test_tracker.py")
needle = '        self.assertIn("1.28 in/day", method["pool_loss_explanation"])\n'
addition = (
    needle
    + '        self.assertIn("13.0-ft operational full-stage reference", method["pool_loss_explanation"])\n'
    + '        self.assertIn("13.66 ft actively spilling", method["pool_loss_explanation"])\n'
)
if 'self.assertIn("13.0-ft operational full-stage reference", method["pool_loss_explanation"])' not in tracker_test:
    if needle not in tracker_test:
        raise RuntimeError("test_tracker.py insertion point missing")
    tracker_test = tracker_test.replace(needle, addition, 1)
write("tests/test_tracker.py", tracker_test)


print(
    "Zero G recalibrated: 13.0-ft operational full stage, Sept. 7 lower logger "
    "anchor 9.43 ft = 72.5%; transferred recession remains 1.28 in/day + Moab ETo."
)
