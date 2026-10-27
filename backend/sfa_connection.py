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


def _login(page, config):
    """
    Navigates to the login page and attempts to log in. Returns
    (username_filled, password_filled, form_appeared) so callers can
    tell a timing/selector problem apart from a genuine success.
    Does NOT confirm login actually succeeded past submission — callers
    should check the resulting page themselves.
    """
    page.goto(config["login_url"], wait_until="load", timeout=30000)

    # SFA is a client-side-routed single-page app: the browser's native
    # "load" event fires once the JS bundle downloads, but Angular then
    # needs a moment afterward to bootstrap and route to the actual login
    # view. Acting immediately after "load" can catch the app
    # mid-transition — so we wait for the password field to actually
    # exist before doing anything else.
    form_appeared = False
    for selector in ["input[type='password']", "#password"]:
        try:
            page.wait_for_selector(selector, timeout=15000)
            form_appeared = True
            break
        except Exception:
            continue

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
            # Give the SPA a moment to route to its post-login view too,
            # same reasoning as the login form itself.
            page.wait_for_timeout(2000)
        except Exception:
            pass

    return username_filled, password_filled, form_appeared


def _verify_logged_in(page, timeout_ms=20000):
    """
    A login counts as successful only when the password box has gone away
    (SFA left the sign-in form). Also reads the 'Welcome, <name>' text SFA
    shows in the top-right corner, when present.
    """
    import re
    try:
        page.locator("input[type='password']").first.wait_for(state="hidden", timeout=timeout_ms)
    except Exception:
        if page.locator("input[type='password']").count() and page.locator("input[type='password']").first.is_visible():
            return False, ""
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    welcome = ""
    try:
        m = re.search(r"Welcome,?\s+([^\n]{2,60})", page.inner_text("body"))
        if m:
            welcome = m.group(1).strip()
    except Exception:
        pass
    return True, welcome


def test_connection():
    """
    Loads the saved SFA config, launches the configured browser, attempts
    to log in, and captures a screenshot of whatever the page looks like
    afterward — success page, error message, or otherwise.

    Returns:
        {
            "ok": bool,               # True only when the login form is gone after
                                       # submitting (a confirmed login)
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
            username_filled, password_filled, form_appeared = _login(page, config)
            logged_in, welcome = False, ""
            if username_filled and password_filled:
                logged_in, welcome = _verify_logged_in(page)
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

        if not logged_in:
            return {
                "ok": False,
                "message": "Login failed — SFA still shows the login form after submitting. Check the "
                           "username and password (the screenshot shows what SFA said).",
                "screenshot_path": screenshot_path,
                "note": "",
            }
        return {
            "ok": True,
            "message": "Logged in to SFA successfully" + (f" — SFA shows “Welcome, {welcome}”." if welcome else "."),
            "screenshot_path": screenshot_path,
            "note": "",
        }

    except Exception as e:
        return {
            "ok": False,
            "message": f"Connection attempt failed: {e}",
            "screenshot_path": screenshot_path if os.path.exists(screenshot_path) else None,
            "note": "",
        }


def test_navigate_sales_organization():
    return _test_navigate_menu("Sales Organization", "nav_salesorg")


def test_navigate_itinerary():
    return _test_navigate_menu("Itinerary", "nav_itinerary")


def _test_navigate_menu(menu_text, screenshot_prefix):
    """
    Logs in, then attempts to click into the given top-level menu item.
    Shared by test_navigate_sales_organization and test_navigate_itinerary
    (and any future menu probe) — same best-effort-selectors-plus-
    screenshot approach that worked for diagnosing the login page.
    """
    config = sfa_config.load_config()
    if not config or not config.get("login_url"):
        return {"ok": False, "message": "No SFA configuration saved yet.", "screenshot_path": None, "note": ""}

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    screenshot_path = os.path.join(
        SCREENSHOT_DIR, f"{screenshot_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
    )

    try:
        with sync_playwright() as p:
            browser = _launch_browser(p, config.get("browser", "chrome"))
            page = browser.new_page()
            username_filled, password_filled, form_appeared = _login(page, config)

            if not (username_filled and password_filled):
                page.screenshot(path=screenshot_path)
                browser.close()
                return {
                    "ok": False,
                    "message": "Login itself failed before navigation could be attempted.",
                    "screenshot_path": screenshot_path,
                    "note": "Fix login first (see Test Connection).",
                }

            nav_clicked = False
            for selector in [f"text={menu_text}", f"a:has-text('{menu_text}')"]:
                try:
                    page.click(selector, timeout=8000)
                    nav_clicked = True
                    break
                except Exception:
                    continue

            if nav_clicked:
                page.wait_for_timeout(1500)  # let the SPA route/render the submenu

            page.screenshot(path=screenshot_path)
            browser.close()

        return {
            "ok": nav_clicked,
            "message": (f"Clicked {menu_text} — check the screenshot to confirm it shows the "
                        "right submenu/page." if nav_clicked else
                        f"Could not find a '{menu_text}' nav item with the selectors tried. "
                        "Check the screenshot to see the real menu."),
            "screenshot_path": screenshot_path,
            "note": "Look for the actual export/download button in this screenshot — that's what we build the automation against next.",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Navigation attempt failed: {e}",
            "screenshot_path": screenshot_path if os.path.exists(screenshot_path) else None,
            "note": "",
        }
