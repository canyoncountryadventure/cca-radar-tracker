#!/usr/bin/env python3
"""Restore immutable historical storm-model evidence after live recomputation.

Later model changes must not silently erase what the tracker actually published
for an earlier storm. Historical refill outputs are retained from durable
operational snapshots, while bulky radar grids remain in the live event record.

Schema v4 also repairs a failure exposed by the Zero G field loggers: a later
rebuild can retain only a short tail of an older storm. Overlapping event
variants are now reconciled so the more complete historical storm envelope is
kept instead of preserving both the complete event and its truncated fragment.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tracker

UTC = timezone.utc
EVIDENCE_KEY = "historical_model_evidence"
EVIDENCE_SCHEMA_VERSION = 4
BASE = (
    "https://raw.githubusercontent.com/canyoncountryadventure/cca-radar-tracker/"
    "operational-data/backups/{date}/status.json"
)
DEFAULT_SOURCES = [
    # The 2026-08-13 snapshot is required because it retains the complete
    # 2026-08-12 Zero G storm (21:35-22:50Z). Later recomputation shortened that
    # storm to a small tail and erased most of its rainfall evidence.
    ("2026-08-13", BASE.format(date="2026-08-13")),
    ("2026-08-24", BASE.format(date="2026-08-24")),
    ("2026-08-31", BASE.format(date="2026-08-31")),
]

BULKY_KEYS = {
    "radar_grid",
    "rain_grid",
    "grid_dbz",
    "grid_rain_inches",
    "radar_pixels",
    "rain_pixels",
    "watershed_grid",
}


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def event_start(event: dict[str, Any] | None) -> datetime | None:
    if not event:
        return None
    return parse_utc(event.get("start_utc"))


def event_end(event: dict[str, Any] | None) -> datetime | None:
    if not event:
        return None
    return parse_utc(event.get("end_utc") or event.get("start_utc"))


def event_key(event: dict[str, Any]) -> tuple[str, str]:
    start = str(event.get("start_utc") or "")
    return start, str(event.get("end_utc") or start)


def event_span_seconds(event: dict[str, Any]) -> float:
    start = event_start(event)
    end = event_end(event)
    if start is None or end is None:
        return 0.0
    return max(0.0, (end - start).total_seconds())


def fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CCA-Historical-Evidence-Recovery/4.0",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def classification_rank(event: dict[str, Any]) -> int:
    return {
        "full_flush": 4,
        "likely_full": 3,
        "moderate": 2,
        "minor": 1,
    }.get(str(event.get("classification") or "").lower(), 0)


def event_quality(event: dict[str, Any]) -> tuple[float, float, float, int]:
    """Prefer stronger/richer model evidence when event boundaries are identical."""
    runoff = float(
        event.get("direct_runoff_ft3")
        or event.get("generated_runoff_ft3")
        or 0.0
    )
    ratio = float(event.get("fill_ratio") or event.get("storage_ratio") or 0.0)
    cumulative = float(event.get("cumulative_ratio") or 0.0)
    return (
        runoff,
        max(ratio, cumulative),
        float(classification_rank(event)),
        len(json.dumps(event, sort_keys=True)),
    )


def event_completeness(
    event: dict[str, Any],
) -> tuple[int, float, float, float, tuple[float, float, float, int]]:
    """Rank alternate envelopes of the same physical storm.

    Rain-frame count and duration come first so a short tail created by replay
    cannot beat the complete event simply because a later model revision
    produced a different runoff number.
    """
    return (
        int(event.get("rain_frames") or event.get("frames") or 0),
        event_span_seconds(event),
        float(event.get("basin_rain_inches") or 0.0),
        float(event.get("peak_dbz") or event.get("peak_frame_maximum_dbz") or 0.0),
        event_quality(event),
    )


def same_storm_variant(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return True when two records are alternate envelopes of one storm."""
    if event_key(left) == event_key(right):
        return True

    left_start, left_end = event_start(left), event_end(left)
    right_start, right_end = event_start(right), event_end(right)
    if None in {left_start, left_end, right_start, right_end}:
        return False

    left_peak = left.get("peak_frame_utc")
    right_peak = right.get("peak_frame_utc")
    if left_peak and right_peak and left_peak == right_peak:
        return True

    overlap_start = max(left_start, right_start)
    overlap_end = min(left_end, right_end)
    if overlap_end < overlap_start:
        return False

    overlap_seconds = (overlap_end - overlap_start).total_seconds()
    shorter = min(event_span_seconds(left), event_span_seconds(right))
    if shorter <= 0:
        return overlap_seconds == 0 and (
            left_start <= right_start <= left_end
            or right_start <= left_start <= right_end
        )

    # This catches the 2026-08-12 Zero G 22:35-22:50Z tail inside the
    # authoritative 21:35-22:50Z event without merging merely adjacent storms.
    return overlap_seconds / shorter >= 0.50


