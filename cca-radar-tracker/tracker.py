#!/usr/bin/env python3
"""Analyze IEM N0Q radar for every CCA canyon and update the dashboard data.

Pool-fill targets are normalized to the mapped Zero G depression storage and each
canyon's technical-section length, then adjusted by the user-defined pothole
modifier. Historical 50/55/60 dBZ watershed-footprint measurements are retained
for event context but do not gate the condition classification. Runoff uses the
adjusted curve-number initial-abstraction relation with Ia/S = 0.05.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
GRID_RESOLUTION = 0.005
GRID_LEFT_EDGE = -126.0025
GRID_TOP_EDGE = 50.0025
SQUARE_FEET_PER_SQUARE_MILE = 5280**2

INITIAL_ABSTRACTION_RATIO = 0.05
RETENTION_S05_COEFFICIENT = 1.33
RETENTION_S05_EXPONENT = 1.15

ZERO_G_STORAGE_FT3 = 52_442
ZERO_G_TECHNICAL_LENGTH_MILES = 0.75

# Modifier convention:
#   0.00 = same pothole-storage rate per technical mile as Zero G
#  -0.25 = 25% less storage per technical mile
#  +0.50 = 50% more storage per technical mile
#  +1.00 = twice the storage per technical mile
CANYON_POOL_STORAGE: dict[str, dict[str, float | str]] = {
    "zerog": {
        "technical_length_miles": 0.75,
        "pothole_modifier": 0.00,
        "basis": "Zero G 1-meter depression inventory: 114 depressions totaling 1,485.0 m3 (52,442 ft3)",
    },
    "black-hole-white-canyon": {
        "technical_length_miles": 2.50,
        "pothole_modifier": 0.50,
        "basis": "User technical-section length and continuous-water/pool morphology adjustment",
    },
    "leprechaun": {
        "technical_length_miles": 1.00,
        "pothole_modifier": -0.90,
        "basis": "User technical-section length and very-low persistent pool-storage adjustment",
    },
    "woody": {
        "technical_length_miles": 0.25,
        "pothole_modifier": 0.00,
        "basis": "User technical-section length and Zero G-equivalent storage rate",
    },
    "hog-canyons": {
        "technical_length_miles": 0.65,
        "pothole_modifier": -0.25,
        "basis": "User technical-section length and lower pothole-storage adjustment",
    },
    "no-kidding": {
        "technical_length_miles": 0.34,
        "pothole_modifier": 0.20,
        "basis": "User technical-section length and higher pothole-storage adjustment",
    },
    "angel-cove": {
        "technical_length_miles": 0.65,
        "pothole_modifier": -0.25,
        "basis": "User technical-section length and lower pothole-storage adjustment",
    },
    "constrychnine": {
        "technical_length_miles": 0.60,
        "pothole_modifier": -0.50,
        "basis": "User technical-section length and lower pothole-storage adjustment",
    },
    "alcatraz": {
        "technical_length_miles": 0.65,
        "pothole_modifier": 0.20,
        "basis": "User technical-section length and higher pothole-storage adjustment",
    },
    "poe": {
        "technical_length_miles": 0.65,
        "pothole_modifier": 1.00,
        "basis": "User technical-section length and large-keeper-pothole adjustment",
    },
    "entrajo": {
        "technical_length_miles": 0.85,
        "pothole_modifier": -0.70,
        "basis": "User technical-section length; result closely matches prior 1-meter depression estimate",
    },
    "pool-arch": {
        "technical_length_miles": 0.10,
        "pothole_modifier": -0.75,
        "basis": "User technical-section length and low-storage adjustment",
    },
    "the-squeeze": {
        "technical_length_miles": 1.25,
        "pothole_modifier": 0.75,
        "basis": "User technical-section length and pothole-dense adjustment",
    },
    "cable-canyon": {
        "technical_length_miles": 2.50,
        "pothole_modifier": 0.50,
        "basis": "User technical-section length and higher pool-storage adjustment",
    },
    "eardley": {
        "technical_length_miles": 1.00,
        "pothole_modifier": 0.90,
        "basis": "User technical-section length and higher pool-storage adjustment",
    },
    "north-fork-iron-wash": {
        "technical_length_miles": 0.75,
        "pothole_modifier": 0.00,
        "basis": "User technical-section length and Zero G-equivalent storage rate",
    },
    "upper-greasewood": {
        "technical_length_miles": 1.20,
        "pothole_modifier": 0.00,
        "basis": "User technical-section length and Zero G-equivalent storage rate",
    },
    "wonderland-canyon": {
        "technical_length_miles": 0.37,
        "pothole_modifier": -0.25,
        "basis": "User technical-section length and lower pothole-storage adjustment",
    },
    "hogwarts": {
        "technical_length_miles": 0.22,
        "pothole_modifier": -0.75,
        "basis": "User technical-section length and low-storage adjustment",
    },
    "neon": {
        "technical_length_miles": 1.60,
        "pothole_modifier": 0.00,
        "basis": "User technical-section length and Zero G-equivalent storage rate",
    },
    "quandary": {
        "technical_length_miles": 1.59,
        "pothole_modifier": 0.00,
        "basis": "User technical-section length and Zero G-equivalent storage rate",
    },
}

FIXED_SPATIAL_RULES = (
    {"dbz": 50.0, "minimum_coverage_percent": 50.0},
    {"dbz": 55.0, "minimum_coverage_percent": 25.0},
    {"dbz": 60.0, "minimum_coverage_percent": 10.0},
)

MINOR_REFILL_RATIO = 0.25
SUBSTANTIAL_REFILL_RATIO = 0.50
LARGE_REFILL_RATIO = 0.75
STATUS_SCHEMA_VERSION = 4


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def floor_five_minutes(value: datetime) -> datetime:
    value = value.astimezone(UTC).replace(second=0, microsecond=0)
    return value.replace(minute=value.minute - value.minute % 5)


def iter_five_minutes(start: datetime, end: datetime) -> Iterable[datetime]:
    current = floor_five_minutes(start)
    end = floor_five_minutes(end)
    while current <= end:
        yield current
        current += timedelta(minutes=5)


@dataclass(frozen=True)
class Grid:
    left: float
    bottom: float
    right: float
    top: float
    width: int
    height: int

    @property
    def bbox(self) -> list[float]:
        return [self.left, self.bottom, self.right, self.top]


@dataclass
class Canyon:
    canyon_id: str
    name: str
    area_sq_mi: float
    geometry: dict[str, Any]
    outlet: list[float]
    grid: Grid
    weights: np.ndarray
    atlas14: dict[str, dict[str, float]]
    model: dict[str, Any]


def geometry_rings(
    geometry: dict[str, Any],
) -> list[tuple[list[list[float]], list[list[list[float]]]]]:
    if geometry["type"] == "Polygon":
        return [(geometry["coordinates"][0], geometry["coordinates"][1:])]
    if geometry["type"] == "MultiPolygon":
        return [(polygon[0], polygon[1:]) for polygon in geometry["coordinates"]]
    raise ValueError(f"Expected Polygon or MultiPolygon, received {geometry['type']}")


def all_points(geometry: dict[str, Any]) -> Iterable[list[float]]:
    for exterior, holes in geometry_rings(geometry):
        yield from exterior
        for hole in holes:
            yield from hole


def aligned_grid_for_points(points: Iterable[list[float]], padding_cells: int) -> Grid:
    points = list(points)
    minimum_x = min(point[0] for point in points)
    maximum_x = max(point[0] for point in points)
    minimum_y = min(point[1] for point in points)
    maximum_y = max(point[1] for point in points)

    first_column = math.floor((minimum_x - GRID_LEFT_EDGE) / GRID_RESOLUTION) - padding_cells
    last_column = math.floor((maximum_x - GRID_LEFT_EDGE) / GRID_RESOLUTION) + padding_cells
    first_row = math.floor((GRID_TOP_EDGE - maximum_y) / GRID_RESOLUTION) - padding_cells
    last_row = math.floor((GRID_TOP_EDGE - minimum_y) / GRID_RESOLUTION) + padding_cells

    return Grid(
        left=GRID_LEFT_EDGE + first_column * GRID_RESOLUTION,
        right=GRID_LEFT_EDGE + (last_column + 1) * GRID_RESOLUTION,
        top=GRID_TOP_EDGE - first_row * GRID_RESOLUTION,
        bottom=GRID_TOP_EDGE - (last_row + 1) * GRID_RESOLUTION,
        width=last_column - first_column + 1,
        height=last_row - first_row + 1,
    )


def aligned_grid(geometry: dict[str, Any], padding_cells: int) -> Grid:
    return aligned_grid_for_points(all_points(geometry), padding_cells)


def watershed_weights(geometry: dict[str, Any], grid: Grid, supersample: int) -> np.ndarray:
    mask = Image.new("L", (grid.width * supersample, grid.height * supersample), 0)
    draw = ImageDraw.Draw(mask)

    def pixels(ring: list[list[float]]) -> list[tuple[float, float]]:
        return [
            (
                (point[0] - grid.left) / GRID_RESOLUTION * supersample,
                (grid.top - point[1]) / GRID_RESOLUTION * supersample,
            )
            for point in ring
        ]

    for exterior, holes in geometry_rings(geometry):
        draw.polygon(pixels(exterior), fill=255)
        for hole in holes:
            draw.polygon(pixels(hole), fill=0)

    values = np.asarray(mask, dtype=np.float32) / 255.0
    return values.reshape(grid.height, supersample, grid.width, supersample).mean(axis=(1, 3))


def load_palette(path: Path) -> dict[tuple[int, int, int], int]:
    palette = json.loads(path.read_text(encoding="utf-8"))
    return {tuple(rgb): index for index, rgb in enumerate(palette)}


def latest_iem_timestamp(config: dict[str, Any]) -> datetime:
    request = urllib.request.Request(
        config["iem_current_png_url"],
        method="HEAD",
        headers={"User-Agent": "CCA-PoolFill-Radar/3.0"},
    )
    with urllib.request.urlopen(
        request, timeout=int(config["request_timeout_seconds"])
    ) as response:
        modified = response.headers.get("Last-Modified")
    if not modified:
        raise RuntimeError("IEM current radar response did not include Last-Modified")
    return floor_five_minutes(parsedate_to_datetime(modified).astimezone(UTC))


def radar_frame_source(
    timestamp: datetime,
    latest_reference: datetime | None,
    config: dict[str, Any],
) -> str:
    """Return ``historical`` once an exact WMS-T frame should be available."""
    if latest_reference is None:
        return "historical"
    age_minutes = int((latest_reference - timestamp).total_seconds() // 60)
    historical_min_age = int(config.get("historical_wms_min_age_minutes", 10))
    use_historical = (
        age_minutes < 0
        or age_minutes >= historical_min_age
        or age_minutes > 55
        or age_minutes % 5 != 0
    )
    return "historical" if use_historical else "provisional"


def fetch_radar_image(
    timestamp: datetime,
    grid: Grid,
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> Image.Image:
    """Fetch one exact radar frame without silently accepting stale WMS imagery.

    The former implementation requested time-relative ``m05m``/``m55m`` layers
    using static URLs. Those URLs can be cached even though their underlying
    valid time changes, causing an old clear-air image to be analyzed as a new
    frame. It also requested a Web-Mercator (900913) layer in EPSG:4326, which
    can alter indexed radar colors during reprojection.

    Frames at least 10 minutes old now use the historical WMS-T service with an
    explicit UTC TIME. The newest one or two frames use the native EPSG:4326
    current layers and a valid-time cache key.
    """
    source = radar_frame_source(timestamp, latest_reference, config)
    use_historical = source == "historical"
    age_minutes = (
        None
        if latest_reference is None
        else int((latest_reference - timestamp).total_seconds() // 60)
    )

    if use_historical:
        endpoint = config["iem_historical_wms_url"]
        layer = "nexrad-n0q-wmst"
    else:
        endpoint = config["iem_current_wms_url"]
        suffix = "" if age_minutes == 0 else f"-m{age_minutes:02d}m"
        # Native lat/lon layer; do not request the 900913 layer in EPSG:4326.
        layer = f"nexrad-n0q{suffix}-conus"

    query = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": layer,
        "STYLES": "",
        "SRS": "EPSG:4326",
        "BBOX": ",".join(f"{number:.7f}" for number in grid.bbox),
        "WIDTH": str(grid.width),
        "HEIGHT": str(grid.height),
        "FORMAT": "image/png",
        "TRANSPARENT": "TRUE",
        # Time-relative WMS layer names otherwise reuse the same URL forever.
        "_valid": (
            latest_reference.strftime("%Y%m%d%H%M")
            if latest_reference is not None
            else timestamp.strftime("%Y%m%d%H%M")
        ),
    }
    if use_historical:
        query["TIME"] = timestamp.strftime("%Y-%m-%dT%H:%M:00Z")

    url = f"{endpoint}?{urllib.parse.urlencode(query)}"
    error: Exception | None = None
    for attempt in range(int(config["request_retries"])):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "CCA-PoolFill-Radar/3.1",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(
                request, timeout=int(config["request_timeout_seconds"])
            ) as response:
                image = Image.open(io.BytesIO(response.read())).convert("RGBA")
            if image.size != (grid.width, grid.height):
                raise ValueError(f"Unexpected WMS image size {image.size}")
            return image
        except Exception as exc:  # pragma: no cover - network failure path
            error = exc
            if attempt + 1 < int(config["request_retries"]):
                time.sleep(2**attempt)
    raise RuntimeError(f"Unable to retrieve radar frame {utc_text(timestamp)}: {error}")


def crop_for_grid(image: Image.Image, source: Grid, target: Grid) -> Image.Image:
    left = round((target.left - source.left) / GRID_RESOLUTION)
    top = round((source.top - target.top) / GRID_RESOLUTION)
    return image.crop((left, top, left + target.width, top + target.height))


def image_to_dbz(
    image: Image.Image, palette: dict[tuple[int, int, int], int]
) -> tuple[np.ndarray, np.ndarray]:
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    indices = np.zeros(pixels.shape[:2], dtype=np.int16)
    for row in range(indices.shape[0]):
        for column in range(indices.shape[1]):
            if pixels[row, column, 3] == 0:
                continue
            indices[row, column] = palette.get(
                tuple(int(x) for x in pixels[row, column, :3]), -1
            )
    dbz = indices.astype(np.float32) * 0.5 - 32.5
    dbz[indices <= 0] = np.nan
    return dbz, indices


def rain_depth_inches(dbz: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    capped = np.minimum(
        np.nan_to_num(dbz, nan=-999.0), float(model["rain_dbz_cap"])
    )
    valid = capped >= float(model["minimum_rain_dbz"])
    depth = np.zeros(dbz.shape, dtype=np.float32)
    reflectivity = np.power(10.0, capped[valid] / 10.0)
    rate_mm_hour = np.power(
        reflectivity / float(model["zr_a"]), 1.0 / float(model["zr_b"])
    )
    depth[valid] = (
        rate_mm_hour / 25.4 * float(model["frame_minutes"]) / 60.0
    )
    return depth


def pool_storage_target(canyon_id: str) -> dict[str, Any]:
    try:
        source = CANYON_POOL_STORAGE[canyon_id]
    except KeyError as exc:
        raise KeyError(
            f"No technical-section pool-storage parameters are defined for {canyon_id!r}"
        ) from exc

    technical_length = float(source["technical_length_miles"])
    modifier = float(source["pothole_modifier"])
    if technical_length <= 0:
        raise ValueError(f"Technical length must be positive for {canyon_id}")
    if modifier <= -1.0:
        raise ValueError(
            f"Pothole modifier must be greater than -1.0 for {canyon_id}; received {modifier}"
        )

    length_ratio = technical_length / ZERO_G_TECHNICAL_LENGTH_MILES
    storage_rate_multiplier = 1.0 + modifier
    fill_target = round(
        ZERO_G_STORAGE_FT3 * length_ratio * storage_rate_multiplier
    )
    return {
        "technical_length_miles": technical_length,
        "pothole_modifier": modifier,
        "length_ratio_to_zerog": round(length_ratio, 4),
        "storage_rate_multiplier": round(storage_rate_multiplier, 4),
        "fill_target_ft3": fill_target,
        "storage_basis": str(source["basis"]),
    }



def canyon_model(
    canyon_id: str,
    area_sq_mi: float,
    config: dict[str, Any],
    hydrology: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the storage target and historical radar-footprint comparisons."""
    model = config["model"]
    storage = pool_storage_target(canyon_id)

    rules = []
    for rule in FIXED_SPATIAL_RULES:
        minimum_coverage = float(rule["minimum_coverage_percent"])
        required_area = area_sq_mi * minimum_coverage / 100.0
        rules.append(
            {
                "dbz": float(rule["dbz"]),
                "minimum_area_sq_mi": round(required_area, 3),
                "minimum_coverage_percent": minimum_coverage,
            }
        )

    fill_target = int(storage["fill_target_ft3"])
    result: dict[str, Any] = {
        **storage,
        "storage_target_ft3": fill_target,
        "flush_target_ft3": round(fill_target * float(model["flush_ratio"])),
        "modifier_percent": round(float(storage["pothole_modifier"]) * 100.0, 1),
        "storage_rate_percent_of_zerog": round(
            float(storage["storage_rate_multiplier"]) * 100.0, 1
        ),
        "calibration": (
            "Measured Zero G depression storage; runoff and routing remain modeled"
            if canyon_id == "zerog"
            else "Technical-length normalized and morphology-adjusted; field calibration needed"
        ),
        "target_method": (
            "52,442 ft3 × (technical length / 0.75 mi) × (1 + pothole modifier)"
        ),
        "spatial_rules": rules,
    }
    if hydrology:
        result["hydrology"] = hydrology
    return result


