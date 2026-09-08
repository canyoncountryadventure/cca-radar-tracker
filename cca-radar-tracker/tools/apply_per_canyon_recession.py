#!/usr/bin/env python3
"""Apply per-canyon refill clocks and explicit 12-month recession references.

Operational intent:
- Any retained rain event with positive modeled runoff advances that canyon's
  condition/recession clock, even when the refill is below the 25% classification
  threshold.
- Recession remains the field-calibrated Zero G lower-pool residual (1.28 in/day)
  plus monthly Moab reference ETo, converted with the 13.0-ft operational full
  stage. This preserves the logger result that temperature alone is not a
  defensible recession predictor while still representing season/temperature,
  solar loading, day length, and atmospheric demand through monthly ETo.
- The active calculation is integrated across calendar-month boundaries and is
  transferred to all 22 canyons until canyon-specific recession data exist.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def write(relative: str, text: str) -> None:
    (ROOT / relative).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# loss_model.py: expose the complete monthly reference table from the same
# active equation already used to integrate loss across month boundaries.
# ---------------------------------------------------------------------------
loss = read("loss_model.py")
monthly_function = '''\n\ndef zero_g_monthly_recession_table(year: int = 2026) -> list[dict[str, float | int | str]]:\n    """Return the 12-month transferred recession reference for ``year``.\n\n    The 1.28 in/day field residual is held constant because the paired logger\n    record does not support a defensible temperature-only seepage coefficient.\n    Seasonality is represented by monthly Moab reference ETo, which captures\n    the atmospheric effects of temperature, solar loading, day length, and\n    seasonal evaporative demand. February is recomputed for leap years.\n    """\n    rows: list[dict[str, float | int | str]] = []\n    for month in range(1, 13):\n        reference = datetime(year, month, 15)\n        values = zero_g_loss_components(reference)\n        rows.append(\n            {\n                "month": month,\n                "month_name": calendar.month_name[month],\n                "eto_inches_per_month": MOAB_MONTHLY_ETO_INCHES[month],\n                "eto_inches_per_day": round(float(values["eto_inches_per_day"]), 4),\n                "total_loss_inches_per_day": round(float(values["total_loss_inches_per_day"]), 4),\n                "percentage_points_per_day": round(float(values["percentage_points_per_day"]), 4),\n            }\n        )\n    return rows\n'''
if "def zero_g_monthly_recession_table(" not in loss:
    marker = "\n\ndef _next_month_start(reference: datetime) -> datetime:\n"
    if marker not in loss:
        raise RuntimeError("loss_model.py monthly-table insertion point missing")
    loss = loss.replace(marker, monthly_function + marker, 1)
write("loss_model.py", loss)


# ---------------------------------------------------------------------------
# tracker.py: every positive-runoff event becomes the current condition basis.
# ---------------------------------------------------------------------------
tracker = read("tracker.py")
tracker = tracker.replace(
    "    zero_g_loss_components,\n)",
    "    zero_g_loss_components,\n    zero_g_monthly_recession_table,\n)",
    1,
)

old_refill_basis = '''    anchor = FIELD_CONDITION_ANCHORS.get(canyon.canyon_id)\n    meaningful_events = [\n        event for event in events if float(event.get("fill_ratio") or 0.0) >= 0.25\n    ]\n    last_meaningful = meaningful_events[-1] if meaningful_events else None\n    last_meaningful_utc = (\n        last_meaningful.get("end_utc") or last_meaningful.get("start_utc")\n        if last_meaningful\n        else None\n    )\n'''
new_refill_basis = '''    anchor = FIELD_CONDITION_ANCHORS.get(canyon.canyon_id)\n    # Current-condition recession is reset by any event that actually adds\n    # modeled water. The 25% threshold remains a classification/display band;\n    # it is not a gate on the recession clock.\n    refill_events = [\n        event\n        for event in events\n        if float(\n            event.get("direct_runoff_ft3", event.get("estimated_runoff_ft3", 0.0))\n            or 0.0\n        ) > 0.0\n    ]\n    last_refill = refill_events[-1] if refill_events else None\n    last_refill_utc = (\n        last_refill.get("end_utc") or last_refill.get("start_utc")\n        if last_refill\n        else None\n    )\n'''
tracker = replace_once(tracker, old_refill_basis, new_refill_basis, "positive-runoff basis")
tracker = tracker.replace("    elif last_meaningful:\n        basis_time = event_end_utc(last_meaningful)\n", "    elif last_refill:\n        basis_time = event_end_utc(last_refill)\n", 1)
tracker = tracker.replace("    if last_meaningful:\n        confidence_time = max(confidence_time, event_end_utc(last_meaningful))\n", "    if last_refill:\n        confidence_time = max(confidence_time, event_end_utc(last_refill))\n", 1)
tracker = tracker.replace("    if not anchor and not last_meaningful:\n", "    if not anchor and not last_refill:\n", 1)
tracker = tracker.replace(
    '        "last_meaningful_refill_utc": last_meaningful_utc,\n',
    '        "last_refill_utc": last_refill_utc,\n        # Backward-compatible alias for older front ends.\n        "last_meaningful_refill_utc": last_refill_utc,\n',
    1,
)

old_cumulative = '''            "cumulative_refill_explanation": (\n                "All canyon condition balances and cumulative refill balances use the same "\n                "transferred Zero G reference recession between storms and from the most recent "\n                "storm through the present. The reference combines 1.28 inches/day of empirical "\n                "Navajo sandstone/seepage-equivalent loss with monthly Moab reference ETo. "\n                "New modeled runoff is added after time-integrated loss and storage is capped "\n                "at 100%."\n            ),\n'''
new_cumulative = '''            "cumulative_refill_explanation": (\n                "All canyon condition balances and cumulative refill balances use the same "\n                "transferred Zero G reference recession. Every retained rain event with positive "\n                "modeled runoff advances that canyon's recession clock, even when the refill is "\n                "below the 25% classification threshold. Loss is integrated between successive "\n                "refill events and from the latest refill through the present. The reference "\n                "combines a 1.28 inches/day empirical Navajo sandstone/seepage-equivalent residual "\n                "with monthly Moab reference ETo; new modeled runoff is then added and storage is "\n                "capped at 100%."\n            ),\n'''
tracker = replace_once(tracker, old_cumulative, new_cumulative, "cumulative explanation")

old_pool_tail = '''                "explicit transfer assumption, not a claim that every canyon has identical pool "\n                "geometry, evaporation, fractures, or seepage."\n            ),\n            "atlas_explanation": (\n'''
new_pool_tail = '''                "explicit transfer assumption, not a claim that every canyon has identical pool "\n                "geometry, evaporation, fractures, or seepage. Temperature alone is not used as "\n                "a recession predictor because the logger record showed large rate differences at "\n                "similar temperatures when geometry/logger position changed; monthly ETo is the "\n                "seasonal atmospheric term, while the 1.28 in/day residual remains constant until "\n                "field data support a seasonal seepage function."\n            ),\n            "monthly_recession_reference": zero_g_monthly_recession_table(),\n            "atlas_explanation": (\n'''
tracker = replace_once(tracker, old_pool_tail, new_pool_tail, "monthly metadata")

if "last_meaningful" in tracker[tracker.find("# Current-condition recession is reset"):tracker.find("def rebuild_events_from_ledger")]:
    # Only the compatibility JSON field name is allowed to retain the word.
    segment = tracker[tracker.find("# Current-condition recession is reset"):tracker.find("def rebuild_events_from_ledger")]
    residue = segment.replace('"last_meaningful_refill_utc"', '"compat_refill_utc"')
    if "last_meaningful" in residue:
        raise RuntimeError("stale >=25% recession-basis variable remains")
write("tracker.py", tracker)


# ---------------------------------------------------------------------------
# README.md: explicit 12-month reference rates and the positive-runoff clock.
# ---------------------------------------------------------------------------
readme = read("README.md")
monthly_table = '''\nThe current **12-month transferred recession reference** is:\n\n| Month | Moab ETo (in/day) | Total stage-equivalent loss (in/day) | Recession (percentage points/day) |\n|---|---:|---:|---:|\n| January | 0.0374 | 1.3174 | 0.8445 |\n| February | 0.0643 | 1.3443 | 0.8617 |\n| March | 0.1145 | 1.3945 | 0.8939 |\n| April | 0.1700 | 1.4500 | 0.9295 |\n| May | 0.2310 | 1.5110 | 0.9686 |\n| June | 0.2833 | 1.5633 | 1.0021 |\n| July | 0.2913 | 1.5713 | 1.0072 |\n| August | 0.2510 | 1.5310 | 0.9814 |\n| September | 0.1907 | 1.4707 | 0.9427 |\n| October | 0.1132 | 1.3932 | 0.8931 |\n| November | 0.0600 | 1.3400 | 0.8590 |\n| December | 0.0342 | 1.3142 | 0.8424 |\n\nThese are the 2026 daily reference values from the active equation. The code recomputes the daily monthly-normal rate from the actual number of days in each month, including leap-year February. The **1.28 in/day residual is held constant** because the logger record does not support a defensible temperature-only seepage coefficient. Season/temperature, solar loading, day length, and atmospheric demand are represented through monthly Moab ETo. This follows the field result that temperature alone could not explain the recession differences observed after logger/geometry changes.\n\nFor each canyon, **any retained rain event with positive modeled runoff resets the recession clock to that event time**. Its runoff is added to the pre-event balance after loss to that time; recession then resumes from that new balance. The 25% refill threshold remains a classification band only and no longer blocks the current-condition clock from advancing.\n'''
if "12-month transferred recession reference" not in readme:
    marker = "That same seasonal stage-equivalent percentage loss is now applied to **every canyon**."
    if marker not in readme:
        raise RuntimeError("README monthly table insertion point missing")
    readme = readme.replace(marker, monthly_table + "\n" + marker, 1)
write("README.md", readme)


# ---------------------------------------------------------------------------
# docs/app.js: render monthly model metadata and show latest positive refill.
# ---------------------------------------------------------------------------
app = read("docs/app.js")
app = app.replace(
    '    : "No field observation or meaningful modeled refill is available. This estimate describes current canyon conditions; selecting an old storm does not replace it.";',
    '    : "No field observation or positive modeled-runoff rain event is available. This estimate describes current canyon conditions; selecting an old storm does not replace it.";',
    1,
)
app = app.replace(
    '${eventMeta("Last meaningful refill", condition.last_meaningful_refill_utc ? dateTime(condition.last_meaningful_refill_utc) : "None")}',
    '${eventMeta("Last modeled refill", (condition.last_refill_utc || condition.last_meaningful_refill_utc) ? dateTime(condition.last_refill_utc || condition.last_meaningful_refill_utc) : "None")}',
    1,
)

old_methods_start = '''function renderMethods() {\n  const method = app.model.method || {};\n  const classifications = method.classification || {};\n  const sources = method.sources || [];\n  const limitations = method.limitations || [];\n'''
new_methods_start = '''function renderMethods() {\n  const method = app.model.method || {};\n  const classifications = method.classification || {};\n  const sources = method.sources || [];\n  const limitations = method.limitations || [];\n  const monthlyRecession = Array.isArray(method.monthly_recession_reference)\n    ? method.monthly_recession_reference\n    : [];\n  const monthlyRecessionRows = monthlyRecession.map((row) => `\n    <tr>\n      <td>${escapeHtml(row.month_name || String(row.month || ""))}</td>\n      <td>${number(row.eto_inches_per_day, 4)}</td>\n      <td>${number(row.total_loss_inches_per_day, 4)}</td>\n      <td>${number(row.percentage_points_per_day, 4)}</td>\n    </tr>\n  `).join("");\n'''
app = replace_once(app, old_methods_start, new_methods_start, "Methods monthly rows")

old_methods_para = '''    <p><strong>Operational equation:</strong> 1.28 in/day empirical Navajo/seepage-equivalent residual + Utah State University Moab monthly reference ETo normals (2000-2022). The conversion to condition loss now uses a <strong>13.0-ft operational full-stage reference</strong> for the lower Zero G pool. Field observations bracket that reference: about 12 ft was nearly full and 13.66 ft was actively spilling. The Sept. 7 lower-logger reading of about 9.43 ft is therefore 72.5% of operational full stage. The resulting seasonal percentage-point loss is transferred to all 22 canyons, including cumulative balances between storms and loss after the latest storm.</p>\n    <p><strong>Interpretation:</strong> this is a reference-transfer assumption for operations, not evidence that every canyon has the same fractures, pool geometry, shade, evaporation, or seepage. Replace it canyon-by-canyon when better recession data become available.</p>\n'''
new_methods_para = '''    <p><strong>Operational equation:</strong> 1.28 in/day empirical Navajo/seepage-equivalent residual + Utah State University Moab monthly reference ETo normals (2000-2022). The conversion to condition loss uses a <strong>13.0-ft operational full-stage reference</strong> for the lower Zero G pool. Field observations bracket that reference: about 12 ft was nearly full and 13.66 ft was actively spilling. The Sept. 7 lower-logger reading of about 9.43 ft is therefore 72.5% of operational full stage.</p>\n    <p><strong>Per-canyon clock:</strong> every retained rain event with <strong>positive modeled runoff</strong> advances that canyon's recession clock, even if the refill is below 25%. Loss is applied to the pre-storm balance through that event, the modeled refill is added, and recession resumes from the storm time. The 25% threshold remains a condition/classification band only.</p>\n    <p><strong>Seasonal logic:</strong> temperature alone is not used because the paired logger record showed materially different recession rates at similar temperatures when logger position/geometry changed. The 1.28 in/day lower-pool residual is therefore held constant with current evidence, while monthly Moab ETo supplies the seasonal atmospheric response to temperature, solar loading, day length, and evaporative demand.</p>\n    <div class="table-wrap">\n      <table>\n        <thead><tr><th>Month</th><th>ETo in/day</th><th>Total loss in/day</th><th>Recession pp/day</th></tr></thead>\n        <tbody>${monthlyRecessionRows || `<tr><td colspan="4">Monthly recession reference unavailable.</td></tr>`}</tbody>\n      </table>\n    </div>\n    <p><strong>Interpretation:</strong> this is a reference-transfer assumption for operations, not evidence that every canyon has the same fractures, pool geometry, shade, evaporation, or seepage. Replace it canyon-by-canyon when better recession data become available.</p>\n'''
app = replace_once(app, old_methods_para, new_methods_para, "Methods seasonal logic")
write("docs/app.js", app)


# ---------------------------------------------------------------------------
# Tests: lock 12-month rates and below-25% refill-clock behavior.
# ---------------------------------------------------------------------------
test_loss = read("tests/test_loss_model.py")
monthly_test = '''\n    def test_monthly_recession_reference_has_all_twelve_months(self):\n        rows = loss_model.zero_g_monthly_recession_table(2026)\n        self.assertEqual(len(rows), 12)\n        by_month = {row["month"]: row for row in rows}\n        self.assertAlmostEqual(by_month[1]["percentage_points_per_day"], 0.8445, places=4)\n        self.assertAlmostEqual(by_month[7]["percentage_points_per_day"], 1.0072, places=4)\n        self.assertAlmostEqual(by_month[9]["percentage_points_per_day"], 0.9427, places=4)\n        self.assertGreater(by_month[7]["percentage_points_per_day"], by_month[1]["percentage_points_per_day"])\n\n'''
if "test_monthly_recession_reference_has_all_twelve_months" not in test_loss:
    marker = "    def test_integrated_loss_crosses_month_boundary(self):\n"
    if marker not in test_loss:
        raise RuntimeError("test_loss_model.py insertion point missing")
    test_loss = test_loss.replace(marker, monthly_test + marker, 1)
write("tests/test_loss_model.py", test_loss)

accum = read("tests/test_event_accumulation.py")
refill_test = '''\n    def test_small_positive_runoff_event_advances_recession_clock(self):\n        canyon = canyon_fixture(fill_target=100)\n        canyon.canyon_id = "unobserved"\n        canyon.name = "Unobserved"\n        status = tracker.empty_canyon_status(canyon)\n        first = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)\n        latest = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)\n        now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)\n        status["events"] = [\n            {\n                "start_utc": tracker.utc_text(first),\n                "end_utc": tracker.utc_text(first),\n                "direct_runoff_ft3": 60,\n                "fill_ratio": 0.60,\n            },\n            {\n                "start_utc": tracker.utc_text(latest),\n                "end_utc": tracker.utc_text(latest),\n                "direct_runoff_ft3": 5,\n                "fill_ratio": 0.05,\n            },\n        ]\n        tracker.cumulative_refill_evidence(status, canyon, self.config, now_utc=now)\n        condition = status["condition_estimate"]\n        self.assertEqual(condition["basis"], "Modeled refill history")\n        self.assertEqual(condition["basis_utc"], tracker.utc_text(latest))\n        self.assertEqual(condition["last_refill_utc"], tracker.utc_text(latest))\n        self.assertEqual(condition["last_meaningful_refill_utc"], tracker.utc_text(latest))\n        self.assertIsNotNone(condition["percent"])\n        self.assertLess(condition["percent"], 65)\n        self.assertGreater(condition["percent"], 50)\n\n'''
if "test_small_positive_runoff_event_advances_recession_clock" not in accum:
    marker = "    def test_moving_storm_core_accumulates_at_its_actual_pixels(self):\n"
    if marker not in accum:
        raise RuntimeError("test_event_accumulation.py insertion point missing")
    accum = accum.replace(marker, refill_test + marker, 1)
write("tests/test_event_accumulation.py", accum)

frontend = read("tests/test_frontend_contract.py")
if 'self.assertIn("Last modeled refill", self.app)' not in frontend:
    marker = '        self.assertIn("All-canyon pool-loss reference", self.app)\n'
    addition = marker + '        self.assertIn("Last modeled refill", self.app)\n        self.assertIn("positive modeled runoff", self.app)\n        self.assertIn("Seasonal logic", self.app)\n'
    if marker not in frontend:
        raise RuntimeError("frontend test insertion point missing")
    frontend = frontend.replace(marker, addition, 1)
write("tests/test_frontend_contract.py", frontend)

tracker_test = read("tests/test_tracker.py")
if 'self.assertEqual(len(method["monthly_recession_reference"]), 12)' not in tracker_test:
    marker = '        self.assertIn("all 22 modeled canyons", method["pool_loss_explanation"])\n'
    addition = marker + '        self.assertEqual(len(method["monthly_recession_reference"]), 12)\n        self.assertIn("positive modeled runoff", method["cumulative_refill_explanation"])\n'
    if marker not in tracker_test:
        raise RuntimeError("tracker metadata test insertion point missing")
    tracker_test = tracker_test.replace(marker, addition, 1)
write("tests/test_tracker.py", tracker_test)

print("Applied positive-runoff per-canyon recession clocks and explicit 12-month seasonal recession reference.")
