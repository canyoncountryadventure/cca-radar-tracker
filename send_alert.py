#!/usr/bin/env python3
"""Send one Gmail alert when a new qualifying pool-fill storm begins."""

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


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mountain_text(value: str) -> str:
    return parse_utc(value).astimezone(MOUNTAIN).strftime("%B %-d, %Y at %-I:%M %p %Z")


def should_send(status: dict) -> bool:
    event = status.get("last_qualifying_event")
    if not event:
        return False
    notification = status.get("notification") or {}
    return notification.get("last_emailed_event_start_utc") != event.get("start_utc")


def event_message(status: dict, sender: str, recipient: str) -> EmailMessage:
    event = status["last_qualifying_event"]
    coverage = event["peak_coverage_percent"]
    frames = int(event.get("qualifying_frames", 1))
    duration = "one qualifying five-minute frame" if frames == 1 else f"{frames} qualifying five-minute frames"

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "GLOBAL CANYON â€” Pool-filling radar trigger detected"
    message.set_content(
        "\n".join(
            [
                "GLOBAL CANYON POOL-FILL TRIGGER",
                "",
                f"Started: {mountain_text(event['start_utc'])}",
                f"Latest qualifying frame: {mountain_text(event['end_utc'])}",
                f"Duration recorded: {duration}",
                f"Peak reflectivity: {event['peak_dbz']} dBZ",
                f"Peak watershed coverage at or above 50 dBZ: {coverage.get('50', 0)}%",
                f"Peak watershed coverage at or above 55 dBZ: {coverage.get('55', 0)}%",
                f"Peak watershed coverage at or above 60 dBZ: {coverage.get('60', 0)}%",
                "",
                "At least one Global Canyon pool-fill radar rule was met.",
                "",
                "Live tracker:",
                "https://canyoncountryadventure.github.io/cca-radar-tracker/",
                "",
                "Radar-based pool-fill indicator; not visual confirmation or flash-flood guidance.",
            ]
        )
    )
    return message


def test_message(sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "TEST â€” Global Canyon pool-fill radar alerts are active"
    message.set_content(
        "\n".join(
            [
                "GLOBAL CANYON RADAR EMAIL TEST",
                "",
                "The automated email connection is working.",
                "A new email will be sent when a qualifying pool-filling radar event begins.",
                "Consecutive qualifying frames within the same storm will not create duplicate alerts.",
                "",
                "Live tracker:",
                "https://canyoncountryadventure.github.io/cca-radar-tracker/",
            ]
        )
    )
    return message


def send_gmail(message: EmailMessage, username: str, app_password: str) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(username, app_password)
        smtp.send_message(message)


def save_status(path: Path, status: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, default=ROOT / "docs/data/status.json")
    parser.add_argument("--test", action="store_true", help="Send a test message regardless of radar state")
    arguments = parser.parse_args()

    username = os.environ.get("SMTP_USERNAME", "canyoncountryadventure@gmail.com").strip()
    recipient = os.environ.get("ALERT_TO", "canyoncountryadventure@gmail.com").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    if not app_password:
        raise RuntimeError("GMAIL_APP_PASSWORD repository secret is missing")

    if arguments.test:
        send_gmail(test_message(username, recipient), username, app_password)
        print(f"Test email sent to {recipient}")
        return 0

    status = json.loads(arguments.status.read_text(encoding="utf-8"))
    if not should_send(status):
        print("No new qualifying storm; no email sent")
        return 0

    event = status["last_qualifying_event"]
    send_gmail(event_message(status, username, recipient), username, app_password)
    status["notification"] = {
        "last_emailed_event_start_utc": event["start_utc"],
        "last_email_sent_utc": utc_text(datetime.now(UTC)),
        "recipient": recipient,
    }
    save_status(arguments.status, status)
    print(f"Pool-fill alert sent to {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())