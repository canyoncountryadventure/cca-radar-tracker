#!/usr/bin/env python3
"""Analyze IEM N0Q radar for every CCA canyon and update the dashboard data."""

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


def geometry_rings(geometry: dict[str, Any]) -> list[tuple[list[list[float]], list[list[list[float]]]]]:
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
        config["iem_current_png_url"], method="HEAD", headers={"User-Agent": "CCA-PoolFill-Radar/2.0"}
    )
    with urllib.request.urlopen(request, timeout=int(config["request_timeout_seconds"])) as response:
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
            request = urllib.request.Request(url, headers={"User-Agent": "CCA-PoolFill-Radar/2.0"})
            with urllib.request.urlopen(request, timeout=int(config["request_timeout_seconds"])) as response:
                image = Image.open(io.BytesIO(response.read())).convert("RGBA")
            if image.size != (grid.width, grid.height):
                raise ValueError(f"Unexpected WMS image size {image.size}")
            return image
        except Exception as exc:
            error = exc
            if attempt + 1 < int(config["request_retries"]):
                time.sleep(2**attempt)
    raise RuntimeError(f"Unable to retrieve radar frame {utc_text(timestamp)}: {error}")


def crop_for_grid(image: Image.Image, source: Grid, target: Grid) -> Image.Image:
    left = round((target.left - source.left) / GRID_RESOLUTION)
    top = round((source.top - target.top) / GRID_RESOLUTION)
    return image.crop((left, top, left + target.width, top + target.height))


def image_to_dbz(image: Image.Image, palette: dict[tuple[int, int, int], int]) -> tuple[np.ndarray, np.ndarray]:
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    indices = np.zeros(pixels.shape[:2], dtype=np.int16)
    for row in range(indices.shape[0]):
        for column in range(indices.shape[1]):
            if pixels[row, column, 3] == 0:
                continue
            indices[row, column] = palette.get(tuple(int(x) for x in pixels[row, column, :3]), -1)
    dbz = indices.astype(np.float32) * 0.5 - 32.5
    dbz[indices <= 0] = np.nan
    return dbz, indices


def rain_depth_inches(dbz: np.ndarray, model: dict[str, Any]) -> np.ndarray:
    capped = np.minimum(np.nan_to_num(dbz, nan=-999.0), float(model["rain_dbz_cap"]))
    valid = capped >= float(model["minimum_rain_dbz"])
    depth = np.zeros(dbz.shape, dtype=np.float32)
    reflectivity = np.power(10.0, capped[valid] / 10.0)
    rate_mm_hour = np.power(reflectivity / float(model["zr_a"]), 1.0 / float(model["zr_b"]))
    depth[valid] = rate_mm_hour / 25.4 * float(model["frame_minutes"]) / 60.0
    return depth


def canyon_model(area_sq_mi: float, config: dict[str, Any]) -> dict[str, Any]:
    model = config["model"]
    scale = (area_sq_mi / float(model["reference_area_sq_mi"])) ** float(model["area_exponent"])
    fill_target = float(model["reference_fill_volume_ft3"]) * scale
    rules = []
    for rule in model["reference_spatial_rules"]:
        required_area = min(area_sq_mi, float(rule["reference_area_sq_mi"]) * scale)
        rules.append(
            {
                "dbz": float(rule["dbz"]),
                "minimum_area_sq_mi": round(required_area, 3),
                "minimum_coverage_percent": round(min(100.0, required_area / area_sq_mi * 100.0), 2),
            }
        )
    return {
        "scale_factor": round(scale, 4),
        "fill_target_ft3": round(fill_target),
        "flush_target_ft3": round(fill_target * float(model["flush_ratio"])),
        "runoff_coefficient": float(model["runoff_coefficient"]),
        "calibration": "field-informed" if math.isclose(area_sq_mi, 1.359, rel_tol=0.01) else "provisional area-scaled",
        "spatial_rules": rules,
    }


