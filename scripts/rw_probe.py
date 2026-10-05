"""
rw_probe.py - list the buttons, inputs and text areas on the Resume Worded page
that is open in the project Chrome, so the scorer script can target them.
Replaces the Playwright recorder (no new browser, no login needed).

Usage (project Chrome open via scripts\\start_chrome.ps1, logged in):
    python scripts/rw_probe.py 1-jd        # on the "Paste your job description" screen
    python scripts/rw_probe.py 2-upload    # on the resume upload screen
    python scripts/rw_probe.py 3-results   # after Magic Target suggestions have loaded

Writes output/rw_probe_<label>.json. Only UI labels are collected, not your resume text.
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

JS = """
(LABELS) => {
  const txt = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g,' ').slice(0, 80);
  const attrs = el => ({id: el.id || null, name: el.getAttribute('name'), cls: (el.className || '').toString().slice(0, 80)});
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  return {
    url: location.href,
    title: document.title,
    buttons: [...document.querySelectorAll('button, [role=button], input[type=submit], a.btn, a.button')]
      .filter(visible).map(el => ({tag: el.tagName, text: txt(el), ...attrs(el)})).filter(b => b.text).slice(0, 80),
    textareas: [...document.querySelectorAll('textarea, [contenteditable=true]')]
      .map(el => ({tag: el.tagName, placeholder: el.getAttribute('placeholder'), visible: visible(el), ...attrs(el)})),
    file_inputs: [...document.querySelectorAll('input[type=file]')]
      .map(el => ({accept: el.getAttribute('accept'), visible: visible(el), ...attrs(el)})),
    tabs_and_headings: [...document.querySelectorAll('h1, h2, h3, h4, [role=tab]')]
      .filter(visible).map(el => txt(el)).filter(Boolean).slice(0, 40),
    score_candidates: [...document.querySelectorAll('*')]
      .filter(el => el.children.length === 0 && /^\\d{1,3}$/.test((el.innerText || '').trim()) && visible(el))
      .map(el => ({text: el.innerText.trim(), ...attrs(el), parent_cls: (el.parentElement?.className || '').toString().slice(0, 80)}))
      .slice(0, 20),
    suggestion_cards: [...document.querySelectorAll('*')]
      .filter(el => [...el.childNodes].some(n => n.nodeType === 3 && /Suggestion\\s*#\\s*\\d+/.test(n.textContent)))
      .map(el => ({tag: el.tagName, text: txt(el), ...attrs(el),
                   parent_tag: el.parentElement?.tagName, parent_cls: (el.parentElement?.className || '').toString().slice(0, 80),
                   card_cls: (el.closest('div[class]')?.className || '').toString().slice(0, 80)}))
      .slice(0, 3),
    tables: [...document.querySelectorAll('table, [role=table], [role=grid]')].filter(visible).map(t => ({
      tag: t.tagName, ...attrs(t),
      headers: [...t.querySelectorAll('th, [role=columnheader]')].map(txt).slice(0, 10),
      row_count: t.querySelectorAll('tbody tr, [role=row]').length,
      sample_rows: [...t.querySelectorAll('tbody tr, [role=row]')].slice(0, 3).map(r =>
        ({cls: (r.className || '').toString().slice(0, 60),
          cells: [...r.querySelectorAll('td, [role=cell]')].map(c => ({text: txt(c), cls: (c.className || '').toString().slice(0, 60),
                  has_icon: !!c.querySelector('svg, i, img')}))}))
    })),
    suggestion_count: (document.body.innerText.match(/Suggestion\\s*#\\s*\\d+/g) || []).length,
    labeled_clickables: LABELS.flatMap(label => [...document.querySelectorAll('*')]
      .filter(el => visible(el) && (el.innerText || '').trim().replace(/\\s+/g,' ').toLowerCase() === label.toLowerCase())
      .filter(el => ![...el.children].some(c => (c.innerText || '').trim().toLowerCase() === label.toLowerCase()))
      .slice(0, 2)
      .map(el => ({label, tag: el.tagName, ...attrs(el), href: el.getAttribute('href'), onclick: !!el.onclick || el.hasAttribute('onclick')}))),
  };
}
"""

LABELS = ["New Job Description", "Reuse Last Job", "Magic Target", "Generate Action Steps", "Keywords",
          "Mark as Done", "Skip", "Generate Alternative", "Re-upload Resume", "Relevancy Score", "Copy to Clipboard", "Export", "Found Keywords"]

label = sys.argv[1] if len(sys.argv) > 1 else "page"
with sync_playwright() as p:
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
    except Exception:
        sys.exit("Could not reach Chrome on port 9222. Start it with scripts\\start_chrome.ps1 first.")
    pages = [pg for ctx in browser.contexts for pg in ctx.pages if "resumeworded" in pg.url]
    if not pages:
        sys.exit("No Resume Worded tab found in the project Chrome.")
    page = pages[-1]
    data = {"url": page.url, "tabs_open": [pg.url for pg in pages], "frames": []}
    for fr in page.frames:
        try:
            info = fr.evaluate(JS, LABELS)
            info["frame_url"] = fr.url
            data["frames"].append(info)
        except Exception as e:
            data["frames"].append({"frame_url": fr.url, "error": str(e)[:200]})

out = Path("output") / f"rw_probe_{label}.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(data, indent=2), encoding="utf-8")
print(f"Saved {out}  ({data['url']})")
print(f"  RW tabs open: {len(data['tabs_open'])}   frames: {len(data['frames'])}")
for i, f in enumerate(data["frames"]):
    if "error" in f:
        print(f"  frame {i}: error ({f['frame_url'][:60]})")
        continue
    print(f"  frame {i}: buttons {len(f['buttons'])}, textareas {len(f['textareas'])}, file inputs {len(f['file_inputs'])}, "
          f"'Suggestion #' in text: {f['suggestion_count']}, labeled clickables: {len(f['labeled_clickables'])}, tables: {len(f['tables'])}  ({f['frame_url'][:60]})")
