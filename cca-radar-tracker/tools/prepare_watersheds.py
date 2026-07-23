#!/usr/bin/env python3
"""Normalize supplied watershed GeoJSON and fetch NOAA Atlas 14 point estimates."""

from __future__ import annotations

import argparse
import json
import math
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


EARTH_RADIUS_M = 6_371_008.8
SQUARE_METERS_PER_SQUARE_MILE = 2_589_988.110336
ATLAS_URL = "https://hdsc.nws.noaa.gov/cgi-bin/new/fe_text.csv"
ATLAS_DURATIONS = {"5-min", "10-min", "15-min", "30-min", "60-min"}


SOURCES = [
    ("zerog", "ZeroG", "watershed.geojson", (38.77378, -110.49403)),
    ("black-hole-white-canyon", "Black Hole of White Canyon", "data.geojson", None),
    ("leprechaun", "Leprechaun", "data (1).geojson", None),
    ("woody", "Woody", "data (2).geojson", None),
    ("hog-canyons", "Hog Canyons", "data (3).geojson", None),
    ("no-kidding", "No Kidding", "data (4).geojson", None),
    ("angel-cove", "Angel Cove", "data (5).geojson", None),
    ("constrychnine", "Constrychnine", "data (6).geojson", None),
    ("alcatraz", "Alcatraz", "data (7).geojson", None),
    ("poe", "Poe", "data (8).geojson", None),
    ("entrajo", "Entrajo", "data (9).geojson", None),
    ("pool-arch", "Pool Arch", "data (10).geojson", None),
    ("the-squeeze", "The Squeeze", "data (11).geojson", None),
    ("cable-canyon", "Cable Canyon", "data (12).geojson", None),
    ("eardley", "Eardley", "data (13).geojson", None),
    ("north-fork-iron-wash", "North Fork of Iron Wash", "data (14).geojson", None),
    ("upper-greasewood", "Upper Greasewood", "data (15).geojson", None),
]


def geometry_polygons(geometry: dict[str, Any]) -> list[list[list[list[float]]]]:
    if geometry["type"] == "Polygon":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiPolygon":
        return geometry["coordinates"]
    return []


def ring_area_square_meters(ring: list[list[float]]) -> float:
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    total = 0.0
    for first, second in zip(ring, ring[1:]):
        lon1, lat1 = map(math.radians, first[:2])
        lon2, lat2 = map(math.radians, second[:2])
        delta = lon2 - lon1
        if delta > math.pi:
            delta -= 2 * math.pi
        elif delta < -math.pi:
            delta += 2 * math.pi
        total += delta * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total) * EARTH_RADIUS_M**2 / 2


def polygon_area_square_miles(polygon: list[list[list[float]]]) -> float:
    square_meters = ring_area_square_meters(polygon[0])
    square_meters -= sum(ring_area_square_meters(ring) for ring in polygon[1:])
    return square_meters / SQUARE_METERS_PER_SQUARE_MILE


def planar_centroid(geometry: dict[str, Any]) -> tuple[float, float]:
    weighted_x = weighted_y = total_area = 0.0
    for polygon in geometry_polygons(geometry):
        ring = polygon[0]
        cross_sum = centroid_x = centroid_y = 0.0
        for first, second in zip(ring, ring[1:]):
            cross = first[0] * second[1] - second[0] * first[1]
            cross_sum += cross
            centroid_x += (first[0] + second[0]) * cross
            centroid_y += (first[1] + second[1]) * cross
        area = cross_sum / 2
        if abs(area) < 1e-15:
            continue
        centroid_x /= 6 * area
        centroid_y /= 6 * area
        weight = abs(area)
        weighted_x += centroid_x * weight
        weighted_y += centroid_y * weight
        total_area += weight
    return weighted_y / total_area, weighted_x / total_area


