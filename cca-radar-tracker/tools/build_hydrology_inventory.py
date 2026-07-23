#!/usr/bin/env python3
"""Build canyon hydrologic parameters from SSURGO, NLCD, 3DEP and StreamStats.

The supplied watershed polygons remain authoritative.  Federal datasets are
sampled only inside each polygon and the resulting parameters are written to
hydrology.json for use by the radar tracker.
"""

from __future__ import annotations

import io
import json
import math
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
UA = "CCA-PoolFill-Hydrology/1.0 canyoncountryadventure@gmail.com"

# TR-55 style curve numbers.  Arid shrub/scrub is treated as poor hydrologic
# condition; exposed rock/barren ground receives the standard barren-land row.
CN = {
    11: (100, 100, 100, 100), 21: (49, 69, 79, 84),
    22: (61, 75, 83, 87), 23: (75, 85, 90, 92), 24: (89, 92, 94, 95),
    31: (77, 86, 91, 94), 41: (30, 55, 70, 77), 42: (30, 55, 70, 77),
    43: (30, 55, 70, 77), 52: (63, 77, 85, 88), 71: (49, 69, 79, 84),
    81: (49, 69, 79, 84), 82: (67, 78, 85, 89),
    90: (100, 100, 100, 100), 95: (100, 100, 100, 100),
}
HSG_INDEX = {"A": 0, "B": 1, "C": 2, "D": 3}


def request(url, data=None, headers=None, timeout=120):
    merged = {"User-Agent": UA, **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def rings(geometry):
    if geometry["type"] == "Polygon":
        return [(geometry["coordinates"][0], geometry["coordinates"][1:])]
    return [(part[0], part[1:]) for part in geometry["coordinates"]]


def bounds(geometry):
    pts = [p for exterior, holes in rings(geometry) for ring in [exterior, *holes] for p in ring]
    return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)


def mask_geometry(geometry, bbox, width, height):
    west, south, east, north = bbox
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    def px(ring):
        return [((p[0] - west) / (east - west) * width, (north - p[1]) / (north - south) * height) for p in ring]
    for exterior, holes in rings(geometry):
        draw.polygon(px(exterior), fill=1)
        for hole in holes:
            draw.polygon(px(hole), fill=0)
    return np.asarray(image, dtype=bool)


def nlcd(geometry):
    west, south, east, north = bounds(geometry)
    pad = 0.0003
    bbox = west-pad, south-pad, east+pad, north+pad
    query = {
        "service": "WCS", "version": "2.0.1", "request": "GetCoverage",
        "coverageId": "mrlc_download__NLCD_2021_Land_Cover_L48",
        "format": "image/tiff",
        "subset": [f"Long({bbox[0]},{bbox[2]})", f"Lat({bbox[1]},{bbox[3]})"],
        "subsettingCrs": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "outputCrs": "http://www.opengis.net/def/crs/EPSG/0/4326",
    }
    # doseq preserves both subset parameters.
    url = "https://www.mrlc.gov/geoserver/mrlc_download/wcs?" + urllib.parse.urlencode(query, doseq=True)
    image = Image.open(io.BytesIO(request(url)))
    values = np.asarray(image)
    mask = mask_geometry(geometry, bbox, image.width, image.height)
    counts = Counter(int(v) for v in values[mask])
    return bbox, values, mask, counts


def ssurgo_polygons(bbox):
    url = "https://sdmdataaccess.sc.egov.usda.gov/Spatial/SDMWGS84Geographic.wfs?" + urllib.parse.urlencode({
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": "MapunitPoly", "bbox": ",".join(map(str, bbox)), "outputFormat": "GML2",
    })
    root = ET.fromstring(request(url))
    result = []
    for member in root.iter():
        if not member.tag.endswith("featureMember"):
            continue
        feature = next(iter(member), None)
        if feature is None:
            continue
        mukey = next((node.text for node in feature.iter() if node.tag.endswith("mukey")), None)
        if not mukey:
            continue
        polygon_rings = []
        for poly in (n for n in feature.iter() if n.tag.endswith("Polygon")):
            ring_list = []
            for coords in (n for n in poly.iter() if n.tag.endswith("coordinates")):
                points = []
                for pair in (coords.text or "").strip().split():
                    x, y = pair.split(",")[:2]
                    points.append([float(x), float(y)])
                if points:
                    ring_list.append(points)
            if ring_list:
                polygon_rings.append(ring_list)
        if polygon_rings:
            result.append((mukey, polygon_rings))
    return result


