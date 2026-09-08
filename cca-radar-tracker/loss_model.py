"""Field-calibrated pool-loss reference used by all slot-canyon condition estimates.

The loss calibration comes from Zero G because it is the canyon with usable
instrumented recession data. The stable lower HOBO MX2001 logger was deployed
2026-08-01 through 2026-09-07. The upper logger physically relocated on
2026-08-08 and is retained only as an exposed/high-loss comparison, not in the
central calibration.

The operational reference separates a seasonal atmospheric term from a
field-calibrated residual term:

    total stage-equivalent loss = Moab reference ETo + Navajo/seepage residual

The residual is 1.28 inches/day. It is an empirical stage-equivalent loss term,
not an assertion of intrinsic Navajo Sandstone hydraulic conductivity. It also
subsumes local fractures, wetted-rock seepage, and any persistent quiet-pool
drainage that cannot be separated with the available logger geometry.

For operational consistency, this Zero G reference recession is transferred to
all 22 modeled canyons until canyon-specific logger or field recession data are
available. The percentage conversion uses the 13.0-ft operational full-stage reference for the lower Zero G pool, so every canyon currently uses the same
stage-equivalent percentage-point loss rate for a given month. This transfer is
an explicit model assumption; it is not a claim that every canyon has identical
pool geometry, seepage, or evaporation.
"""

from __future__ import annotations

import calendar
from datetime import datetime


ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT = 13.0
ZERO_G_NAVAJO_SEEPAGE_INCHES_PER_DAY = 1.28

# Utah State University Moab monthly reference ETo normals (2000-2022), inches.
# Source: USU Extension, "Evapotranspiration and Precipitation Data for
# Calculating Irrigation Water Requirements in Utah".
MOAB_MONTHLY_ETO_INCHES = {
    1: 1.16,
    2: 1.80,
    3: 3.55,
    4: 5.10,
    5: 7.16,
    6: 8.50,
    7: 9.03,
    8: 7.78,
    9: 5.72,
    10: 3.51,
    11: 1.80,
    12: 1.06,
}


def zero_g_eto_inches_per_day(reference: datetime) -> float:
    """Return the monthly-normal Moab ETo rate for ``reference``."""
    monthly = MOAB_MONTHLY_ETO_INCHES[reference.month]
    return monthly / calendar.monthrange(reference.year, reference.month)[1]


def zero_g_loss_components(reference: datetime) -> dict[str, float | str]:
    """Return the transferred Zero G reference loss components.

    Percentage-point conversion uses the 13.0-ft operational full-stage
    reference. Field observations bracket full stage: about 12 ft was nearly full
    and 13.66 ft was actively spilling. The tracker applies
    this same reference recession to every canyon as an explicit transfer
    assumption until canyon-specific recession data are available. It remains a
    stage-equivalent condition model, not a surveyed stage-volume curve.
    """
    eto = zero_g_eto_inches_per_day(reference)
    total = ZERO_G_NAVAJO_SEEPAGE_INCHES_PER_DAY + eto
    reference_depth_inches = ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT * 12.0
    points_per_day = total / reference_depth_inches * 100.0
    return {
        "model": "zero_g_mx2001_et_plus_navajo",
        "eto_inches_per_day": eto,
        "navajo_seepage_inches_per_day": ZERO_G_NAVAJO_SEEPAGE_INCHES_PER_DAY,
        "total_loss_inches_per_day": total,
        "reference_pool_depth_ft": ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT,
        "percentage_points_per_day": points_per_day,
        "transfer_scope": "all_modeled_canyons",
    }


def zero_g_monthly_recession_table(year: int = 2026) -> list[dict[str, float | int | str]]:
    """Return the 12-month transferred recession reference for ``year``.

    The 1.28 in/day field residual is held constant because the paired logger
    record does not support a defensible temperature-only seepage coefficient.
    Seasonality is represented by monthly Moab reference ETo, which captures
    the atmospheric effects of temperature, solar loading, day length, and
    seasonal evaporative demand. February is recomputed for leap years.
    """
    rows: list[dict[str, float | int | str]] = []
    for month in range(1, 13):
        reference = datetime(year, month, 15)
        values = zero_g_loss_components(reference)
        rows.append(
            {
                "month": month,
                "month_name": calendar.month_name[month],
                "eto_inches_per_month": MOAB_MONTHLY_ETO_INCHES[month],
                "eto_inches_per_day": round(float(values["eto_inches_per_day"]), 4),
                "total_loss_inches_per_day": round(float(values["total_loss_inches_per_day"]), 4),
                "percentage_points_per_day": round(float(values["percentage_points_per_day"]), 4),
            }
        )
    return rows


def _next_month_start(reference: datetime) -> datetime:
    """Return the first instant of the next month, preserving timezone."""
    if reference.month == 12:
        return reference.replace(
            year=reference.year + 1,
            month=1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
    return reference.replace(
        month=reference.month + 1,
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def zero_g_integrated_loss_ratio(start: datetime, end: datetime) -> float:
    """Integrate the transferred reference loss between two datetimes.

    ETo is piecewise constant within each calendar month, so integration is
    explicitly split at month boundaries. The empirical Navajo/seepage term is
    constant until additional field data support a seasonal or head-dependent
    seepage function. This integrated ratio is applied to every modeled canyon.
    """
    if end <= start:
        return 0.0
    current = start
    total_ratio = 0.0
    while current < end:
        next_step = min(end, _next_month_start(current))
        elapsed_days = (next_step - current).total_seconds() / 86400.0
        points_per_day = float(
            zero_g_loss_components(current)["percentage_points_per_day"]
        )
        total_ratio += points_per_day / 100.0 * elapsed_days
        current = next_step
    return max(0.0, total_ratio)
