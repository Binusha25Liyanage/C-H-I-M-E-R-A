"""
sfa_connection.py
The actual browser automation entry point: launches a real, chosen
browser (Chrome, Edge, or Firefox) via Playwright and attempts to log
into SFA using stored credentials.

IMPORTANT — read before trusting this:
The login field selectors below are a best-effort guess based on
patterns observed in the IT Manager's existing Selenium script
(sfa_automation.py) — a "Username" label with a sibling input, a
password input[type=password], and an Enter/submit action. They have
NOT been confirmed against a real Test SFA login page in this project.
test_connection() always saves a screenshot after attempting login
specifically so you can visually verify whether it actually worked,
rather than trusting a guessed "success" signal.
"""

import os
from datetime import datetime
from playwright.sync_api import sync_playwright

import sfa_config

SCREENSHOT_DIR = os.path.join(os.path.expanduser("~"), "Chimera", "screenshots")


def _launch_browser(playwright, browser_key):
    channel = sfa_config.SUPPORTED_BROWSERS.get(browser_key)
    if browser_key == "firefox":
        return playwright.firefox.launch(headless=False)
    return playwright.chromium.launch(headless=False, channel=channel)


def test_connection():
    """
    Loads the saved SFA config, launches the configured browser, attempts
    to log in, and captures a screenshot of whatever the page looks like
    afterward — success page, error message, or otherwise.

    Returns:
        {
            "ok": bool,               # True only if config was present and
                                       # the browser could navigate + submit
                                       # the login form — NOT a confirmed
                                       # successful login, see "note" below
            "message": str,
            "screenshot_path": str or None,
            "note": str,              # always present — reminds you to
                                       # check the screenshot yourself
        }
    """
    config = sfa_config.load_config()
    if not config or not config.get("login_url"):
        return {
            "ok": False,
            "message": "No SFA configuration saved yet. Save your login URL, username, and password first.",
            "screenshot_path": None,
            "note": "",
        }

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    screenshot_path = os.path.join(
        SCREENSHOT_DIR, f"login_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    try:
        with sync_playwright() as p:
            browser = _launch_browser(p, config.get("browser", "chrome"))
            page = browser.new_page()
            page.goto(config["login_url"], wait_until="load", timeout=30000)

            # SFA is a client-side-routed single-page app: the browser's
            # native "load" event fires once the JS bundle downloads, but
            # Angular then needs a moment afterward to bootstrap and route
            # to the actual login view. Acting immediately after "load"
            # can catch the app mid-transition (looks like a 404/blank
            # screen even though the real page is a second away) — so we
            # wait for the password field to actually exist before doing
            # anything else, rather than trusting the load event alone.
            form_appeared = False
            for selector in ["input[type='password']", "#password"]:
                try:
                    page.wait_for_selector(selector, timeout=15000)
                    form_appeared = True
                    break
                except Exception:
                    continue

            # Best-effort login form fill — see module docstring. Tries a
            # few common patterns rather than one hardcoded selector, so
            # small markup differences don't immediately break this.
            username_filled = False
            for selector in ["input[name='username']", "input[type='text']", "#username"]:
                try:
                    page.fill(selector, config["username"], timeout=3000)
                    username_filled = True
                    break
                except Exception:
                    continue

            password_filled = False
            for selector in ["input[type='password']", "#password"]:
                try:
                    page.fill(selector, config["password"], timeout=3000)
                    password_filled = True
                    break
                except Exception:
                    continue

            if username_filled and password_filled:
                try:
                    page.keyboard.press("Enter")
                    page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass

            page.screenshot(path=screenshot_path)
            browser.close()

        if not (username_filled and password_filled):
            reason = ("The login form never appeared within 15 seconds of the page loading."
                      if not form_appeared else
                      "The login form appeared, but its field selectors didn't match this "
                      "version's guesses.")
            return {
                "ok": False,
                "message": f"{reason} See the screenshot to see what the page actually looked "
                            "like, and share it so this can be fixed.",
                "screenshot_path": screenshot_path,
                "note": "This is expected until real Test SFA login selectors are confirmed.",
            }

        return {
            "ok": True,
            "message": "Navigated to the login page and submitted the form. This does NOT "
                        "confirm login actually succeeded — check the screenshot.",
            "screenshot_path": screenshot_path,
            "note": "Please open the screenshot and confirm it shows a logged-in SFA page, "
                    "not an error message or the login page again.",
        }

    except Exception as e:
        return {
            "ok": False,
            "message": f"Connection attempt failed: {e}",
            "screenshot_path": screenshot_path if os.path.exists(screenshot_path) else None,
            "note": "",
        }