def ssurgo_hsg(mukeys):
    result = {}
    keys = sorted(set(mukeys))
    for start in range(0, len(keys), 250):
        chunk = keys[start:start+250]
        sql = (
            "SELECT mu.mukey, mu.muname, c.compname, c.comppct_r, c.hydgrp "
            "FROM mapunit mu LEFT JOIN component c ON c.mukey=mu.mukey "
            f"WHERE mu.mukey IN ({','.join(repr(k) for k in chunk)})"
        )
        payload = json.dumps({"format": "JSON", "query": sql}).encode()
        obj = json.loads(request(
            "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest", payload,
            {"Content-Type": "application/json"},
        ))
        table = obj.get("Table", [])
        if not table:
            continue
        # SDA JSON returns rows only (unlike the CSV response, no header row).
        header = ["mukey", "muname", "compname", "comppct_r", "hydgrp"]
        for row in table:
            record = dict(zip(header, row))
            key = str(record["mukey"])
            name = f"{record.get('muname') or ''} {record.get('compname') or ''}".lower()
            group = (record.get("hydgrp") or "").strip().upper()
            # Dual groups represent drained/undrained conditions.  Use the
            # conservative undrained group; desert rock/badland is also D.
            group = group[-1:] if group else ""
            if group not in HSG_INDEX and any(term in name for term in ("rock", "badland", "rubble")):
                group = "D"
            if group not in HSG_INDEX:
                continue
            weight = float(record.get("comppct_r") or 0)
            entry = result.setdefault(key, {"name": record.get("muname"), "weights": defaultdict(float), "inferred": False})
            entry["weights"][group] += weight
            if not record.get("hydgrp"):
                entry["inferred"] = True
    for entry in result.values():
        total = sum(entry["weights"].values())
        entry["weights"] = {k: v/total for k, v in entry["weights"].items()} if total else {"D": 1.0}
    return result


def soil_grid(polygons, bbox, width, height):
    west, south, east, north = bbox
    image = Image.new("I", (width, height), 0)
    draw = ImageDraw.Draw(image)
    keys = []
    key_index = {}
    def px(ring):
        return [((p[0]-west)/(east-west)*width, (north-p[1])/(north-south)*height) for p in ring]
    for mukey, polys in polygons:
        if mukey not in key_index:
            keys.append(mukey)
            key_index[mukey] = len(keys)
        value = key_index[mukey]
        for part in polys:
            draw.polygon(px(part[0]), fill=value)
            for hole in part[1:]:
                draw.polygon(px(hole), fill=0)
    return np.asarray(image), keys


def composite_cn(nlcd_values, soil_values, mask, keys, soil_info):
    total = int(mask.sum())
    cn_sum = 0.0
    hsg = defaultdict(float)
    unknown = 0
    inferred = 0
    for lc, soil_idx in zip(nlcd_values[mask], soil_values[mask]):
        row = CN.get(int(lc), CN[52])
        if soil_idx <= 0 or soil_idx > len(keys) or keys[soil_idx-1] not in soil_info:
            weights = {"D": 1.0}
            unknown += 1
        else:
            entry = soil_info[keys[soil_idx-1]]
            weights = entry["weights"]
            inferred += int(entry["inferred"])
        cn_sum += sum(row[HSG_INDEX[group]] * fraction for group, fraction in weights.items())
        for group, fraction in weights.items():
            hsg[group] += fraction
    return (
        cn_sum / total,
        {group: round(100*value/total, 1) for group, value in sorted(hsg.items())},
        round(100*unknown/total, 1),
        round(100*inferred/total, 1),
    )


def terrain(geometry, bbox, mask):
    height, width = mask.shape
    url = "https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer/exportImage?" + urllib.parse.urlencode({
        "f": "image", "bbox": ",".join(map(str, bbox)), "bboxSR": 4326, "imageSR": 4326,
        "size": f"{width},{height}", "format": "tiff", "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
    })
    dem = np.asarray(Image.open(io.BytesIO(request(url))), dtype=float)
    west, south, east, north = bbox
    latitude = (south+north)/2
    dx = (east-west)/width * 111320 * math.cos(math.radians(latitude))
    dy = (north-south)/height * 110540
    gy, gx = np.gradient(dem, dy, dx)
    slope = np.hypot(gx, gy) * 100
    vals = dem[mask]
    return {
        "elevation_min_ft": round(float(vals.min()) * 3.28084),
        "elevation_mean_ft": round(float(vals.mean()) * 3.28084),
        "elevation_max_ft": round(float(vals.max()) * 3.28084),
        "mean_slope_percent": round(float(np.mean(slope[mask])), 1),
    }