def normalize_source(
    source_dir: Path,
    project_root: Path,
    slug: str,
    name: str,
    filename: str,
    fallback_outlet: tuple[float, float] | None,
) -> dict[str, Any]:
    path = project_root / filename if slug == "zerog" else source_dir / filename
    source = json.loads(path.read_text(encoding="utf-8"))
    features = source.get("features", [])
    point = next((feature for feature in features if (feature.get("geometry") or {}).get("type") == "Point"), None)
    polygon_feature = next(
        (
            feature
            for feature in features
            if (feature.get("geometry") or {}).get("type") in {"Polygon", "MultiPolygon"}
        ),
        None,
    )
    if polygon_feature is None:
        raise ValueError(f"{filename} does not contain a polygon")

    polygons = geometry_polygons(polygon_feature["geometry"])
    retained = [polygon for polygon in polygons if polygon_area_square_miles(polygon) >= 0.001]
    if not retained:
        raise ValueError(f"{filename} has no non-trivial polygon")
    geometry = {
        "type": "Polygon" if len(retained) == 1 else "MultiPolygon",
        "coordinates": retained[0] if len(retained) == 1 else retained,
    }
    area = sum(polygon_area_square_miles(polygon) for polygon in retained)
    centroid = planar_centroid(geometry)
    if point:
        longitude, latitude = point["geometry"]["coordinates"][:2]
        outlet = (latitude, longitude)
    elif fallback_outlet:
        outlet = fallback_outlet
    else:
        outlet = centroid

    return {
        "type": "Feature",
        "properties": {
            "id": slug,
            "name": name,
            "area_sq_mi": round(area, 3),
            "centroid": [round(centroid[1], 6), round(centroid[0], 6)],
            "outlet": [round(outlet[1], 6), round(outlet[0], 6)],
            "source_file": filename,
        },
        "geometry": geometry,
    }


def parse_atlas(text: str) -> dict[str, Any]:
    rows: dict[str, dict[str, float]] = {}
    periods: list[str] | None = None
    in_estimates = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "PRECIPITATION FREQUENCY ESTIMATES":
            in_estimates = True
            continue
        if in_estimates and line.startswith("PRECIPITATION FREQUENCY ESTIMATES AT"):
            break
        if in_estimates and line.startswith("by duration for ARI"):
            periods = [item.strip() for item in line.split(":,", 1)[1].split(",")]
            continue
        match = re.match(r"^(\d+-min):,\s*(.*)$", line)
        if not (in_estimates and periods and match and match.group(1) in ATLAS_DURATIONS):
            continue
        values = [float(item.strip()) for item in match.group(2).split(",")]
        rows[match.group(1)] = {period: value for period, value in zip(periods, values)}
    if rows.keys() != ATLAS_DURATIONS:
        raise ValueError(f"Incomplete Atlas 14 response: {sorted(rows)}")
    return rows


def fetch_atlas(feature: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    properties = feature["properties"]
    longitude, latitude = properties["outlet"]
    query = urllib.parse.urlencode(
        {"data": "depth", "lat": latitude, "lon": longitude, "series": "pds", "units": "english"}
    )
    error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(f"{ATLAS_URL}?{query}", headers={"User-Agent": "CCA-Radar-Tracker/2.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
            return properties["id"], parse_atlas(text)
        except Exception as exc:
            error = exc
            time.sleep(2**attempt)
    raise RuntimeError(f"Atlas 14 request failed for {properties['name']}: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    arguments = parser.parse_args()
    features = [
        normalize_source(arguments.source_dir, arguments.project_root, *source)
        for source in SOURCES
    ]
    atlas: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch_atlas, feature) for feature in features]
        for future in as_completed(futures):
            slug, estimates = future.result()
            atlas[slug] = estimates

    collection = {"type": "FeatureCollection", "features": features}
    data_dir = arguments.project_root / "docs/data"
    (arguments.project_root / "watersheds.geojson").write_text(
        json.dumps(collection, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    (data_dir / "watersheds.geojson").write_text(
        json.dumps(collection, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    (arguments.project_root / "atlas14.json").write_text(
        json.dumps(atlas, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Prepared {len(features)} watersheds and {len(atlas)} Atlas 14 tables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
