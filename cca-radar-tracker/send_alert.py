#!/usr/bin/env python3
"""Send Gmail alerts for newly qualifying canyon pool-fill events."""

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


def parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def mountain_text(value: str) -> str:
    return parse_utc(value).astimezone(MOUNTAIN).strftime("%B %-d, %Y at %-I:%M %p %Z")


def pending_alerts(status: dict) -> list[tuple[dict, dict]]:
    alerts = []
    for canyon in status.get("canyons", {}).values():
        event = canyon.get("last_qualifying_event")
        notification = canyon.get("notification") or {}
        if event and notification.get("last_emailed_event_start_utc") != event.get("start_utc"):
            alerts.append((canyon, event))
    return alerts


def alert_message(alerts: list[tuple[dict, dict]], sender: str, recipient: str) -> EmailMessage:
    names = ", ".join(canyon["name"] for canyon, _ in alerts)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = f"CCA RADAR — Pool-filling trigger: {names}"
    lines = ["CCA CANYON POOL-CONDITION ALERT", ""]
    for canyon, event in alerts:
        lines.extend(
            [
                canyon["name"].upper(),
                event.get("classification_label", "Pool-filling radar trigger"),
                f"Storm began: {mountain_text(event['start_utc'])}",
                f"Latest wet frame: {mountain_text(event['end_utc'])}",
                f"Radar-estimated basin rain: {event.get('basin_rain_inches', 0):.3f} inches",
                f"Estimated delivered runoff: {event.get('estimated_runoff_ft3', 0):,.0f} ft³",
                f"Fill-target ratio: {event.get('fill_ratio', 0):.2f}×",
                f"Peak reflectivity: {event.get('peak_dbz', 0)} dBZ",
                "",
            ]
        )
    lines.extend(
        [
            "Live dashboard:",
            LIVE_URL,
            "",
            "Radar-based model result; field conditions are not visually confirmed.",
        ]
    )
    message.set_content("\n".join(lines))
    return message


def test_message(sender: str, recipient: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "TEST — CCA multi-canyon radar alerts are active"
    message.set_content(
        "\n".join(
            [
                "CCA MULTI-CANYON RADAR EMAIL TEST",
                "",
                "The automated email connection is working.",
                "One alert will be sent when any monitored canyon first reaches a likely-full classification.",
                "The dashboard retains the last rain event and last qualifying storm for every canyon.",
                "",
                LIVE_URL,
            ]
        )
    )
    return message


def send_gmail(message: EmailMessage, username: str, app_password: str) -> None:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as smtp:
        smtp.login(username, app_password)
        smtp.send_message(message)


def save_status(path: Path, status: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, default=ROOT / "docs/data/status.json")
    parser.add_argument("--test", action="store_true")
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
    alerts = pending_alerts(status)
    if not alerts:
        print("No new qualifying canyon storm; no email sent")
        return 0
    send_gmail(alert_message(alerts, username, recipient), username, app_password)
    sent = utc_text(datetime.now(UTC))
    for canyon, event in alerts:
        canyon["notification"] = {
            "last_emailed_event_start_utc": event["start_utc"],
            "last_email_sent_utc": sent,
            "recipient": recipient,
        }
    save_status(arguments.status, status)
    print(f"Pool-condition alert sent for {len(alerts)} canyon{'s' if len(alerts) != 1 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
