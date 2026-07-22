#!/usr/bin/env python3
"""Analyze IEM N0Q reflectivity over a watershed and update website JSON."""

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


def geometry_rings(geometry: dict[str, Any]) -> list[tuple[list[list[float]], list[list[list[float]]]]]:
    geometry_type = geometry["type"]
    coordinates = geometry["coordinates"]
    if geometry_type == "Polygon":
        return [(coordinates[0], coordinates[1:])]
    if geometry_type == "MultiPolygon":
        return [(polygon[0], polygon[1:]) for polygon in coordinates]
    raise ValueError(f"Expected Polygon or MultiPolygon, received {geometry_type}")


def all_points(geometry: dict[str, Any]) -> Iterable[list[float]]:
    for exterior, holes in geometry_rings(geometry):
        yield from exterior
        for hole in holes:
            yield from hole


def aligned_grid(geometry: dict[str, Any], padding_cells: int) -> Grid:
    points = list(all_points(geometry))
    minimum_x = min(point[0] for point in points)
    maximum_x = max(point[0] for point in points)
    minimum_y = min(point[1] for point in points)
    maximum_y = max(point[1] for point in points)

    first_column = math.floor((minimum_x - GRID_LEFT_EDGE) / GRID_RESOLUTION) - padding_cells
    last_column = math.floor((maximum_x - GRID_LEFT_EDGE) / GRID_RESOLUTION) + padding_cells
    first_row = math.floor((GRID_TOP_EDGE - maximum_y) / GRID_RESOLUTION) - padding_cells
    last_row = math.floor((GRID_TOP_EDGE - minimum_y) / GRID_RESOLUTION) + padding_cells

    left = GRID_LEFT_EDGE + first_column * GRID_RESOLUTION
    right = GRID_LEFT_EDGE + (last_column + 1) * GRID_RESOLUTION
    top = GRID_TOP_EDGE - first_row * GRID_RESOLUTION
    bottom = GRID_TOP_EDGE - (last_row + 1) * GRID_RESOLUTION
    return Grid(
        left=left,
        bottom=bottom,
        right=right,
        top=top,
        width=last_column - first_column + 1,
        height=last_row - first_row + 1,
    )


def watershed_weights(geometry: dict[str, Any], grid: Grid, supersample: int) -> np.ndarray:
    image_width = grid.width * supersample
    image_height = grid.height * supersample
    mask = Image.new("L", (image_width, image_height), 0)
    draw = ImageDraw.Draw(mask)

    def to_pixel(ring: list[list[float]]) -> list[tuple[float, float]]:
        return [
            (
                (point[0] - grid.left) / GRID_RESOLUTION * supersample,
                (grid.top - point[1]) / GRID_RESOLUTION * supersample,
            )
            for point in ring
        ]

    for exterior, holes in geometry_rings(geometry):
        draw.polygon(to_pixel(exterior), fill=255)
        for hole in holes:
            draw.polygon(to_pixel(hole), fill=0)

    values = np.asarray(mask, dtype=np.float32) / 255.0
    return values.reshape(grid.height, supersample, grid.width, supersample).mean(axis=(1, 3))


def load_palette(path: Path) -> dict[tuple[int, int, int], int]:
    palette = json.loads(path.read_text(encoding="utf-8"))
    return {tuple(rgb): index for index, rgb in enumerate(palette)}


