#!/usr/bin/env python3
"""Finish front-end wording for positive-runoff recession clocks."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "docs" / "app.js"
text = path.read_text(encoding="utf-8")

old_summary = '''      <span class="summary-date">${condition.last_meaningful_refill_utc ? `Last meaningful: ${summaryDateTime(condition.last_meaningful_refill_utc)}` : "No meaningful refill"}</span>'''
new_summary = '''      <span class="summary-date">${(condition.last_refill_utc || condition.last_meaningful_refill_utc) ? `Last modeled refill: ${summaryDateTime(condition.last_refill_utc || condition.last_meaningful_refill_utc)}` : "No modeled refill"}</span>'''
if old_summary in text:
    text = text.replace(old_summary, new_summary, 1)
elif new_summary not in text:
    raise RuntimeError("summary refill label insertion point missing")

old_clock = '''    <p><strong>Per-canyon clock:</strong> every retained rain event with <strong>positive modeled runoff</strong> advances that canyon's recession clock, even if the refill is below 25%. Loss is applied to the pre-storm balance through that event, the modeled refill is added, and recession resumes from the storm time. The 25% threshold remains a condition/classification band only.</p>'''
new_clock = '''    <p><strong>Per-canyon clock:</strong> every retained rain event with <strong>positive modeled runoff</strong> advances that canyon's recession clock, even if the refill is below 25%. Loss is applied to the pre-storm balance through that event, the modeled refill is added, and recession resumes from the storm time. The same time-integrated loss is used for cumulative balances between storms and from the latest refill through the present. The 25% threshold remains a condition/classification band only.</p>'''
if old_clock in text:
    text = text.replace(old_clock, new_clock, 1)
elif new_clock not in text:
    raise RuntimeError("Methods cumulative-balance wording insertion point missing")

old_interpretation = '''    <p><strong>Interpretation:</strong> this is a reference-transfer assumption for operations, not evidence that every canyon has the same fractures, pool geometry, shade, evaporation, or seepage. Replace it canyon-by-canyon when better recession data become available.</p>'''
new_interpretation = '''    <p><strong>Interpretation:</strong> this seasonal recession reference is transferred to all 22 canyons as an operational assumption, not evidence that every canyon has the same fractures, pool geometry, shade, evaporation, or seepage. Replace it canyon-by-canyon when better recession data become available.</p>'''
if old_interpretation in text:
    text = text.replace(old_interpretation, new_interpretation, 1)
elif new_interpretation not in text:
    raise RuntimeError("Methods all-22-canyons wording insertion point missing")

path.write_text(text, encoding="utf-8")
print("Updated positive-runoff timing, cumulative-balance wording, and all-22-canyons Methods disclosure.")