def build_canyons(
    collection: dict[str, Any],
    atlas: dict[str, Any],
    config: dict[str, Any],
    hydrology: dict[str, Any] | None = None,
) -> tuple[list[Canyon], Grid]:
    supersample = int(config["mask_supersample"])
    padding = int(config["grid_padding_cells"])
    canyons: list[Canyon] = []
    all_geometry_points: list[list[float]] = []

    for feature in collection["features"]:
        properties = feature["properties"]
        geometry = feature["geometry"]
        grid = aligned_grid(geometry, padding)
        weights = watershed_weights(geometry, grid, supersample)
        if float(weights.sum()) <= 0:
            raise ValueError(f"Watershed mask is empty for {properties['name']}")

        canyon_id = str(properties["id"])
        canyons.append(
            Canyon(
                canyon_id=canyon_id,
                name=properties["name"],
                area_sq_mi=float(properties["area_sq_mi"]),
                geometry=geometry,
                outlet=properties["outlet"],
                grid=grid,
                weights=weights,
                atlas14=atlas[canyon_id],
                model=canyon_model(
                    canyon_id,
                    float(properties["area_sq_mi"]),
                    config,
                    (hydrology or {}).get("canyons", {}).get(canyon_id),
                ),
            )
        )
        all_geometry_points.extend(all_points(geometry))

    expected = set(CANYON_POOL_STORAGE)
    loaded = {canyon.canyon_id for canyon in canyons}
    missing = loaded - expected
    unused = expected - loaded
    if missing:
        raise ValueError(f"Pool-storage table is missing canyon IDs: {sorted(missing)}")
    if unused:
        raise ValueError(f"Pool-storage table contains unknown canyon IDs: {sorted(unused)}")

    return canyons, aligned_grid_for_points(all_geometry_points, padding)


def grid_list(values: np.ndarray, digits: int = 3) -> list[list[float | None]]:
    return [
        [None if not np.isfinite(value) else round(float(value), digits) for value in row]
        for row in values
    ]



def analyze_canyon_image(
    image: Image.Image,
    canyon: Canyon,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
) -> tuple[dict[str, Any], np.ndarray]:
    """Measure radar rain and intense-rain coverage for one canyon watershed.

    No fixed runoff coefficient is applied at the frame level. Event runoff is
    calculated later from the accumulated basin-average rainfall with each
    canyon's dry/normal/wet NRCS curve numbers.
    """
    dbz, indices = image_to_dbz(image, palette)
    total_weight = float(canyon.weights.sum())
    unknown_weight = float(canyon.weights[indices < 0].sum())
    rain = rain_depth_inches(dbz, config["model"])
    known_weights = np.where(indices >= 0, canyon.weights, 0.0)
    known_weight = float(known_weights.sum())
    unknown_percent = 100.0 * unknown_weight / total_weight
    quality_limit = float(config.get("maximum_unknown_watershed_percent", 20))
    quality_status = (
        "insufficient_radar_data"
        if known_weight <= 0 or unknown_percent > quality_limit
        else ("degraded" if unknown_percent > 0 else "valid")
    )
    basin_rain = (
        float((rain * known_weights).sum() / known_weight)
        if known_weight > 0
        else 0.0
    )
    rain_volume = (
        basin_rain / 12.0 * canyon.area_sq_mi * SQUARE_FEET_PER_SQUARE_MILE
    )

    coverages: dict[str, float] = {}
    rules = []
    comparison_dbz = np.nan_to_num(dbz, nan=-999.0)
    for rule in canyon.model["spatial_rules"]:
        threshold = float(rule["dbz"])
        coverage = (
            100.0
            * float(canyon.weights[comparison_dbz >= threshold].sum())
            / known_weight
            if known_weight > 0
            else 0.0
        )
        covered_area = coverage / 100.0 * canyon.area_sq_mi
        coverages[str(int(threshold))] = round(coverage, 1)
        rules.append(
            {
                **rule,
                "coverage_percent": round(coverage, 1),
                "covered_area_sq_mi": round(covered_area, 3),
                "qualified": coverage + 1e-9
                >= float(rule["minimum_coverage_percent"]),
            }
        )

    watershed_values = dbz[canyon.weights > 0]
    maximum = (
        float(np.nanmax(watershed_values))
        if np.any(np.isfinite(watershed_values))
        else None
    )
    wet = bool(
        quality_status != "insufficient_radar_data"
        and
        maximum is not None
        and maximum >= float(config["model"]["storm_dbz_threshold"])
    )
    rain_detected = quality_status != "insufficient_radar_data" and basin_rain >= float(
        config["model"].get("event_continue_minimum_basin_rain_inches", 0.0001)
    )

    return (
        {
            "maximum_dbz": None if maximum is None else round(maximum, 1),
            "coverage_percent": coverages,
            "spatial_rules": rules,
            "spatial_gate": any(rule["qualified"] for rule in rules),
            "frame_basin_rain_inches": round(basin_rain, 4),
            "frame_rain_volume_ft3": round(rain_volume),
            "wet": wet,
            "rain_detected": rain_detected,
            "unknown_watershed_percent": round(unknown_percent, 1),
            "radar_data_quality": quality_status,
            "radar_data_sufficient": quality_status != "insufficient_radar_data",
            "grid_dbz": grid_list(dbz, 1),
            "grid_bbox": canyon.grid.bbox,
        },
        rain,
    )


def empty_canyon_status(canyon: Canyon) -> dict[str, Any]:
    return {
        "id": canyon.canyon_id,
        "name": canyon.name,
        "area_sq_mi": canyon.area_sq_mi,
        "latest_analysis": None,
        "open_event": None,
        "last_rain_event": None,
        "last_qualifying_event": None,
        "events": [],
        "refill_history": [],
        "historical_records": {
            "peak_individual_event": None,
            "peak_seven_day_evidence": None,
        },
        "recent_refill_evidence": {
            "window_days": 7,
            "percent": 0,
            "ratio": 0.0,
            "through_utc": None,
            "last_meaningful_event_utc": None,
            "trend": "no recent evidence",
        },
        "cumulative_refill_evidence": {
            "event_count": 0,
            "balance_ft3": 0,
            "ratio": 0.0,
            "percent": 0,
            "overflow_ft3": 0,
            "milestones_utc": {"25": None, "50": None, "75": None, "100": None},
            "loss_model": "not_modeled",
        },
        "notification": {
            "last_emailed_event_start_utc": None,
            "last_email_sent_utc": None,
        },
    }


