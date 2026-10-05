"""
check_resume.py - deterministic quality gates for a built resume.

Usage:
    python scripts/check_resume.py output/<name>_resume.pdf
    (expects the matching .tex next to the PDF)

Checks:
    PDF : exactly 1 page, fill 92-96%, fonts NimbusRom only (zero CMSY),
          Technical Skills = exactly 5 rows with no wrapping
    TEX : bullets in Experience + Project Experience -> weak lead verbs,
          duplicate lead verbs, banned words, ending period, semicolons,
          em dashes, non-ASCII, project count == 2

Exit code 0 = all gates pass, 1 = at least one gate failed.
Only needs: pip install pdfplumber
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

import pdfplumber

FILL_MIN, FILL_MAX = 92.0, 96.0
SKILL_ROWS = 5
PROJECT_COUNT = 2

WEAK_VERBS = {"wrote", "handled", "worked", "helped", "assisted", "used",
              "managed", "participated", "supported", "involved"}
BANNED = ["scalable", "robust", "end-to-end", "cross-functional", "cutting-edge",
          "state-of-the-art", "seamless", "production-grade", "leveraged",
          "utilizing", "ensuring", "various", "significantly", "directly",
          "responsible for"]
BULLET_SECTIONS = {"Experience", "Project Experience"}


# ---------------------------------------------------------------- PDF checks
def check_pdf(pdf_path):
    res = {}
    with pdfplumber.open(pdf_path) as pdf:
        res["pages"] = len(pdf.pages)
        page = pdf.pages[0]
        words = page.extract_words()
        bottom = max(w["bottom"] for w in words) if words else 0
        res["fill_pct"] = round(bottom / page.height * 100, 1)

        fonts = set()
        for p in pdf.pages:
            for c in p.chars:
                fonts.add(c.get("fontname", "").split("+")[-1])
        res["fonts"] = sorted(fonts)
        res["bad_fonts"] = sorted(f for f in fonts if "NimbusRom" not in f)

        lines = [l.strip() for l in (page.extract_text(x_tolerance=1) or "").splitlines()]
        keys = [l.replace(" ", "") for l in lines]  # header match ignores spacing
        try:
            start = keys.index("TechnicalSkills") + 1
            end = keys.index("Experience", start)
            rows = lines[start:end]
        except ValueError:
            rows = None
        res["skill_rows"] = rows
    return res


# ---------------------------------------------------------------- TEX checks
def strip_latex(s):
    s = s.replace("\\%", "%").replace("\\&", "&").replace("\\$", "$").replace("\\#", "#")
    prev = None
    while prev != s:  # unwrap \cmd{...} repeatedly, keep inner text
        prev = s
        s = re.sub(r"\\[a-zA-Z]+\*?\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    s = s.replace("{", "").replace("}", "").replace("~", " ")
    return re.sub(r"\s+", " ", s).strip()


def split_sections(tex):
    body = tex.split("\\begin{document}", 1)[-1]
    parts = re.split(r"\\section\*?\{([^}]*)\}", body)
    # parts = [preamble, name1, content1, name2, content2, ...]
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def extract_items(content):
    items = []
    for block in re.findall(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", content, re.S):
        for raw in block.split("\\item")[1:]:
            raw = re.sub(r"(?<!\\)%.*", "", raw).strip()  # drop LaTeX comments
            if raw:
                items.append(raw)
    return items


def check_tex(tex_path):
    tex = Path(tex_path).read_text(encoding="utf-8")
    sections = split_sections(tex)
    res = {"sections": list(sections.keys()), "bullets": [], "issues": []}

    proj = sections.get("Project Experience", "")
    res["project_count"] = len(re.findall(r"\\projectheading", proj))

    for sec_name, content in sections.items():
        if sec_name not in BULLET_SECTIONS:
            continue
        for raw in extract_items(content):
            text = strip_latex(raw)
            lead = re.sub(r"[^A-Za-z-]", "", text.split(" ", 1)[0]).lower()
            res["bullets"].append({"section": sec_name, "lead": lead, "text": text})
            low = text.lower()
            short = text[:60] + ("..." if len(text) > 60 else "")
            if lead in WEAK_VERBS:
                res["issues"].append(f"weak lead verb '{lead}': {short}")
            for b in BANNED:
                if re.search(r"\b" + re.escape(b) + r"\b", low):
                    res["issues"].append(f"banned word '{b}': {short}")
            if not text.endswith("."):
                res["issues"].append(f"no ending period: {short}")
            if ";" in text:
                res["issues"].append(f"semicolon: {short}")
            if "---" in raw or "\u2014" in raw:
                res["issues"].append(f"em dash: {short}")
            if any(ord(ch) > 127 for ch in raw):
                res["issues"].append(f"non-ASCII character: {short}")

    counts = Counter(b["lead"] for b in res["bullets"])
    for verb, n in counts.items():
        if n > 1:
            res["issues"].append(f"duplicate lead verb '{verb}' x{n}")
    return res


# ---------------------------------------------------------------- main
def main():
    if len(sys.argv) != 2:
        print("usage: python scripts/check_resume.py output/<name>.pdf")
        sys.exit(2)
    pdf_path = Path(sys.argv[1])
    tex_path = pdf_path.with_suffix(".tex")

    pdf = check_pdf(pdf_path)
    tex = check_tex(tex_path) if tex_path.exists() else None

    gates = {
        "one_page": pdf["pages"] == 1,
        "fill": FILL_MIN <= pdf["fill_pct"] <= FILL_MAX,
        "fonts": not pdf["bad_fonts"],
        "skills_rows": pdf["skill_rows"] is not None
                       and len(pdf["skill_rows"]) == SKILL_ROWS
                       and all(r.startswith("\u2022") for r in pdf["skill_rows"]),
    }
    if tex is not None:
        gates["project_count"] = tex["project_count"] == PROJECT_COUNT
        gates["bullet_audit"] = not tex["issues"]
    else:
        gates["tex_found"] = False

    report = {
        "file": pdf_path.name,
        "pass": all(gates.values()),
        "gates": gates,
        "pages": pdf["pages"],
        "fill_pct": pdf["fill_pct"],
        "bad_fonts": pdf["bad_fonts"],
        "skill_rows": pdf["skill_rows"],
        "project_count": tex["project_count"] if tex else None,
        "bullet_count": len(tex["bullets"]) if tex else None,
        "issues": tex["issues"] if tex else ["matching .tex not found"],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("RESULT:", "PASS" if report["pass"] else "FAIL",
          "|", " ".join(f"{k}={'ok' if v else 'FAIL'}" for k, v in gates.items()))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
