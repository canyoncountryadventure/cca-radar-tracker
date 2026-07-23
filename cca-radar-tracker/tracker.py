#!/usr/bin/env python3
"""Analyze IEM N0Q radar for every CCA canyon and update the dashboard data.

Pool-fill targets are normalized to the mapped Zero G depression storage and each
canyon's technical-section length, then adjusted by the user-defined pothole
modifier. Heavy-rain gates use fixed watershed percentages for every canyon:
50+ dBZ over 50%, 55+ dBZ over 25%, or 60+ dBZ over 10%.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import urllib.parse
import urllib.request
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
        "pothole_modifier": 0.25,
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
}

FIXED_SPATIAL_RULES = (
    {"dbz": 50.0, "minimum_coverage_percent": 50.0},
    {"dbz": 55.0, "minimum_coverage_percent": 25.0},
    {"dbz": 60.0, "minimum_coverage_percent": 10.0},
)

MINOR_REFILL_RATIO = 0.25
SUBSTANTIAL_REFILL_RATIO = 0.50
LARGE_REFILL_RATIO = 0.75


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


def fetch_radar_image(
    timestamp: datetime,
    grid: Grid,
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> Image.Image:
    current_offset: int | None = None
    if latest_reference is not None:
        current_offset = int((latest_reference - timestamp).total_seconds() // 60)
        if current_offset < 0 or current_offset > 55 or current_offset % 5:
            current_offset = None

    if current_offset is None:
        endpoint, layer = config["iem_historical_wms_url"], "nexrad-n0q-wmst"
    else:
        endpoint = config["iem_current_wms_url"]
        suffix = "" if current_offset == 0 else f"-m{current_offset:02d}m"
        layer = f"nexrad-n0q-900913{suffix}-conus"

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
    }
    if current_offset is None:
        query["TIME"] = timestamp.strftime("%Y-%m-%dT%H:%M:00Z")

    url = f"{endpoint}?{urllib.parse.urlencode(query)}"
    error: Exception | None = None
    for attempt in range(int(config["request_retries"])):
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": "CCA-PoolFill-Radar/3.0"}
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
    """Build the canyon-specific storage target and fixed radar-footprint gates."""
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
    basin_rain = float((rain * canyon.weights).sum() / total_weight)
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
            / total_weight
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
        maximum is not None
        and maximum >= float(config["model"]["storm_dbz_threshold"])
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
            "unknown_watershed_percent": round(
                100.0 * unknown_weight / total_weight, 1
            ),
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
        "notification": {
            "last_emailed_event_start_utc": None,
            "last_email_sent_utc": None,
        },
    }


def empty_status(canyons: list[Canyon] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "monitoring_started_utc": None,
        "last_checked_utc": None,
        "latest_frame_utc": None,
        "canyons": {
            canyon.canyon_id: empty_canyon_status(canyon)
            for canyon in (canyons or [])
        },
        "health": {"ok": True, "message": "Waiting for first radar check"},
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


def load_status(path: Path, canyons: list[Canyon]) -> dict[str, Any]:
    fresh = empty_status(canyons)
    if not path.exists():
        return fresh
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh

    if existing.get("schema_version") == 2:
        for canyon in canyons:
            existing.setdefault("canyons", {}).setdefault(
                canyon.canyon_id, empty_canyon_status(canyon)
            )
        return existing

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
    return fresh


def refresh_status_events(
    status: dict[str, Any], canyons: list[Canyon], config: dict[str, Any]
) -> None:
    """Recalculate retained events when target or display definitions change."""
    for canyon in canyons:
        canyon_status = status.get("canyons", {}).get(canyon.canyon_id, {})
        for key in ("last_rain_event", "last_qualifying_event"):
            event = canyon_status.get(key)
            if (
                event
                and event.get("estimated_runoff_ft3") is not None
                and event.get("frames")
            ):
                canyon_status[key] = event_public(event, canyon, config)

        refreshed = []
        for event in canyon_status.get("events", []):
            if event.get("estimated_runoff_ft3") is not None and event.get("frames"):
                refreshed.append(
                    event_public(event, canyon, config, include_grid=False)
                )
            else:
                refreshed.append(event)
        canyon_status["events"] = refreshed


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
    supported = [5, 10, 15, 30, 60]
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


def nrcs_runoff_depth(rain_inches: float, curve_number: float) -> float:
    """NRCS direct-runoff depth using the traditional Ia=0.20S relation."""
    retention = 1000.0 / curve_number - 10.0
    abstraction = 0.20 * retention
    if rain_inches <= abstraction:
        return 0.0
    return (rain_inches - abstraction) ** 2 / (
        rain_inches + 0.80 * retention
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
    for state in ("dry", "normal", "wet"):
        depth = nrcs_runoff_depth(
            rain, float(hydrology["curve_number"][state])
        )
        volume = depth / 12.0 * area_ft2
        base_seconds = max(
            300.0, (duration_hr + 2.0 * lag_hr) * 3600.0
        )
        runoff_depths[state] = round(depth, 4)
        volumes[state] = round(volume)
        peaks[state] = round(2.0 * volume / base_seconds, 2)

    event["hydrology_available"] = True
    event["runoff_depth_inches"] = runoff_depths
    event["direct_runoff_ft3_range"] = volumes
    event["direct_runoff_ft3"] = volumes["normal"]
    event["routed_peak_cfs_range"] = peaks
    event["routed_peak_cfs"] = peaks["normal"]

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
    gate = bool(event.get("spatial_gate_seen"))
    storage_met = ratio >= 1.0
    flush_met = ratio >= float(config["model"]["flush_ratio"])

    event["decision_tests"] = {
        "storage_target_met": storage_met,
        "flush_target_met": flush_met,
        "heavy_rain_footprint_met": gate,
        "minimum_wet_duration_met": enough_frames,
        "minimum_wet_frames_required": required_frames,
    }

    if flush_met and gate and enough_frames:
        label = "Strong flush likely — pools likely full"
        reason = (
            "Estimated watershed runoff was at least twice the provisional empty-storage "
            "target, and both the intense-rain footprint and duration checks passed. "
            "This indicates a strong refill/flush event, not a direct field observation."
        )
        code = "full_flush"
    elif storage_met and gate and enough_frames:
        label = "Major refill likely — pools may be full"
        reason = (
            "Estimated watershed runoff met the provisional empty-storage target, and both "
            "the intense-rain footprint and duration checks passed. Existing pool "
            "levels and channel losses remain unknown."
        )
        code = "likely_full"
    elif storage_met:
        missing = []
        if not gate:
            missing.append("intense-rain footprint")
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
    elif gate:
        label = "Localized intense rain detected — refill uncertain"
        reason = (
            "An intense-rain footprint threshold was reached, but estimated watershed "
            "runoff remained below 25% of the provisional empty-storage target."
        )
        code = "moderate"
    else:
        label = "No meaningful pool refill indicated"
        reason = (
            "Estimated watershed runoff was below 25% of the provisional empty-storage "
            "target and no intense-rain footprint threshold was reached."
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
    public["storage_target_ft3"] = int(canyon.model["fill_target_ft3"])
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



def start_event(
    timestamp: datetime, analysis: dict[str, Any], rain: np.ndarray
) -> dict[str, Any]:
    return {
        "start_utc": utc_text(timestamp),
        "end_utc": utc_text(timestamp),
        "frames": 1,
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


def update_open_event(
    event: dict[str, Any],
    timestamp: datetime,
    analysis: dict[str, Any],
    rain: np.ndarray,
) -> None:
    event["end_utc"] = utc_text(timestamp)
    event["frames"] = int(event.get("frames") or 0) + 1
    event["wet_frames"] = int(event.get("wet_frames") or 0) + 1
    event["basin_rain_inches"] = round(
        float(event.get("basin_rain_inches") or 0.0)
        + analysis["frame_basin_rain_inches"],
        4,
    )
    event["radar_rain_volume_ft3"] = round(
        float(event.get("radar_rain_volume_ft3") or 0.0)
        + analysis["frame_rain_volume_ft3"]
    )
    event["spatial_gate_seen"] = bool(
        event.get("spatial_gate_seen") or analysis["spatial_gate"]
    )
    event["peak_dbz"] = max(
        event.get("peak_dbz") or -999,
        analysis.get("maximum_dbz") or -999,
    )

    event.setdefault("peak_coverage_percent", {})
    event.setdefault("peak_covered_area_sq_mi", {})
    for key, value in analysis["coverage_percent"].items():
        event["peak_coverage_percent"][key] = max(
            event["peak_coverage_percent"].get(key, 0), value
        )
    for rule in analysis["spatial_rules"]:
        key = str(int(rule["dbz"]))
        event["peak_covered_area_sq_mi"][key] = max(
            event["peak_covered_area_sq_mi"].get(key, 0),
            rule["covered_area_sq_mi"],
        )

    accumulated = (
        np.asarray(event["accumulated_rain_grid_inches"], dtype=np.float32)
        + rain
    )
    event["accumulated_rain_grid_inches"] = grid_list(accumulated, 4)
    event["max_pixel_storm_inches"] = round(float(np.nanmax(accumulated)), 3)

    previous_peak = float(
        event.get(
            "peak_frame_rain_volume_ft3",
            event.get("peak_frame_runoff_ft3", -1),
        )
    )
    if analysis["frame_rain_volume_ft3"] >= previous_peak:
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
        events.insert(0, event_public(event, canyon, config, include_grid=False))
    del events[12:]
    canyon_status["open_event"] = None


def update_canyon_event(
    canyon_status: dict[str, Any],
    canyon: Canyon,
    timestamp: datetime,
    analysis: dict[str, Any],
    rain: np.ndarray,
    config: dict[str, Any],
) -> None:
    event = canyon_status.get("open_event")
    gap = int(config["model"]["event_gap_minutes"])

    if analysis["wet"]:
        if event and timestamp - parse_utc(event["end_utc"]) > timedelta(
            minutes=gap
        ):
            finalize_event(canyon_status, canyon, config)
            event = None

        if event is None:
            event = start_event(timestamp, analysis, rain)
            canyon_status["open_event"] = event
        else:
            update_open_event(event, timestamp, analysis, rain)

        public = event_public(event, canyon, config)
        canyon_status["last_rain_event"] = public
        if public["classification"] in {"likely_full", "full_flush"}:
            canyon_status["last_qualifying_event"] = public
        return

    if event and timestamp - parse_utc(event["end_utc"]) >= timedelta(
        minutes=gap
    ):
        finalize_event(canyon_status, canyon, config)


def process_timestamp(
    timestamp: datetime,
    status: dict[str, Any],
    canyons: list[Canyon],
    global_grid: Grid,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> dict[str, Any]:
    global_image = fetch_radar_image(
        timestamp, global_grid, config, latest_reference
    )
    summary = {}
    for canyon in canyons:
        image = crop_for_grid(global_image, global_grid, canyon.grid)
        analysis, rain = analyze_canyon_image(image, canyon, palette, config)
        analysis["frame_utc"] = utc_text(timestamp)
        canyon_status = status["canyons"][canyon.canyon_id]
        canyon_status["latest_analysis"] = analysis
        update_canyon_event(
            canyon_status, canyon, timestamp, analysis, rain, config
        )
        summary[canyon.canyon_id] = {
            "maximum_dbz": analysis["maximum_dbz"],
            "frame_basin_rain_inches": analysis[
                "frame_basin_rain_inches"
            ],
            "spatial_gate": analysis["spatial_gate"],
        }
    status["latest_frame_utc"] = utc_text(timestamp)
    return summary


def scheduled_timestamps(
    status: dict[str, Any], config: dict[str, Any], latest_complete: datetime
) -> list[datetime]:
    last_frame = status.get("latest_frame_utc")
    start = (
        parse_utc(last_frame) + timedelta(minutes=5)
        if last_frame
        else latest_complete
        - timedelta(minutes=int(config["schedule_lookback_minutes"]))
    )
    return list(iter_five_minutes(start, latest_complete))[
        : int(config["max_frames_per_run"])
    ]



def model_metadata(
    canyons: list[Canyon], config: dict[str, Any]
) -> dict[str, Any]:
    model = config["model"]
    return {
        "schema_version": 2,
        "method": {
            "radar_source": "Iowa Environmental Mesonet N0Q 5-minute composite",
            "rainfall_formula": (
                f"Z = {model['zr_a']} × R^{model['zr_b']}; dBZ capped at "
                f"{model['rain_dbz_cap']} for rainfall-volume conversion"
            ),
            "rainfall_explanation": (
                "The tracker converts each five-minute radar frame to rainfall, then "
                "area-weights the pixels inside the watershed polygon. Radar rainfall "
                "is an estimate and may be biased by hail, beam geometry, or evaporation."
            ),
            "runoff_formula": (
                "NRCS direct runoff: S = 1000/CN − 10; Ia = 0.20S; "
                "Q = (P − Ia)²/(P + 0.80S) when P > Ia"
            ),
            "direct_runoff_explanation": (
                "No fixed runoff coefficient is used. Accumulated basin-average radar "
                "rainfall is converted to dry, normal, and wet direct-runoff estimates "
                "with canyon-specific composite curve numbers from SSURGO soils and "
                "2021 NLCD land cover. The central display uses the normal condition."
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
                "The same watershed-percentage gate applies to every canyon. It is a "
                "storm-footprint confirmation test and does not replace the runoff-volume test."
            ),
            "fill_ratio_explanation": (
                "Estimated fill ratio = normal-condition NRCS watershed direct runoff ÷ provisional "
                "empty-pool storage target. It is not a measured pool-depth percentage and does "
                "not explicitly subtract channel transmission losses."
            ),
            "atlas_explanation": (
                "Atlas 14 context compares event-duration watershed-average radar rainfall "
                "with duration-interpolated NOAA Atlas 14 point-frequency depths at the "
                "canyon outlet. It is context, not a watershed return interval."
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
                    "least two wet five-minute frames: strong refill/flush likely; pools likely full"
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
                    "Existing pool level is unknown. A partly full canyon needs less new "
                    "water than the provisional empty-storage target, while evaporation "
                    "and leakage can reduce retained water after a storm."
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
    parser.add_argument("--dry-run", action="store_true")
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
    status = load_status(arguments.status, canyons)
    refresh_status_events(status, canyons, config)
    save_json(arguments.model_output, model_metadata(canyons, config))

    if arguments.at:
        timestamp = floor_five_minutes(parse_utc(arguments.at))
        working_status = empty_status(canyons) if arguments.dry_run else status
        result = process_timestamp(
            timestamp, working_status, canyons, global_grid, palette, config
        )
        print(json.dumps(result, indent=2))
        if not arguments.dry_run:
            working_status["monitoring_started_utc"] = (
                working_status["monitoring_started_utc"] or utc_text(timestamp)
            )
            working_status["last_checked_utc"] = utc_text(datetime.now(UTC))
            working_status["health"] = {
                "ok": True,
                "message": "Historical radar frame analyzed",
            }
            save_json(arguments.status, working_status)
        return 0

    now = datetime.now(UTC)
    latest_reference = latest_iem_timestamp(config)
    timestamps = scheduled_timestamps(status, config, latest_reference)
    status["monitoring_started_utc"] = status[
        "monitoring_started_utc"
    ] or utc_text(timestamps[0] if timestamps else now)
    processed = 0

    try:
        for timestamp in timestamps:
            process_timestamp(
                timestamp,
                status,
                canyons,
                global_grid,
                palette,
                config,
                latest_reference,
            )
            processed += 1
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {
            "ok": True,
            "message": (
                f"Radar check completed; {processed} frame"
                f"{'s' if processed != 1 else ''} analyzed for {len(canyons)} canyons"
            ),
        }
        save_json(arguments.status, status)
        print(status["health"]["message"])
        return 0
    except Exception as exc:  # pragma: no cover - network/runtime failure path
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {"ok": False, "message": str(exc)}
        save_json(arguments.status, status)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
