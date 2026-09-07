#!/usr/bin/env python3
"""Select the freshest valid persisted tracker state for a workflow run."""

from __future__ import annotations

import argparse
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
    _, path, status = max(valid, key=lambda item: item[0])
    return path, status


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
