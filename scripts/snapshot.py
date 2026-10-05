"""
snapshot.py - save the HTML of a page open in the project Chrome, so its structure can be read.

Usage (from the project root, project Chrome open):
    python scripts/snapshot.py jobright joblist     # latest tab whose URL contains "jobright"
    python scripts/snapshot.py jobright jobdetail

Writes output/snap_<name>.html and prints the page URL.
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

if len(sys.argv) != 3:
    sys.exit("usage: python scripts/snapshot.py <url-contains> <name>")
site, name = sys.argv[1].lower(), sys.argv[2]

with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
    except Exception:
        sys.exit("Could not reach Chrome on port 9222. Start it with scripts\\start_chrome.ps1 first.")
    pages = [pg for ctx in browser.contexts for pg in ctx.pages if site in pg.url.lower()]
    if not pages:
        sys.exit(f"No tab with '{site}' in its URL is open in the project Chrome.")
    page = pages[-1]
    out = Path("output") / f"snap_{name}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(page.content(), encoding="utf-8")
    print(f"Saved {out}  ({page.url})")
