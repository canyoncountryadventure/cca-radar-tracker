#!/usr/bin/env python3
"""Export the exact current-model reconstruction of the Zero G Aug 12 event."""
import argparse
import json
from pathlib import Path

import backfill_zerog_aug12 as recovery
import tracker

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text())
    collection = json.loads((ROOT / "watersheds.geojson").read_text())
    atlas = json.loads((ROOT / "atlas14.json").read_text())
    hydrology = json.loads((ROOT / "hydrology.json").read_text())
    palette = tracker.load_palette(ROOT / "n0q_palette.json")
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    canyon = next(item for item in canyons if item.canyon_id == "zerog")
    event = recovery.reconstruct(canyon, palette, config)
    core = float((event.get("zero_g_field_core_evidence") or {}).get("maximum_in_basin_storm_inches") or 0)
    if event.get("peak_frame_utc") != recovery.EXPECTED_PEAK:
        raise SystemExit(f"Unexpected peak: {event.get('peak_frame_utc')}")
    if event.get("classification") != "likely_full" or core < 0.20:
        raise SystemExit(f"Aug 12 reconstruction did not validate: class={event.get('classification')} core={core}")
    args.output.write_text(json.dumps(event, indent=2) + "\n")
    print(f"Exported Aug 12 event {event['start_utc']}–{event['end_utc']}; rain={event.get('basin_rain_inches')} in; core={core} in; class={event.get('classification')}")


if __name__ == "__main__":
    main()
