#!/usr/bin/env python3
"""
DHSUD License-to-Sell List Monitor
-----------------------------------
Checks the DHSUD "List of Projects with License to Sell" data source for its
"Data as of <Month> <Day>, <Year>" label, and emails you when that date changes
(e.g. when it flips from "June 30, 2026" to "July 31, 2026").

WHY THIS URL:
The public page https://dhsud.gov.ph/list-of-license-to-sell/ is just a wrapper
page. The actual list + "Data as of ..." label are served from an embedded
Google Apps Script web app (loaded via <iframe>). That script.google.com URL
is what we check directly here — it's more reliable and loads faster than the
full DHSUD page.

REQUIREMENTS:
    pip install playwright
    playwright install chromium

USAGE:
    python3 check_dhsud_update.py

CONFIGURATION:
    Set these via environment variables (recommended) or edit the CONFIG
    section below directly.

    SMTP_HOST        e.g. smtp.gmail.com
    SMTP_PORT        e.g. 587
    SMTP_USER        your email address (used to log in to SMTP)
    SMTP_PASSWORD    your email password or app-specific password
    EMAIL_FROM       sender address shown on the email (usually same as SMTP_USER)
    EMAIL_TO         recipient address(es), comma-separated

    NOTE ON GMAIL: If using a Gmail account for sending, you must create an
    "App Password" (Google Account > Security > 2-Step Verification > App
    Passwords) — your normal Gmail password will NOT work with SMTP.

SCHEDULING (run automatically, e.g. daily):
    Add to crontab (`crontab -e`) to run every day at 9:00 AM:
        0 9 * * * /usr/bin/python3 /path/to/check_dhsud_update.py >> /path/to/dhsud_monitor.log 2>&1

STATE FILE:
    The script remembers the last "as of" date it saw in `state.json`
    (created next to this script). Delete that file to reset / force a
    fresh baseline.
"""

import os
import re
import sys
import json
import smtplib
import traceback
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# The embedded Google Apps Script URL that actually serves the list + date.
DATA_SOURCE_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbw13EhfEHWfvd97PTAgyB9VcOlmfC89lD3ul8WLoL3ubL35fbgHva7GDzjwhhNP7Dfi/exec"
)

# Fallback: the public DHSUD page (used only if the direct script URL fails).
FALLBACK_URL = "https://dhsud.gov.ph/list-of-license-to-sell/"

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_FILE = SCRIPT_DIR / "state.json"

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USER)
EMAIL_TO = os.environ.get("EMAIL_TO", "")  # comma-separated list allowed

# Regex to find "Data as of June 30, 2026" style text
DATE_PATTERN = re.compile(
    r"Data as of\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# CORE LOGIC
# ---------------------------------------------------------------------------

def fetch_as_of_date():
    """Load the data source in a headless browser and extract the 'Data as of' date."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for url in (DATA_SOURCE_URL, FALLBACK_URL):
            try:
                page.goto(url, timeout=30000, wait_until="networkidle")
                page.wait_for_timeout(2500)  # let async widget content render
                text = page.inner_text("body")
                match = DATE_PATTERN.search(text)
                if match:
                    browser.close()
                    return match.group(1).strip(), url
            except Exception:
                continue

        browser.close()
        raise RuntimeError(
            "Could not find a 'Data as of ...' date on either the primary "
            "or fallback URL. The page structure may have changed."
        )


def load_last_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(as_of_date, source_url):
    STATE_FILE.write_text(
        json.dumps(
            {
                "last_as_of_date": as_of_date,
                "source_url": source_url,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            },
            indent=2,
        )
    )


def send_email(subject, body):
    if not (SMTP_USER and SMTP_PASSWORD and EMAIL_TO):
        print(
            "[WARN] Email not sent — SMTP_USER, SMTP_PASSWORD, or EMAIL_TO "
            "is not configured. Set them as environment variables.",
            file=sys.stderr,
        )
        return False

    recipients = [addr.strip() for addr in EMAIL_TO.split(",") if addr.strip()]

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())

    return True


def main():
    print(f"[{datetime.now().isoformat(timespec='seconds')}] Checking DHSUD list...")

    try:
        current_date, source_url = fetch_as_of_date()
    except Exception as e:
        print(f"[ERROR] Failed to check the site: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)

    print(f"  -> Found: 'Data as of {current_date}' (source: {source_url})")

    last_state = load_last_state()
    last_date = last_state.get("last_as_of_date")

    if last_date is None:
        # First run — establish baseline, don't email.
        print("  -> No prior state found. Saving baseline, no email sent.")
        save_state(current_date, source_url)
        return

    if current_date != last_date:
        print(f"  -> CHANGE DETECTED: '{last_date}' -> '{current_date}'")
        subject = f"DHSUD License-to-Sell list updated: now as of {current_date}"
        body = (
            f"The DHSUD List of Projects with License to Sell has been updated.\n\n"
            f"Previous: Data as of {last_date}\n"
            f"Now:      Data as of {current_date}\n\n"
            f"View it here: https://dhsud.gov.ph/list-of-license-to-sell/\n"
            f"(Direct data source: {source_url})\n"
        )
        sent = send_email(subject, body)
        if sent:
            print("  -> Email notification sent.")
        save_state(current_date, source_url)
    else:
        print(f"  -> No change (still 'Data as of {current_date}').")


if __name__ == "__main__":
    main()