def streamstats(outlet):
    lon, lat = outlet
    url = f"https://streamstats.usgs.gov/ss-delineate/v1/delineate/sshydro/UT?lat={lat}&lon={lon}"
    delineation = json.loads(request(url))
    data = json.dumps(delineation).encode()
    values = json.loads(request(
        "https://streamstats.usgs.gov/ss-hydro/v1/basin-characteristics/calculate",
        data, {"Content-Type": "application/json"}, timeout=180,
    ))
    characteristics = {}
    items = values if isinstance(values, list) else values.get("parameters", values.get("basinCharacteristics", []))
    for item in items:
        code = item.get("code") or item.get("name")
        value = item.get("value")
        if code and value is not None:
            characteristics[code] = value
    return characteristics


def hydraulic_length_miles(geometry, outlet):
    lon0, lat0 = outlet
    longest = 0.0
    for exterior, holes in rings(geometry):
        for lon, lat in [*exterior, *(p for hole in holes for p in hole)]:
            dx = (lon-lon0)*69.172*math.cos(math.radians((lat+lat0)/2))
            dy = (lat-lat0)*69.0
            longest = max(longest, math.hypot(dx, dy))
    return longest * 1.15


def main():
    collection = json.loads((ROOT/"watersheds.geojson").read_text())
    output = {"schema_version": 1, "method": {
        "curve_number": "NRCS runoff curve number, composite of SSURGO hydrologic soil group and 2021 NLCD land cover",
        "initial_abstraction": "Ia = 0.20S; S = 1000/CN - 10 inches",
        "terrain": "USGS 3DEP elevation and slope sampled within supplied watershed polygon",
        "lag": "NRCS watershed lag equation; hydraulic length approximated from supplied pour point and basin extent",
        "warning": "Parameters are screening estimates and require field calibration; they are not measured streamflow."
    }, "canyons": {}}
    for number, feature in enumerate(collection["features"], 1):
        p = feature["properties"]
        geometry = feature["geometry"]
        print(f"[{number}/{len(collection['features'])}] {p['name']}", flush=True)
        bbox, lc, mask, land_counts = nlcd(geometry)
        polys = ssurgo_polygons(bbox)
        soils = ssurgo_hsg([key for key, _ in polys])
        soil_values, keys = soil_grid(polys, bbox, lc.shape[1], lc.shape[0])
        cn2, hsg, unknown, inferred = composite_cn(lc, soil_values, mask, keys, soils)
        cn1 = cn2/(2.281-0.01281*cn2)
        cn3 = cn2/(0.427+0.00573*cn2)
        terr = terrain(geometry, bbox, mask)
        length_mi = hydraulic_length_miles(geometry, p["outlet"])
        slope = max(0.5, terr["mean_slope_percent"])
        retention = 1000/cn2-10
        lag_hr = ((length_mi*5280)**0.8 * (retention+1)**0.7)/(1900*math.sqrt(slope))
        try:
            ss = streamstats(p["outlet"])
        except Exception as exc:
            print(f"  StreamStats unavailable: {exc}", flush=True)
            ss = {}
        land_total = sum(land_counts.values())
        output["canyons"][p["id"]] = {
            "name": p["name"], "area_sq_mi": p["area_sq_mi"],
            "curve_number": {"dry": round(cn1, 1), "normal": round(cn2, 1), "wet": round(cn3, 1)},
            "initial_abstraction_inches": {
                state: round(0.2*(1000/cn-10), 3)
                for state, cn in (("dry", cn1), ("normal", cn2), ("wet", cn3))
            },
            "hydrologic_soil_group_percent": hsg,
            "soil_unmapped_percent": unknown, "soil_group_inferred_percent": inferred,
            "land_cover_percent": {str(k): round(100*v/land_total, 1) for k, v in sorted(land_counts.items())},
            **terr,
            "hydraulic_length_miles": round(length_mi, 2),
            "lag_hours": round(lag_hr, 2),
            "time_of_concentration_hours": round(lag_hr/0.6, 2),
            "streamstats": {k: v for k, v in ss.items() if k in {"DRNAREA", "BSLDEM10M", "ELEV"}},
            "parameter_quality": "screening; field calibration required",
        }
        (ROOT/"hydrology.json").write_text(json.dumps(output, indent=2)+"\n")
        time.sleep(0.2)
    print(f"Wrote {ROOT/'hydrology.json'}")


if __name__ == "__main__":
    main()
