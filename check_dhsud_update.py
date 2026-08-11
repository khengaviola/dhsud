#!/usr/bin/env python3
"""
DHSUD License-to-Sell List Monitor
-----------------------------------
Checks the DHSUD "List of Projects with License to Sell" data source for its
"Data as of <Month> <Day>, <Year>" label, and emails you EVERY TIME it runs
(configured for hourly) — one message type if the date is unchanged ("still
as of June ...") and a different, clearly-flagged message if the date has
just changed (e.g. flips to "July 31, 2026").

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

                # Poll for up to ~15s instead of a single fixed sleep, since
                # render time can vary between
