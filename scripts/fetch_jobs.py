"""
fetch_jobs.py - pull your Liked jobs from Jobright into jds/ as ready-to-build JD files.

Usage (from the project root, project Chrome open and logged into Jobright):
    python scripts/fetch_jobs.py              # all new Active jobs in your Liked list
    python scripts/fetch_jobs.py --limit 10   # at most 10 new jobs this run
    python scripts/fetch_jobs.py --refetch    # re-download jobs already fetched

Workflow: heart (Like) jobs in Jobright -> run this -> run scripts/run_batch.py.

Writes jds/<company>_<role>.txt and remembers what it fetched in jds/fetched.json,
so a job is only downloaded once. Closed jobs are ignored.
"""
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

sys.path.insert(0, str(Path(__file__).resolve().parent))
from original_posting import fetch_original  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
JDS = ROOT / "jds"
STATE = JDS / "fetched.json"
BASE = "https://jobright.ai"
LIKED_URL = f"{BASE}/jobs/liked"
CARD_SEL = "div.job-card-flag-classname[id]"
DETAIL_SEL = "script#jobright-helper-job-detail-info"

READ_CARDS_JS = """
(sel) => [...document.querySelectorAll(sel)].map(c => ({
  id: c.id,
  title: (c.querySelector('[class*="job-title__"]')?.innerText || '').trim(),
  company: (c.querySelector('[class*="company-name__"]')?.innerText || '').trim()
}))
"""


# ----------------------------------------------------------------- naming
def slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_(inc|llc|ltd|corp|corporation|co|company|group|holdings)$", "", s) or "company"


def role_abbrev(title):
    t = title.lower()
    if "intern" in t or "co-op" in t:
        return "swe_intern"
    if re.search(r"machine learning|\bml\b|\bai\b|artificial intelligence|data scien|deep learning|\bnlp\b|llm", t):
        return "ml"
    if re.search(r"data engineer|analytics engineer|\betl\b|data platform|data analyst|business intelligence|\bbi\b", t):
        return "de"
    return "swe"


# ----------------------------------------------------------------- JD text
def bullets(items):
    return "\n".join(f"- {x.strip()}" for x in (items or []) if isinstance(x, str) and x.strip()) or "- (none listed)"


def yesno(v):
    return "yes" if v is True else "no" if v is False else "unknown"


def format_jd(data, original=None, source=""):
    j, c = data.get("jobResult", {}), data.get("companyResult", {})
    q = j.get("qualifications") or {}
    skills = ", ".join(s.get("skill", "") for s in (j.get("jdCoreSkills") or []) if isinstance(s, dict))
    yoe = f"{j.get('minYearsOfExperience', '?')}-{j.get('maxYearsOfExperience', '?')} years"
    header = f"""Company: {c.get('companyName', '')}
Title: {j.get('jobTitle', '')}
Seniority: {j.get('jobSeniority', '')} | Experience: {yoe}
Location: {j.get('jobLocation', '')} | Work model: {j.get('workModel', '')} | Type: {j.get('employmentType', '')}
Posted: {j.get('publishTime', '')}
Jobright job: {BASE}/jobs/info/{j.get('jobId', '')}
Apply link: {j.get('applyLink') or j.get('originalUrl') or ''}

Jobright eligibility flags (verify against the posting):
- H-1B sponsor: {yesno(j.get('isH1bSponsor'))}
- Citizens only: {yesno(j.get('isCitizenOnly'))}
- Clearance required: {yesno(j.get('isClearanceRequired'))}
- Work authorization required: {yesno(j.get('isWorkAuthRequired'))}
"""
    if original:
        return header + f"""
JD source: ORIGINAL POSTING via {source} (boilerplate removed). This text is authoritative.

Original posting:
{original}
"""
    return header + f"""
JD source: JOBRIGHT SUMMARY (original posting unavailable: {source}). Wording may be paraphrased.

Summary:
{(j.get('jobSummary') or '').strip()}

Responsibilities:
{bullets(j.get('coreResponsibilities'))}

Qualifications (required):
{bullets(q.get('mustHave'))}

Qualifications (preferred):
{bullets(q.get('preferredHave'))}

Core skills: {skills}
"""


