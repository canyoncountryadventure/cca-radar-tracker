#!/usr/bin/env python3
"""Keep email deduplication markers separate from the large radar status snapshots.

GitHub Pages can serve an older status.json during propagation. The small ledger
on the operational-data branch is the durable authority for already-sent alerts.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path


SCHEMA_VERSION = 1


def marker_order(marker: dict | None) -> tuple[str, int, str]:
    """Compare sent notifications without letting an older snapshot roll back a send."""
    marker = marker or {}
    return (
        str(marker.get("last_email_sent_utc") or ""),
        int(marker.get("last_emailed_alert_tier") or 0),
        str(marker.get("last_emailed_event_start_utc") or ""),
    )


def fresher(current: dict | None, saved: dict | None) -> dict:
    current, saved = current or {}, saved or {}
    return copy.deepcopy(saved if marker_order(saved) > marker_order(current) else current)


def restore_notifications(status: dict, ledger: dict) -> bool:
    """Apply durable markers to the current run before sending any email."""
    validate_ledger(ledger)
    changed = False
    canyons = status["canyons"]
    for canyon_id, saved in ledger["canyons"].items():
        if canyon_id not in canyons:
            raise ValueError(f"Unknown canyon ID in notification ledger: {canyon_id}")
        current = canyons[canyon_id].get("notification") or {}
        chosen = fresher(current, saved)
        if chosen != current:
            canyons[canyon_id]["notification"] = chosen
            changed = True

    current_health = status.get("health_notification") or {}
    chosen_health = fresher(current_health, ledger.get("health_notification"))
    if chosen_health != current_health:
        status["health_notification"] = chosen_health
        changed = True
    return changed


def extract_notifications(status: dict, ledger: dict) -> dict:
    """Store newest send markers, never overwriting an event with an older one."""
    validate_ledger(ledger)
    result = copy.deepcopy(ledger)
    for canyon_id, canyon in status["canyons"].items():
        marker = canyon.get("notification") or {}
        if not marker.get("last_emailed_event_start_utc"):
            continue
        previous = result["canyons"].get(canyon_id) or {}
        result["canyons"][canyon_id] = fresher(previous, marker)
    health = status.get("health_notification") or {}
    result["health_notification"] = fresher(
        result.get("health_notification"), health
    )
    return result


def validate_ledger(ledger: dict) -> None:
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version") != SCHEMA_VERSION
        or not isinstance(ledger.get("canyons"), dict)
        or not isinstance(ledger.get("health_notification", {}), dict)
    ):
        raise ValueError("Invalid notification ledger; refusing to send duplicate alerts")
    if any(not isinstance(marker, dict) for marker in ledger["canyons"].values()):
        raise ValueError("Invalid canyon marker in notification ledger")


def write_if_changed(path: Path, original: dict, updated: dict) -> bool:
    if original == updated:
        return False
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("restore", "persist"))
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()

    status = json.loads(args.status.read_text(encoding="utf-8"))
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    if args.mode == "restore":
        updated = copy.deepcopy(status)
        changed = restore_notifications(updated, ledger)
        if changed:
            write_if_changed(args.status, status, updated)
        print("Restored durable notification markers" if changed else "Notification markers already current")
    else:
        updated = extract_notifications(status, ledger)
        changed = write_if_changed(args.ledger, ledger, updated)
        print("Updated durable notification ledger" if changed else "Notification ledger unchanged")


if __name__ == "__main__":
    main()