def empty_status(canyons: list[Canyon] | None = None) -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "monitoring_started_utc": None,
        "last_checked_utc": None,
        "last_scheduled_run_utc": None,
        "last_run_trigger": None,
        "latest_frame_utc": None,
        "latest_provisional_frame_utc": None,
        "latest_archive_confirmed_frame_utc": None,
        "ledger_started_utc": None,
        "earliest_missing_archive_frame_utc": None,
        "manual_replay_from_utc": None,
        "missing_archive_frames_utc": [],
        "stale_missing_archive_frames_utc": [],
        "frame_ledger": {},
        "health_notification": {
            "last_email_sent_utc": None,
            "last_missing_signature": None,
        },
        "canyons": {
            canyon.canyon_id: empty_canyon_status(canyon)
            for canyon in (canyons or [])
        },
        "health": {"ok": True, "message": "Waiting for first radar check"},
        "state_restoration": {"validated": False, "source": None},
    }


def legacy_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None
    return {
        **event,
        "classification": "legacy_spatial_trigger",
        "classification_label": "Legacy Zero G radar trigger",
        "estimated_runoff_ft3": None,
        "fill_ratio": None,
        "basin_rain_inches": None,
        "atlas14_return_period_years": None,
    }


def ensure_status_defaults(status: dict[str, Any], canyons: list[Canyon]) -> dict[str, Any]:
    """Migrate published state in place without discarding retained events."""
    defaults = empty_status(canyons)
    status["schema_version"] = STATUS_SCHEMA_VERSION
    for key in (
        "monitoring_started_utc",
        "last_checked_utc",
        "last_scheduled_run_utc",
        "last_run_trigger",
        "latest_frame_utc",
        "latest_provisional_frame_utc",
        "latest_archive_confirmed_frame_utc",
        "ledger_started_utc",
        "earliest_missing_archive_frame_utc",
        "manual_replay_from_utc",
    ):
        status.setdefault(key, defaults[key])
    for key in (
        "missing_archive_frames_utc",
        "stale_missing_archive_frames_utc",
    ):
        status.setdefault(key, [])
    status.setdefault("frame_ledger", {})
    status.setdefault("health_notification", defaults["health_notification"])
    status.setdefault("health", defaults["health"])
    status.setdefault("state_restoration", defaults["state_restoration"])
    status.setdefault("canyons", {})
    for canyon in canyons:
        canyon_status = status["canyons"].setdefault(
            canyon.canyon_id, empty_canyon_status(canyon)
        )
        canyon_status.setdefault("notification", {})
        for key, value in empty_canyon_status(canyon).items():
            canyon_status.setdefault(key, value)
    return status


def load_status(
    path: Path, canyons: list[Canyon], require_existing: bool = False
) -> dict[str, Any]:
    fresh = empty_status(canyons)
    if not path.exists():
        if require_existing:
            raise ValueError(f"Required operational state is missing: {path}")
        return fresh
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if require_existing:
            raise ValueError(f"Operational state is unreadable: {path}") from exc
        return fresh

    if not isinstance(existing, dict):
        if require_existing:
            raise ValueError("Operational state root must be a JSON object")
        return fresh

    if existing.get("schema_version") in {2, 3, STATUS_SCHEMA_VERSION}:
        restored = ensure_status_defaults(existing, canyons)
        restored["state_restoration"] = {"validated": True, "source": str(path)}
        return restored

    if existing.get("schema_version") == 1:
        fresh["monitoring_started_utc"] = existing.get("monitoring_started_utc")
        fresh["last_checked_utc"] = existing.get("last_checked_utc")
        fresh["latest_frame_utc"] = existing.get("latest_frame_utc")
        zerog = fresh["canyons"]["zerog"]
        zerog["last_qualifying_event"] = legacy_event(
            existing.get("last_qualifying_event")
        )
        zerog["events"] = [
            legacy_event(event) for event in existing.get("events", []) if event
        ]
        fresh["health"] = {
            "ok": True,
            "message": "Earlier Zero G history preserved; multi-canyon monitoring active",
        }
    if existing.get("schema_version") != 1 and require_existing:
        raise ValueError(
            f"Unsupported operational-state schema: {existing.get('schema_version')}"
        )
    restored = ensure_status_defaults(fresh, canyons)
    restored["state_restoration"] = {"validated": True, "source": str(path)}
    return restored


def refresh_status_events(
    status: dict[str, Any], canyons: list[Canyon], config: dict[str, Any]
) -> None:
    """Recalculate retained events while preserving every available radar snippet."""
    for canyon in canyons:
        canyon_status = status.get("canyons", {}).get(canyon.canyon_id, {})
        for key in ("last_rain_event", "last_qualifying_event"):
            event = canyon_status.get(key)
            if event and event.get("frames") and event.get("basin_rain_inches") is not None:
                canyon_status[key] = event_public(event, canyon, config, include_grid=True)

        refreshed = []
        for event in canyon_status.get("events", []):
            if event.get("frames") and event.get("basin_rain_inches") is not None:
                refreshed.append(event_public(event, canyon, config, include_grid=True))
            else:
                refreshed.append(event)
        canyon_status["events"] = refreshed


def restore_missing_event_grids(
    status: dict[str, Any],
    canyons: list[Canyon],
    global_grid: Grid,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
    latest_reference: datetime,
) -> int:
    """Restore radar snippets lost by older gridless replay/history records.

    Compact event history intentionally omits radar grids, but the dashboard's
    full ``last_rain_event`` and ``last_qualifying_event`` records should retain
    them. Earlier replay logic could promote a compact event into those fields.
    Fetching only each affected event's exact peak timestamp repairs the map
    without replaying days of five-minute frames.
    """
    pending: dict[str, list[tuple[Canyon, dict[str, Any]]]] = {}
    for canyon in canyons:
        canyon_status = status.get("canyons", {}).get(canyon.canyon_id, {})
        for key in ("last_rain_event", "last_qualifying_event"):
            event = canyon_status.get(key)
            if (
                not event
                or event.get("peak_grid_dbz") is not None
                or not event.get("peak_frame_utc")
            ):
                continue
            timestamp = utc_text(floor_five_minutes(parse_utc(event["peak_frame_utc"])))
            pending.setdefault(timestamp, []).append((canyon, event))

    restored = 0
    threshold = float(config["model"]["storm_dbz_threshold"])
    for timestamp_text, targets in sorted(pending.items()):
        timestamp = parse_utc(timestamp_text)
        try:
            global_image = fetch_radar_image(
                timestamp, global_grid, config, latest_reference
            )
        except Exception as exc:  # pragma: no cover - network recovery path
            print(
                f"{timestamp_text}: unable to restore retained event radar "
                f"snippet; will retry: {exc}",
                file=sys.stderr,
            )
            continue

        for canyon, event in targets:
            image = crop_for_grid(global_image, global_grid, canyon.grid)
            analysis, _ = analyze_canyon_image(image, canyon, palette, config)
            maximum = analysis.get("maximum_dbz")
            # A retained wet event should still contain threshold-level echo at
            # its peak timestamp. Treat a blank/weak response as unavailable so
            # the repair is retried instead of permanently saving a blank map.
            if maximum is None or float(maximum) < threshold:
                print(
                    f"{timestamp_text}: {canyon.canyon_id} retained-event "
                    f"snippet was not restored because the exact frame returned "
                    f"{maximum if maximum is not None else 'no'} dBZ",
                    file=sys.stderr,
                )
                continue
            event["peak_grid_dbz"] = analysis["grid_dbz"]
            event["grid_bbox"] = analysis["grid_bbox"]
            restored += 1

    return restored


def event_duration_minutes(event: dict[str, Any], frame_minutes: int) -> int:
    if event.get("start_utc") and event.get("end_utc"):
        elapsed = int(
            (
                parse_utc(event["end_utc"]) - parse_utc(event["start_utc"])
            ).total_seconds()
            // 60
        )
        return max(frame_minutes, elapsed + frame_minutes)
    return max(frame_minutes, int(event["frames"]) * frame_minutes)


def atlas_return_period(
    event: dict[str, Any], canyon: Canyon, frame_minutes: int
) -> float | None:
    duration = event_duration_minutes(event, frame_minutes)
    supported = sorted(
        int(key.removesuffix("-min"))
        for key in canyon.atlas14
        if key.endswith("-min") and key.removesuffix("-min").isdigit()
    )
    if not supported or duration > supported[-1]:
        return None
    lower = max(
        (value for value in supported if value <= duration), default=supported[0]
    )
    upper = min(
        (value for value in supported if value >= duration), default=supported[-1]
    )
    periods = sorted(float(period) for period in canyon.atlas14[f"{lower}-min"])

    def duration_depth(period: float) -> float:
        low_depth = float(canyon.atlas14[f"{lower}-min"][str(int(period))])
        if lower == upper:
            return low_depth
        high_depth = float(canyon.atlas14[f"{upper}-min"][str(int(period))])
        fraction = (math.log(duration) - math.log(lower)) / (
            math.log(upper) - math.log(lower)
        )
        return math.exp(
            math.log(low_depth)
            + fraction * (math.log(high_depth) - math.log(low_depth))
        )

    depth = float(event.get("basin_rain_inches") or 0)
    pairs = [(period, duration_depth(period)) for period in periods]
    if depth <= 0:
        return None
    if depth <= pairs[0][1]:
        return round(max(0.1, pairs[0][0] * depth / pairs[0][1]), 1)
    for (period1, depth1), (period2, depth2) in zip(pairs, pairs[1:]):
        if depth1 <= depth <= depth2:
            fraction = (depth - depth1) / (depth2 - depth1)
            return round(
                math.exp(
                    math.log(period1)
                    + fraction * (math.log(period2) - math.log(period1))
                ),
                1,
            )
    return pairs[-1][0]


def nrcs_retention_s20(curve_number: float) -> float:
    """Traditional table-CN retention, in inches, based on Ia/S = 0.20."""
    if not 0.0 < curve_number <= 100.0:
        raise ValueError(f"Curve number must be in (0, 100]; received {curve_number}")
    return max(0.0, 1000.0 / curve_number - 10.0)


def nrcs_retention_s05(curve_number: float) -> float:
    """Convert traditional table-CN retention to the adjusted Ia/S = 0.05 basis."""
    retention_s20 = nrcs_retention_s20(curve_number)
    return RETENTION_S05_COEFFICIENT * retention_s20**RETENTION_S05_EXPONENT


def nrcs_initial_abstraction(curve_number: float) -> float:
    """Adjusted initial abstraction, in inches, for a traditional table CN."""
    return INITIAL_ABSTRACTION_RATIO * nrcs_retention_s05(curve_number)


def nrcs_runoff_depth(rain_inches: float, curve_number: float) -> float:
    """Adjusted NRCS direct-runoff depth using Ia/S = 0.05.

    The canyon curve numbers come from traditional NRCS tables developed on the
    Ia/S = 0.20 basis. Their retention term is therefore converted to S0.05
    before applying the 0.05 runoff equation.
    """
    if rain_inches <= 0.0:
        return 0.0
    retention_s05 = nrcs_retention_s05(curve_number)
    abstraction = INITIAL_ABSTRACTION_RATIO * retention_s05
    if rain_inches <= abstraction:
        return 0.0
    return (rain_inches - abstraction) ** 2 / (
        rain_inches + (1.0 - INITIAL_ABSTRACTION_RATIO) * retention_s05
    )



