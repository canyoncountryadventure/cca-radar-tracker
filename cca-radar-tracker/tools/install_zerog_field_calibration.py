#!/usr/bin/env python3
"""Install the Zero G logger-derived rainfall/runoff calibration into tracker.py.

The installer is intentionally idempotent. It keeps basin-average NRCS results for
comparison/fallback, uses spatially distributed accumulated radar rainfall for the
Zero G NRCS volume when the event grid is available, and adds an independent
field-observed storm-core response test derived from the Aug 2026 logger anchors.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "tracker.py"
text = TRACKER.read_text(encoding="utf-8")

marker = "ZERO_G_FIELD_CORE_MAJOR_INCHES = 0.20"
if marker in text:
    print("Zero G field calibration already installed")
    raise SystemExit(0)

text = text.replace(
    "STATUS_SCHEMA_VERSION = 5\n",
    "STATUS_SCHEMA_VERSION = 5\n\n"
    "# Zero G field calibration from paired MX2001 logger responses, August 2026.\n"
    "# These are rainfall-core response anchors, independent of the NRCS volume test.\n"
    "ZERO_G_FIELD_CORE_MAJOR_INCHES = 0.20\n"
    "ZERO_G_FIELD_CORE_FLUSH_INCHES = 1.00\n"
    "ZERO_G_FIELD_CALIBRATION_BASIS = (\n"
    "    \"Paired Zero G MX2001 logger anchors: Aug 21 small response at 0.0779 in \"\n"
    "    \"maximum in-basin accumulated radar rain; Aug 12 major refill at 0.2123 in; \"\n"
    "    \"Aug 29 major refill at 0.5101 in; Aug 30 strong flush at 1.1051 in; \"\n"
    "    \"Aug 31 secondary rise at 0.0574 in is not treated as a new-rain refill.\"\n"
    ")\n",
    1,
)

needle = '''def apply_hydrologic_model(
    event: dict[str, Any], canyon: Canyon, config: dict[str, Any]
) -> None:
'''
helper = '''def spatial_nrcs_runoff_depth(
    event: dict[str, Any], canyon: Canyon, curve_number: float
) -> float | None:
    """Area-weight NRCS runoff after applying the nonlinear equation cell by cell.

    This preserves localized slickrock storm cores that can exceed initial
    abstraction even when watershed-average rainfall does not. The field-validated
    production use is currently limited to Zero G; other canyons retain the lumped
    calculation until they have comparable observations.
    """
    values = event.get("accumulated_rain_grid_inches")
    if values is None:
        return None
    rainfall = np.asarray(values, dtype=np.float64)
    if rainfall.shape != canyon.weights.shape:
        return None
    valid = np.isfinite(rainfall)
    weights = np.where(valid, canyon.weights, 0.0)
    denominator = float(weights.sum())
    if denominator <= 0:
        return None
    runoff = np.zeros(rainfall.shape, dtype=np.float64)
    for row, column in zip(*np.where(valid & (canyon.weights > 0))):
        runoff[row, column] = nrcs_runoff_depth(
            float(rainfall[row, column]), curve_number
        )
    return float((runoff * weights).sum() / denominator)


def zero_g_field_core_evidence(
    event: dict[str, Any], canyon: Canyon
) -> dict[str, Any]:
    """Return the independent logger-calibrated Zero G storm-core evidence."""
    result = {
        "available": False,
        "maximum_in_basin_storm_inches": None,
        "major_threshold_inches": ZERO_G_FIELD_CORE_MAJOR_INCHES,
        "flush_threshold_inches": ZERO_G_FIELD_CORE_FLUSH_INCHES,
        "major_threshold_met": False,
        "flush_threshold_met": False,
        "basis": ZERO_G_FIELD_CALIBRATION_BASIS,
    }
    if canyon.canyon_id != "zerog":
        return result
    values = event.get("accumulated_rain_grid_inches")
    if values is None:
        return result
    rainfall = np.asarray(values, dtype=np.float64)
    if rainfall.shape != canyon.weights.shape:
        return result
    mask = np.isfinite(rainfall) & (canyon.weights > 0)
    if not np.any(mask):
        return result
    maximum = float(np.max(rainfall[mask]))
    result.update(
        {
            "available": True,
            "maximum_in_basin_storm_inches": round(maximum, 4),
            "major_threshold_met": maximum >= ZERO_G_FIELD_CORE_MAJOR_INCHES,
            "flush_threshold_met": maximum >= ZERO_G_FIELD_CORE_FLUSH_INCHES,
        }
    )
    return result


'''
if needle not in text:
    raise SystemExit("apply_hydrologic_model marker not found")
text = text.replace(needle, helper + needle, 1)

old_loop = '''        depth = nrcs_runoff_depth(rain, curve_number)
        volume = depth / 12.0 * area_ft2
        base_seconds = max(
            300.0, (duration_hr + 2.0 * lag_hr) * 3600.0
        )
        runoff_depths[state] = round(depth, 4)
        volumes[state] = round(volume)
        peaks[state] = round(2.0 * volume / base_seconds, 2)
'''
new_loop = '''        lumped_depth = nrcs_runoff_depth(rain, curve_number)
        spatial_depth = (
            spatial_nrcs_runoff_depth(event, canyon, curve_number)
            if canyon.canyon_id == "zerog"
            else None
        )
        depth = spatial_depth if spatial_depth is not None else lumped_depth
        volume = depth / 12.0 * area_ft2
        base_seconds = max(
            300.0, (duration_hr + 2.0 * lag_hr) * 3600.0
        )
        runoff_depths[state] = round(depth, 4)
        volumes[state] = round(volume)
        peaks[state] = round(2.0 * volume / base_seconds, 2)
        event.setdefault("lumped_runoff_depth_inches", {})[state] = round(lumped_depth, 4)
        event.setdefault("lumped_direct_runoff_ft3_range", {})[state] = round(
            lumped_depth / 12.0 * area_ft2
        )
        event.setdefault("spatial_runoff_depth_inches", {})[state] = (
            None if spatial_depth is None else round(spatial_depth, 4)
        )
        event.setdefault("spatial_direct_runoff_ft3_range", {})[state] = (
            None
            if spatial_depth is None
            else round(spatial_depth / 12.0 * area_ft2)
        )
'''
if old_loop not in text:
    raise SystemExit("NRCS loop marker not found")
text = text.replace(old_loop, new_loop, 1)

text = text.replace(
    '    event["generated_runoff_ft3_range"] = volumes\n',
    '    event["generated_runoff_ft3_range"] = volumes\n'
    '    event["runoff_spatialization_applied"] = bool(\n'
    '        canyon.canyon_id == "zerog"\n'
    '        and any(value is not None for value in event.get("spatial_runoff_depth_inches", {}).values())\n'
    '    )\n'
    '    event["runoff_spatialization_basis"] = (\n'
    '        "Zero G field-calibrated: NRCS runoff applied cell-by-cell to accumulated radar rainfall before watershed weighting"\n'
    '        if event["runoff_spatialization_applied"]\n'
    '        else "Basin-average NRCS fallback"\n'
    '    )\n',
    1,
)

old_tests = '''    footprint_observed = bool(event.get("spatial_gate_seen"))
    storage_met = ratio >= 1.0
    flush_met = ratio >= float(config["model"]["flush_ratio"])

    event["decision_tests"] = {
        "storage_target_met": storage_met,
        "flush_target_met": flush_met,
        "heavy_rain_footprint_observed": footprint_observed,
        "minimum_wet_duration_met": enough_frames,
        "minimum_wet_frames_required": required_frames,
    }

    if flush_met and enough_frames:
'''
new_tests = '''    footprint_observed = bool(event.get("spatial_gate_seen"))
    storage_met = ratio >= 1.0
    flush_met = ratio >= float(config["model"]["flush_ratio"])
    field_core = zero_g_field_core_evidence(event, canyon)
    field_major_met = bool(field_core["major_threshold_met"] and enough_frames)
    field_flush_met = bool(field_core["flush_threshold_met"] and enough_frames)
    event["zero_g_field_core_evidence"] = field_core

    event["decision_tests"] = {
        "storage_target_met": storage_met,
        "flush_target_met": flush_met,
        "heavy_rain_footprint_observed": footprint_observed,
        "minimum_wet_duration_met": enough_frames,
        "minimum_wet_frames_required": required_frames,
        "zero_g_field_core_major_met": field_major_met,
        "zero_g_field_core_flush_met": field_flush_met,
    }

    if (flush_met or field_flush_met) and enough_frames:
'''
if old_tests not in text:
    raise SystemExit("classification decision marker not found")
text = text.replace(old_tests, new_tests, 1)

old_reason_flush = '''        reason = (
            "Estimated watershed runoff was at least twice the provisional empty-storage "
            "target and the minimum wet-duration check passed. "
            "This indicates a strong refill/flush event, not a direct field observation."
        )
'''
new_reason_flush = '''        reason = (
            "Strong flush evidence passed the duration check. This is supported by "
            + (
                f"the Zero G field-calibrated in-basin storm core ({field_core['maximum_in_basin_storm_inches']:.3f} in)"
                if field_flush_met
                else "estimated watershed runoff at least twice the provisional empty-storage target"
            )
            + ". The logger-derived core threshold is provisional field calibration, not a direct observation of every pool."
        )
'''
if old_reason_flush not in text:
    raise SystemExit("flush reason marker not found")
text = text.replace(old_reason_flush, new_reason_flush, 1)

text = text.replace(
    '    elif storage_met and enough_frames:\n',
    '    elif (storage_met or field_major_met) and enough_frames:\n',
    1,
)
old_reason_major = '''        reason = (
            "Estimated watershed runoff met the provisional empty-storage target, and both "
            "the minimum wet-duration check passed. Existing pool "
            "levels and channel losses remain unknown."
        )
'''
new_reason_major = '''        reason = (
            "Major refill evidence passed the duration check. This is supported by "
            + (
                f"the Zero G field-calibrated in-basin storm core ({field_core['maximum_in_basin_storm_inches']:.3f} in)"
                if field_major_met
                else "estimated watershed runoff meeting the provisional empty-storage target"
            )
            + ". Existing pool level and delivery losses remain uncertain."
        )
'''
if old_reason_major not in text:
    raise SystemExit("major reason marker not found")
text = text.replace(old_reason_major, new_reason_major, 1)

# Metadata wording: explain both production changes and retain the baseline formulas.
old_explain = '''                "No fixed runoff coefficient is used. Accumulated basin-average radar "
                "rainfall is converted to dry, normal, and wet direct-runoff estimates "
                "with canyon-specific composite curve numbers from SSURGO soils and "
                "2021 NLCD land cover. Traditional table CNs are converted to the "
                "Ia/S = 0.05 retention basis before runoff is calculated. Pixels "
                "without a usable SSURGO group are conservatively assigned to HSG D. "
                "The central display uses the normal condition."
'''
new_explain = '''                "No fixed runoff coefficient is used. Zero G now applies the adjusted "
                "NRCS equation to each accumulated radar-rainfall cell before area weighting, "
                "because paired logger events showed that basin averaging can erase localized "
                "slickrock runoff. Other canyons retain the basin-average calculation pending "
                "field calibration. Dry, normal, and wet estimates use canyon-specific composite "
                "curve numbers from SSURGO soils and 2021 NLCD land cover; the central display "
                "uses normal conditions. Zero G also carries an independent field-calibrated "
                "storm-core response test: 0.20 in for major refill evidence and 1.00 in for "
                "strong-flush evidence, each requiring the normal duration check."
'''
if old_explain not in text:
    raise SystemExit("metadata explanation marker not found")
text = text.replace(old_explain, new_explain, 1)

TRACKER.write_text(text, encoding="utf-8")
print("Installed Zero G spatial-runoff and field-core calibration")
