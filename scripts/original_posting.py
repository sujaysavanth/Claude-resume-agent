"""
original_posting.py - fetch the employer's original job posting text for an apply link,
then strip boilerplate. Used by fetch_jobs.py (no tokens: plain Python + your project Chrome).

Order: ATS public feeds (Lever, Greenhouse, Ashby, SmartRecruiters, Workday)
       -> open the link in the project Chrome and read the main text
       -> give up (caller falls back to Jobright's version).
"""
import html
import json
import re
import urllib.request
from urllib.parse import urlparse, parse_qs

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "application/json, text/html;q=0.9"}
MIN_CHARS = 400          # anything shorter isn't a real posting
MAX_CHARS = 12_000       # cap what Claude has to read

# lines that must survive cleaning, whatever section they're in
KEEP_LINE = re.compile(r"sponsor|visa|h-?1b|work authori[sz]|authori[sz]ed to work|citizen|green card|"
                       r"permanent resident|clearance|itar|export control|opt\b|cpt\b|relocat", re.I)
DROP_HEAD = re.compile(r"about (us|the company|[A-Z][\w&.\- ]{1,40}$)|who we are|our (company|mission|story|values|culture)|"
                       r"why (join|work)|life at|benefits|perks|what we offer|compensation|salary|pay (range|transparency)|"
                       r"total rewards|equal (employment|opportunity)|\beeo\b|diversity|accommodation|privacy|"
                       r"disclaimer|legal|e-verify|recruitment fraud|how to apply|application process", re.I)
KEEP_HEAD = re.compile(r"responsib|requirement|qualification|what you|you will|you'll|you have|about the (role|job|team|position)|"
                       r"the role|role overview|job (summary|description)|position|skills|experience|nice to have|"
                       r"preferred|bonus|basic|minimum|must have|day to day|what we.re looking|your impact|tech stack|who you are", re.I)
DROP_LINE = re.compile(r"equal opportunity employer|without regard to (race|age)|reasonable accommodation|"
                       r"e-verify|\$\s?\d[\d,]*(\.\d+)?\s?(k|/hr|per hour|-|–|to)|base (salary|pay) range|"
                       r"pay range|salary range", re.I)


# ----------------------------------------------------------------- text helpers
def html_to_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?i)<li[^>]*>", "\n- ", s)
    s = re.sub(r"(?i)</?(p|div|br|h[1-6]|ul|ol|section|tr)[^>]*>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t\u00a0]+", " ", l).strip() for l in s.splitlines()]
    return "\n".join(l for l in lines if l)


def is_heading(line):
    l = line.strip().rstrip(":").strip()
    return 0 < len(l) <= 70 and not l.startswith("-") and not l.endswith(".") and len(l.split()) <= 9


def clean(text):
    """Drop boilerplate sections/lines, always keep eligibility lines, cap length."""
    out, dropping = [], False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if KEEP_LINE.search(s):
            out.append(s)
            continue
        if is_heading(s):
            if DROP_HEAD.search(s):
                dropping = True
                continue
            if KEEP_HEAD.search(s):
                dropping = False
        if dropping or DROP_LINE.search(s):
            continue
        out.append(s)
    text = "\n".join(out)
    return text[:MAX_CHARS] + ("\n[...truncated]" if len(text) > MAX_CHARS else "")


def get_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def post_json(url, timeout=20):
    req = urllib.request.Request(url, data=b"{}", headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


# ----------------------------------------------------------------- ATS feeds
def lever(u):
    m = re.match(r"/([^/]+)/([0-9a-f-]{36})", u.path)
    if "lever.co" not in u.netloc or not m:
        return None
    d = get_json(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}?mode=json")
    parts = [d.get("descriptionPlain") or html_to_text(d.get("description"))]
    for lst in d.get("lists") or []:
        parts.append(f"{lst.get('text', '')}:\n{html_to_text(lst.get('content'))}")
    parts.append(d.get("additionalPlain") or html_to_text(d.get("additional")))
    return "\n\n".join(p for p in parts if p)


def greenhouse(u):
    q = parse_qs(u.query)
    m = re.match(r"/([^/]+)/jobs/(\d+)", u.path)
    if "greenhouse.io" in u.netloc and m:
        board, jid = m.group(1), m.group(2)
    elif "greenhouse.io" in u.netloc and "for" in q and "token" in q:
        board, jid = q["for"][0], q["token"][0]
    else:
        return None  # company-site ?gh_jid= links: board name unknown -> browser fallback
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
    return html_to_text(d.get("content"))


def ashby(u):
    m = re.match(r"/([^/]+)/([0-9a-f-]{36})", u.path)
    if "ashbyhq.com" not in u.netloc or not m:
        return None
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    for job in d.get("jobs") or []:
        if job.get("id") == m.group(2):
            return job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml"))
    return None


def smartrecruiters(u):
    m = re.match(r"/([^/]+)/(\d+)", u.path)
    if "smartrecruiters.com" not in u.netloc or not m:
        return None
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
    secs = (d.get("jobAd") or {}).get("sections") or {}
    return "\n\n".join(f"{s.get('title', '')}:\n{html_to_text(s.get('text'))}"
                       for k, s in secs.items() if isinstance(s, dict) and k != "companyDescription")


def workday(u):
    host = u.netloc
    m = re.match(r"(?:/[a-z]{2}-[A-Z]{2})?/([^/]+)/job/(.+?)/?(?:apply.*)?$", u.path)
    if "myworkdayjobs.com" not in host or not m:
        return None
    tenant = host.split(".")[0]
    d = get_json(f"https://{host}/wday/cxs/{tenant}/{m.group(1)}/job/{m.group(2)}")
    return html_to_text((d.get("jobPostingInfo") or {}).get("jobDescription"))


FEEDS = [("lever", lever), ("greenhouse", greenhouse), ("ashby", ashby),
         ("smartrecruiters", smartrecruiters), ("workday", workday)]

# ----------------------------------------------------------------- browser fallback
MAIN_TEXT_JS = """
() => {
  const cands = [...document.querySelectorAll(
    'main, article, [class*="description" i], [id*="description" i], [class*="job-details" i], [class*="posting" i], [role=main]')];
  let best = document.body, bestLen = 0;
  for (const el of cands) {
    const n = (el.innerText || '').length;
    if (n > bestLen && n < 60000) { best = el; bestLen = n; }
  }
  return (bestLen > 400 ? best : document.body).innerText;
}
"""


def via_browser(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(3500)  # let client-side content render
    return page.evaluate(MAIN_TEXT_JS)


# ----------------------------------------------------------------- entry point
def fetch_original(apply_url, page=None):
    """Return (clean_text, source) or (None, reason)."""
    if not apply_url:
        return None, "no apply link"
    u = urlparse(apply_url)
    tried = []
    for name, fn in FEEDS:
        try:
            raw = fn(u)
        except Exception as e:
            tried.append(f"{name}: {type(e).__name__}")
            continue
        if raw and len(raw) >= MIN_CHARS:
            return clean(raw), f"{name} feed"
    if page is not None:
        for url in dict.fromkeys([apply_url, re.sub(r"/apply/?$", "", apply_url)]):
            try:
                raw = via_browser(page, url)
                if raw and len(raw) >= MIN_CHARS:
                    return clean(raw), f"browser ({u.netloc})"
            except Exception as e:
                tried.append(f"browser: {type(e).__name__}")
    return None, "; ".join(tried) or "no feed matched and browser text too short"