def apply_hydrologic_model(
    event: dict[str, Any], canyon: Canyon, config: dict[str, Any]
) -> None:
    """Estimate NRCS direct runoff and a routed screening peak.

    These volumes are generated-runoff estimates at the watershed scale. They
    do not explicitly subtract transmission losses between the watershed and
    the technical canyon.
    """
    hydrology = canyon.model.get("hydrology")
    if not hydrology:
        event["hydrology_available"] = False
        return

    rain = float(event.get("basin_rain_inches") or 0.0)
    area_ft2 = canyon.area_sq_mi * SQUARE_FEET_PER_SQUARE_MILE
    duration_minutes = event_duration_minutes(
        event, int(config["model"]["frame_minutes"])
    )
    duration_hr = duration_minutes / 60.0
    lag_hr = max(0.05, float(hydrology["lag_hours"]))

    volumes: dict[str, int] = {}
    peaks: dict[str, float] = {}
    runoff_depths: dict[str, float] = {}
    retention_s05: dict[str, float] = {}
    initial_abstraction: dict[str, float] = {}
    for state in ("dry", "normal", "wet"):
        curve_number = float(hydrology["curve_number"][state])
        retention_s05[state] = round(nrcs_retention_s05(curve_number), 4)
        initial_abstraction[state] = round(nrcs_initial_abstraction(curve_number), 4)
        depth = nrcs_runoff_depth(rain, curve_number)
        volume = depth / 12.0 * area_ft2
        base_seconds = max(
            300.0, (duration_hr + 2.0 * lag_hr) * 3600.0
        )
        runoff_depths[state] = round(depth, 4)
        volumes[state] = round(volume)
        peaks[state] = round(2.0 * volume / base_seconds, 2)

    event["hydrology_available"] = True
    event["retention_s05_inches"] = retention_s05
    event["initial_abstraction_inches"] = initial_abstraction
    event["runoff_depth_inches"] = runoff_depths
    event["direct_runoff_ft3_range"] = volumes
    event["direct_runoff_ft3"] = volumes["normal"]
    event["routed_peak_cfs_range"] = peaks
    event["routed_peak_cfs"] = peaks["normal"]
    event["generated_runoff_ft3"] = volumes["normal"]
    event["generated_runoff_ft3_range"] = volumes
    event["delivered_runoff_ft3"] = None
    event["delivery_status"] = "not_calibrated"
    event["delivery_explanation"] = (
        "Generated watershed runoff is reported separately because channel "
        "infiltration, seepage, upstream storage, routing, and attenuation have "
        "not been calibrated for this canyon."
    )

    event["antecedent_condition"] = "normal (central estimate)"
    event["storm_duration_minutes"] = duration_minutes
    event["wet_duration_minutes"] = (
        int(event.get("wet_frames") or 0)
        * int(config["model"]["frame_minutes"])
    )
    event["hydrograph_method"] = (
        "NRCS curve-number direct runoff + volume-conserving triangular routing"
    )



def classify_event(
    event: dict[str, Any], canyon: Canyon, config: dict[str, Any]
) -> tuple[str, str]:
    """Classify estimated pool response with transparent decision tests."""
    if event.get("radar_data_sufficient") is False:
        event["fill_ratio"] = None
        event["fill_ratio_range"] = {}
        event["decision_tests"] = {
            "radar_data_sufficient": False,
            "storage_target_met": None,
            "flush_target_met": None,
            "heavy_rain_footprint_observed": None,
            "minimum_wet_duration_met": None,
        }
        label = "Insufficient radar data"
        event["classification_explanation"] = (
            "Too much of the watershed radar image was unknown to make a "
            "rainfall or pool-refill classification."
        )
        event["condition_statement"] = label
        return "insufficient_data", label

    target = float(canyon.model["fill_target_ft3"])
    if target <= 0:
        raise ValueError(f"Invalid fill target for {canyon.canyon_id}: {target}")

    central_runoff = float(
        event.get("direct_runoff_ft3", event.get("estimated_runoff_ft3", 0.0))
        or 0.0
    )
    ratio = central_runoff / target
    event["fill_ratio"] = round(ratio, 2)

    runoff_range = event.get(
        "direct_runoff_ft3_range", event.get("estimated_runoff_ft3_range", {})
    )
    event["fill_ratio_range"] = {
        state: round(float(volume) / target, 2)
        for state, volume in runoff_range.items()
    }

    required_frames = int(config["model"]["minimum_wet_frames_for_likely"])
    enough_frames = int(event.get("wet_frames") or 0) >= required_frames
    footprint_observed = bool(event.get("spatial_gate_seen"))
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
        label = "Strong refill/flush potential — full pools possible"
        reason = (
            "Estimated watershed runoff was at least twice the provisional empty-storage "
            "target and the minimum wet-duration check passed. "
            "This indicates a strong refill/flush event, not a direct field observation."
        )
        code = "full_flush"
    elif storage_met and enough_frames:
        label = "Major refill likely — pools may be full"
        reason = (
            "Estimated watershed runoff met the provisional empty-storage target, and both "
            "the minimum wet-duration check passed. Existing pool "
            "levels and channel losses remain unknown."
        )
        code = "likely_full"
    elif storage_met:
        missing = []
        if not enough_frames:
            missing.append("minimum wet duration")
        label = "Potential major refill — confirmation tests incomplete"
        reason = (
            "Estimated watershed runoff met the provisional empty-storage target, but the "
            + " and ".join(missing)
            + " check"
            + ("s were" if len(missing) != 1 else " was")
            + " not met."
        )
        code = "moderate"
    elif ratio >= LARGE_REFILL_RATIO:
        label = "Large partial refill possible — full pools uncertain"
        reason = (
            f"Estimated watershed runoff was {ratio:.0%} of the provisional empty-storage "
            "target. That supports a large partial refill and could fill pools that were "
            "already partly full, but it does not meet the full empty-storage target."
        )
        code = "moderate"
    elif ratio >= SUBSTANTIAL_REFILL_RATIO:
        label = "Substantial partial refill possible"
        reason = (
            f"Estimated watershed runoff was {ratio:.0%} of the provisional empty-storage "
            "target. A meaningful partial refill is possible, but full pools are not supported."
        )
        code = "moderate"
    elif ratio >= MINOR_REFILL_RATIO:
        label = "Some pool refill possible"
        reason = (
            f"Estimated watershed runoff was {ratio:.0%} of the provisional empty-storage "
            "target. Some pools may have gained water, but the modeled volume is limited."
        )
        code = "moderate"
    else:
        label = "No meaningful pool refill indicated"
        reason = (
            "Estimated watershed runoff was below 25% of the provisional empty-storage "
            "target."
        )
        code = "minor"

    event["classification_explanation"] = reason
    event["condition_statement"] = label
    return code, label



def event_public(
    event: dict[str, Any],
    canyon: Canyon,
    config: dict[str, Any],
    include_grid: bool = True,
) -> dict[str, Any]:
    public = {
        key: value
        for key, value in event.items()
        if key not in {"accumulated_rain_grid_inches"}
    }
    apply_hydrologic_model(public, canyon, config)
    if not public.get("hydrology_available"):
        public.setdefault("direct_runoff_ft3", 0)
        public.setdefault("direct_runoff_ft3_range", {})
        public.setdefault("routed_peak_cfs", 0.0)
        public.setdefault("routed_peak_cfs_range", {})

    classification, label = classify_event(public, canyon, config)
    public["classification"] = classification
    public["classification_label"] = label
    public["atlas14_return_period_years"] = atlas_return_period(
        public, canyon, int(config["model"]["frame_minutes"])
    )
    public["atlas14_basis"] = "watershed-average radar rainfall"
    public["atlas14_duration_minutes"] = event_duration_minutes(
        public, int(config["model"]["frame_minutes"])
    )
    public["atlas14_depth_inches"] = public.get("basin_rain_inches")
    public["rainfall_depth_source"] = "base_reflectivity_zr_screening"
    public["storm_core_evidence_source"] = "base_reflectivity"
    public["experimental_model_applied"] = False
    public["storage_target_ft3"] = int(canyon.model["fill_target_ft3"])
    public["visible_storage_ft3"] = (
        ZERO_G_STORAGE_FT3 if canyon.canyon_id == "zerog" else None
    )
    public["estimated_hidden_storage_ft3"] = (
        0 if canyon.canyon_id == "zerog" else None
    )
    public["storage_uncertainty"] = (
        "measured visible depression benchmark"
        if canyon.canyon_id == "zerog"
        else "normalized total; visible and hidden components not yet surveyed"
    )
    public["flush_target_ft3"] = int(canyon.model["flush_target_ft3"])
    public["storage_deficit_ft3"] = max(
        0, int(canyon.model["fill_target_ft3"]) - int(public["direct_runoff_ft3"])
    )
    public["storage_excess_ft3"] = max(
        0, int(public["direct_runoff_ft3"]) - int(canyon.model["fill_target_ft3"])
    )

    event_time = parse_utc(public["peak_frame_utc"])
    viewer_query = urllib.parse.urlencode(
        {
            "prod": "usrad",
            "java": "script",
            "mode": "archive",
            "frames": max(12, int(public["frames"]) + 6),
            "interval": int(config["model"]["frame_minutes"]),
            "year": event_time.year,
            "month": event_time.month,
            "day": event_time.day,
            "hour": event_time.hour,
            "minute": event_time.minute,
        }
    )
    public["iem_archive_url"] = (
        "https://mesonet.agron.iastate.edu/current/mcview.phtml?"
        f"{viewer_query}"
    )
    if not include_grid:
        public.pop("peak_grid_dbz", None)
        public.pop("grid_bbox", None)
    return public



def analysis_has_rain(analysis: dict[str, Any], config: dict[str, Any]) -> bool:
    """Return whether a frame contains enough basin-average radar rain to retain."""
    threshold = float(
        config["model"].get("event_continue_minimum_basin_rain_inches", 0.0001)
    )
    return bool(
        analysis.get("rain_detected")
        or float(analysis.get("frame_basin_rain_inches") or 0.0) >= threshold
    )


def start_event(
    timestamp: datetime, analysis: dict[str, Any], rain: np.ndarray
) -> dict[str, Any]:
    """Start an event on a 25+ dBZ trigger frame."""
    return {
        "start_utc": utc_text(timestamp),
        "end_utc": utc_text(timestamp),
        "last_rain_utc": utc_text(timestamp),
        "frames": 1,
        "rain_frames": 1,
        "wet_frames": 1,
        "peak_dbz": analysis["maximum_dbz"],
        "peak_coverage_percent": dict(analysis["coverage_percent"]),
        "peak_covered_area_sq_mi": {
            str(int(rule["dbz"])): rule["covered_area_sq_mi"]
            for rule in analysis["spatial_rules"]
        },
        "basin_rain_inches": analysis["frame_basin_rain_inches"],
        "radar_rain_volume_ft3": analysis["frame_rain_volume_ft3"],
        "spatial_gate_seen": analysis["spatial_gate"],
        "max_pixel_storm_inches": round(float(np.nanmax(rain)), 3),
        "accumulated_rain_grid_inches": grid_list(rain, 4),
        "peak_grid_dbz": analysis["grid_dbz"],
        "grid_bbox": analysis["grid_bbox"],
        "peak_frame_utc": utc_text(timestamp),
        "peak_frame_rain_volume_ft3": analysis["frame_rain_volume_ft3"],
    }


def prepend_rain_frame(
    event: dict[str, Any],
    timestamp: datetime,
    analysis: dict[str, Any],
    rain: np.ndarray,
) -> None:
    """Add lower-reflectivity rain that immediately preceded the trigger frame."""
    event["start_utc"] = min(event["start_utc"], utc_text(timestamp))
    event["frames"] = int(event.get("frames") or 0) + 1
    event["rain_frames"] = int(event.get("rain_frames") or 0) + 1
    event["basin_rain_inches"] = round(
        float(event.get("basin_rain_inches") or 0.0)
        + float(analysis.get("frame_basin_rain_inches") or 0.0),
        4,
    )
    event["radar_rain_volume_ft3"] = round(
        float(event.get("radar_rain_volume_ft3") or 0.0)
        + float(analysis.get("frame_rain_volume_ft3") or 0.0)
    )
    event["peak_dbz"] = max(
        float(event.get("peak_dbz") or -999.0),
        float(analysis.get("maximum_dbz") or -999.0),
    )
    accumulated_values = event.get("accumulated_rain_grid_inches")
    if accumulated_values is not None:
        accumulated = np.asarray(accumulated_values, dtype=np.float32)
        if accumulated.shape == rain.shape:
            accumulated += rain
            event["accumulated_rain_grid_inches"] = grid_list(accumulated, 4)
            event["max_pixel_storm_inches"] = round(float(np.nanmax(accumulated)), 3)


