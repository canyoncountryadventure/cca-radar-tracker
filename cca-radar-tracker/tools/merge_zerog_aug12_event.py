#!/usr/bin/env python3
"""Merge a validated reconstructed Aug 12 Zero G event into current operational state."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import backfill_zerog_aug12 as recovery
import tracker

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()

    status = json.loads(args.status.read_text())
    event = json.loads(args.event.read_text())
    if event.get("classification") != "likely_full" or not event.get("field_reconstructed"):
        raise SystemExit("Refusing to merge an unvalidated Aug 12 event")

    config = json.loads((ROOT / "config.json").read_text())
    collection = json.loads((ROOT / "watersheds.geojson").read_text())
    atlas = json.loads((ROOT / "atlas14.json").read_text())
    hydrology = json.loads((ROOT / "hydrology.json").read_text())
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    canyon = next(item for item in canyons if item.canyon_id == "zerog")

    canyon_status = status["canyons"]["zerog"]
    existing = list(canyon_status.get("events") or [])
    retained = [item for item in existing if not recovery.overlaps(item, event)]
    retained.append(event)
    canyon_status["events"] = tracker.dedupe_events(retained)[
        : int(config.get("max_retained_events_per_canyon", 120))
    ]

    qualifying = [
        item for item in canyon_status["events"]
        if item.get("classification") in {"likely_full", "full_flush"}
    ]
    canyon_status["last_qualifying_event"] = max(
        qualifying, key=tracker.event_end_utc, default=None
    )

    now = tracker.parse_utc(status["last_checked_utc"]) if status.get("last_checked_utc") else datetime.now(UTC)
    tracker.cumulative_refill_evidence(canyon_status, canyon, config, now_utc=now)
    core = float((event.get("zero_g_field_core_evidence") or {}).get("maximum_in_basin_storm_inches") or 0)
    status.setdefault("field_history_recovery", {})["zerog_aug12_2026"] = {
        "ok": True,
        "reconstructed_utc": tracker.utc_text(datetime.now(UTC)),
        "event_start_utc": event["start_utc"],
        "event_end_utc": event["end_utc"],
        "peak_frame_utc": event["peak_frame_utc"],
        "basin_rain_inches": event.get("basin_rain_inches"),
        "maximum_in_basin_storm_inches": core,
        "classification": event.get("classification"),
    }
    args.status.write_text(json.dumps(status, indent=2) + "\n")
    print(json.dumps(status["field_history_recovery"]["zerog_aug12_2026"], indent=2))


if __name__ == "__main__":
    main()
