#!/usr/bin/env python3
"""Restore immutable historical storm-model evidence after live recomputation.

Later hydrology-model changes must not silently erase what the tracker actually
published for an earlier storm. Historical refill outputs are retained from
multiple durable operational snapshots, but bulky radar grids are not duplicated.
New storms continue to use the current model.
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
EVIDENCE_SCHEMA_VERSION = 3
BASE = (
    "https://raw.githubusercontent.com/canyoncountryadventure/cca-radar-tracker/"
    "operational-data/backups/{date}/status.json"
)
DEFAULT_SOURCES = [
    ("2026-08-24", BASE.format(date="2026-08-24")),
    ("2026-08-31", BASE.format(date="2026-08-31")),
]

# Radar arrays/snippets are already retained in the normal event record. They are
# intentionally omitted from the immutable model-evidence copy so status.json does
# not balloon by tens of megabytes.
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
            "User-Agent": "CCA-Historical-Evidence-Recovery/3.0",
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
    runoff = float(event.get("direct_runoff_ft3") or event.get("generated_runoff_ft3") or 0.0)
    ratio = float(event.get("fill_ratio") or event.get("storage_ratio") or 0.0)
    cumulative = float(event.get("cumulative_ratio") or 0.0)
    return runoff, max(ratio, cumulative), float(classification_rank(event)), len(json.dumps(event, sort_keys=True))


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
        # Large numeric arrays are radar payloads; short semantic lists are useful.
        if len(value) > 500:
            return []
        return [compact_value(item) for item in value]
    return copy.deepcopy(value)


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    compacted = compact_value(event)
    if not isinstance(compacted, dict):
        raise TypeError("Event compaction did not return an object")
    return compacted


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
        key = event_key(event)
        prior = unique.get(key)
        if prior is None or event_quality(event) > event_quality(prior):
            unique[key] = event
    return list(unique.values())


def bootstrap_evidence(
    status: dict[str, Any], sources: list[tuple[str, str]]
) -> dict[str, Any]:
    current_ids = set((status.get("canyons") or {}).keys())
    accumulated: dict[str, dict[tuple[str, str], dict[str, Any]]] = {
        canyon_id: {} for canyon_id in current_ids
    }
    source_meta: list[dict[str, Any]] = []
    max_cutoff: datetime | None = None

    for source_label, source_url in sources:
        snapshot = fetch_json(source_url)
        cutoff = parse_utc(snapshot.get("latest_frame_utc") or snapshot.get("last_checked_utc"))
        if cutoff is None:
            raise RuntimeError(f"Historical recovery snapshot {source_label} has no usable cutoff")
        max_cutoff = cutoff if max_cutoff is None else max(max_cutoff, cutoff)
        snapshot_canyons = snapshot.get("canyons") or {}
        snapshot_ids = set(snapshot_canyons)
        if current_ids and current_ids != snapshot_ids:
            raise RuntimeError(
                f"Historical snapshot {source_label} canyon IDs do not match current configuration: "
                f"missing={sorted(current_ids - snapshot_ids)}, extra={sorted(snapshot_ids - current_ids)}"
            )

        source_meta.append({
            "label": source_label,
            "url": source_url,
            "last_checked_utc": snapshot.get("last_checked_utc"),
            "latest_frame_utc": snapshot.get("latest_frame_utc"),
        })

        for canyon_id, canyon in snapshot_canyons.items():
            signature = canyon.get("model_signature")
            for full_event in collect_snapshot_events(canyon, cutoff):
                event = compact_event(full_event)
                event["historical_model_evidence"] = True
                event["historical_model_signature"] = signature
                event["historical_model_snapshot_utc"] = snapshot.get("last_checked_utc")
                event["historical_model_source"] = source_label
                key = event_key(event)
                prior = accumulated[canyon_id].get(key)
                if prior is None or event_quality(event) > event_quality(prior):
                    accumulated[canyon_id][key] = event

    if max_cutoff is None:
        raise RuntimeError("No historical recovery sources were loaded")

    canyon_evidence: dict[str, Any] = {}
    total_events = 0
    for canyon_id in sorted(current_ids):
        events = sorted(
            accumulated[canyon_id].values(),
            key=lambda item: event_end(item) or datetime.min.replace(tzinfo=UTC),
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
            "Previously published historical model outputs retained across model revisions. "
            "Bulky radar grids remain only in the normal event record and are not duplicated. "
            "These preserved values are historical model outputs, not field observations."
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

    for historical_event in historical:
        end = event_end(historical_event)
        if end is None or end > cutoff:
            continue
        key = event_key(historical_event)
        if key in merged:
            # Overlay preserved model outputs onto the current event while retaining
            # the current event's radar grids/snippets and any newer metadata.
            merged[key].update(copy.deepcopy(historical_event))
        else:
            merged[key] = copy.deepcopy(historical_event)

    return sorted(
        merged.values(),
        key=lambda item: event_end(item) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )


def restore(
    status: dict[str, Any], sources: list[tuple[str, str]], root: Path
) -> dict[str, Any]:
    evidence = status.get(EVIDENCE_KEY) or {}
    if int(evidence.get("schema_version") or 0) < EVIDENCE_SCHEMA_VERSION:
        evidence = bootstrap_evidence(status, sources)

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

        events = canyon_status["events"]
        canyon_status["last_rain_event"] = events[0] if events else None
        canyon_status["last_qualifying_event"] = next(
            (event for event in events if event.get("classification") in {"likely_full", "full_flush"}),
            None,
        )
        tracker.cumulative_refill_evidence(canyon_status, by_id[canyon_id], config)

    status["historical_model_evidence_status"] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "restored_event_count": restored_count,
        "authoritative_through_utc": evidence.get("authoritative_through_utc"),
        "sources": [item.get("label") for item in evidence.get("sources") or []],
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
        help="Optional override recovery snapshot URL. May be supplied multiple times.",
    )
    args = parser.parse_args()

    sources = DEFAULT_SOURCES
    if args.source_url:
        sources = [(f"custom-{index + 1}", url) for index, url in enumerate(args.source_url)]

    status = json.loads(args.status.read_text(encoding="utf-8"))
    restored = restore(status, sources, ROOT)
    args.status.write_text(json.dumps(restored, indent=2) + "\n", encoding="utf-8")

    info = restored["historical_model_evidence_status"]
    print(
        "Historical model evidence restored: "
        f"{info['restored_event_count']} events through {info['authoritative_through_utc']} "
        f"from {', '.join(info['sources'])}; status size={args.status.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    main()