def is_bulky_key(key: str) -> bool:
    lowered = key.lower()
    return (
        lowered in BULKY_KEYS
        or lowered.endswith("_zlib")
        or lowered.endswith("_base64")
        or ("grid" in lowered and lowered not in {"grid_cell_count"})
    )


def compact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: compact_value(item)
            for key, item in value.items()
            if not is_bulky_key(str(key))
        }
    if isinstance(value, list):
        if len(value) > 500:
            return []
        return [compact_value(item) for item in value]
    return copy.deepcopy(value)


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    compacted = compact_value(event)
    if not isinstance(compacted, dict):
        raise TypeError("Event compaction did not return an object")
    return compacted


def collect_snapshot_events(
    canyon: dict[str, Any], cutoff: datetime
) -> list[dict[str, Any]]:
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
        key = event_key(event)
        prior = unique.get(key)
        if prior is None or event_quality(event) > event_quality(prior):
            unique[key] = event
    return list(unique.values())


def add_reconciled_event(
    events: list[dict[str, Any]], candidate: dict[str, Any]
) -> None:
    """Insert an event while collapsing truncated/expanded variants."""
    matches = [
        index
        for index, existing in enumerate(events)
        if same_storm_variant(existing, candidate)
    ]
    if not matches:
        events.append(candidate)
        return

    contenders = [events[index] for index in matches] + [candidate]
    preferred = max(contenders, key=event_completeness)
    for index in reversed(matches):
        events.pop(index)
    events.append(copy.deepcopy(preferred))


def bootstrap_evidence(
    status: dict[str, Any], sources: list[tuple[str, str]]
) -> dict[str, Any]:
    current_ids = set((status.get("canyons") or {}).keys())
    accumulated: dict[str, list[dict[str, Any]]] = {
        canyon_id: [] for canyon_id in current_ids
    }
    source_meta: list[dict[str, Any]] = []
    max_cutoff: datetime | None = None

    for source_label, source_url in sources:
        snapshot = fetch_json(source_url)
        cutoff = parse_utc(
            snapshot.get("latest_frame_utc") or snapshot.get("last_checked_utc")
        )
        if cutoff is None:
            raise RuntimeError(
                f"Historical recovery snapshot {source_label} has no usable cutoff"
            )
        max_cutoff = cutoff if max_cutoff is None else max(max_cutoff, cutoff)
        snapshot_canyons = snapshot.get("canyons") or {}
        snapshot_ids = set(snapshot_canyons)
        if current_ids and current_ids != snapshot_ids:
            raise RuntimeError(
                f"Historical snapshot {source_label} canyon IDs do not match "
                f"current configuration: missing={sorted(current_ids - snapshot_ids)}, "
                f"extra={sorted(snapshot_ids - current_ids)}"
            )

        source_meta.append(
            {
                "label": source_label,
                "url": source_url,
                "last_checked_utc": snapshot.get("last_checked_utc"),
                "latest_frame_utc": snapshot.get("latest_frame_utc"),
            }
        )

        for canyon_id, canyon in snapshot_canyons.items():
            signature = canyon.get("model_signature")
            for full_event in collect_snapshot_events(canyon, cutoff):
                event = compact_event(full_event)
                event["historical_model_evidence"] = True
                event["historical_model_signature"] = signature
                event["historical_model_snapshot_utc"] = snapshot.get(
                    "last_checked_utc"
                )
                event["historical_model_source"] = source_label

                exact = next(
                    (
                        prior
                        for prior in accumulated[canyon_id]
                        if event_key(prior) == event_key(event)
                    ),
                    None,
                )
                if exact is not None and event_quality(event) <= event_quality(exact):
                    continue
                add_reconciled_event(accumulated[canyon_id], event)

    if max_cutoff is None:
        raise RuntimeError("No historical recovery sources were loaded")

    canyon_evidence: dict[str, Any] = {}
    total_events = 0
    for canyon_id in sorted(current_ids):
        events = sorted(
            accumulated[canyon_id],
            key=lambda item: event_end(item)
            or datetime.min.replace(tzinfo=UTC),
        )
        total_events += len(events)
        canyon_evidence[canyon_id] = {"events": events}

    evidence = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "sources": source_meta,
        "authoritative_through_utc": max_cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event_count": total_events,
        "canyons": canyon_evidence,
        "explanation": (
            "Previously published historical model outputs retained across model "
            "revisions. Overlapping replay variants are reconciled to the most "
            "complete storm envelope. Bulky radar grids remain only in the normal "
            "event record and are not duplicated. These preserved values are "
            "historical model outputs, not field observations."
        ),
    }
    status[EVIDENCE_KEY] = evidence
    return evidence


