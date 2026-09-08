#!/usr/bin/env python3
"""Reconstruct the field-observed 2026-08-12 Zero G storm and merge it into state.

The event is rebuilt from exact timestamped historical IEM N0Q frames with the
current production model. It is tagged with the paired MX2001 observation so a
future model/state migration cannot mistake it for an unsupported synthetic event.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tracker

UTC = timezone.utc
START = tracker.parse_utc("2026-08-12T20:30:00Z")
END = tracker.parse_utc("2026-08-12T23:30:00Z")
FIELD_RISE_UTC = tracker.parse_utc("2026-08-12T22:02:00Z")
EXPECTED_PEAK = "2026-08-12T21:55:00Z"


def overlaps(left: dict, right: dict) -> bool:
    left_start = tracker.parse_utc(left["start_utc"])
    left_end = tracker.parse_utc(left.get("end_utc") or left["start_utc"])
    right_start = tracker.parse_utc(right["start_utc"])
    right_end = tracker.parse_utc(right.get("end_utc") or right["start_utc"])
    return left_start <= right_end and right_start <= left_end


def reconstruct(canyon, palette, config):
    temporary = tracker.empty_canyon_status(canyon)
    for timestamp in tracker.iter_five_minutes(START, END):
        image = tracker.fetch_radar_image(timestamp, canyon.grid, config, latest_reference=None)
        analysis, rain = tracker.analyze_canyon_image(image, canyon, palette, config)
        analysis["frame_utc"] = tracker.utc_text(timestamp)
        tracker.update_canyon_event(temporary, canyon, timestamp, analysis, rain, config)
    if temporary.get("open_event"):
        tracker.finalize_event(temporary, canyon, config)

    candidates = list(temporary.get("events") or [])
    if temporary.get("last_rain_event"):
        candidates.append(temporary["last_rain_event"])
    candidates = tracker.dedupe_events(candidates)
    causal = [
        event for event in candidates
        if tracker.parse_utc(event["start_utc"]) <= FIELD_RISE_UTC
        and tracker.parse_utc(event.get("end_utc") or event["start_utc"]) >= tracker.parse_utc(EXPECTED_PEAK)
    ]
    if not causal:
        raise RuntimeError("No reconstructed Zero G radar event spans the 2026-08-12 field response")
    event = max(
        causal,
        key=lambda item: (
            float((item.get("zero_g_field_core_evidence") or {}).get("maximum_in_basin_storm_inches") or 0),
            float(item.get("basin_rain_inches") or 0),
        ),
    )
    event = dict(event)
    event["field_reconstructed"] = True
    event["field_observation"] = {
        "observed_response_utc": tracker.utc_text(FIELD_RISE_UTC),
        "upper_stage_rise_ft": 3.1781,
        "lower_stage_rise_ft": 3.06,
        "response": "major refill",
        "logger_basis": "paired Zero G upper/lower MX2001 loggers",
        "note": "Both loggers independently recorded an approximately 3.1-ft rapid rise; upper relocation artifact was on Aug 8, not this event.",
    }
    event["history_recovery_basis"] = (
        "Exact IEM N0Q five-minute replay with current field-calibrated Zero G model"
    )
    return event


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    collection = json.loads((ROOT / "watersheds.geojson").read_text(encoding="utf-8"))
    atlas = json.loads((ROOT / "atlas14.json").read_text(encoding="utf-8"))
    hydrology = json.loads((ROOT / "hydrology.json").read_text(encoding="utf-8"))
    palette = tracker.load_palette(ROOT / "n0q_palette.json")
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    canyon = next(item for item in canyons if item.canyon_id == "zerog")

    status = json.loads(args.status.read_text(encoding="utf-8"))
    if "zerog" not in (status.get("canyons") or {}):
        raise RuntimeError("Operational status has no Zero G canyon state")

    rebuilt = reconstruct(canyon, palette, config)
    if rebuilt.get("peak_frame_utc") != EXPECTED_PEAK:
        raise RuntimeError(
            f"Unexpected reconstructed peak {rebuilt.get('peak_frame_utc')}; expected {EXPECTED_PEAK}"
        )
    core = float((rebuilt.get("zero_g_field_core_evidence") or {}).get("maximum_in_basin_storm_inches") or 0)
    if core < tracker.ZERO_G_FIELD_CORE_MAJOR_INCHES:
        raise RuntimeError(f"Reconstructed Aug 12 core {core:.4f} in does not meet field major threshold")
    if rebuilt.get("classification") != "likely_full":
        raise RuntimeError(
            f"Reconstructed Aug 12 classification is {rebuilt.get('classification')}, expected likely_full"
        )

    canyon_status = status["canyons"]["zerog"]
    existing = list(canyon_status.get("events") or [])
    retained = [event for event in existing if not overlaps(event, rebuilt)]
    retained.append(rebuilt)
    canyon_status["events"] = tracker.dedupe_events(retained)[
        : int(config.get("max_retained_events_per_canyon", 120))
    ]

    latest = canyon_status.get("last_rain_event")
    if latest is None or tracker.event_end_utc(rebuilt) > tracker.event_end_utc(latest):
        canyon_status["last_rain_event"] = rebuilt

    qualifying = [
        event for event in canyon_status["events"]
        if event.get("classification") in {"likely_full", "full_flush"}
    ]
    canyon_status["last_qualifying_event"] = max(
        qualifying, key=tracker.event_end_utc, default=None
    )

    now = (
        tracker.parse_utc(status["last_checked_utc"])
        if status.get("last_checked_utc")
        else datetime.now(UTC)
    )
    tracker.cumulative_refill_evidence(canyon_status, canyon, config, now_utc=now)
    status.setdefault("field_history_recovery", {})["zerog_aug12_2026"] = {
        "ok": True,
        "reconstructed_utc": tracker.utc_text(datetime.now(UTC)),
        "event_start_utc": rebuilt["start_utc"],
        "event_end_utc": rebuilt["end_utc"],
        "peak_frame_utc": rebuilt["peak_frame_utc"],
        "basin_rain_inches": rebuilt.get("basin_rain_inches"),
        "maximum_in_basin_storm_inches": core,
        "classification": rebuilt.get("classification"),
    }

    args.status.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status["field_history_recovery"]["zerog_aug12_2026"], indent=2))


if __name__ == "__main__":
    main()
