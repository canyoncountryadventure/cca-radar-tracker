#!/usr/bin/env python3
"""Send Gmail alerts when canyon refill events reach configured severity tiers."""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
MOUNTAIN = ZoneInfo("America/Denver")
LIVE_URL = "https://canyoncountryadventure.github.io/cca-radar-tracker/"
ALERT_SUBSTANTIAL_RATIO = 0.50
ALERT_TIER_SUBSTANTIAL = 1
ALERT_TIER_LIKELY_FULL = 2
ALERT_TIER_FULL_FLUSH = 3


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mountain_text(value: str) -> str:
    return parse_utc(value).astimezone(MOUNTAIN).strftime(
        "%B %-d, %Y at %-I:%M %p %Z"
    )


def atlas_text(event: dict) -> str:
    years = event.get("atlas14_return_period_years")
    if years is None:
        return "not available"
    years = float(years)
    if years < 1:
        return "<1-year equivalent"
    if years >= 1000:
        return ">=1,000-year equivalent"
    return f"{years:.1f}-year equivalent" if years < 10 else f"{years:.0f}-year equivalent"


def direct_runoff(event: dict) -> float:
    return float(
        event.get("direct_runoff_ft3", event.get("estimated_runoff_ft3", 0))
        or 0
    )


def alert_tier(event: dict | None) -> int:
    """Return the email severity tier for one modeled rain event."""
    if not event:
        return 0
    classification = str(event.get("classification") or "")
    if classification == "full_flush":
        return ALERT_TIER_FULL_FLUSH
    if classification == "likely_full":
        return ALERT_TIER_LIKELY_FULL
    if float(event.get("fill_ratio") or 0) >= ALERT_SUBSTANTIAL_RATIO:
        return ALERT_TIER_SUBSTANTIAL
    return 0


def alert_tier_label(tier: int) -> str:
    return {
        ALERT_TIER_SUBSTANTIAL: "Substantial partial refill",
        ALERT_TIER_LIKELY_FULL: "Major refill likely",
        ALERT_TIER_FULL_FLUSH: "Strong flush likely",
    }.get(tier, "Canyon refill")


def pending_alerts(status: dict) -> list[tuple[dict, dict, int]]:
    """Return new threshold crossings, including escalation within one storm."""
    alerts: list[tuple[dict, dict, int]] = []
    for canyon in status.get("canyons", {}).values():
        event = canyon.get("last_rain_event")
        tier = alert_tier(event)
        if not event or tier == 0:
            continue

        notification = canyon.get("notification") or {}
        same_event = (
            notification.get("last_emailed_event_start_utc")
            == event.get("start_utc")
        )
        # Older notifications predate tier tracking and were only sent for
        # likely-full/flush events, so treat them as tier 2 to prevent repeats.
        previous_tier = int(
            notification.get(
                "last_emailed_alert_tier",
                ALERT_TIER_LIKELY_FULL if same_event else 0,
            )
            or 0
        )
        if not same_event or tier > previous_tier:
            alerts.append((canyon, event, tier))
    return alerts


def alert_message(
    alerts: list[tuple[dict, dict, int]], sender: str, recipient: str
) -> EmailMessage:
    names = ", ".join(canyon["name"] for canyon, _, _ in alerts)
    highest_tier = max(tier for _, _, tier in alerts)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = (
        f"CCA RADAR — {alert_tier_label(highest_tier)}: {names}"
    )

    lines = ["CCA CANYON POOL-REFILL MODEL ALERT", ""]
    for canyon, event, tier in alerts:
        tests = event.get("decision_tests") or {}
        lines.extend(
            [
                canyon["name"].upper(),
                f"Alert threshold: {alert_tier_label(tier)}",
                event.get("classification_label", "Major refill model trigger"),
                event.get("classification_explanation", ""),
                f"Storm began: {mountain_text(event['start_utc'])}",
                f"Latest wet frame: {mountain_text(event['end_utc'])}",
                f"Basin-average radar rain: {event.get('basin_rain_inches', 0):.3f} inches",
                f"Estimated NRCS direct runoff: {direct_runoff(event):,.0f} ft³",
                f"Storage-target ratio: {event.get('fill_ratio', 0):.2f}×",
                f"Peak reflectivity: {event.get('peak_dbz', 0)} dBZ",
                f"Atlas 14 context: {atlas_text(event)}",
                "Heavy-rain footprint: "
                + ("passed" if tests.get("heavy_rain_footprint_met", event.get("spatial_gate_seen")) else "not reached"),
                "Minimum wet duration: "
                + ("passed" if tests.get("minimum_wet_duration_met") else "not reached"),
                "",
            ]
        )

    lines.extend(
        [
            "Live dashboard:",
            LIVE_URL,
            "",
            "This is a radar/runoff/storage model result, not a field observation. "
            "Channel transmission losses and starting pool levels are unknown.",
        ]
    )
    message.set_content("\n".join(lines))
    return message


def test_message(sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "TEST — CCA canyon refill alerts are active"
    message.set_content(
        "\n".join(
            [
                "CCA MULTI-CANYON RADAR EMAIL TEST",
                "",
                "The automated email connection is working.",
                "An alert is sent when a canyon reaches at least a substantial-partial-refill ratio, with additional alerts if the same storm escalates to likely-full or strong-flush.",
                "The dashboard retains the last rain event and last major refill event for every canyon.",
                "",
                LIVE_URL,
            ]
        )
    )
    return message


def send_gmail(message: EmailMessage, username: str, app_password: str) -> None:
    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=ssl.create_default_context(),
        timeout=30,
    ) as smtp:
        smtp.login(username, app_password)
        smtp.send_message(message)


def save_status(path: Path, status: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status", type=Path, default=ROOT / "docs/data/status.json"
    )
    parser.add_argument("--test", action="store_true")
    arguments = parser.parse_args()

    username = os.environ.get(
        "SMTP_USERNAME", "canyoncountryadventure@gmail.com"
    ).strip()
    recipient = os.environ.get(
        "ALERT_TO", "canyoncountryadventure@gmail.com"
    ).strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD repository secret is missing")

    if arguments.test:
        send_gmail(test_message(username, recipient), username, app_password)
        print(f"Test email sent to {recipient}")
        return 0

    status = json.loads(arguments.status.read_text(encoding="utf-8"))
    alerts = pending_alerts(status)
    if not alerts:
        print("No new substantial-or-higher canyon threshold crossing; no email sent")
        return 0

    send_gmail(alert_message(alerts, username, recipient), username, app_password)
    sent = utc_text(datetime.now(UTC))
    for canyon, event, tier in alerts:
        canyon["notification"] = {
            "last_emailed_event_start_utc": event["start_utc"],
            "last_email_sent_utc": sent,
            "last_emailed_alert_tier": tier,
            "last_emailed_alert_label": alert_tier_label(tier),
            "recipient": recipient,
        }
    save_status(arguments.status, status)
    print(
        f"Pool-refill threshold alert sent for {len(alerts)} "
        f"canyon{'s' if len(alerts) != 1 else ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