def merge_events(
    current: list[dict[str, Any]],
    historical: list[dict[str, Any]],
    cutoff: datetime,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = [
        copy.deepcopy(event)
        for event in current
        if event and event.get("start_utc")
    ]

    for historical_event in historical:
        end = event_end(historical_event)
        if end is None or end > cutoff:
            continue

        matches = [
            index
            for index, existing in enumerate(merged)
            if same_storm_variant(existing, historical_event)
        ]
        if not matches:
            merged.append(copy.deepcopy(historical_event))
            continue

        contenders = [merged[index] for index in matches] + [historical_event]
        preferred = max(contenders, key=event_completeness)
        selected = copy.deepcopy(preferred)

        same_peak_live = next(
            (
                merged[index]
                for index in matches
                if merged[index].get("peak_frame_utc")
                and merged[index].get("peak_frame_utc")
                == selected.get("peak_frame_utc")
            ),
            None,
        )
        if same_peak_live is not None:
            combined = copy.deepcopy(same_peak_live)
            combined.update(selected)
            selected = combined

        for index in reversed(matches):
            merged.pop(index)
        merged.append(selected)

    return sorted(
        merged,
        key=lambda item: event_end(item)
        or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def restore(
    status: dict[str, Any],
    sources: list[tuple[str, str]],
    root: Path,
) -> dict[str, Any]:
    evidence = status.get(EVIDENCE_KEY) or {}
    if int(evidence.get("schema_version") or 0) < EVIDENCE_SCHEMA_VERSION:
        evidence = bootstrap_evidence(status, sources)

    cutoff = parse_utc(evidence.get("authoritative_through_utc"))
    if cutoff is None:
        raise RuntimeError("Stored historical evidence has no cutoff")

    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    collection = json.loads(
        (root / "watersheds.geojson").read_text(encoding="utf-8")
    )
    atlas = json.loads((root / "atlas14.json").read_text(encoding="utf-8"))
    hydrology = json.loads((root / "hydrology.json").read_text(encoding="utf-8"))
    canyons, _ = tracker.build_canyons(collection, atlas, config, hydrology)
    by_id = {canyon.canyon_id: canyon for canyon in canyons}

    restored_count = 0
    for canyon_id, stored in (evidence.get("canyons") or {}).items():
        if (
            canyon_id not in by_id
            or canyon_id not in (status.get("canyons") or {})
        ):
            continue
        canyon_status = status["canyons"][canyon_id]
        historical = stored.get("events") or []
        canyon_status["events"] = merge_events(
            list(canyon_status.get("events") or []),
            historical,
            cutoff,
        )
        restored_count += len(historical)

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
        tracker.cumulative_refill_evidence(
            canyon_status, by_id[canyon_id], config
        )

    status["historical_model_evidence_status"] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "restored_event_count": restored_count,
        "authoritative_through_utc": evidence.get("authoritative_through_utc"),
        "sources": [
            item.get("label") for item in evidence.get("sources") or []
        ],
        "ok": True,
    }
    return status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument(
        "--source-url",
        action="append",
        default=[],
        help=(
            "Optional override recovery snapshot URL. May be supplied multiple times."
        ),
    )
    args = parser.parse_args()

    sources = DEFAULT_SOURCES
    if args.source_url:
        sources = [
            (f"custom-{index + 1}", url)
            for index, url in enumerate(args.source_url)
        ]

    status = json.loads(args.status.read_text(encoding="utf-8"))
    restored = restore(status, sources, ROOT)
    args.status.write_text(
        json.dumps(restored, indent=2) + "\n", encoding="utf-8"
    )

    info = restored["historical_model_evidence_status"]
    print(
        "Historical model evidence restored: "
        f"{info['restored_event_count']} events through "
        f"{info['authoritative_through_utc']} from "
        f"{', '.join(info['sources'])}; "
        f"status size={args.status.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    main()
