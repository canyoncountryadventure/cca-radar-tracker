#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import math
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw

import tracker

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[1]
TARGET_IDS = ("alcatraz", "hog-canyons", "poe")
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
UA = {"User-Agent": "CCA-pool-fill-history-audit/1.0"}\n# Branch-only audit; does not modify production state.

def fetch_bytes(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def mrms_url(prod: str, when: datetime, ext: str = "png") -> str:
    return (
        "https://mesonet.agron.iastate.edu/archive/data/"
        f"{when:%Y/%m/%d}/GIS/mrms/{prod}_{when:%Y%m%d%H%M}.{ext}"
    )

def read_world_file(when: datetime, prod: str = "p24h"):
    raw = fetch_bytes(mrms_url(prod, when, "wld")).decode("ascii").strip().splitlines()
    vals = [float(x) for x in raw]
    if len(vals) != 6:
        raise ValueError(f"Unexpected world file: {vals}")
    # A, D, B, E, C, F
    return vals

def index_to_mm(values: np.ndarray) -> np.ndarray:
    v = values.astype(np.int16)
    out = np.full(v.shape, np.nan, dtype=np.float32)
    out[v == 0] = 0.0
    m = (v >= 1) & (v <= 100)
    out[m] = v[m] * 0.25
    m = (v >= 101) & (v <= 180)
    out[m] = 25.0 + (v[m] - 100) * 1.25
    m = (v >= 181) & (v <= 254)
    out[m] = 125.0 + (v[m] - 180) * 5.0
    return out

def lonlat_to_pixel(lon: float, lat: float, wld):
    A, D, B, E, C, F = wld
    if abs(B) > 1e-12 or abs(D) > 1e-12:
        det = A * E - B * D
        col = (E * (lon - C) - B * (lat - F)) / det
        row = (-D * (lon - C) + A * (lat - F)) / det
    else:
        col = (lon - C) / A
        row = (lat - F) / E
    return col, row

def geometry_bounds(geometry):
    pts = list(tracker.all_points(geometry))
    return (
        min(p[0] for p in pts), min(p[1] for p in pts),
        max(p[0] for p in pts), max(p[1] for p in pts),
    )

def build_mask(canyon, shape, wld):
    h, w = shape
    minx, miny, maxx, maxy = geometry_bounds(canyon.geometry)
    corners = [
        lonlat_to_pixel(minx, miny, wld),
        lonlat_to_pixel(minx, maxy, wld),
        lonlat_to_pixel(maxx, miny, wld),
        lonlat_to_pixel(maxx, maxy, wld),
    ]
    cols = [p[0] for p in corners]
    rows = [p[1] for p in corners]
    left = max(0, int(math.floor(min(cols))) - 2)
    right = min(w, int(math.ceil(max(cols))) + 3)
    top = max(0, int(math.floor(min(rows))) - 2)
    bottom = min(h, int(math.ceil(max(rows))) + 3)
    mask = Image.new("L", (right-left, bottom-top), 0)
    draw = ImageDraw.Draw(mask)

    def px(ring):
        return [
            (
                lonlat_to_pixel(float(lon), float(lat), wld)[0] - left,
                lonlat_to_pixel(float(lon), float(lat), wld)[1] - top,
            )
            for lon, lat in ring
        ]

    for exterior, holes in tracker.geometry_rings(canyon.geometry):
        draw.polygon(px(exterior), fill=255)
        for hole in holes:
            draw.polygon(px(hole), fill=0)
    return (left, top, right, bottom), np.asarray(mask) > 0

def precip_stats(arr_mm, cache_entry):
    (left, top, right, bottom), mask = cache_entry
    cut = arr_mm[top:bottom, left:right]
    vals = cut[mask]
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return {"max_in": None, "mean_in": None}
    return {
        "max_in": round(float(np.max(vals)) / 25.4, 4),
        "mean_in": round(float(np.mean(vals)) / 25.4, 4),
    }

def load_mrms(prod: str, when: datetime):
    raw = fetch_bytes(mrms_url(prod, when))
    img = Image.open(io.BytesIO(raw))
    if img.mode != "P":
        img = img.convert("P")
    return index_to_mm(np.asarray(img)), img.size

def overlaps(a, b, slack_minutes=45):
    def dt(v):
        return datetime.fromisoformat(v.replace("Z","+00:00")) if v else None
    a0, a1 = dt(a.get("start_utc")), dt(a.get("end_utc") or a.get("start_utc"))
    b0, b1 = dt(b.get("start_utc")), dt(b.get("end_utc") or b.get("start_utc"))
    if not all((a0,a1,b0,b1)):
        return False
    slack = timedelta(minutes=slack_minutes)
    return a0 <= b1 + slack and b0 <= a1 + slack

def event_summary(e):
    keys = [
        "start_utc","end_utc","basin_rain_inches","max_pixel_storm_inches",
        "direct_runoff_ft3","generated_runoff_ft3","estimated_runoff_ft3",
        "fill_ratio","storage_ratio","classification","classification_label",
        "peak_dbz","peak_frame_utc","wet_frames","frames"
    ]
    return {k:e.get(k) for k in keys if e.get(k) is not None}

def main():
    config = json.loads((ROOT/"config.json").read_text())
    collection = json.loads((ROOT/"watersheds.geojson").read_text())
    atlas = json.loads((ROOT/"atlas14.json").read_text())
    hydrology = json.loads((ROOT/"hydrology.json").read_text())
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    targets = [c for c in canyons if c.canyon_id in TARGET_IDS]
    target_by_id = {c.canyon_id:c for c in targets}
    target_grid = tracker.aligned_grid_for_points(
        [p for c in targets for p in tracker.all_points(c.geometry)],
        int(config["grid_padding_cells"]),
    )
    palette = tracker.load_palette(ROOT/"n0q_palette.json")

    # Establish MRMS grid/masks from the first available daily field.
    probe = datetime(2026,8,2,tzinfo=UTC)
    wld = read_world_file(probe, "p24h")
    probe_arr, _ = load_mrms("p24h", probe)
    masks = {cid: build_mask(target_by_id[cid], probe_arr.shape, wld) for cid in TARGET_IDS}

    daily = []
    candidate_ends = []
    cursor = START + timedelta(days=1)
    while cursor <= END:
        try:
            arr, _ = load_mrms("p24h", cursor)
        except Exception as exc:
            print("P24H_MISSING", cursor.isoformat(), type(exc).__name__, str(exc)[:120])
            cursor += timedelta(days=1)
            continue
        row = {"end_utc": tracker.utc_text(cursor), "canyons": {}}
        hit = False
        for cid in TARGET_IDS:
            st = precip_stats(arr, masks[cid])
            row["canyons"][cid] = st
            if (st["max_in"] or 0) >= 0.02 or (st["mean_in"] or 0) >= 0.005:
                hit = True
        daily.append(row)
        if hit:
            candidate_ends.append(cursor)
        cursor += timedelta(days=1)

    # Add a rolling 24h field ending at the latest complete hour to screen the current partial UTC day.
    if END.hour != 0:
        try:
            arr, _ = load_mrms("p24h", END)
            row = {"end_utc": tracker.utc_text(END), "canyons": {}}
            hit = False
            for cid in TARGET_IDS:
                st = precip_stats(arr, masks[cid])
                row["canyons"][cid] = st
                if (st["max_in"] or 0) >= 0.02 or (st["mean_in"] or 0) >= 0.005:
                    hit = True
            daily.append(row)
            if hit:
                candidate_ends.append(END)
        except Exception as exc:
            print("P24H_CURRENT_MISSING", type(exc).__name__, str(exc)[:120])

    # Use hourly MRMS precipitation to identify storm hours inside candidate 24h windows.
    hours = set()
    for end in candidate_ends:
        h = end - timedelta(hours=23)
        while h <= end:
            if h >= START and h <= END:
                hours.add(h.replace(minute=0,second=0,microsecond=0))
            h += timedelta(hours=1)

    hourly = []
    active_hours = set()
    for h in sorted(hours):
        try:
            arr, _ = load_mrms("p1h", h)
        except Exception as exc:
            print("P1H_MISSING", h.isoformat(), type(exc).__name__, str(exc)[:120])
            continue
        row = {"end_utc": tracker.utc_text(h), "canyons": {}}
        hit = False
        for cid in TARGET_IDS:
            st = precip_stats(arr, masks[cid])
            row["canyons"][cid] = st
            if (st["max_in"] or 0) >= 0.015 or (st["mean_in"] or 0) >= 0.003:
                hit = True
        if hit:
            active_hours.add(h)
            hourly.append(row)

    # Detailed 5-minute N0Q scan around every active p1h period.
    radar_times = set()
    for h in active_hours:
        t = h - timedelta(hours=2)
        stop = h + timedelta(minutes=30)
        while t <= stop:
            if START <= t <= END:
                radar_times.add(tracker.floor_five_minutes(t))
            t += timedelta(minutes=5)

    status = tracker.empty_status(targets)
    failures = []
    latest_ref = END
    for i, t in enumerate(sorted(radar_times), 1):
        try:
            rec, _ = tracker.analyze_timestamp_record(
                t, targets, target_grid, palette, config, latest_ref
            )
            tracker.upsert_frame_record(status, rec)
        except Exception as exc:
            failures.append((tracker.utc_text(t), type(exc).__name__, str(exc)[:160]))
        if i % 250 == 0:
            print("RADAR_PROGRESS", i, "of", len(radar_times))
    tracker.rebuild_events_from_ledger(status, targets, config)

    raw_events = {
        cid: [event_summary(e) for e in (status["canyons"][cid].get("events") or [])]
        for cid in TARGET_IDS
    }

    # Pull the live operational state once and extract its retained events.
    live_url = (
        "https://raw.githubusercontent.com/canyoncountryadventure/"
        "cca-radar-tracker/operational-data/latest/status.json"
    )
    print("DOWNLOADING_LIVE_STATUS")
    live = json.loads(fetch_bytes(live_url, timeout=240))
    live_events = {}
    for cid in TARGET_IDS:
        c = live.get("canyons",{}).get(cid,{})
        evs = list(c.get("events") or [])
        for key in ("last_rain_event","last_qualifying_event"):
            if c.get(key):
                evs.append(c[key])
        dedup = []
        seen = set()
        for e in evs:
            k = (e.get("start_utc"), e.get("end_utc"), e.get("classification"), e.get("fill_ratio"))
            if k not in seen:
                seen.add(k)
                dedup.append(event_summary(e))
        live_events[cid] = dedup

    missing = defaultdict(list)
    for cid in TARGET_IDS:
        for e in raw_events[cid]:
            if not any(overlaps(e, p) for p in live_events[cid]):
                missing[cid].append(e)

    report = {
        "generated_utc": tracker.utc_text(datetime.now(UTC)),
        "scan_start_utc": tracker.utc_text(START),
        "scan_end_utc": tracker.utc_text(END),
        "targets": list(TARGET_IDS),
        "candidate_daily_p24h": daily,
        "active_hourly_p1h": hourly,
        "radar_frame_count": len(radar_times),
        "radar_failures": failures,
        "raw_radar_events": raw_events,
        "live_operational_events": live_events,
        "raw_events_missing_from_live": dict(missing),
        "live_latest_frame_utc": live.get("latest_frame_utc"),
        "live_latest_archive_confirmed_frame_utc": live.get("latest_archive_confirmed_frame_utc"),
        "live_missing_archive_frame_count": len(live.get("missing_archive_frames_utc") or []),
    }
    out = ROOT/"audit_pool_fill_history_report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("AUDIT_SUMMARY_BEGIN")
    print(json.dumps({
        "scan_start_utc": report["scan_start_utc"],
        "scan_end_utc": report["scan_end_utc"],
        "radar_frame_count": report["radar_frame_count"],
        "radar_failure_count": len(failures),
        "live_latest_frame_utc": report["live_latest_frame_utc"],
        "live_latest_archive_confirmed_frame_utc": report["live_latest_archive_confirmed_frame_utc"],
        "live_missing_archive_frame_count": report["live_missing_archive_frame_count"],
        "raw_radar_events": raw_events,
        "raw_events_missing_from_live": dict(missing),
    }, indent=2))
    print("AUDIT_SUMMARY_END")

if __name__ == "__main__":
    main()