# ----------------------------------------------------------------- browser steps
def pause(a=2.0, b=4.5):
    time.sleep(random.uniform(a, b))


def collect_liked(page):
    page.goto(LIKED_URL, wait_until="domcontentloaded")
    try:
        page.locator(CARD_SEL).first.wait_for(state="attached", timeout=30_000)
    except PWTimeout:
        if "login" in page.url or "sign" in page.url:
            sys.exit("ERROR: Jobright isn't logged in. Log in again in the project Chrome.")
        print("No jobs found in your Liked list (or the page didn't load).")
        return []
    active = page.locator("button:has-text('Active')")
    if active.count():
        active.first.click()
        pause(1, 2)
    # the list is virtualized: scroll it and collect cards as they render
    seen, stale = {}, 0
    for _ in range(60):
        for card in page.evaluate(READ_CARDS_JS, CARD_SEL):
            seen.setdefault(card["id"], card)
        before = len(seen)
        page.evaluate("""() => { const d = document.querySelector('#scrollableDiv');
                                 if (d) d.scrollBy(0, d.clientHeight * 0.8); }""")
        time.sleep(0.8)
        for card in page.evaluate(READ_CARDS_JS, CARD_SEL):
            seen.setdefault(card["id"], card)
        stale = stale + 1 if len(seen) == before else 0
        if stale >= 3:
            break
    return list(seen.values())


def fetch_detail(page, job_id):
    page.goto(f"{BASE}/jobs/info/{job_id}", wait_until="domcontentloaded")
    page.locator(DETAIL_SEL).first.wait_for(state="attached", timeout=30_000)
    return json.loads(page.locator(DETAIL_SEL).first.text_content())


# ----------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    refetch = "--refetch" in args
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else None
    JDS.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            sys.exit("ERROR: project Chrome not reachable. Run: powershell -ExecutionPolicy Bypass -File scripts\\start_chrome.ps1")
        page = browser.contexts[0].new_page()
        try:
            cards = collect_liked(page)
            new = [c for c in cards if refetch or c["id"] not in state]
            if limit:
                new = new[:limit]
            print(f"Liked (active): {len(cards)} | new to fetch: {len(new)}")

            for i, card in enumerate(new, 1):
                pause()
                try:
                    data = fetch_detail(page, card["id"])
                except Exception as e:
                    print(f"  [{i}/{len(new)}] FAILED {card['company']} - {card['title']}: {str(e)[:100]}")
                    continue
                j, c = data.get("jobResult", {}), data.get("companyResult", {})
                company = c.get("companyName") or card["company"]
                title = j.get("jobTitle") or card["title"]
                name = f"{slug(company)}_{role_abbrev(title)}"
                path = JDS / f"{name}.txt"
                n = 2
                while path.exists() and state.get(card["id"], {}).get("file") != path.name:
                    path = JDS / f"{name}_{n}.txt"   # same company + role, different job
                    n += 1
                apply = j.get("applyLink") or j.get("originalUrl")
                original, source = fetch_original(apply, page)
                path.write_text(format_jd(data, original, source), encoding="utf-8")
                state[card["id"]] = {"file": path.name, "company": company, "title": title,
                                     "apply": apply, "source": source if original else "jobright",
                                     "fetched": datetime.now().isoformat(timespec="seconds")}
                STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
                flags = [k for k, v in (("citizens-only", j.get("isCitizenOnly")),
                                        ("clearance", j.get("isClearanceRequired"))) if v]
                src = source if original else "JOBRIGHT fallback"
                print(f"  [{i}/{len(new)}] {path.name:<32} [{src}] {company} - {title}"
                      f"{'   !! ' + ', '.join(flags) if flags else ''}")
        finally:
            page.close()

    print(f"\nDone. Next: python scripts\\run_batch.py")


if __name__ == "__main__":
    main()