def build_canyons(
    collection: dict[str, Any], atlas: dict[str, Any], config: dict[str, Any]
) -> tuple[list[Canyon], Grid]:
    supersample = int(config["mask_supersample"])
    padding = int(config["grid_padding_cells"])
    canyons = []
    all_geometry_points: list[list[float]] = []
    for feature in collection["features"]:
        properties = feature["properties"]
        geometry = feature["geometry"]
        grid = aligned_grid(geometry, padding)
        weights = watershed_weights(geometry, grid, supersample)
        if float(weights.sum()) <= 0:
            raise ValueError(f"Watershed mask is empty for {properties['name']}")
        canyons.append(
            Canyon(
                canyon_id=properties["id"],
                name=properties["name"],
                area_sq_mi=float(properties["area_sq_mi"]),
                geometry=geometry,
                outlet=properties["outlet"],
                grid=grid,
                weights=weights,
                atlas14=atlas[properties["id"]],
                model=canyon_model(float(properties["area_sq_mi"]), config),
            )
        )
        all_geometry_points.extend(all_points(geometry))
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
    dbz, indices = image_to_dbz(image, palette)
    total_weight = float(canyon.weights.sum())
    unknown_weight = float(canyon.weights[indices < 0].sum())
    rain = rain_depth_inches(dbz, config["model"])
    basin_rain = float((rain * canyon.weights).sum() / total_weight)
    rain_volume = basin_rain / 12.0 * canyon.area_sq_mi * SQUARE_FEET_PER_SQUARE_MILE
    runoff = rain_volume * float(config["model"]["runoff_coefficient"])
    coverages: dict[str, float] = {}
    rules = []
    for rule in canyon.model["spatial_rules"]:
        threshold = float(rule["dbz"])
        coverage = 100.0 * float(canyon.weights[np.nan_to_num(dbz, nan=-999) >= threshold].sum()) / total_weight
        covered_area = coverage / 100.0 * canyon.area_sq_mi
        coverages[str(int(threshold))] = round(coverage, 1)
        rules.append(
            {
                **rule,
                "coverage_percent": round(coverage, 1),
                "covered_area_sq_mi": round(covered_area, 3),
                "qualified": covered_area + 1e-9 >= float(rule["minimum_area_sq_mi"]),
            }
        )
    watershed_values = dbz[canyon.weights > 0]
    maximum = float(np.nanmax(watershed_values)) if np.any(np.isfinite(watershed_values)) else None
    wet = bool(maximum is not None and maximum >= float(config["model"]["storm_dbz_threshold"]))
    return (
        {
            "maximum_dbz": None if maximum is None else round(maximum, 1),
            "coverage_percent": coverages,
            "spatial_rules": rules,
            "spatial_gate": any(rule["qualified"] for rule in rules),
            "frame_basin_rain_inches": round(basin_rain, 4),
            "frame_rain_volume_ft3": round(rain_volume),
            "frame_estimated_runoff_ft3": round(runoff),
            "wet": wet,
            "unknown_watershed_percent": round(100.0 * unknown_weight / total_weight, 1),
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
        "notification": {"last_emailed_event_start_utc": None, "last_email_sent_utc": None},
    }


def empty_status(canyons: list[Canyon] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "monitoring_started_utc": None,
        "last_checked_utc": None,
        "latest_frame_utc": None,
        "canyons": {c.canyon_id: empty_canyon_status(c) for c in (canyons or [])},
        "health": {"ok": True, "message": "Waiting for first radar check"},
    }


def legacy_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if not event:
        return None
    return {
        **event,
        "classification": "legacy_spatial_trigger",
        "classification_label": "Legacy ZeroG radar trigger",
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
            existing.setdefault("canyons", {}).setdefault(canyon.canyon_id, empty_canyon_status(canyon))
        return existing
    if existing.get("schema_version") == 1:
        fresh["monitoring_started_utc"] = existing.get("monitoring_started_utc")
        fresh["last_checked_utc"] = existing.get("last_checked_utc")
        fresh["latest_frame_utc"] = existing.get("latest_frame_utc")
        zerog = fresh["canyons"]["zerog"]
        zerog["last_qualifying_event"] = legacy_event(existing.get("last_qualifying_event"))
        zerog["events"] = [legacy_event(event) for event in existing.get("events", []) if event]
        fresh["health"] = {"ok": True, "message": "Migrated Version 1 history; Version 2 monitoring active"}
    return fresh


def atlas_return_period(event: dict[str, Any], canyon: Canyon, frame_minutes: int) -> float | None:
    duration = max(frame_minutes, int(event["frames"]) * frame_minutes)
    supported = [5, 10, 15, 30, 60]
    selected = min(supported, key=lambda value: abs(value - duration))
    table = canyon.atlas14[f"{selected}-min"]
    depth = float(event.get("max_pixel_storm_inches") or 0)
    pairs = sorted((float(period), float(value)) for period, value in table.items())
    if depth <= 0:
        return None
    if depth <= pairs[0][1]:
        return round(max(0.1, pairs[0][0] * depth / pairs[0][1]), 1)
    for (p1, d1), (p2, d2) in zip(pairs, pairs[1:]):
        if d1 <= depth <= d2:
            fraction = (depth - d1) / (d2 - d1)
            return round(math.exp(math.log(p1) + fraction * (math.log(p2) - math.log(p1))), 1)
    # Atlas 14 tables stop at 1,000 years. Do not imply precision beyond NOAA's
    # published range when a radar-derived depth exceeds the final quantile.
    return pairs[-1][0]


def classify_event(event: dict[str, Any], canyon: Canyon, config: dict[str, Any]) -> tuple[str, str]:
    ratio = float(event["estimated_runoff_ft3"]) / float(canyon.model["fill_target_ft3"])
    event["fill_ratio"] = round(ratio, 2)
    enough_frames = int(event["wet_frames"]) >= int(config["model"]["minimum_wet_frames_for_likely"])
    gate = bool(event["spatial_gate_seen"])
    if ratio >= float(config["model"]["flush_ratio"]) and gate and enough_frames:
        return "full_flush", "Very high likelihood — full pools and completely new water"
    if ratio >= 1.0 and gate and enough_frames:
        return "likely_full", "High likelihood — pools likely full"
    if ratio >= float(config["model"]["moderate_ratio"]) or gate:
        return "moderate", "Moderate likelihood — pools may have filled somewhat"
    return "minor", "Minor rainfall — limited pool change expected"


def event_public(event: dict[str, Any], canyon: Canyon, config: dict[str, Any], include_grid: bool = True) -> dict[str, Any]:
    public = {key: value for key, value in event.items() if key not in {"accumulated_rain_grid_inches"}}
    classification, label = classify_event(public, canyon, config)
    public["classification"] = classification
    public["classification_label"] = label
    public["atlas14_return_period_years"] = atlas_return_period(public, canyon, int(config["model"]["frame_minutes"]))
    if not include_grid:
        public.pop("peak_grid_dbz", None)
        public.pop("grid_bbox", None)
    return public


def start_event(timestamp: datetime, analysis: dict[str, Any], rain: np.ndarray) -> dict[str, Any]:
    return {
        "start_utc": utc_text(timestamp),
        "end_utc": utc_text(timestamp),
        "frames": 1,
        "wet_frames": 1,
        "peak_dbz": analysis["maximum_dbz"],
        "peak_coverage_percent": dict(analysis["coverage_percent"]),
        "peak_covered_area_sq_mi": {
            str(int(rule["dbz"])): rule["covered_area_sq_mi"] for rule in analysis["spatial_rules"]
        },
        "basin_rain_inches": analysis["frame_basin_rain_inches"],
        "radar_rain_volume_ft3": analysis["frame_rain_volume_ft3"],
        "estimated_runoff_ft3": analysis["frame_estimated_runoff_ft3"],
        "spatial_gate_seen": analysis["spatial_gate"],
        "max_pixel_storm_inches": round(float(np.nanmax(rain)), 3),
        "accumulated_rain_grid_inches": grid_list(rain, 4),
        "peak_grid_dbz": analysis["grid_dbz"],
        "grid_bbox": analysis["grid_bbox"],
        "peak_frame_utc": utc_text(timestamp),
        "peak_frame_runoff_ft3": analysis["frame_estimated_runoff_ft3"],
    }


def update_open_event(
    event: dict[str, Any], timestamp: datetime, analysis: dict[str, Any], rain: np.ndarray
) -> None:
    event["end_utc"] = utc_text(timestamp)
    event["frames"] += 1
    event["wet_frames"] += 1
    event["basin_rain_inches"] = round(float(event["basin_rain_inches"]) + analysis["frame_basin_rain_inches"], 4)
    event["radar_rain_volume_ft3"] = round(float(event["radar_rain_volume_ft3"]) + analysis["frame_rain_volume_ft3"])
    event["estimated_runoff_ft3"] = round(float(event["estimated_runoff_ft3"]) + analysis["frame_estimated_runoff_ft3"])
    event["spatial_gate_seen"] = bool(event["spatial_gate_seen"] or analysis["spatial_gate"])
    event["peak_dbz"] = max(event.get("peak_dbz") or -999, analysis.get("maximum_dbz") or -999)
    for key, value in analysis["coverage_percent"].items():
        event["peak_coverage_percent"][key] = max(event["peak_coverage_percent"].get(key, 0), value)
    for rule in analysis["spatial_rules"]:
        key = str(int(rule["dbz"]))
        event["peak_covered_area_sq_mi"][key] = max(
            event["peak_covered_area_sq_mi"].get(key, 0), rule["covered_area_sq_mi"]
        )
    accumulated = np.asarray(event["accumulated_rain_grid_inches"], dtype=np.float32) + rain
    event["accumulated_rain_grid_inches"] = grid_list(accumulated, 4)
    event["max_pixel_storm_inches"] = round(float(np.nanmax(accumulated)), 3)
    if analysis["frame_estimated_runoff_ft3"] >= event.get("peak_frame_runoff_ft3", -1):
        event["peak_frame_runoff_ft3"] = analysis["frame_estimated_runoff_ft3"]
        event["peak_frame_utc"] = utc_text(timestamp)
        event["peak_grid_dbz"] = analysis["grid_dbz"]
        event["grid_bbox"] = analysis["grid_bbox"]


def finalize_event(canyon_status: dict[str, Any], canyon: Canyon, config: dict[str, Any]) -> None:
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
        if event and timestamp - parse_utc(event["end_utc"]) > timedelta(minutes=gap):
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
    if event and timestamp - parse_utc(event["end_utc"]) >= timedelta(minutes=gap):
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
    global_image = fetch_radar_image(timestamp, global_grid, config, latest_reference)
    summary = {}
    for canyon in canyons:
        image = crop_for_grid(global_image, global_grid, canyon.grid)
        analysis, rain = analyze_canyon_image(image, canyon, palette, config)
        analysis["frame_utc"] = utc_text(timestamp)
        canyon_status = status["canyons"][canyon.canyon_id]
        canyon_status["latest_analysis"] = analysis
        update_canyon_event(canyon_status, canyon, timestamp, analysis, rain, config)
        summary[canyon.canyon_id] = {
            "maximum_dbz": analysis["maximum_dbz"],
            "frame_basin_rain_inches": analysis["frame_basin_rain_inches"],
            "spatial_gate": analysis["spatial_gate"],
        }
    status["latest_frame_utc"] = utc_text(timestamp)
    return summary


def scheduled_timestamps(status: dict[str, Any], config: dict[str, Any], latest_complete: datetime) -> list[datetime]:
    last_frame = status.get("latest_frame_utc")
    start = (
        parse_utc(last_frame) + timedelta(minutes=5)
        if last_frame
        else latest_complete - timedelta(minutes=int(config["schedule_lookback_minutes"]))
    )
    return list(iter_five_minutes(start, latest_complete))[: int(config["max_frames_per_run"])]


def model_metadata(canyons: list[Canyon], config: dict[str, Any]) -> dict[str, Any]:
    model = config["model"]
    return {
        "schema_version": 2,
        "method": {
            "radar_source": "Iowa Environmental Mesonet N0Q 5-minute composite",
            "rainfall_formula": f"Z = {model['zr_a']} × R^{model['zr_b']}; dBZ capped at {model['rain_dbz_cap']} for rainfall volume",
            "runoff_formula": "Estimated delivered runoff = radar rain volume × effective runoff coefficient",
            "target_formula": "Fill target = 18,000 ft³ × (watershed area ÷ 1.36 mi²)^0.4",
            "spatial_formula": "Required high-dBZ area = ZeroG reference area × (watershed area ÷ 1.36 mi²)^0.4",
            "scaling_basis": "The 0.4 drainage-area exponent is a provisional regional transfer based on the supplied USGS StreamStats comparisons; it is not a claim that every canyon behaves identically.",
            "sources": [
                {"label": "IEM N0Q composite documentation", "url": "https://mesonet.agron.iastate.edu/docs/nexrad_composites/"},
                {"label": "IEM N0Q raster and dBZ encoding", "url": "https://mesonet.agron.iastate.edu/GIS/rasters.php?rid=2"},
                {"label": "NOAA Atlas 14 precipitation frequency", "url": "https://hdsc.nws.noaa.gov/pfds/"},
                {"label": "USGS StreamStats", "url": "https://streamstats.usgs.gov/ss/"},
            ],
            "classification": {
                "minor": "Runoff ratio below 0.5 and no spatial intensity gate",
                "moderate": "Runoff ratio at least 0.5 or a spatial intensity gate was reached",
                "likely_full": "Runoff ratio at least 1.0, spatial gate reached, and at least two wet frames",
                "full_flush": "Runoff ratio at least 2.0, spatial gate reached, and at least two wet frames",
            },
            "limitations": [
                "ZeroG is field-informed; other canyon targets are provisional until pool observations calibrate them.",
                "Radar reflectivity is an indirect rainfall estimate and values above 55 dBZ can be hail-contaminated.",
                "The 5% coefficient represents combined runoff and delivery; antecedent moisture and channel losses vary.",
                "NOAA Atlas 14 values are point-frequency context, not direct proof of runoff or pool condition.",
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
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--watersheds", type=Path, default=ROOT / "watersheds.geojson")
    parser.add_argument("--atlas", type=Path, default=ROOT / "atlas14.json")
    parser.add_argument("--palette", type=Path, default=ROOT / "n0q_palette.json")
    parser.add_argument("--status", type=Path, default=ROOT / "docs/data/status.json")
    parser.add_argument("--model-output", type=Path, default=ROOT / "docs/data/model.json")
    parser.add_argument("--at", help="Analyze one UTC frame, for example 2024-06-21T22:25:00Z")
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()
    config = json.loads(arguments.config.read_text(encoding="utf-8"))
    collection = json.loads(arguments.watersheds.read_text(encoding="utf-8"))
    atlas = json.loads(arguments.atlas.read_text(encoding="utf-8"))
    canyons, global_grid = build_canyons(collection, atlas, config)
    palette = load_palette(arguments.palette)
    status = load_status(arguments.status, canyons)
    save_json(arguments.model_output, model_metadata(canyons, config))

    if arguments.at:
        timestamp = floor_five_minutes(parse_utc(arguments.at))
        working_status = empty_status(canyons) if arguments.dry_run else status
        result = process_timestamp(timestamp, working_status, canyons, global_grid, palette, config)
        print(json.dumps(result, indent=2))
        if not arguments.dry_run:
            working_status["monitoring_started_utc"] = working_status["monitoring_started_utc"] or utc_text(timestamp)
            working_status["last_checked_utc"] = utc_text(datetime.now(UTC))
            working_status["health"] = {"ok": True, "message": "Historical radar frame analyzed"}
            save_json(arguments.status, working_status)
        return 0

    now = datetime.now(UTC)
    latest_reference = latest_iem_timestamp(config)
    timestamps = scheduled_timestamps(status, config, latest_reference)
    status["monitoring_started_utc"] = status["monitoring_started_utc"] or utc_text(timestamps[0] if timestamps else now)
    processed = 0
    try:
        for timestamp in timestamps:
            process_timestamp(timestamp, status, canyons, global_grid, palette, config, latest_reference)
            processed += 1
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {
            "ok": True,
            "message": f"Radar check completed; {processed} frame{'s' if processed != 1 else ''} analyzed for {len(canyons)} canyons",
        }
        save_json(arguments.status, status)
        print(status["health"]["message"])
        return 0
    except Exception as exc:
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {"ok": False, "message": str(exc)}
        save_json(arguments.status, status)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
