"""
build.py - compile a resume .tex twice, clean up LaTeX junk, then run the gate checker.

Usage (from the project root):
    python scripts/build.py output/<name>_resume.tex

- Finds pdflatex on PATH, or falls back to the MiKTeX install locations,
  so it works even when Git Bash doesn't have MiKTeX on its PATH.
- On a compile error it prints the LaTeX error lines and exits 1.
- Otherwise it runs scripts/check_resume.py and exits with its result
  (0 = all gates pass, 1 = a gate failed).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

JUNK = (".aux", ".log", ".out")


def find_pdflatex():
    found = shutil.which("pdflatex")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/MiKTeX/miktex/bin/x64/pdflatex.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "MiKTeX/miktex/bin/x64/pdflatex.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    sys.exit("ERROR: pdflatex not found on PATH or in the usual MiKTeX folders.")


def compile_tex(pdflatex, tex):
    for run in (1, 2):
        proc = subprocess.run(
            [pdflatex, "-interaction=nonstopmode", tex.name],
            cwd=tex.parent, capture_output=True, text=True, errors="replace",
        )
        if proc.returncode != 0 or not tex.with_suffix(".pdf").exists():
            log = tex.with_suffix(".log")
            text = log.read_text(errors="replace") if log.exists() else proc.stdout
            errors = [l for l in text.splitlines() if l.startswith("!") or "Error" in l]
            print(f"COMPILE FAILED (run {run}). LaTeX errors:")
            print("\n".join(errors[:15]) or text[-2000:])
            sys.exit(1)


def clean(tex):
    for ext in JUNK:
        f = tex.with_suffix(ext)
        if f.exists():
            f.unlink()


def main():
    if len(sys.argv) != 2 or not sys.argv[1].endswith(".tex"):
        sys.exit("usage: python scripts/build.py output/<name>_resume.tex")
    tex = Path(sys.argv[1]).resolve()
    if not tex.exists():
        sys.exit(f"ERROR: {tex} not found")

    compile_tex(find_pdflatex(), tex)
    clean(tex)
    print(f"Compiled {tex.with_suffix('.pdf').name} (2 passes), cleaned aux files.\n")

    checker = Path(__file__).with_name("check_resume.py")
    result = subprocess.run([sys.executable, str(checker), str(tex.with_suffix(".pdf"))])
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
