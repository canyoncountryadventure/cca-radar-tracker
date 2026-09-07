#!/usr/bin/env python3
"""Merge a known-good historical tracker snapshot into a newer operational state.

The recovery snapshot is authoritative only through its own latest frame. Newer
frames and current operational metadata remain in place. This is intended for
repairing deleted/corrupted historical frame-ledger intervals without rolling
back the live tracker.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def event_time(event: dict[str, Any] | None) -> datetime | None:
    if not event:
        return None
    return parse_utc(event.get("end_utc") or event.get("start_utc"))


def event_key(event: dict[str, Any]) -> tuple[str, str]:
    start = str(event.get("start_utc") or "")
    end = str(event.get("end_utc") or start)
    return start, end


def merge_events(
    current_events: list[dict[str, Any]],
    recovery_events: list[dict[str, Any]],
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Use recovery copies for events at/before cutoff; retain newer live events."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for event in current_events:
        if event and event.get("start_utc"):
            merged[event_key(event)] = copy.deepcopy(event)
    for event in recovery_events:
        end = event_time(event)
        if event and event.get("start_utc") and end is not None and end <= cutoff:
            merged[event_key(event)] = copy.deepcopy(event)
    return sorted(
        merged.values(),
        key=lambda item: event_time(item) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def newer_notification(
    current: dict[str, Any] | None, recovery: dict[str, Any] | None
) -> dict[str, Any]:
    current = copy.deepcopy(current or {})
    recovery = copy.deepcopy(recovery or {})
    current_time = parse_utc(current.get("last_email_sent_utc"))
    recovery_time = parse_utc(recovery.get("last_email_sent_utc"))
    if recovery_time and (not current_time or recovery_time > current_time):
        return recovery
    return current


def merge_status(current: dict[str, Any], recovery: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(current)
    recovery_cutoff = parse_utc(
        recovery.get("latest_frame_utc") or recovery.get("last_checked_utc")
    )
    if recovery_cutoff is None:
        raise ValueError("Recovery snapshot has no latest frame/check timestamp")

    current_canyons = merged.setdefault("canyons", {})
    recovery_canyons = recovery.get("canyons", {})
    if set(recovery_canyons) - set(current_canyons):
        missing = sorted(set(recovery_canyons) - set(current_canyons))
        raise ValueError(f"Current state is missing configured canyons: {missing}")

    # The known-good snapshot is authoritative for every frame it actually
    # contains. This repairs both missing frames and later-corrupted copies.
    ledger = merged.setdefault("frame_ledger", {})
    recovery_ledger = recovery.get("frame_ledger", {})
    replaced = 0
    added = 0
    for timestamp, record in recovery_ledger.items():
        if timestamp in ledger:
            if ledger[timestamp] != record:
                replaced += 1
        else:
            added += 1
        ledger[timestamp] = copy.deepcopy(record)

    # Seed exact historical event cards as well. The normal tracker rebuild
    # that follows will regenerate these from the repaired ledger, but seeding
    # them here makes the recovery state safe even before that rebuild runs.
    for canyon_id, recovery_canyon in recovery_canyons.items():
        current_canyon = current_canyons[canyon_id]
        current_canyon["events"] = merge_events(
            list(current_canyon.get("events") or []),
            list(recovery_canyon.get("events") or []),
            recovery_cutoff,
        )

        for key in ("last_rain_event", "last_qualifying_event"):
            recovery_event = recovery_canyon.get(key)
            current_event = current_canyon.get(key)
            recovery_time = event_time(recovery_event)
            current_time = event_time(current_event)
            if recovery_time and recovery_time <= recovery_cutoff:
                if current_time is None or current_time <= recovery_cutoff:
                    current_canyon[key] = copy.deepcopy(recovery_event)

        current_canyon["notification"] = newer_notification(
            current_canyon.get("notification"), recovery_canyon.get("notification")
        )

        # Retain known-good historical record maxima until the subsequent
        # deterministic rebuild recomputes them from the repaired ledger.
        recovery_records = recovery_canyon.get("historical_records") or {}
        current_records = current_canyon.get("historical_records") or {}
        for record_key in ("peak_individual_event", "peak_seven_day_evidence"):
            recovery_record = recovery_records.get(record_key)
            current_record = current_records.get(record_key)
            recovery_ratio = float((recovery_record or {}).get("ratio", (recovery_record or {}).get("fill_ratio", 0.0)) or 0.0)
            current_ratio = float((current_record or {}).get("ratio", (current_record or {}).get("fill_ratio", 0.0)) or 0.0)
            if recovery_record and recovery_ratio > current_ratio:
                current_records[record_key] = copy.deepcopy(recovery_record)
        current_canyon["historical_records"] = current_records

    ledger_keys = sorted(ledger)
    if ledger_keys:
        merged["ledger_started_utc"] = ledger_keys[0]
        merged["latest_frame_utc"] = max(
            ledger_keys[-1], str(merged.get("latest_frame_utc") or "")
        )

    starts = [
        parse_utc(value)
        for value in (
            current.get("monitoring_started_utc"),
            recovery.get("monitoring_started_utc"),
        )
        if value
    ]
    if starts:
        merged["monitoring_started_utc"] = utc_text(min(starts))

    # Make the merged operational backup slightly newer than the pre-recovery
    # published copy so select_status.py deterministically chooses it once.
    current_checked = parse_utc(current.get("last_checked_utc")) or datetime.now(UTC)
    merged["last_checked_utc"] = utc_text(max(datetime.now(UTC), current_checked + timedelta(seconds=1)))
    merged["history_recovery"] = {
        "applied_utc": utc_text(datetime.now(UTC)),
        "source_last_checked_utc": recovery.get("last_checked_utc"),
        "source_latest_frame_utc": recovery.get("latest_frame_utc"),
        "authoritative_through_utc": utc_text(recovery_cutoff),
        "recovery_frame_count": len(recovery_ledger),
        "added_frames": added,
        "replaced_frames": replaced,
        "method": "Known-good historical frame ledger merged into newer live state; newer frames preserved",
    }
    return merged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--recovery", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    current = json.loads(args.current.read_text(encoding="utf-8"))
    recovery = json.loads(args.recovery.read_text(encoding="utf-8"))
    merged = merge_status(current, recovery)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")

    info = merged["history_recovery"]
    print(
        "Merged historical state: "
        f"{info['recovery_frame_count']} authoritative frames; "
        f"{info['added_frames']} added; {info['replaced_frames']} replaced"
    )


if __name__ == "__main__":
    main()
