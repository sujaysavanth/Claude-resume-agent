"""
save_auth.py - copy your logged-in Resume Worded session out of the project Chrome
into rw_auth.json, so the Playwright recorder (and later the scorer) start logged in.

Usage (from the project root, with the project Chrome still open and logged in):
    python scripts/save_auth.py
"""
import sys
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
    except Exception:
        sys.exit("Could not reach Chrome on port 9222. Start it with scripts\\start_chrome.ps1 first.")
    context = browser.contexts[0]
    state = context.storage_state(path="rw_auth.json")
    rw = [c for c in state["cookies"] if "resumeworded" in c["domain"]]
    if not rw:
        print("WARNING: saved rw_auth.json but found no Resume Worded cookies. Are you logged in?")
    else:
        print(f"Saved rw_auth.json with {len(rw)} Resume Worded cookies. Chrome can stay open.")
