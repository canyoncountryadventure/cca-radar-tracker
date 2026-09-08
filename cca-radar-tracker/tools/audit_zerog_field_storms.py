#!/usr/bin/env python3
"""Audit Zero G field-observed rises against exact IEM historical N0Q frames.

This is deliberately a calibration/QA tool, not part of the operational classifier.
It scans the two anomalous field events (2026-08-12 and 2026-08-31) over a much
larger radar neighborhood than the current watershed polygon, then reports where
the strongest accumulated rain core actually fell relative to the modeled basin.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tracker

UTC = timezone.utc

WINDOWS = {
    "2026-08-12_major_field_rise": {
        "start": "2026-08-12T20:30:00Z",
        "end": "2026-08-12T22:45:00Z",
        "field_rise_mdt": "2026-08-12 16:02 MDT",
        "upper_rise_ft": 3.1781,
        "lower_rise_ft": 3.0600,
        "purpose": "Find the storm that preceded the ~3.1 ft dual-logger rise.",
    },
    "2026-08-31_evening_field_rise": {
        "start": "2026-08-31T23:30:00Z",
        "end": "2026-09-01T01:30:00Z",
        "field_rise_mdt": "2026-08-31 18:32-19:13 MDT",
        "upper_rise_ft": 0.96,
        "lower_rise_ft": 1.57,
        "purpose": "Explain the large field response despite only ~0.011 in basin-average tracked rain.",
    },
}


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def expanded_grid(base: tracker.Grid, cells: int = 30) -> tracker.Grid:
    return tracker.Grid(
        left=base.left - cells * tracker.GRID_RESOLUTION,
        bottom=base.bottom - cells * tracker.GRID_RESOLUTION,
        right=base.right + cells * tracker.GRID_RESOLUTION,
        top=base.top + cells * tracker.GRID_RESOLUTION,
        width=base.width + 2 * cells,
        height=base.height + 2 * cells,
    )


def pixel_center(grid: tracker.Grid, row: int, column: int) -> tuple[float, float]:
    lon = grid.left + (column + 0.5) * tracker.GRID_RESOLUTION
    lat = grid.top - (row + 0.5) * tracker.GRID_RESOLUTION
    return lat, lon


def nearest_basin_distance_miles(canyon: tracker.Canyon, lat: float, lon: float) -> float:
    points: list[tuple[float, float]] = []
    for row in range(canyon.grid.height):
        for col in range(canyon.grid.width):
            if float(canyon.weights[row, col]) > 0.05:
                plat, plon = pixel_center(canyon.grid, row, col)
                points.append((plat, plon))
    return min(haversine_miles(lat, lon, plat, plon) for plat, plon in points)


def scan_window(name: str, spec: dict, canyon: tracker.Canyon, config: dict, palette: dict) -> dict:
    grid = expanded_grid(canyon.grid, 30)
    start = parse(spec["start"])
    end = parse(spec["end"])
    accumulated = np.zeros((grid.height, grid.width), dtype=np.float64)
    basin_total = 0.0
    peak_dbz = None
    peak_dbz_utc = None
    peak_basin_frame = 0.0
    peak_basin_frame_utc = None
    frames = []

    for ts in tracker.iter_five_minutes(start, end):
        image = tracker.fetch_radar_image(ts, grid, config, latest_reference=None)
        dbz, _ = tracker.image_to_dbz(image, palette)
        rain = tracker.rain_depth_inches(dbz, config["model"])
        accumulated += rain

        basin_image = tracker.crop_for_grid(image, grid, canyon.grid)
        analysis, _ = tracker.analyze_canyon_image(basin_image, canyon, palette, config)
        basin_frame = float(analysis.get("frame_basin_rain_inches") or 0.0)
        basin_total += basin_frame
        mx = analysis.get("maximum_dbz")
        if mx is not None and (peak_dbz is None or float(mx) > peak_dbz):
            peak_dbz = float(mx)
            peak_dbz_utc = tracker.utc_text(ts)
        if basin_frame > peak_basin_frame:
            peak_basin_frame = basin_frame
            peak_basin_frame_utc = tracker.utc_text(ts)
        frames.append({
            "utc": tracker.utc_text(ts),
            "basin_rain_in": round(basin_frame, 4),
            "basin_max_dbz": mx,
        })

    finite = np.where(np.isfinite(accumulated), accumulated, -1.0)
    row, col = np.unravel_index(np.argmax(finite), finite.shape)
    core_inches = float(accumulated[row, col])
    core_lat, core_lon = pixel_center(grid, int(row), int(col))
    distance = nearest_basin_distance_miles(canyon, core_lat, core_lon)

    inside_crop = accumulated[
        30:30 + canyon.grid.height,
        30:30 + canyon.grid.width,
    ]
    basin_mask = canyon.weights > 0.05
    max_inside = float(np.max(inside_crop[basin_mask])) if np.any(basin_mask) else 0.0

    strongest_frames = sorted(frames, key=lambda item: item["basin_rain_in"], reverse=True)[:12]

    return {
        "name": name,
        **spec,
        "modeled_basin_area_sq_mi": canyon.area_sq_mi,
        "scan_grid_bbox": grid.bbox,
        "scan_padding_cells": 30,
        "scan_padding_degrees": round(30 * tracker.GRID_RESOLUTION, 3),
        "basin_rain_total_inches": round(basin_total, 4),
        "basin_peak_dbz": peak_dbz,
        "basin_peak_dbz_utc": peak_dbz_utc,
        "peak_basin_5min_rain_inches": round(peak_basin_frame, 4),
        "peak_basin_5min_rain_utc": peak_basin_frame_utc,
        "max_accumulated_pixel_inside_basin_inches": round(max_inside, 4),
        "max_nearby_accumulated_pixel_inches": round(core_inches, 4),
        "max_nearby_core_lat": round(core_lat, 5),
        "max_nearby_core_lon": round(core_lon, 5),
        "max_nearby_core_distance_from_basin_miles": round(distance, 2),
        "nearby_core_to_basin_average_ratio": None if basin_total <= 0 else round(core_inches / basin_total, 2),
        "strongest_basin_frames": strongest_frames,
    }


def main() -> None:
    config = json.loads((ROOT / "config.json").read_text())
    collection = json.loads((ROOT / "watersheds.geojson").read_text())
    atlas = json.loads((ROOT / "atlas14.json").read_text())
    hydrology = json.loads((ROOT / "hydrology.json").read_text())
    palette = tracker.load_palette(ROOT / "n0q_palette.json")
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    canyon = next(item for item in canyons if item.canyon_id == "zerog")

    results = {
        "generated_utc": tracker.utc_text(datetime.now(UTC)),
        "purpose": "Field-event audit using exact historical IEM N0Q frames and a 0.15-degree radar neighborhood around Zero G.",
        "field_calibration_anchors": {
            "2026-08-21": {"basin_rain_inches": 0.0632, "field_response": "small but real", "upper_rise_ft": 0.37, "lower_rise_ft": 0.15},
            "2026-08-29": {"basin_rain_inches": 0.2727, "field_response": "major refill", "upper_rise_ft": 3.16, "lower_rise_ft": 2.85},
            "2026-08-30": {"basin_rain_inches": 0.7407, "field_response": "strong flush", "upper_rise_ft": 2.64, "modeled_runoff_ft3": 552264},
        },
        "windows": {},
    }
    for name, spec in WINDOWS.items():
        results["windows"][name] = scan_window(name, spec, canyon, config, palette)

    out = ROOT / "docs" / "data" / "zerog_field_calibration.json"
    out.write_text(json.dumps(results, indent=2) + "\n")

    md = ROOT / "docs" / "zerog-field-calibration.md"
    lines = [
        "# Zero G field-event radar audit",
        "",
        "This report compares field-observed stage rises with exact historical IEM N0Q radar and deliberately scans beyond the current watershed polygon.",
        "",
    ]
    for item in results["windows"].values():
        lines.extend([
            f"## {item['name']}",
            "",
            f"- Field response: upper +{item['upper_rise_ft']:.2f} ft; lower +{item['lower_rise_ft']:.2f} ft",
            f"- Rebuilt basin-average rain: **{item['basin_rain_total_inches']:.4f} in**",
            f"- Maximum accumulated pixel inside basin: **{item['max_accumulated_pixel_inside_basin_inches']:.4f} in**",
            f"- Strongest nearby accumulated pixel: **{item['max_nearby_accumulated_pixel_inches']:.4f} in**",
            f"- Nearby core distance from modeled basin: **{item['max_nearby_core_distance_from_basin_miles']:.2f} mi**",
            f"- Nearby-core / basin-average ratio: **{item['nearby_core_to_basin_average_ratio']}**",
            f"- Peak basin dBZ: **{item['basin_peak_dbz']}** at {item['basin_peak_dbz_utc']}",
            "",
        ])
    md.write_text("\n".join(lines) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
