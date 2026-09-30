#!/usr/bin/env python3
"""Select the freshest valid persisted tracker state for a workflow run.

The published Pages copy is usually the freshest operational snapshot, while the
operational-data branch is the durable historical store. A historical recovery
must not disappear merely because Pages has a newer last_checked_utc. Normal
selection therefore uses the freshest snapshot for current state and unions
retained event/history records from every other valid persisted snapshot.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

SUPPORTED_SCHEMAS = {2, 3, 4, 5}
EXPECTED_CANYON_IDS = {
    "alcatraz",
    "angel-cove",
    "black-hole-white-canyon",
    "cable-canyon",
    "constrychnine",
    "eardley",
    "entrajo",
    "hog-canyons",
    "hogwarts",
    "leprechaun",
    "neon",
    "no-kidding",
    "north-fork-iron-wash",
    "poe",
    "pool-arch",
    "quandary",
    "the-squeeze",
    "upper-greasewood",
    "wonderland-canyon",
    "woody",
    "yankee-doodle",
    "zerog",
}


def load_candidate(path: Path) -> tuple[datetime, dict]:
    status = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(status.get("canyons"), dict) or not status["canyons"]:
        raise ValueError("missing canyon state")
    canyon_ids = set(status["canyons"])
    if canyon_ids != EXPECTED_CANYON_IDS:
        missing = sorted(EXPECTED_CANYON_IDS - canyon_ids)
        extra = sorted(canyon_ids - EXPECTED_CANYON_IDS)
        raise ValueError(
            f"incomplete canyon state; missing={missing}; extra={extra}"
        )
    try:
        schema = int(status.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid schema") from exc
    if schema not in SUPPORTED_SCHEMAS:
        raise ValueError(f"unsupported schema {schema}")
    checked = status.get("last_checked_utc")
    if not checked:
        raise ValueError("missing last_checked_utc")
    timestamp = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    status["schema_version"] = schema
    return timestamp.astimezone(timezone.utc), status


def event_key(event: dict) -> tuple[str, str]:
    start = str(event.get("start_utc") or "")
    end = str(event.get("end_utc") or start)
    return start, end


def event_end(event: dict) -> str:
    return str(event.get("end_utc") or event.get("start_utc") or "")


def event_quality(event: dict) -> tuple[int, float, int]:
    classification_rank = {
        "minor": 0,
        "moderate": 1,
        "likely_full": 2,
        "full_flush": 3,
    }
    return (
        classification_rank.get(str(event.get("classification") or ""), -1),
        float(event.get("fill_ratio") or event.get("storage_ratio") or 0.0),
        1 if event.get("peak_grid_dbz") is not None else 0,
    )


def merge_canyon_history(primary: dict, secondary: dict) -> None:
    """Union retained historical events without rolling back current state."""
    merged: dict[tuple[str, str], dict] = {}

    def add_candidates(canyon: dict, prefer_existing: bool) -> None:
        candidates = list(canyon.get("events") or [])
        for key in ("last_rain_event", "last_qualifying_event"):
            if canyon.get(key):
                candidates.append(canyon[key])
        for event in candidates:
            if not event or not event.get("start_utc"):
                continue
            key = event_key(event)
            existing = merged.get(key)
            candidate = copy.deepcopy(event)
            if existing is None:
                merged[key] = candidate
            elif not prefer_existing and event_quality(candidate) > event_quality(existing):
                merged[key] = candidate

    # Freshest selected state stays authoritative when copies are equivalent.
    add_candidates(primary, prefer_existing=True)
    add_candidates(secondary, prefer_existing=False)

    events = sorted(merged.values(), key=event_end, reverse=True)
    primary["events"] = events[:120]
    if events:
        primary["last_rain_event"] = events[0]
    qualifying = [
        event
        for event in events
        if event.get("classification") in {"likely_full", "full_flush"}
    ]
    primary["last_qualifying_event"] = (
        max(qualifying, key=event_end) if qualifying else None
    )

    secondary_records = secondary.get("historical_records") or {}
    primary_records = primary.setdefault("historical_records", {})
    for record_key in ("peak_individual_event", "peak_seven_day_evidence"):
        candidate = secondary_records.get(record_key)
        existing = primary_records.get(record_key)
        candidate_ratio = float(
            (candidate or {}).get("fill_ratio", (candidate or {}).get("ratio", 0.0))
            or 0.0
        )
        existing_ratio = float(
            (existing or {}).get("fill_ratio", (existing or {}).get("ratio", 0.0))
            or 0.0
        )
        if candidate and candidate_ratio > existing_ratio:
            primary_records[record_key] = copy.deepcopy(candidate)


def choose_status(paths: list[Path], recovery: Path | None = None) -> tuple[Path, dict]:
    if recovery is not None:
        _, status = load_candidate(recovery)
        return recovery, status

    valid = []
    for path in paths:
        if not path.exists():
            continue
        try:
            timestamp, status = load_candidate(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Ignoring invalid state {path}: {exc}")
            continue
        valid.append((timestamp, path, status))

    if not valid:
        raise SystemExit("No valid persisted tracker state is available")

    _, source_path, selected = max(valid, key=lambda item: item[0])
    selected = copy.deepcopy(selected)

    merged_sources = []
    for _, candidate_path, candidate in valid:
        if candidate_path == source_path:
            continue
        for canyon_id in EXPECTED_CANYON_IDS:
            merge_canyon_history(
                selected["canyons"][canyon_id],
                candidate["canyons"][canyon_id],
            )
        merged_sources.append(str(candidate_path))

    if merged_sources:
        selected.setdefault("state_restoration", {})[
            "historical_merge_sources"
        ] = merged_sources

    return source_path, selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--published", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--recovery", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source, status = choose_status(
        [args.published, args.backup], recovery=args.recovery
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(status), encoding="utf-8")
    print(f"Restored tracker state from {source}: {status['last_checked_utc']}")


if __name__ == "__main__":
    main()