def update_open_event(
    event: dict[str, Any],
    timestamp: datetime,
    analysis: dict[str, Any],
    rain: np.ndarray,
) -> None:
    """Add one measurable-rain frame to an active event."""
    event["end_utc"] = utc_text(timestamp)
    event["last_rain_utc"] = utc_text(timestamp)
    event["frames"] = int(event.get("frames") or 0) + 1
    event["rain_frames"] = int(event.get("rain_frames") or 0) + 1
    if analysis.get("wet"):
        event["wet_frames"] = int(event.get("wet_frames") or 0) + 1
    event["basin_rain_inches"] = round(
        float(event.get("basin_rain_inches") or 0.0)
        + float(analysis.get("frame_basin_rain_inches") or 0.0),
        4,
    )
    event["radar_rain_volume_ft3"] = round(
        float(event.get("radar_rain_volume_ft3") or 0.0)
        + float(analysis.get("frame_rain_volume_ft3") or 0.0)
    )
    event["spatial_gate_seen"] = bool(
        event.get("spatial_gate_seen") or analysis.get("spatial_gate")
    )
    event["peak_dbz"] = max(
        float(event.get("peak_dbz") or -999.0),
        float(analysis.get("maximum_dbz") or -999.0),
    )

    event.setdefault("peak_coverage_percent", {})
    event.setdefault("peak_covered_area_sq_mi", {})
    for key, value in (analysis.get("coverage_percent") or {}).items():
        event["peak_coverage_percent"][key] = max(
            event["peak_coverage_percent"].get(key, 0), value
        )
    for rule in analysis.get("spatial_rules") or []:
        key = str(int(rule["dbz"]))
        event["peak_covered_area_sq_mi"][key] = max(
            event["peak_covered_area_sq_mi"].get(key, 0),
            rule["covered_area_sq_mi"],
        )

    accumulated_values = event.get("accumulated_rain_grid_inches")
    if accumulated_values is not None:
        accumulated = np.asarray(accumulated_values, dtype=np.float32)
        if accumulated.shape == rain.shape:
            accumulated = accumulated + rain
            event["accumulated_rain_grid_inches"] = grid_list(accumulated, 4)
            event["max_pixel_storm_inches"] = round(
                float(np.nanmax(accumulated)),
                3,
            )

    previous_peak = float(
        event.get(
            "peak_frame_rain_volume_ft3",
            event.get("peak_frame_runoff_ft3", -1),
        )
    )
    has_grid = analysis.get("grid_dbz") is not None
    if has_grid and (
        event.get("peak_grid_dbz") is None
        or float(analysis.get("frame_rain_volume_ft3") or 0.0) >= previous_peak
    ):
        event["peak_frame_rain_volume_ft3"] = analysis[
            "frame_rain_volume_ft3"
        ]
        event["peak_frame_utc"] = utc_text(timestamp)
        event["peak_grid_dbz"] = analysis["grid_dbz"]
        event["grid_bbox"] = analysis["grid_bbox"]


def finalize_event(
    canyon_status: dict[str, Any], canyon: Canyon, config: dict[str, Any]
) -> None:
    event = canyon_status.get("open_event")
    if not event:
        return
    public = event_public(event, canyon, config)
    canyon_status["last_rain_event"] = public
    events = canyon_status.setdefault("events", [])
    if not events or events[0].get("start_utc") != public["start_utc"]:
        events.insert(0, event_public(event, canyon, config, include_grid=True))
    del events[int(config.get("max_retained_events_per_canyon", 50)) :]
    canyon_status["open_event"] = None


def prune_pending_rain(
    canyon_status: dict[str, Any],
    timestamp: datetime,
    gap_minutes: int,
) -> list[dict[str, Any]]:
    pending = canyon_status.setdefault("_pending_rain_frames", [])
    cutoff = timestamp - timedelta(minutes=gap_minutes)
    pending[:] = [
        item
        for item in pending
        if parse_utc(item["timestamp_utc"]) > cutoff
    ]
    return pending


def update_canyon_event(
    canyon_status: dict[str, Any],
    canyon: Canyon,
    timestamp: datetime,
    analysis: dict[str, Any],
    rain: np.ndarray,
    config: dict[str, Any],
) -> None:
    """Update one canyon using separate trigger, accumulation, and ending rules.

    A new event requires the configured dBZ trigger. Once triggered, every
    measurable basin-rain frame is accumulated. The event closes only after the
    configured dry gap has elapsed since the last measurable rain frame.
    """
    event = canyon_status.get("open_event")
    gap = int(config["model"]["event_gap_minutes"])
    rainy = analysis_has_rain(analysis, config)
    triggered = bool(analysis.get("wet"))

    if event:
        last_rain = parse_utc(
            event.get("last_rain_utc") or event.get("end_utc") or event["start_utc"]
        )
        if timestamp - last_rain >= timedelta(minutes=gap):
            finalize_event(canyon_status, canyon, config)
            event = None

    pending = prune_pending_rain(canyon_status, timestamp, gap)

    if event is None:
        if rainy and not triggered:
            pending.append(
                {
                    "timestamp_utc": utc_text(timestamp),
                    "analysis": {
                        key: value
                        for key, value in analysis.items()
                        if key not in {"grid_dbz", "grid_bbox"}
                    },
                    "rain_grid_zlib": encode_grid(grid_list(rain, 4)),
                }
            )
            return
        if not triggered:
            return

        event = start_event(timestamp, analysis, rain)
        for item in sorted(pending, key=lambda value: value["timestamp_utc"]):
            prepend_rain_frame(
                event,
                parse_utc(item["timestamp_utc"]),
                item["analysis"],
                np.asarray(decode_grid(item["rain_grid_zlib"]), dtype=np.float32),
            )
        pending.clear()
        canyon_status["open_event"] = event
    elif rainy:
        update_open_event(event, timestamp, analysis, rain)
    else:
        return

    public = event_public(event, canyon, config)
    canyon_status["last_rain_event"] = public
    if public["classification"] in {"likely_full", "full_flush"}:
        canyon_status["last_qualifying_event"] = public


def encode_grid(values: list[list[float | None]]) -> str:
    """Compress a radar grid for compact storage in the rolling frame ledger."""
    payload = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(payload, level=9)).decode("ascii")


def decode_grid(value: str) -> list[list[float | None]]:
    payload = zlib.decompress(base64.b64decode(value.encode("ascii")))
    return json.loads(payload.decode("utf-8"))


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    """Retain the event's peak radar grid for clickable 90-day history."""
    return dict(event)


def frame_summary(analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "maximum_dbz": analysis.get("maximum_dbz"),
        "frame_basin_rain_inches": analysis.get("frame_basin_rain_inches"),
        "frame_rain_volume_ft3": analysis.get("frame_rain_volume_ft3"),
        "spatial_gate": bool(analysis.get("spatial_gate")),
        "unknown_watershed_percent": analysis.get("unknown_watershed_percent"),
        "wet": bool(analysis.get("wet")),
        "rain_detected": bool(analysis.get("rain_detected")),
        "radar_data_quality": analysis.get("radar_data_quality"),
        "radar_data_sufficient": analysis.get("radar_data_sufficient"),
    }