def latest_iem_timestamp(config: dict[str, Any]) -> datetime:
    request = urllib.request.Request(
        config["iem_current_png_url"],
        method="HEAD",
        headers={"User-Agent": "CCA-PoolFill-Radar/1.0"},
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
        endpoint = config["iem_historical_wms_url"]
        layer = "nexrad-n0q-wmst"
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
        query["TIME"] = timestamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00Z")
    url = f"{endpoint}?{urllib.parse.urlencode(query)}"
    timeout = int(config["request_timeout_seconds"])
    retries = int(config["request_retries"])
    error: Exception | None = None

    for attempt in range(retries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "CCA-PoolFill-Radar/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            image = Image.open(io.BytesIO(payload)).convert("RGBA")
            if image.size != (grid.width, grid.height):
                raise ValueError(f"Unexpected WMS image size {image.size}")
            return image
        except Exception as exc:  # network and image errors use the same retry path
            error = exc
            if attempt + 1 < retries:
                time.sleep(2**attempt)
    raise RuntimeError(f"Unable to retrieve radar frame {utc_text(timestamp)}: {error}")


def analyze_image(
    image: Image.Image,
    weights: np.ndarray,
    palette: dict[tuple[int, int, int], int],
    thresholds: list[dict[str, float]],
) -> dict[str, Any]:
    pixels = np.asarray(image.convert("RGBA"), dtype=np.uint8)
    indices = np.zeros(weights.shape, dtype=np.int16)
    for row in range(indices.shape[0]):
        for column in range(indices.shape[1]):
            if pixels[row, column, 3] == 0:
                indices[row, column] = 0
            else:
                indices[row, column] = palette.get(tuple(int(x) for x in pixels[row, column, :3]), -1)

    dbz = indices.astype(np.float32) * 0.5 - 32.5
    dbz[indices <= 0] = np.nan
    total_weight = float(weights.sum())
    if total_weight <= 0:
        raise ValueError("Watershed mask has no area")

    unknown_weight = float(weights[indices < 0].sum())
    coverage: dict[str, float] = {}
    rules: list[dict[str, Any]] = []
    for threshold in thresholds:
        dbz_threshold = float(threshold["dbz"])
        minimum = float(threshold["minimum_coverage_percent"])
        percent = 100.0 * float(weights[np.nan_to_num(dbz, nan=-999) >= dbz_threshold].sum()) / total_weight
        coverage[str(int(dbz_threshold))] = round(percent, 1)
        rules.append(
            {
                "dbz": dbz_threshold,
                "coverage_percent": round(percent, 1),
                "minimum_coverage_percent": minimum,
                "qualified": percent + 1e-9 >= minimum,
            }
        )

    watershed_values = dbz[weights > 0]
    maximum = float(np.nanmax(watershed_values)) if np.any(np.isfinite(watershed_values)) else None
    grid_values: list[list[float | None]] = []
    for row in dbz:
        grid_values.append([None if not np.isfinite(value) else round(float(value), 1) for value in row])

    return {
        "qualified": any(rule["qualified"] for rule in rules),
        "maximum_dbz": None if maximum is None else round(maximum, 1),
        "coverage_percent": coverage,
        "rules": rules,
        "unknown_watershed_percent": round(100.0 * unknown_weight / total_weight, 1),
        "grid_dbz": grid_values,
    }


def empty_status() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "monitoring_started_utc": None,
        "last_checked_utc": None,
        "latest_frame_utc": None,
        "latest_analysis": None,
        "open_event": None,
        "last_qualifying_event": None,
        "events": [],
        "health": {"ok": True, "message": "Waiting for first radar check"},
    }


def load_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_status()
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        if status.get("schema_version") != 1:
            return empty_status()
        return status
    except (OSError, json.JSONDecodeError):
        return empty_status()


def new_event(timestamp: datetime, analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "start_utc": utc_text(timestamp),
        "end_utc": utc_text(timestamp),
        "qualifying_frames": 1,
        "peak_dbz": analysis["maximum_dbz"],
        "peak_coverage_percent": dict(analysis["coverage_percent"]),
    }


def update_event(status: dict[str, Any], timestamp: datetime, analysis: dict[str, Any]) -> None:
    event = status.get("open_event")
    if analysis["qualified"]:
        if event is None or timestamp - parse_utc(event["end_utc"]) > timedelta(minutes=10):
            event = new_event(timestamp, analysis)
        else:
            event["end_utc"] = utc_text(timestamp)
            event["qualifying_frames"] += 1
            if analysis["maximum_dbz"] is not None:
                event["peak_dbz"] = max(event.get("peak_dbz") or -999, analysis["maximum_dbz"])
            for key, value in analysis["coverage_percent"].items():
                event["peak_coverage_percent"][key] = max(event["peak_coverage_percent"].get(key, 0), value)
        status["open_event"] = event
        status["last_qualifying_event"] = dict(event)
        return

    if event is not None:
        events = status.setdefault("events", [])
        if not events or events[0].get("start_utc") != event.get("start_utc"):
            events.insert(0, dict(event))
            del events[20:]
        status["open_event"] = None


def process_timestamp(
    timestamp: datetime,
    status: dict[str, Any],
    grid: Grid,
    weights: np.ndarray,
    palette: dict[tuple[int, int, int], int],
    config: dict[str, Any],
    latest_reference: datetime | None = None,
) -> dict[str, Any]:
    image = fetch_radar_image(timestamp, grid, config, latest_reference)
    analysis = analyze_image(image, weights, palette, config["thresholds"])
    analysis["frame_utc"] = utc_text(timestamp)
    analysis["grid_bbox"] = grid.bbox
    status["latest_frame_utc"] = utc_text(timestamp)
    status["latest_analysis"] = analysis
    update_event(status, timestamp, analysis)
    return analysis


def scheduled_timestamps(status: dict[str, Any], config: dict[str, Any], latest_complete: datetime) -> list[datetime]:
    last_frame = status.get("latest_frame_utc")
    if last_frame:
        start = parse_utc(last_frame) + timedelta(minutes=5)
    else:
        start = latest_complete - timedelta(minutes=int(config["schedule_lookback_minutes"]))
    frames = list(iter_five_minutes(start, latest_complete))
    maximum = int(config["max_frames_per_run"])
    return frames[:maximum]


def save_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--watershed", type=Path, default=ROOT / "watershed.geojson")
    parser.add_argument("--palette", type=Path, default=ROOT / "n0q_palette.json")
    parser.add_argument("--status", type=Path, default=ROOT / "docs/data/status.json")
    parser.add_argument("--at", help="Analyze one UTC frame, for example 2024-06-21T22:25:00Z")
    parser.add_argument("--dry-run", action="store_true", help="Print analysis without updating status")
    arguments = parser.parse_args()

    config = json.loads(arguments.config.read_text(encoding="utf-8"))
    geojson = json.loads(arguments.watershed.read_text(encoding="utf-8"))
    if len(geojson.get("features", [])) != 1:
        raise ValueError("Watershed GeoJSON must contain exactly one feature")
    geometry = geojson["features"][0]["geometry"]
    grid = aligned_grid(geometry, int(config["grid_padding_cells"]))
    weights = watershed_weights(geometry, grid, int(config["mask_supersample"]))
    palette = load_palette(arguments.palette)
    status = load_status(arguments.status)

    if arguments.at:
        timestamp = floor_five_minutes(parse_utc(arguments.at))
        scratch_status = empty_status() if arguments.dry_run else status
        analysis = process_timestamp(timestamp, scratch_status, grid, weights, palette, config)
        print(json.dumps(analysis, indent=2))
        if not arguments.dry_run:
            if not scratch_status["monitoring_started_utc"]:
                scratch_status["monitoring_started_utc"] = utc_text(timestamp)
            scratch_status["last_checked_utc"] = utc_text(datetime.now(UTC))
            scratch_status["health"] = {"ok": True, "message": "Radar check completed"}
            save_status(arguments.status, scratch_status)
        return 0

    now = datetime.now(UTC)
    latest_reference = latest_iem_timestamp(config)
    timestamps = scheduled_timestamps(status, config, latest_reference)
    if not status["monitoring_started_utc"]:
        status["monitoring_started_utc"] = utc_text(timestamps[0] if timestamps else now)

    processed = 0
    try:
        for timestamp in timestamps:
            process_timestamp(timestamp, status, grid, weights, palette, config, latest_reference)
            processed += 1
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {
            "ok": True,
            "message": f"Radar check completed; {processed} new frame{'s' if processed != 1 else ''} analyzed",
        }
        save_status(arguments.status, status)
        print(status["health"]["message"])
        return 0
    except Exception as exc:
        status["last_checked_utc"] = utc_text(datetime.now(UTC))
        status["health"] = {"ok": False, "message": str(exc)}
        save_status(arguments.status, status)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
