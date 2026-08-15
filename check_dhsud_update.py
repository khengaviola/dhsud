#!/usr/bin/env python3
"""
DHSUD License-to-Sell List Monitor
-----------------------------------
Checks the DHSUD "List of Projects with License to Sell" data source for its
"Data as of <Month> <Day>, <Year>" label. Runs hourly, but only emails you
when that date actually CHANGES (e.g. flips from "June 30, 2026" to
"July 31, 2026") — silent, no email, on runs where nothing has changed.

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

def _find_date_in_page(page):
    """Search the main frame AND all iframes on the page for the date pattern.

    Google Apps Script web apps often render their actual content inside a
    nested sandboxed iframe (for security isolation), so text can be visible
    on screen (and in a screenshot) while page.inner_text("body") on the top
    frame alone would miss it entirely.
    """
    for frame in page.frames:
        try:
            text = frame.inner_text("body")
        except Exception:
            continue
        match = DATE_PATTERN.search(text)
        if match:
            return match.group(1).strip()
    return None


def fetch_as_of_date():
    """Load the data source in a headless browser and extract the 'Data as of' date."""
    debug_dir = SCRIPT_DIR / "debug"
    last_error = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ))

        for idx, url in enumerate((DATA_SOURCE_URL, FALLBACK_URL)):
            label = "primary" if idx == 0 else "fallback"
            try:
                # "load" is more reliable than "networkidle" for pages that
                # keep background connections open (e.g. Apps Script apps).
                page.goto(url, timeout=45000, wait_until="load")

                # Poll for up to ~20s, checking every frame each time, since
                # nested iframes can take a moment to finish loading.
                found_date = None
                for _ in range(20):
                    found_date = _find_date_in_page(page)
                    if found_date:
                        break
                    page.wait_for_timeout(1000)

                if found_date:
                    browser.close()
                    return found_date, url

                last_error = (
                    f"No date pattern found in any frame's body text from "
                    f"{url} ({label}); page had {len(page.frames)} frame(s)"
                )

            except Exception as e:
                last_error = f"Error loading {url} ({label}): {e}"

            # Always save a screenshot/HTML for THIS attempt before moving on,
            # so failures on either URL are individually diagnosable.
            try:
                debug_dir.mkdir(exist_ok=True)
                page.screenshot(
                    path=str(debug_dir / f"failure_{label}.png"), full_page=True
                )
                (debug_dir / f"failure_{label}.html").write_text(page.content())
            except Exception:
                pass

        browser.close()
        raise RuntimeError(
            "Could not find a 'Data as of ...' date on either the primary "
            f"or fallback URL. Last error: {last_error}. "
            "Debug screenshots/HTML for BOTH attempts saved to the 'debug' "
            "folder if the workflow is configured to upload it as an artifact."
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
        # First run — establish baseline only. No email sent, since there's
        # nothing yet to compare against.
        print("  -> No prior state found. Saving baseline, no email sent.")
        save_state(current_date, source_url)
        return

    if current_date != last_date:
        print(f"  -> CHANGE DETECTED: '{last_date}' -> '{current_date}'")
        subject = f"DHSUD list UPDATED: now as of {current_date}"
        body = (
            f"The DHSUD List of Projects with License to Sell has been updated!\n\n"
            f"Previous: Data as of {last_date}\n"
            f"Now:      Data as of {current_date}\n\n"
            f"View it here: https://dhsud.gov.ph/list-of-license-to-sell/\n"
            f"(Direct data source: {source_url})\n"
        )
        sent = send_email(subject, body)
        if sent:
            print("  -> Email notification sent (change detected).")
        save_state(current_date, source_url)
    else:
        # No change — stay silent. No email sent.
        print(f"  -> No change (still 'Data as of {current_date}'). No email sent.")


if __name__ == "__main__":
    main()