def analyze_timestamp_record(
    timestamp: datetime,
    canyons: list[Canyon],
    global_grid: Grid,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Analyze one frame without mutating event totals."""
    global_image = fetch_radar_image(
        timestamp, global_grid, config, latest_reference
    )
    source = radar_frame_source(timestamp, latest_reference, config)
    record: dict[str, Any] = {
        "frame_utc": utc_text(timestamp),
        "source": source,
        "confirmed": source == "historical",
        "processed_utc": utc_text(datetime.now(UTC)),
        "summary": {},
        "wet_canyons": {},
    }
    summary: dict[str, Any] = {}
    for canyon in canyons:
        image = crop_for_grid(global_image, global_grid, canyon.grid)
        analysis, rain = analyze_canyon_image(image, canyon, palette, config)
        analysis["frame_utc"] = utc_text(timestamp)
        compact = frame_summary(analysis)
        record["summary"][canyon.canyon_id] = compact
        summary[canyon.canyon_id] = compact
        if analysis_has_rain(analysis, config) or analysis["wet"]:
            stored_analysis = {
                key: value for key, value in analysis.items() if key != "grid_dbz"
            }
            record["wet_canyons"][canyon.canyon_id] = {
                "analysis": stored_analysis,
                "grid_dbz_zlib": encode_grid(analysis["grid_dbz"]),
                "rain_grid_zlib": encode_grid(grid_list(rain, 4)),
            }
    return record, summary


def note_planned_ledger_start(status: dict[str, Any], timestamp: datetime) -> None:
    """Remember the first timestamp that should exist, even if its fetch fails."""
    planned = floor_five_minutes(timestamp)
    existing = status.get("ledger_started_utc")
    if not existing or planned < parse_utc(existing):
        status["ledger_started_utc"] = utc_text(planned)


def upsert_frame_record(status: dict[str, Any], record: dict[str, Any]) -> bool:
    """Insert or replace one timestamp; confirmed archive data wins over provisional."""
    ledger = status.setdefault("frame_ledger", {})
    key = str(record["frame_utc"])
    existing = ledger.get(key)
    if existing and existing.get("confirmed") and not record.get("confirmed"):
        return False
    ledger[key] = record
    note_planned_ledger_start(status, parse_utc(key))
    timestamps = sorted(ledger)
    status["latest_frame_utc"] = timestamps[-1] if timestamps else None
    provisional = [
        value["frame_utc"]
        for value in ledger.values()
        if not value.get("confirmed")
    ]
    status["latest_provisional_frame_utc"] = max(provisional) if provisional else None
    return True


def event_end_utc(event: dict[str, Any]) -> datetime:
    return parse_utc(event.get("end_utc") or event["start_utc"])


def dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        if not event or not event.get("start_utc"):
            continue
        key = (event["start_utc"], event.get("end_utc") or event["start_utc"])
        prior = unique.get(key)
        # Prefer the copy that still has a retained radar grid.
        if prior is None or (
            event.get("peak_grid_dbz") is not None
            and prior.get("peak_grid_dbz") is None
        ):
            unique[key] = event
    return sorted(unique.values(), key=event_end_utc, reverse=True)


def preserved_events_before(
    canyon_status: dict[str, Any], cutoff: datetime
) -> list[dict[str, Any]]:
    candidates = list(canyon_status.get("events", []))
    for key in ("last_rain_event", "last_qualifying_event"):
        event = canyon_status.get(key)
        if event:
            candidates.append(event)
    return [
        event
        for event in dedupe_events(candidates)
        if event_end_utc(event) < cutoff
    ]


def dry_analysis_from_summary(
    timestamp: datetime,
    summary: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    values = summary or {}
    maximum = values.get("maximum_dbz")
    wet = bool(
        maximum is not None
        and float(maximum) >= float(config["model"]["storm_dbz_threshold"])
    )
    rain_detected = bool(
        values.get("rain_detected")
        or float(values.get("frame_basin_rain_inches") or 0.0)
        >= float(
            config["model"].get(
                "event_continue_minimum_basin_rain_inches",
                0.0001,
            )
        )
    )
    return {
        "frame_utc": utc_text(timestamp),
        "maximum_dbz": values.get("maximum_dbz"),
        "coverage_percent": {},
        "spatial_rules": [],
        "spatial_gate": bool(values.get("spatial_gate")),
        "frame_basin_rain_inches": float(
            values.get("frame_basin_rain_inches") or 0.0
        ),
        "frame_rain_volume_ft3": int(values.get("frame_rain_volume_ft3") or 0),
        "wet": wet,
        "rain_detected": rain_detected,
        "unknown_watershed_percent": values.get("unknown_watershed_percent"),
        "radar_data_quality": values.get("radar_data_quality", "legacy_summary"),
        "radar_data_sufficient": values.get("radar_data_sufficient", True),
        "grid_dbz": None,
        "grid_bbox": None,
    }



def cumulative_refill_evidence(
    canyon_status: dict[str, Any],
    canyon: Canyon,
    config: dict[str, Any],
) -> None:
    """Build seven-day evidence, 90-day details, and permanent peak records."""
    candidates = list(canyon_status.get("events", []))
    latest = canyon_status.get("last_rain_event")
    if latest:
        candidates.append(latest)

    events = [
        event
        for event in dedupe_events(candidates)
        if event.get("direct_runoff_ft3") is not None
        or event.get("estimated_runoff_ft3") is not None
    ]
    events.sort(
        key=lambda event: parse_utc(
            event.get("end_utc") or event.get("start_utc")
        )
    )

    target = float(canyon.model["fill_target_ft3"])
    window_days = int(config.get("recent_refill_window_days", 7))
    retention_days = int(config.get("event_detail_retention_days", 90))
    now = datetime.now(timezone.utc)
    if events and now < event_end_utc(events[-1]):
        now = event_end_utc(events[-1])

    records = canyon_status.get("historical_records") or {}
    peak_individual = records.get("peak_individual_event")
    peak_seven_day = records.get("peak_seven_day_evidence")

    def event_record(event: dict[str, Any], ratio: float) -> dict[str, Any]:
        return {
            "start_utc": event.get("start_utc"),
            "end_utc": event.get("end_utc"),
            "peak_frame_utc": event.get("peak_frame_utc"),
            "fill_ratio": round(ratio, 3),
            "percent": min(100, round(ratio * 100)),
            "classification_label": event.get("classification_label"),
            "basin_rain_inches": event.get("basin_rain_inches"),
            "direct_runoff_ft3": event.get(
                "direct_runoff_ft3", event.get("estimated_runoff_ft3")
            ),
            "peak_dbz": event.get("peak_dbz"),
            "peak_grid_dbz": event.get("peak_grid_dbz"),
            "grid_bbox": event.get("grid_bbox"),
        }

    for index, event in enumerate(events):
        ratio = max(0.0, float(event.get("fill_ratio") or 0.0))
        prior_ratio = float((peak_individual or {}).get("fill_ratio") or -1.0)
        prior_time = (
            parse_utc(peak_individual.get("end_utc") or peak_individual["start_utc"])
            if peak_individual
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        if ratio > prior_ratio or (
            ratio == prior_ratio and event_end_utc(event) > prior_time
        ):
            peak_individual = event_record(event, ratio)

        event_time = event_end_utc(event)
        rolling_ratio = 0.0
        for candidate in events[: index + 1]:
            age_days = (event_time - event_end_utc(candidate)).total_seconds() / 86400
            if 0 <= age_days < window_days:
                rolling_ratio += max(
                    0.0, float(candidate.get("fill_ratio") or 0.0)
                ) * (1.0 - age_days / window_days)
        prior_rolling = float((peak_seven_day or {}).get("ratio") or -1.0)
        prior_rolling_time = (
            parse_utc(peak_seven_day["through_utc"])
            if peak_seven_day
            else datetime.min.replace(tzinfo=timezone.utc)
        )
        if rolling_ratio > prior_rolling or (
            rolling_ratio == prior_rolling and event_time > prior_rolling_time
        ):
            peak_seven_day = {
                "through_utc": event.get("end_utc") or event.get("start_utc"),
                "ratio": round(rolling_ratio, 3),
                "percent": min(100, round(rolling_ratio * 100)),
                "peak_frame_utc": event.get("peak_frame_utc"),
                "peak_grid_dbz": event.get("peak_grid_dbz"),
                "grid_bbox": event.get("grid_bbox"),
            }

    def evidence_at(reference: datetime) -> float:
        total = 0.0
        for event in events:
            age_days = (reference - event_end_utc(event)).total_seconds() / 86400
            if 0 <= age_days < window_days:
                total += max(0.0, float(event.get("fill_ratio") or 0.0)) * (
                    1.0 - age_days / window_days
                )
        return total

    recent_ratio = evidence_at(now)
    prior_day_ratio = evidence_at(now - timedelta(days=1))
    if recent_ratio > prior_day_ratio + 0.02:
        trend = "increasing"
    elif recent_ratio < prior_day_ratio - 0.02:
        trend = "fading"
    elif recent_ratio > 0:
        trend = "holding"
    else:
        trend = "no recent evidence"

    meaningful = [
        event for event in events if float(event.get("fill_ratio") or 0.0) >= 0.25
    ]
    canyon_status["recent_refill_evidence"] = {
        "window_days": window_days,
        "percent": min(100, round(recent_ratio * 100)),
        "ratio": round(recent_ratio, 3),
        "through_utc": utc_text(now),
        "last_meaningful_event_utc": (
            meaningful[-1].get("end_utc") or meaningful[-1].get("start_utc")
            if meaningful
            else None
        ),
        "trend": trend,
        "description": "Age-weighted modeled refill evidence; not observed pool level.",
    }
    canyon_status["historical_records"] = {
        "peak_individual_event": peak_individual,
        "peak_seven_day_evidence": peak_seven_day,
    }

    detail_cutoff = now - timedelta(days=retention_days)
    canyon_status["events"] = [
        event
        for event in canyon_status.get("events", [])
        if event_end_utc(event) >= detail_cutoff
    ]

    balance = 0.0
    overflow_total = 0.0
    milestones: dict[str, str | None] = {
        "25": None,
        "50": None,
        "75": None,
        "100": None,
    }
    history: list[dict[str, Any]] = []

    for event in events:
        runoff = float(
            event.get("direct_runoff_ft3", event.get("estimated_runoff_ft3", 0.0))
            or 0.0
        )
        before = balance
        raw_after = before + max(0.0, runoff)
        balance = min(target, raw_after)
        overflow = max(0.0, raw_after - target)
        overflow_total += overflow
        ratio = balance / target if target > 0 else 0.0
        timestamp = event.get("end_utc") or event.get("start_utc")

        for percent in (25, 50, 75, 100):
            key = str(percent)
            if milestones[key] is None and ratio + 1e-9 >= percent / 100.0:
                milestones[key] = timestamp

        history.append(
            {
                "start_utc": event.get("start_utc"),
                "end_utc": event.get("end_utc"),
                "classification": event.get("classification"),
                "classification_label": event.get("classification_label"),
                "basin_rain_inches": event.get("basin_rain_inches"),
                "direct_runoff_ft3": round(runoff),
                "event_fill_ratio": event.get("fill_ratio"),
                "balance_before_ft3": round(before),
                "cumulative_balance_ft3": round(balance),
                "cumulative_ratio": round(ratio, 2),
                "cumulative_percent": min(100, round(ratio * 100)),
                "overflow_ft3": round(overflow),
            }
        )

    canyon_status["refill_history"] = [
        item
        for item in reversed(history)
        if parse_utc(item.get("end_utc") or item["start_utc"]) >= detail_cutoff
    ]
    canyon_status["cumulative_refill_evidence"] = {
        "event_count": len(history),
        "period_start_utc": history[0]["start_utc"] if history else None,
        "through_utc": history[-1]["end_utc"] if history else None,
        "balance_ft3": round(balance),
        "ratio": round(balance / target, 2) if target > 0 else 0.0,
        "percent": min(100, round(balance / target * 100)) if target > 0 else 0,
        "overflow_ft3": round(overflow_total),
        "milestones_utc": milestones,
        "loss_model": "not_modeled",
        "assumption": (
            "Starts at zero storage at the first retained modeled event and "
            "subtracts no evaporation, seepage, drainage, or other losses."
        ),
    }


def rebuild_events_from_ledger(
    status: dict[str, Any], canyons: list[Canyon], config: dict[str, Any]
) -> None:
    """Rebuild recent storms deterministically from timestamp-keyed frame records."""
    ledger = status.get("frame_ledger", {})
    if not ledger:
        return
    frame_keys = sorted(ledger)
    cutoff = parse_utc(frame_keys[0])

    for canyon in canyons:
        current = status["canyons"][canyon.canyon_id]
        notification = dict(current.get("notification") or {})
        historical_records = dict(current.get("historical_records") or {})
        preserved = preserved_events_before(current, cutoff)
        rebuilt = empty_canyon_status(canyon)
        rebuilt["historical_records"] = historical_records
        rebuilt["events"] = [compact_event(event) for event in preserved]
        rebuilt["last_rain_event"] = preserved[0] if preserved else None
        rebuilt["last_qualifying_event"] = next(
            (
                event
                for event in preserved
                if event.get("classification") in {"likely_full", "full_flush"}
            ),
            None,
        )
        rebuilt["notification"] = notification

        for key in frame_keys:
            timestamp = parse_utc(key)
            record = ledger[key]
            wet_record = record.get("wet_canyons", {}).get(canyon.canyon_id)
            if wet_record:
                analysis = dict(wet_record["analysis"])
                analysis["grid_dbz"] = decode_grid(wet_record["grid_dbz_zlib"])
                encoded_rain = wet_record.get("rain_grid_zlib")
                rain = (
                    np.asarray(decode_grid(encoded_rain), dtype=np.float32)
                    if encoded_rain
                    else rain_depth_inches(
                        np.asarray(analysis["grid_dbz"], dtype=np.float32),
                        config["model"],
                    )
                )
            else:
                analysis = dry_analysis_from_summary(
                    timestamp,
                    record.get("summary", {}).get(canyon.canyon_id),
                    config,
                )
                rain = np.zeros((1, 1), dtype=np.float32)
            rebuilt["latest_analysis"] = analysis
            update_canyon_event(
                rebuilt, canyon, timestamp, analysis, rain, config
            )

        open_event = rebuilt.get("open_event")
        if open_event:
            public = event_public(open_event, canyon, config, include_grid=True)
            rebuilt["last_rain_event"] = public
            if public.get("classification") in {"likely_full", "full_flush"}:
                rebuilt["last_qualifying_event"] = public
        elif rebuilt.get("events") and rebuilt.get("last_rain_event") is None:
            rebuilt["last_rain_event"] = rebuilt["events"][0]

        if rebuilt.get("last_qualifying_event") is None:
            rebuilt["last_qualifying_event"] = next(
                (
                    event
                    for event in rebuilt.get("events", [])
                    if event.get("classification") in {"likely_full", "full_flush"}
                ),
                None,
            )
        maximum_events = int(config.get("max_retained_events_per_canyon", 50))
        rebuilt["events"] = [
            compact_event(event)
            for event in dedupe_events(rebuilt.get("events", []))[:maximum_events]
        ]
        rebuilt.pop("_pending_rain_frames", None)
        cumulative_refill_evidence(rebuilt, canyon, config)
        status["canyons"][canyon.canyon_id] = rebuilt


def protected_ledger_cutoff(
    status: dict[str, Any], latest_reference: datetime, config: dict[str, Any]
) -> datetime:
    cutoff = latest_reference - timedelta(
        hours=float(config.get("frame_ledger_retention_hours", 72))
    )
    gap = timedelta(minutes=int(config["model"]["event_gap_minutes"]))
    for canyon_status in status.get("canyons", {}).values():
        for key in ("open_event", "last_rain_event"):
            event = canyon_status.get(key)
            if not event or not event.get("start_utc"):
                continue
            start = parse_utc(event["start_utc"])
            end = event_end_utc(event)
            if start < cutoff <= end + gap:
                cutoff = start
    return floor_five_minutes(cutoff)


def prune_frame_ledger(
    status: dict[str, Any], latest_reference: datetime, config: dict[str, Any]
) -> None:
    ledger = status.setdefault("frame_ledger", {})
    cutoff = protected_ledger_cutoff(status, latest_reference, config)
    for key in list(ledger):
        if parse_utc(key) < cutoff:
            del ledger[key]
    keys = sorted(ledger)
    if keys:
        existing_start = (
            parse_utc(status["ledger_started_utc"])
            if status.get("ledger_started_utc")
            else parse_utc(keys[0])
        )
        status["ledger_started_utc"] = utc_text(max(existing_start, cutoff))
        status["latest_frame_utc"] = keys[-1]
    else:
        status["ledger_started_utc"] = None
        status["latest_frame_utc"] = None


def update_frame_health(
    status: dict[str, Any], latest_reference: datetime, config: dict[str, Any]
) -> None:
    ledger = status.get("frame_ledger", {})
    delay = int(
        config.get(
            "archive_confirmation_delay_minutes",
            config.get("historical_wms_min_age_minutes", 10),
        )
    )
    warning_age = int(config.get("missing_frame_warning_minutes", 20))
    confirmation_end = floor_five_minutes(
        latest_reference - timedelta(minutes=delay)
    )
    started_text = status.get("ledger_started_utc")
    if not started_text or parse_utc(started_text) > confirmation_end:
        expected: list[datetime] = []
    else:
        expected = list(
            iter_five_minutes(parse_utc(started_text), confirmation_end)
        )

    missing = [
        timestamp
        for timestamp in expected
        if not ledger.get(utc_text(timestamp), {}).get("confirmed")
    ]
    stale = [
        timestamp
        for timestamp in missing
        if latest_reference - timestamp >= timedelta(minutes=warning_age)
    ]
    status["missing_archive_frames_utc"] = [utc_text(value) for value in missing]
    status["stale_missing_archive_frames_utc"] = [
        utc_text(value) for value in stale
    ]
    earliest_missing = missing[0] if missing else None
    status["earliest_missing_archive_frame_utc"] = (
        utc_text(earliest_missing) if earliest_missing else None
    )
    status["manual_replay_from_utc"] = (
        utc_text(earliest_missing) if earliest_missing else None
    )

    confirmed_through: datetime | None = None
    for timestamp in expected:
        if not ledger.get(utc_text(timestamp), {}).get("confirmed"):
            break
        confirmed_through = timestamp
    status["latest_archive_confirmed_frame_utc"] = (
        utc_text(confirmed_through) if confirmed_through else None
    )

    provisional = [
        parse_utc(key)
        for key, value in ledger.items()
        if not value.get("confirmed")
    ]
    status["latest_provisional_frame_utc"] = (
        utc_text(max(provisional)) if provisional else None
    )
    status["latest_frame_utc"] = max(ledger) if ledger else None

    confirmed_text = (
        utc_text(confirmed_through) if confirmed_through else "not established"
    )
    status["health"] = {
        "ok": not stale,
        "message": (
            f"All expected five-minute radar frames are archive-confirmed through "
            f"{confirmed_text}. "
            f"{len(missing)} archive frame"
            f"{'s are' if len(missing) != 1 else ' is'} missing. "
            f"{len(provisional)} newer live frame"
            f"{'s are' if len(provisional) != 1 else ' is'} awaiting archive confirmation."
        ),
        "latest_iem_frame_utc": utc_text(latest_reference),
        "archive_confirmation_delay_minutes": delay,
        "missing_archive_frame_count": len(missing),
        "stale_missing_archive_frame_count": len(stale),
        "provisional_frame_count": len(provisional),
        "earliest_missing_archive_frame_utc": (
            utc_text(earliest_missing) if earliest_missing else None
        ),
        "manual_replay_from_utc": (
            utc_text(earliest_missing) if earliest_missing else None
        ),
        "frame_ledger_count": len(ledger),
    }


def scheduled_timestamps(
    status: dict[str, Any], config: dict[str, Any], latest_complete: datetime
) -> list[datetime]:
    """Return catch-up timestamps plus an overlapping live/archive reconciliation window."""
    frame_minutes = int(config["model"]["frame_minutes"])
    overlap_minutes = int(config.get("reconciliation_window_minutes", 90))
    overlap_start = floor_five_minutes(
        latest_complete - timedelta(minutes=overlap_minutes)
    )
    overlap = list(iter_five_minutes(overlap_start, latest_complete))

    ledger = status.get("frame_ledger", {})
    catchup_candidates: list[datetime] = []
    for value in status.get("missing_archive_frames_utc", []):
        timestamp = parse_utc(value)
        if timestamp < overlap_start:
            catchup_candidates.append(timestamp)

    confirmed = status.get("latest_archive_confirmed_frame_utc")
    if confirmed:
        catchup_start = parse_utc(confirmed) + timedelta(minutes=frame_minutes)
    elif status.get("ledger_started_utc"):
        catchup_start = parse_utc(status["ledger_started_utc"])
    else:
        catchup_start = floor_five_minutes(
            latest_complete
            - timedelta(minutes=int(config.get("schedule_lookback_minutes", 180)))
        )
    catchup_end = overlap_start - timedelta(minutes=frame_minutes)
    if catchup_start <= catchup_end:
        for timestamp in iter_five_minutes(catchup_start, catchup_end):
            record = ledger.get(utc_text(timestamp))
            if not record or not record.get("confirmed"):
                catchup_candidates.append(timestamp)

    catchup = sorted(set(catchup_candidates))[
        : int(config.get("max_catchup_frames_per_run", 72))
    ]
    maximum = int(config.get("max_frames_per_run", 96))
    room = max(0, maximum - len(overlap))
    selected = sorted(set(catchup[:room] + overlap))
    return selected


def process_timestamp(
    timestamp: datetime,
    status: dict[str, Any],
    canyons: list[Canyon],
    global_grid: Grid,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for one timestamp using the frame ledger."""
    record, summary = analyze_timestamp_record(
        timestamp, canyons, global_grid, palette, config, latest_reference
    )
    upsert_frame_record(status, record)
    rebuild_events_from_ledger(status, canyons, config)
    if latest_reference is not None:
        update_frame_health(status, latest_reference, config)
    return summary


def rewind_status(
    status: dict[str, Any],
    canyons: list[Canyon],
    rebuild_from: datetime,
) -> None:
    """Remove only frames/events at or after a replay cutoff; retain full old grids."""
    cutoff = floor_five_minutes(rebuild_from)
    ledger = status.setdefault("frame_ledger", {})
    for key in list(ledger):
        if parse_utc(key) >= cutoff:
            del ledger[key]

    for canyon in canyons:
        canyon_status = status["canyons"][canyon.canyon_id]
        retained = preserved_events_before(canyon_status, cutoff)[
            : int(50)
        ]
        canyon_status["events"] = [compact_event(event) for event in retained]
        canyon_status["open_event"] = None
        canyon_status["latest_analysis"] = None
        canyon_status["last_rain_event"] = retained[0] if retained else None
        canyon_status["last_qualifying_event"] = next(
            (
                event
                for event in retained
                if event.get("classification") in {"likely_full", "full_flush"}
            ),
            None,
        )

    keys = sorted(ledger)
    status["ledger_started_utc"] = keys[0] if keys else None
    status["latest_frame_utc"] = keys[-1] if keys else None
    status["latest_provisional_frame_utc"] = None
    status["latest_archive_confirmed_frame_utc"] = None
    status["missing_archive_frames_utc"] = []
    status["stale_missing_archive_frames_utc"] = []
    status["earliest_missing_archive_frame_utc"] = None
    status["manual_replay_from_utc"] = None
    status["last_checked_utc"] = None
    status["health"] = {
        "ok": True,
        "message": f"Radar history rewound to {utc_text(cutoff)} for exact replay",
    }


def model_metadata(
    canyons: list[Canyon], config: dict[str, Any]
) -> dict[str, Any]:
    model = config["model"]
    return {
        "schema_version": 3,
        "method": {
            "radar_source": "Iowa Environmental Mesonet N0Q 5-minute composite",
            "rainfall_formula": (
                f"Z = {model['zr_a']} × R^{model['zr_b']}; dBZ capped at "
                f"{model['rain_dbz_cap']} for rainfall-volume conversion"
            ),
            "rainfall_explanation": (
                "The tracker converts each five-minute radar frame to rainfall, then "
                "area-weights the pixels inside the watershed polygon. Radar rainfall "
                "is an estimate and may be biased by hail, beam geometry, or evaporation. "
                "Base reflectivity remains the storm-core evidence source. MRMS QPE can "
                "be evaluated beside it later without replacing the existing gates."
            ),
            "rain_event_explanation": (
                f"A new event requires at least {model['storm_dbz_threshold']} dBZ "
                "somewhere in the watershed. Once triggered, every later frame with at "
                "least 0.0001 inch of basin-average radar-estimated rain is added, including "
                f"echoes down to the {model['minimum_rain_dbz']} dBZ rainfall-conversion floor. "
                f"The event closes after {model['event_gap_minutes']} consecutive minutes "
                "without measurable basin-average radar rain. Lower-intensity rain immediately "
                "before the trigger is also included when it falls within that dry-gap window."
            ),
            "frame_reconciliation_explanation": (
                "Each run rechecks an overlapping 90-minute window. Newest frames are "
                "provisional; once the exact timestamped IEM WMS-T frame is old enough, "
                "it replaces the provisional record. Events are rebuilt from a "
                "timestamp-keyed ledger so repeated frames cannot double-count rainfall. "
                "Every retained rain frame includes its complete compressed spatial rain "
                "grid, so moving cores and pre-trigger rainfall accumulate cell by cell."
            ),
            "radar_quality_explanation": (
                f"Coverage and rainfall use only decoded radar cells. A frame with more "
                f"than {config.get('maximum_unknown_watershed_percent', 20)}% unknown "
                "watershed area is reported as insufficient radar data, not zero rain."
            ),
            "experimental_comparison_explanation": (
                "Weak-echo persistence, connected-core area, watershed-size scaling, "
                "MRMS QPE, and spatial curve-number runoff are reserved as disabled "
                "comparison modes until historical and field calibration show that they "
                "outperform the fixed baseline."
            ),
            "runoff_formula": (
                "Adjusted NRCS direct runoff: S0.20 = 1000/CN − 10; "
                "S0.05 = 1.33 × S0.20^1.15; Ia = 0.05S0.05; "
                "Q = (P − Ia)²/(P + 0.95S0.05) when P > Ia"
            ),
            "direct_runoff_explanation": (
                "No fixed runoff coefficient is used. Accumulated basin-average radar "
                "rainfall is converted to dry, normal, and wet direct-runoff estimates "
                "with canyon-specific composite curve numbers from SSURGO soils and "
                "2021 NLCD land cover. Traditional table CNs are converted to the "
                "Ia/S = 0.05 retention basis before runoff is calculated. Pixels "
                "without a usable SSURGO group are conservatively assigned to HSG D. "
                "The central display uses the normal condition."
            ),
            "peak_flow_formula": (
                "Screening peak CFS = 2 × direct-runoff volume ÷ triangular hydrograph "
                "base time; base time = rain duration + 2 × NRCS watershed lag"
            ),
            "peak_flow_explanation": (
                "Peak flow is a routed screening estimate. Lag uses USGS 3DEP terrain, "
                "the supplied outlet, and basin extent. It is not used by itself to declare "
                "pools full."
            ),
            "target_formula": (
                "Fill target = 52,442 ft³ × (technical-section length ÷ 0.75 mi) "
                "× (1 + canyon pothole modifier)"
            ),
            "target_explanation": (
                "Zero G is anchored to the 1-meter depression inventory of 114 depressions "
                "totaling 1,485.0 m³ (52,442 ft³). Other canyons are normalized by user-"
                "supplied technical-section length and adjusted for relative pothole/pool "
                "storage density. These are provisional empty-storage targets."
            ),
            "spatial_formula": (
                "50+ dBZ over 50% of the watershed, or 55+ dBZ over 25%, "
                "or 60+ dBZ over 10%"
            ),
            "spatial_explanation": (
                "These historical watershed-percentage comparisons are retained only as "
                "storm context. They do not control the canyon-condition classification."
            ),
            "fill_ratio_explanation": (
                "Estimated fill ratio = normal-condition NRCS watershed direct runoff ÷ provisional "
                "empty-pool storage target. It is not a measured pool-depth percentage and does "
                "not explicitly subtract channel transmission losses."
            ),
            "cumulative_refill_explanation": (
                "Current refill evidence combines modeled event fill ratios from the previous "
                "seven days with a linear age weight. New zero-runoff events do not erase prior "
                "evidence. Detailed events and their peak radar maps are retained for 90 days; "
                "all-time individual-event and seven-day records are retained separately."
            ),
            "pool_loss_explanation": (
                "The seven-day weighting represents evidence freshness, not physical pool-water "
                "loss. Pool geometry, shade, wind, temperature, humidity, seepage, and drainage "
                "remain uncalibrated and can produce very different hydroperiods."
            ),
            "atlas_explanation": (
                "Atlas 14 context compares event-duration watershed-average radar rainfall "
                "with duration-interpolated NOAA Atlas 14 point-frequency depths at the "
                "canyon outlet. It is context, not a watershed return interval. The "
                "comparison is suppressed when the event exceeds the longest available "
                "duration instead of being clamped to a shorter storm."
            ),
            "scaling_basis": (
                "Technical-section length replaces drainage-area scaling for pool storage. "
                "Drainage area remains in the runoff calculation because it controls how "
                "much watershed runoff a given rain depth can generate."
            ),
            "condition_language": (
                "Condition statements describe modeled refill evidence, not observed pool depth. "
                "Below 25% of the empty-storage target is reported as no meaningful refill "
                "unless an intense-rain footprint is detected. 'Likely full' requires the "
                "storage-volume, intense-rain footprint, "
                "and minimum-duration tests to pass together."
            ),
            "sources": [
                {
                    "label": "IEM N0Q composite documentation",
                    "url": "https://mesonet.agron.iastate.edu/docs/nexrad_composites/",
                },
                {
                    "label": "IEM N0Q raster and dBZ encoding",
                    "url": "https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=2",
                },
                {
                    "label": "NWS radar rainfall estimation and default Z–R relationship",
                    "url": "https://www.weather.gov/mrx/radarrainfallestimates",
                },
                {
                    "label": "NOAA Atlas 14 precipitation frequency",
                    "url": "https://hdsc.nws.noaa.gov/pfds/",
                },
                {
                    "label": "USDA Web Soil Survey / SSURGO",
                    "url": "https://websoilsurvey.nrcs.usda.gov/",
                },
                {
                    "label": "USGS National Land Cover Database",
                    "url": "https://www.usgs.gov/centers/eros/science/national-land-cover-database",
                },
                {
                    "label": "USGS 3D Elevation Program",
                    "url": "https://www.usgs.gov/3d-elevation-program",
                },
                {
                    "label": "NRCS runoff curve-number method",
                    "url": "https://directives.nrcs.usda.gov/sites/default/files2/1720460920/Chapter%2010%20-%20Estimation%20of%20Direct%20Runoff%20from%20Storm%20Rainfall.pdf",
                },
                {
                    "label": "Hawkins et al. adjusted 0.05 initial-abstraction method",
                    "url": "https://ponce.sdsu.edu/hawkins_initial_abstraction.pdf",
                },
                {
                    "label": "Rainfall minimum inter-event-time methodology",
                    "url": "https://www.tucson.ars.ag.gov/unit/publications/PDFfiles/2470.pdf",
                },
                {
                    "label": "National Park Service ephemeral-pool hydroperiod overview",
                    "url": "https://www.nps.gov/articles/ephemeral-pools.htm",
                },
            ],
            "classification": {
                "minor": (
                    "Normal-condition runoff below 25% of the empty-storage target and no "
                    "intense-rain footprint: no meaningful refill indicated"
                ),
                "some_refill": (
                    "Runoff ratio from 0.25 through 0.49: some pool refill possible"
                ),
                "substantial_partial": (
                    "Runoff ratio from 0.50 through 0.74: substantial partial refill possible"
                ),
                "large_partial": (
                    "Runoff ratio from 0.75 through 0.99: large partial refill possible; "
                    "full pools remain uncertain"
                ),
                "confirmation_incomplete": (
                    "Runoff ratio at least 1.0 without both confirmation tests: the empty-storage "
                    "volume threshold was met, but a likely-full statement is withheld"
                ),
                "likely_full": (
                    "Runoff ratio at least 1.0, intense-rain footprint reached, and at "
                    "least two wet five-minute frames: major refill likely; pools may be full"
                ),
                "full_flush": (
                    "Runoff ratio at least 2.0, intense-rain footprint reached, and at "
                    "least two wet five-minute frames: strong refill/flush potential; "
                    "full pools possible"
                ),
            },
            "limitations": [
                (
                    "Only Zero G has a mapped depression-volume anchor. Other storage "
                    "targets depend on technical-section lengths and user-assigned "
                    "morphology modifiers."
                ),
                (
                    "NRCS direct runoff is generated watershed runoff, not a measurement "
                    "of water delivered to every pothole. Bedrock fractures, channel "
                    "transmission losses, diversions, and disconnected subbasins can reduce delivery."
                ),
                (
                    "Existing pool level is unknown. Cumulative no-loss evidence preserves "
                    "multiple storm contributions, but evaporation, seepage, drainage, and "
                    "starting pool level are not yet modeled."
                ),
                (
                    "Radar reflectivity is an indirect rainfall estimate; hail and radar "
                    "sampling can bias both rainfall volume and the intense-rain footprint."
                ),
                (
                    "Peak CFS and NOAA Atlas 14 equivalent are context only and do not "
                    "independently determine pool condition."
                ),
            ],
        },
        "canyons": {
            canyon.canyon_id: {
                "name": canyon.name,
                "area_sq_mi": canyon.area_sq_mi,
                "outlet": canyon.outlet,
                **canyon.model,
                "atlas14_inches": canyon.atlas14,
            }
            for canyon in canyons
        },
    }


def save_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument(
        "--watersheds", type=Path, default=ROOT / "watersheds.geojson"
    )
    parser.add_argument("--atlas", type=Path, default=ROOT / "atlas14.json")
    parser.add_argument(
        "--hydrology", type=Path, default=ROOT / "hydrology.json"
    )
    parser.add_argument(
        "--palette", type=Path, default=ROOT / "n0q_palette.json"
    )
    parser.add_argument(
        "--status", type=Path, default=ROOT / "docs/data/status.json"
    )
    parser.add_argument(
        "--model-output", type=Path, default=ROOT / "docs/data/model.json"
    )
    parser.add_argument(
        "--at", help="Analyze one UTC frame, for example 2024-06-21T22:25:00Z"
    )
    parser.add_argument(
        "--rebuild-from",
        help=(
            "Replay all five-minute frames from this UTC time while preserving "
            "older event history, for example 2026-07-24T22:00:00Z"
        ),
    )
    parser.add_argument(
        "--run-trigger",
        default="local",
        help="Workflow trigger name, normally schedule or workflow_dispatch",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--require-existing-state",
        action="store_true",
        help="Fail instead of starting fresh when operational state is missing or invalid",
    )
    arguments = parser.parse_args()

    config = json.loads(arguments.config.read_text(encoding="utf-8"))
    collection = json.loads(arguments.watersheds.read_text(encoding="utf-8"))
    atlas = json.loads(arguments.atlas.read_text(encoding="utf-8"))
    hydrology = (
        json.loads(arguments.hydrology.read_text(encoding="utf-8"))
        if arguments.hydrology.exists()
        else {}
    )
    canyons, global_grid = build_canyons(
        collection, atlas, config, hydrology
    )
    palette = load_palette(arguments.palette)
    status = load_status(
        arguments.status, canyons, require_existing=arguments.require_existing_state
    )
    refresh_status_events(status, canyons, config)
    if arguments.rebuild_from:
        rewind_status(status, canyons, parse_utc(arguments.rebuild_from))
    save_json(arguments.model_output, model_metadata(canyons, config))

    if arguments.at:
        timestamp = floor_five_minutes(parse_utc(arguments.at))
        latest_reference = latest_iem_timestamp(config)
        note_planned_ledger_start(status, timestamp)
        record, result = analyze_timestamp_record(
            timestamp, canyons, global_grid, palette, config, latest_reference
        )
        print(json.dumps(result, indent=2))
        if not arguments.dry_run:
            upsert_frame_record(status, record)
            rebuild_events_from_ledger(status, canyons, config)
            prune_frame_ledger(status, latest_reference, config)
            update_frame_health(status, latest_reference, config)
            status["monitoring_started_utc"] = (
                status["monitoring_started_utc"] or utc_text(timestamp)
            )
            now = datetime.now(UTC)
            status["last_checked_utc"] = utc_text(now)
            status["last_run_trigger"] = arguments.run_trigger
            if arguments.run_trigger == "schedule":
                status["last_scheduled_run_utc"] = utc_text(now)
            save_json(arguments.status, status)
        return 0

    latest_reference = latest_iem_timestamp(config)
    restored_event_grids = restore_missing_event_grids(
        status, canyons, global_grid, palette, config, latest_reference
    )
    if restored_event_grids:
        print(
            f"Restored {restored_event_grids} retained event radar "
            f"snippet{'s' if restored_event_grids != 1 else ''}"
        )
    if arguments.rebuild_from:
        start = floor_five_minutes(parse_utc(arguments.rebuild_from))
        timestamps = list(iter_five_minutes(start, latest_reference))
    else:
        timestamps = scheduled_timestamps(status, config, latest_reference)

    status["monitoring_started_utc"] = status[
        "monitoring_started_utc"
    ] or utc_text(timestamps[0] if timestamps else datetime.now(UTC))
    if timestamps:
        note_planned_ledger_start(status, timestamps[0])
    processed = 0
    replaced = 0
    failures: list[tuple[datetime, str]] = []
    for timestamp in timestamps:
        try:
            record, summary = analyze_timestamp_record(
                timestamp,
                canyons,
                global_grid,
                palette,
                config,
                latest_reference,
            )
            if upsert_frame_record(status, record):
                replaced += 1
            wet_canyons = [
                canyon_id
                for canyon_id, values in summary.items()
                if values.get("wet")
            ]
            strongest = max(
                (
                    (float(values["maximum_dbz"]), canyon_id)
                    for canyon_id, values in summary.items()
                    if values.get("maximum_dbz") is not None
                ),
                default=(float("nan"), "none"),
            )
            source = record["source"]
            print(
                f"{utc_text(timestamp)} [{source}]: "
                f"wet={','.join(wet_canyons) or 'none'}; "
                f"strongest={strongest[1]} "
                f"{strongest[0] if math.isfinite(strongest[0]) else 'NA'} dBZ"
            )
            processed += 1
        except Exception as exc:  # pragma: no cover - network/runtime failure path
            failures.append((timestamp, str(exc)))
            print(
                f"{utc_text(timestamp)}: frame retrieval failed and will be retried: {exc}",
                file=sys.stderr,
            )

    rebuild_events_from_ledger(status, canyons, config)
    prune_frame_ledger(status, latest_reference, config)
    update_frame_health(status, latest_reference, config)
    now = datetime.now(UTC)
    status["last_checked_utc"] = utc_text(now)
    status["last_run_trigger"] = arguments.run_trigger
    if arguments.run_trigger == "schedule":
        status["last_scheduled_run_utc"] = utc_text(now)
    status["health"]["frames_attempted"] = len(timestamps)
    status["health"]["frames_processed"] = processed
    status["health"]["frames_replaced"] = replaced
    status["health"]["current_run_failures"] = [
        {"frame_utc": utc_text(timestamp), "error": error}
        for timestamp, error in failures
    ]
    if failures:
        status["health"]["message"] += (
            f"; {len(failures)} frame retrieval failure"
            f"{'s' if len(failures) != 1 else ''} queued for retry"
        )
    save_json(arguments.status, status)
    print(
        f"Radar reconciliation completed; {processed}/{len(timestamps)} frames "
        f"analyzed, {len(status.get('missing_archive_frames_utc', []))} archive "
        "frames still missing"
    )
    # A missing frame is state to retry and publish, not a reason to discard the run.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
