#!/usr/bin/env python3
"""Restore immutable historical storm-model evidence after live recomputation.

A tracker model change must not silently rewrite what an earlier published model
actually concluded for a historical storm.  This tool bootstraps a compact copy
of the known-good Aug. 31, 2026 event records once, stores that evidence inside
status.json, and re-applies those exact historical event objects after each
normal tracker run.  Newer events remain governed by the current model.
"""

from __future__ import annotations

import argparse
import copy
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import tracker

UTC = timezone.utc
DEFAULT_SOURCE = (
    "https://raw.githubusercontent.com/canyoncountryadventure/cca-radar-tracker/"
    "operational-data/backups/2026-08-31/status.json"
)
EVIDENCE_KEY = "historical_model_evidence"


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def event_end(event: dict[str, Any] | None) -> datetime | None:
    if not event:
        return None
    return parse_utc(event.get("end_utc") or event.get("start_utc"))


def event_key(event: dict[str, Any]) -> tuple[str, str]:
    start = str(event.get("start_utc") or "")
    return start, str(event.get("end_utc") or start)


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CCA-Historical-Evidence-Recovery/1.0",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def collect_snapshot_events(canyon: dict[str, Any], cutoff: datetime) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    candidates.extend(copy.deepcopy(canyon.get("events") or []))
    for key in ("last_rain_event", "last_qualifying_event"):
        event = canyon.get(key)
        if event:
            candidates.append(copy.deepcopy(event))

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for event in candidates:
        end = event_end(event)
        if not event.get("start_utc") or end is None or end > cutoff:
            continue
        # Prefer the richest exact published copy.
        key = event_key(event)
        prior = unique.get(key)
        if prior is None or len(json.dumps(event)) > len(json.dumps(prior)):
            unique[key] = event

    return sorted(
        unique.values(),
        key=lambda item: event_end(item) or datetime.min.replace(tzinfo=UTC),
    )


def bootstrap_evidence(status: dict[str, Any], source_url: str) -> dict[str, Any]:
    snapshot = fetch_json(source_url)
    cutoff = parse_utc(snapshot.get("latest_frame_utc") or snapshot.get("last_checked_utc"))
    if cutoff is None:
        raise RuntimeError("Historical recovery snapshot has no usable cutoff timestamp")

    snapshot_canyons = snapshot.get("canyons") or {}
    current_ids = set((status.get("canyons") or {}).keys())
    snapshot_ids = set(snapshot_canyons.keys())
    if current_ids and current_ids != snapshot_ids:
        raise RuntimeError(
            "Historical recovery canyon IDs do not match current configuration: "
            f"missing={sorted(current_ids - snapshot_ids)}, "
            f"extra={sorted(snapshot_ids - current_ids)}"
        )

    canyon_evidence: dict[str, Any] = {}
    total_events = 0
    for canyon_id, canyon in snapshot_canyons.items():
        events = collect_snapshot_events(canyon, cutoff)
        signature = canyon.get("model_signature")
        for event in events:
            event["historical_model_evidence"] = True
            event["historical_model_signature"] = signature
            event["historical_model_snapshot_utc"] = snapshot.get("last_checked_utc")
        total_events += len(events)
        canyon_evidence[canyon_id] = {
            "model_signature": signature,
            "events": events,
        }

    evidence = {
        "schema_version": 1,
        "source": source_url,
        "source_last_checked_utc": snapshot.get("last_checked_utc"),
        "source_latest_frame_utc": snapshot.get("latest_frame_utc"),
        "authoritative_through_utc": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_count": total_events,
        "canyons": canyon_evidence,
        "explanation": (
            "Exact published historical model-event records retained so later model "
            "changes cannot silently rewrite prior storm conclusions. Newer storms "
            "continue to use the current model."
        ),
    }
    status[EVIDENCE_KEY] = evidence
    return evidence


def merge_events(
    current: list[dict[str, Any]], historical: list[dict[str, Any]], cutoff: datetime
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for event in current:
        if event and event.get("start_utc"):
            merged[event_key(event)] = copy.deepcopy(event)

    # The old snapshot is authoritative for historical event-model outputs.
    for event in historical:
        end = event_end(event)
        if end is not None and end <= cutoff:
            merged[event_key(event)] = copy.deepcopy(event)

    return sorted(
        merged.values(),
        key=lambda item: event_end(item) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def restore(status: dict[str, Any], source_url: str, root: Path) -> dict[str, Any]:
    evidence = status.get(EVIDENCE_KEY)
    if not evidence:
        evidence = bootstrap_evidence(status, source_url)

    cutoff = parse_utc(evidence.get("authoritative_through_utc"))
    if cutoff is None:
        raise RuntimeError("Stored historical evidence has no cutoff")

    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    collection = json.loads((root / "watersheds.geojson").read_text(encoding="utf-8"))
    atlas = json.loads((root / "atlas14.json").read_text(encoding="utf-8"))
    hydrology = json.loads((root / "hydrology.json").read_text(encoding="utf-8"))
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    by_id = {canyon.canyon_id: canyon for canyon in canyons}

    restored_count = 0
    for canyon_id, stored in (evidence.get("canyons") or {}).items():
        if canyon_id not in by_id or canyon_id not in (status.get("canyons") or {}):
            continue
        canyon_status = status["canyons"][canyon_id]
        historical = stored.get("events") or []
        canyon_status["events"] = merge_events(
            list(canyon_status.get("events") or []), historical, cutoff
        )
        restored_count += len(historical)

        # Rebuild event pointers from the merged event list.
        events = canyon_status["events"]
        canyon_status["last_rain_event"] = events[0] if events else None
        canyon_status["last_qualifying_event"] = next(
            (
                event
                for event in events
                if event.get("classification") in {"likely_full", "full_flush"}
            ),
            None,
        )

        # Recompute today's condition from the restored historical outputs plus
        # all newer current-model events. This updates refill history, records,
        # decay, confidence, and condition estimate consistently.
        tracker.cumulative_refill_evidence(canyon_status, by_id[canyon_id], config)

    status["historical_model_evidence_status"] = {
        "restored_event_count": restored_count,
        "authoritative_through_utc": evidence.get("authoritative_through_utc"),
        "source_last_checked_utc": evidence.get("source_last_checked_utc"),
        "ok": True,
    }
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--source-url", default=DEFAULT_SOURCE)
    args = parser.parse_args()

    status = json.loads(args.status.read_text(encoding="utf-8"))
    root = Path(__file__).resolve().parents[1]
    restored = restore(status, args.source_url, root)
    args.status.write_text(json.dumps(restored, indent=2) + "\n", encoding="utf-8")

    info = restored["historical_model_evidence_status"]
    print(
        "Historical model evidence restored: "
        f"{info['restored_event_count']} events through "
        f"{info['authoritative_through_utc']}"
    )


if __name__ == "__main__":
    main()
