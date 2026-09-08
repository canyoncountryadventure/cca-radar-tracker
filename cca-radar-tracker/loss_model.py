"""Field-calibrated pool-loss models for slot-canyon condition estimates.

Zero G calibration is based on the stable lower HOBO MX2001 logger deployed
2026-08-01 through 2026-09-07. The upper logger physically relocated and is
retained as an exposed/high-loss comparison rather than the central calibration.

The Zero G model intentionally separates a seasonal atmospheric term from a
site-calibrated residual term:

    total stage-equivalent loss = Moab reference ETo + Navajo/seepage residual

The residual is an empirical equivalent stage-loss term. It is not asserted to
be intrinsic Navajo Sandstone hydraulic conductivity; it also subsumes local
fractures, wetted-rock seepage, and any persistent closed-pool drainage that
cannot be separated with the available logger geometry.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta


ZERO_G_REFERENCE_LOWER_POOL_DEPTH_FT = 11.9288
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
    """Return the current Zero G field-calibrated loss components.

    Percentage-point conversion uses the stable lower logger's initial
    11.9288-ft water column as the field reference depth. This remains a
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
    }


def zero_g_integrated_loss_ratio(start: datetime, end: datetime) -> float:
    """Integrate the seasonal Zero G loss between two datetimes.

    ETo is piecewise constant within a calendar month. One-day stepping keeps
    the implementation transparent and makes month/year transitions exact
    enough for this empirical condition model.
    """
    if end <= start:
        return 0.0
    current = start
    total_ratio = 0.0
    while current < end:
        next_step = min(end, current + timedelta(days=1))
        elapsed_days = (next_step - current).total_seconds() / 86400.0
        points_per_day = float(zero_g_loss_components(current)["percentage_points_per_day"])
        total_ratio += points_per_day / 100.0 * elapsed_days
        current = next_step
    return max(0.0, total_ratio)
