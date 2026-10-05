"""
rw_score.py - score a built resume on Resume Worded (Targeted Resume) using the
logged-in project Chrome, and save the result for fix mode.

Usage (from the project root, project Chrome open via scripts\\start_chrome.ps1):
    python scripts/rw_score.py output/<basename>              # score + missing keywords
    python scripts/rw_score.py output/<basename> --magic      # also run Magic Target (uses 1 of 20 daily runs)
    python scripts/rw_score.py output/<basename> --rescore    # score only, after a fix round

<basename> is the resume name without extension, e.g.
    output/sujay_govindappa_rajashekar_trellis_energy_swe_resume
It reads <basename>.pdf and <basename>_rw_jd.txt.

Writes:
    <basename>_rw_feedback.json   (normal / --magic)  -> input for FIX MODE
    <basename>_rw_rescore.json    (--rescore)
Resume Worded's own rewritten lines are never saved.

Exit codes: 0 ok, 1 usage/setup problem, 2 something on the page didn't appear
(a screenshot is saved to <basename>_rw_error.png; check the project Chrome).
"""
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

START_URL = "https://resumeworded.com/target-start"
TARGET = 85

SCORE_SEL = "#overall_relevancy_score text.value"
MISSING_ROW_SEL = "#missing_table_container .tr-mw-toggle-row"
MAGIC_TAB_SEL = ".single-tab-header[rel='smart_target_tab']"
MAGIC_BTN_SEL = "#generate-action-steps"
MAGIC_CARD_SEL = "#smart_target_suggestions_container .smart-target-suggestion-card:not(.locked-card)"

READ_KEYWORDS_JS = """
(sel) => [...document.querySelectorAll(sel)].map(r => ({
  keyword: (r.querySelector('.keyword-text')?.innerText || r.dataset.skill || '').trim(),
  count: parseInt((r.querySelector('.frequency-table')?.innerText || '0').trim(), 10) || 0,
  key_skill: !!r.querySelector('.important-flag-table .fa-star')
})).filter(k => k.keyword)
"""

# Deliberately does NOT read .after-line-content / data-after_snippet (Resume Worded's rewrite).
READ_MAGIC_JS = """
(sel) => [...document.querySelectorAll(sel)].map((c, i) => {
  const t = q => (c.querySelector(q)?.innerText || '').trim().replace(/\\s+/g, ' ');
  const instr = t('.smart-target-suggestion-keyword');
  const m = instr.match(/Add\\s+"(.*)"/);
  const beforeBlock = c.querySelector('.before-line-content')?.closest('.smart-target-line-block');
  const hasBefore = beforeBlock && beforeBlock.style.display !== 'none';
  return {
    id: i + 1,
    type: t('.smart-target-suggestion-card-header-right'),
    keywords: m ? m[1].split(',').map(s => s.trim()).filter(Boolean) : [],
    reason: t('.smart-target-suggestion-rationale'),
    location: t('.smart-target-location').replace(/^Apply this change in\\s*/i, ''),
    current_line: hasBefore ? t('.before-line-content') : null
  };
})
"""


def pause(a=1.0, b=2.5):
    time.sleep(random.uniform(a, b))  # human pacing between actions


def fail(page, base, msg):
    shot = Path(f"{base}_rw_error.png")
    try:
        page.screenshot(path=str(shot), full_page=True)
        msg += f"\nScreenshot saved: {shot}"
    except Exception:
        pass
    print("ERROR:", msg)
    sys.exit(2)


def read_score(page):
    # the score is an SVG <text> element, so use text_content (inner_text only works on HTML elements)
    loc = page.locator(SCORE_SEL).first
    loc.wait_for(state="attached", timeout=60_000)
    txt = (loc.text_content() or "").strip()
    return int(re.sub(r"\D", "", txt))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1:
        sys.exit(__doc__)
    base = args[0].removesuffix(".pdf")
    pdf, jd_file = Path(f"{base}.pdf"), Path(f"{base}_rw_jd.txt")
    for f in (pdf, jd_file):
        if not f.exists():
            sys.exit(f"ERROR: {f} not found")
    jd_text = jd_file.read_text(encoding="utf-8").strip()
    rescore, magic = "--rescore" in flags, "--magic" in flags

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            sys.exit("ERROR: project Chrome not reachable. Run: powershell -ExecutionPolicy Bypass -File scripts\\start_chrome.ps1")
        page = browser.contexts[0].new_page()
        try:
            # 1. start page: paste JD, attach PDF (Resume Worded submits on upload)
            page.goto(START_URL, wait_until="domcontentloaded")
            pause()
            if "target-start" not in page.url:
                fail(page, base, f"Landed on {page.url} instead of the Targeted Resume start page. "
                                 "Log into Resume Worded again in the project Chrome.")
            new_jd = page.get_by_text("New Job Description", exact=True)
            if new_jd.count() and new_jd.first.is_visible():
                new_jd.first.click()
                pause(0.5, 1.2)
            box = page.locator("textarea:visible").first
            box.fill(jd_text)
            pause()
            page.locator("input[type=file]").first.set_input_files(str(pdf.resolve()))
            try:
                page.wait_for_url("**/target-results*", timeout=180_000)
                score = read_score(page)
            except PWTimeout:
                fail(page, base, "Results page or score didn't appear within 3 minutes "
                                 "(possible CAPTCHA, upload error or site change).")
            pause()

            # 2. missing keywords table (no daily limit)
            keywords = page.evaluate(READ_KEYWORDS_JS, MISSING_ROW_SEL)

            if rescore:
                out = Path(f"{base}_rw_rescore.json")
                data = {"score": score, "target": TARGET, "keywords_missing": keywords,
                        "scored_at": datetime.now().isoformat(timespec="seconds")}
                out.write_text(json.dumps(data, indent=2), encoding="utf-8")
                print(f"RESCORE: {score}/100 | missing keywords: {len(keywords)} | saved {out}")
                return

            # 3. optional Magic Target (1 of 20 daily runs)
            suggestions, runs_left = [], None
            if magic:
                page.locator(MAGIC_TAB_SEL).first.click()
                pause()
                note = page.locator("#daily-limit-note")
                if note.count():
                    m = re.search(r"(\d+)\s+magic target runs? left", note.first.inner_text())
                    runs_left = int(m.group(1)) if m else None
                if runs_left == 0:
                    print("WARNING: no Magic Target runs left today; saving keywords only.")
                else:
                    page.locator(MAGIC_BTN_SEL).first.click()
                    try:
                        page.locator(MAGIC_CARD_SEL).first.wait_for(state="visible", timeout=240_000)
                    except PWTimeout:
                        fail(page, base, "Magic Target suggestions didn't appear within 4 minutes.")
                    pause(2, 3)  # let remaining cards render
                    suggestions = page.evaluate(READ_MAGIC_JS, MAGIC_CARD_SEL)
                    runs_left = runs_left - 1 if isinstance(runs_left, int) else None

            out = Path(f"{base}_rw_feedback.json")
            data = {"score": score, "target": TARGET, "keywords_missing": keywords,
                    "suggestions": suggestions, "magic_runs_left": runs_left,
                    "scored_at": datetime.now().isoformat(timespec="seconds")}
            out.write_text(json.dumps(data, indent=2), encoding="utf-8")
            key = sum(1 for k in keywords if k["key_skill"])
            print(f"SCORE: {score}/100 | missing keywords: {len(keywords)} ({key} key) | "
                  f"magic suggestions: {len(suggestions)} | saved {out}")
        finally:
            page.close()  # closes only the tab this script opened


if __name__ == "__main__":
    main()
