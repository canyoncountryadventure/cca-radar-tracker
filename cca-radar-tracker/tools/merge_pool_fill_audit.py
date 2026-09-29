#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tracker

UTC = timezone.utc


def overlaps(a: dict, b: dict, slack_minutes: int = 45) -> bool:
    from datetime import timedelta
    def dt(v):
        return tracker.parse_utc(v) if v else None
    a0, a1 = dt(a.get("start_utc")), dt(a.get("end_utc") or a.get("start_utc"))
    b0, b1 = dt(b.get("start_utc")), dt(b.get("end_utc") or b.get("start_utc"))
    if not all((a0, a1, b0, b1)):
        return False
    slack = timedelta(minutes=slack_minutes)
    return a0 <= b1 + slack and b0 <= a1 + slack


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--status", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()

    status = json.loads(args.status.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    collection = json.loads((ROOT / "watersheds.geojson").read_text(encoding="utf-8"))
    atlas = json.loads((ROOT / "atlas14.json").read_text(encoding="utf-8"))
    hydrology = json.loads((ROOT / "hydrology.json").read_text(encoding="utf-8"))
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    by_id = {c.canyon_id: c for c in canyons}

    missing = report.get("raw_events_missing_from_live") or {}
    merged_counts = {}
    merged_events = {}

    for cid, events in missing.items():
        if cid not in status.get("canyons", {}) or cid not in by_id:
            continue
        cs = status["canyons"][cid]
        existing = list(cs.get("events") or [])
        added = []
        for event in events or []:
            if not event.get("start_utc"):
                continue
            if any(overlaps(event, old) for old in existing):
                continue
            recovered = dict(event)
            recovered["history_recovered_from_raw_radar"] = True
            recovered["history_recovery_method"] = (
                "MRMS p24h/p1h screening followed by exact 5-minute N0Q replay"
            )
            existing.append(recovered)
            added.append(recovered)

        if not added:
            merged_counts[cid] = 0
            continue

        cs["events"] = tracker.dedupe_events(existing)[
            : int(config.get("max_retained_events_per_canyon", 120))
        ]
        latest_rain = max(cs["events"], key=tracker.event_end_utc, default=None)
        if latest_rain:
            cs["last_rain_event"] = latest_rain
        qualifying = [
            e for e in cs["events"]
            if e.get("classification") in {"likely_full", "full_flush"}
        ]
        cs["last_qualifying_event"] = max(
            qualifying, key=tracker.event_end_utc, default=None
        )
        now = (
            tracker.parse_utc(status.get("last_checked_utc"))
            if status.get("last_checked_utc")
            else datetime.now(UTC)
        )
        tracker.cumulative_refill_evidence(cs, by_id[cid], config, now_utc=now)
        merged_counts[cid] = len(added)
        merged_events[cid] = [
            {
                "start_utc": e.get("start_utc"),
                "end_utc": e.get("end_utc"),
                "fill_ratio": e.get("fill_ratio"),
                "classification": e.get("classification"),
                "basin_rain_inches": e.get("basin_rain_inches"),
                "direct_runoff_ft3": e.get("direct_runoff_ft3"),
                "peak_dbz": e.get("peak_dbz"),
            }
            for e in added
        ]

    status.setdefault("history_recovery", {})["pool_fill_raw_radar_20260929"] = {
        "applied_utc": tracker.utc_text(datetime.now(UTC)),
        "scan_start_utc": report.get("scan_start_utc"),
        "scan_end_utc": report.get("scan_end_utc"),
        "targets": report.get("targets"),
        "merged_counts": merged_counts,
        "merged_events": merged_events,
        "radar_frame_count": report.get("radar_frame_count"),
        "radar_failure_count": len(report.get("radar_failures") or []),
    }

    args.status.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status["history_recovery"]["pool_fill_raw_radar_20260929"], indent=2))


if __name__ == "__main__":
    main()
