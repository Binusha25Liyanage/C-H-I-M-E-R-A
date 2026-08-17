"""
sfa_config.py
Local storage for SFA connection settings: login URL, username, password,
and which installed browser Playwright should drive.

Stored as plain JSON in the user's home folder (same reasoning as the
original app's sfa_config.py) — this is plaintext on disk, so only ever
save this on a machine you trust.
"""

import os
import json

CONFIG_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "sfa_config.json")

# Playwright launches real installed browsers via a "channel" name for
# Chromium-based ones. Firefox is a separate engine Playwright ships its
# own build of, not your installed Firefox — but it still behaves like a
# real independent browser for automation purposes.
SUPPORTED_BROWSERS = {
    "chrome": "chrome",
    "edge": "msedge",
    "firefox": None,  # handled as playwright.firefox, not a Chromium channel
}


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(login_url, username, password, browser="chrome"):
    if browser not in SUPPORTED_BROWSERS:
        raise ValueError(f"Unsupported browser '{browser}'. Choose one of: {list(SUPPORTED_BROWSERS)}")
    os.makedirs(CONFIG_DIR, exist_ok=True)
    data = {
        "login_url": login_url,
        "username": username,
        "password": password,
        "browser": browser,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data


def clear_config():
    if os.path.exists(CONFIG_PATH):
        os.remove(CONFIG_PATH)
