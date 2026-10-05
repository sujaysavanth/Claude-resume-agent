"""
run_batch.py - one command for the whole pipeline, for every JD in jds/.

    1. BUILD    claude -p "Tailor the resume for jds/<file>"        (parallel, default 2 at a time)
    2. SCORE    python scripts/rw_score.py <base> [--magic]         (one at a time, paced)
    3. FIX      claude -p "FIX MODE ..."  only if score < 85         (parallel)
    4. RESCORE  python scripts/rw_score.py <base> --rescore          (one at a time, paced)
    5. SUMMARY  table sorted by final score -> output/batch_summary.md

Usage (from the project root):
    python scripts/run_batch.py                 # all new JDs in jds/
    python scripts/run_batch.py --magic         # also use Magic Target (1 of 20 daily runs per job)
    python scripts/run_batch.py --no-rw         # build only, skip Resume Worded
    python scripts/run_batch.py --redo          # re-run JDs that already finished
    python scripts/run_batch.py --parallel 3    # builds/fixes at a time (Claude Pro limits: keep 2-3)
    python scripts/run_batch.py jds/a.txt jds/b.txt   # only these JDs

Progress is saved to output/batch_state.json after every step, so if anything
crashes you can just run the same command again and it picks up where it left off.
Per-step logs: output/logs/.
"""
import json
import random
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JDS, OUT, LOGS = ROOT / "jds", ROOT / "output", ROOT / "output" / "logs"
STATE_FILE = OUT / "batch_state.json"
TARGET = 85
CLAUDE_TIMEOUT = 20 * 60      # seconds per Claude run
RW_PAUSE = (20, 40)           # seconds between Resume Worded jobs (human pacing)
# passed to every `claude -p` run so builds work even if the folder isn't trusted
# (an untrusted folder ignores the allow list in .claude/settings.json)
CLAUDE_TOOLS = ("Read,Write,Edit,Glob,Grep,"
                "Bash(python scripts/build.py:*),Bash(python scripts/check_resume.py:*),"
                "PowerShell(python scripts/build.py:*),PowerShell(python scripts/check_resume.py:*)")

BUILD_LINE = re.compile(r"(sujay_govindappa_rajashekar_\S+?_resume)\.pdf\s*\|\s*fill\s*([\d.na/]+)%?\s*\|\s*gates\s*(pass|fail|skip)\s*\|\s*flags\s*(\d+)", re.I)
SCORE_LINE = re.compile(r"(?:SCORE|RESCORE):\s*(\d+)/100")


# ----------------------------------------------------------------- helpers
def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def run(cmd, log_name, timeout=CLAUDE_TIMEOUT):
    """Run a command from the project root, log its output, return (exit code, output)."""
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        out = (proc.stdout or "") + ("\n[stderr]\n" + proc.stderr if proc.stderr else "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        out, code = f"TIMEOUT after {timeout}s", 124
    (LOGS / log_name).write_text(out, encoding="utf-8")
    return code, out


def chrome_up():
    try:
        urllib.request.urlopen("http://localhost:9222/json/version", timeout=3)
        return True
    except Exception:
        return False


def flags_skipped(base):
    f = OUT / f"{base}_flags.md"
    if f.exists():
        first = f.read_text(encoding="utf-8", errors="replace").lstrip().splitlines()[:1]
        return first[0].strip() if first and first[0].strip().upper().startswith("SKIPPED") else None
    return None


# ----------------------------------------------------------------- stages
def build(job, claude):
    jd = job["jd"]
    log = f"{Path(jd).stem}_build.log"
    code, out = run([claude, "-p", f"Tailor the resume for {jd}", "--allowedTools", CLAUDE_TOOLS], log)
    m = None
    for m in BUILD_LINE.finditer(out):
        pass  # keep the last match (the summary line)
    if m:
        job.update(base=f"output/{m.group(1)}", fill=m.group(2), gates=m.group(3).lower(), flags=int(m.group(4)))
        skipped = flags_skipped(m.group(1))
        pdf = (OUT / f"{m.group(1)}.pdf").exists()
        if skipped:
            job.update(status="skipped", note=skipped[:120])
        elif job["gates"] != "pass" or not pdf:
            job.update(status="build_failed",
                       note=f"gates {job['gates']}, PDF {'found' if pdf else 'missing'}; see logs/{log}")
        else:
            job["status"] = "built"
    else:
        job.update(status="build_failed", note=f"no summary line (exit {code}); see logs/{Path(jd).stem}_build.log")
    return job


def score(job, magic, rescore=False):
    base = job["base"]
    cmd = [sys.executable, "scripts/rw_score.py", base] + (["--rescore"] if rescore else (["--magic"] if magic else []))
    code, out = run(cmd, f"{Path(job['jd']).stem}_{'rescore' if rescore else 'score'}.log", timeout=15 * 60)
    m = SCORE_LINE.search(out)
    if code == 0 and m:
        job["score_after" if rescore else "score_before"] = int(m.group(1))
        job.pop("note", None)  # clear a failure note left by an earlier attempt
        return True
    job["note"] = f"Resume Worded {'rescore' if rescore else 'score'} failed (exit {code}); see {base}_rw_error.png"
    return False


def fix(job, claude):
    base = job["base"]
    prompt = f"FIX MODE for {base} using {base}_rw_feedback.json"
    code, out = run([claude, "-p", prompt, "--allowedTools", CLAUDE_TOOLS], f"{Path(job['jd']).stem}_fix.log")
    m = re.search(r"applied\s+(\d+)\s*\|\s*rejected\s+(\d+)\s*\|\s*gates\s+(pass|fail)", out, re.I)
    if m and m.group(3).lower() == "pass":
        job.update(applied=int(m.group(1)), rejected=int(m.group(2)), gates="pass", status="fixed")
    elif m:
        job.update(status="fix_failed", note=f"fix ended with gates fail; see logs/{Path(job['jd']).stem}_fix.log")
    else:
        job.update(status="fix_failed", note=f"no fix summary (exit {code}); see logs/{Path(job['jd']).stem}_fix.log")
    return job


# ----------------------------------------------------------------- summary
def summarize(state, jd_list):
    rows = [state[j] for j in jd_list if j in state]
    final = lambda r: r.get("score_after", r.get("score_before", -1))
    rows.sort(key=lambda r: (r.get("status") in ("skipped", "build_failed"), -final(r)))
    head = "| # | JD | Resume | Before | After | Gates | Flags | Status / note |"
    lines = [head, "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        name = Path(r.get("base", "")).name.replace("sujay_govindappa_rajashekar_", "").replace("_resume", "")
        lines.append(f"| {i} | {Path(r['jd']).name} | {name or '-'} | {r.get('score_before', '-')} | "
                     f"{r.get('score_after', '-')} | {r.get('gates', '-')} | {r.get('flags', '-')} | "
                     f"{r.get('status', '-')}{' - ' + r['note'] if r.get('note') else ''} |")
    md = f"# Batch summary ({datetime.now():%Y-%m-%d %H:%M})\n\n" + "\n".join(lines) + "\n"
    (OUT / "batch_summary.md").write_text(md, encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nSaved {OUT / 'batch_summary.md'}. Review each *_flags.md before applying.")


# ----------------------------------------------------------------- main
def main():
    args = sys.argv[1:]
    magic, no_rw, redo = "--magic" in args, "--no-rw" in args, "--redo" in args
    par = int(args[args.index("--parallel") + 1]) if "--parallel" in args else 2
    explicit = [a for a in args if a.endswith(".txt")]

    OUT.mkdir(exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    claude = shutil.which("claude")
    if not claude:
        sys.exit("ERROR: 'claude' not found on PATH.")
    if not no_rw and not chrome_up():
        sys.exit("ERROR: project Chrome isn't running. Start it first:\n"
                 "  powershell -ExecutionPolicy Bypass -File scripts\\start_chrome.ps1\n"
                 "(or add --no-rw to build without Resume Worded)")

    jd_list = [str(Path(p).as_posix()) for p in explicit] or \
              [f"jds/{p.name}" for p in sorted(JDS.glob("*.txt"))]
    if not jd_list:
        sys.exit("No JDs found in jds/.")
    state = load_state()
    todo = [j for j in jd_list if redo or state.get(j, {}).get("status") not in ("done", "skipped")]
    print(f"{len(jd_list)} JDs, {len(todo)} to process "
          f"({'Resume Worded ON' + (' + Magic Target' if magic else '') if not no_rw else 'Resume Worded OFF'}).")
    t0 = time.time()

    # 1. BUILD (parallel)
    to_build = [j for j in todo if redo or state.get(j, {}).get("status") in (None, "build_failed")]
    if to_build:
        print(f"\n[1/4] Building {len(to_build)} resume(s), {par} at a time...")
        with ThreadPoolExecutor(max_workers=par) as pool:
            for job in pool.map(lambda j: build({"jd": j}, claude), to_build):
                state[job["jd"]] = job
                save_state(state)
                print(f"  {job['status']:<12} {job['jd']}  {job.get('note', '')}")

    if no_rw:
        for j in todo:
            if state[j]["status"] == "built":
                state[j]["status"] = "done"
        save_state(state)
        return summarize(state, jd_list)

    # resume jobs that failed mid-way on a previous run
    for j in todo:
        st = state.get(j, {}).get("status")
        if st == "rw_failed":
            state[j]["status"] = "fixed" if "applied" in state[j] else ("scored" if "score_before" in state[j] else "built")
        elif st == "fix_failed":
            state[j]["status"] = "scored"

    # 2. SCORE (sequential, paced)
    to_score = [j for j in todo if state[j]["status"] == "built"]
    if to_score:
        print(f"\n[2/4] Scoring {len(to_score)} on Resume Worded (one at a time)...")
    fails = 0
    for i, j in enumerate(to_score):
        if i:
            time.sleep(random.uniform(*RW_PAUSE))
        ok = score(state[j], magic)
        state[j]["status"] = "scored" if ok else "rw_failed"
        save_state(state)
        print(f"  {state[j].get('score_before', 'ERR'):>3}  {j}")
        fails = 0 if ok else fails + 1
        if fails >= 2:
            print("  Two Resume Worded failures in a row - stopping scoring. Check the project Chrome "
                  "(logged in? CAPTCHA?) and run the same command again to resume.")
            break

    # 3. FIX (parallel) only below target
    to_fix = [j for j in todo if state[j]["status"] == "scored" and state[j]["score_before"] < TARGET]
    for j in todo:
        if state[j]["status"] == "scored" and state[j]["score_before"] >= TARGET:
            state[j]["status"] = "done"
    save_state(state)
    if to_fix:
        print(f"\n[3/4] Fixing {len(to_fix)} resume(s) below {TARGET}, {par} at a time...")
        with ThreadPoolExecutor(max_workers=par) as pool:
            for job in pool.map(lambda j: fix(state[j], claude), to_fix):
                save_state(state)
                print(f"  {job['status']:<10} {job['jd']}  applied {job.get('applied', '-')}, rejected {job.get('rejected', '-')}")

    # 4. RESCORE (sequential, paced)
    to_rescore = [j for j in todo if state[j]["status"] == "fixed"]
    if to_rescore:
        print(f"\n[4/4] Re-scoring {len(to_rescore)}...")
    for i, j in enumerate(to_rescore):
        if i:
            time.sleep(random.uniform(*RW_PAUSE))
        ok = score(state[j], magic, rescore=True)
        state[j]["status"] = "done" if ok else "rw_failed"
        save_state(state)
        print(f"  {state[j].get('score_before')} -> {state[j].get('score_after', 'ERR')}  {j}")

    print(f"\nFinished in {(time.time() - t0) / 60:.1f} min.")
    summarize(state, jd_list)


if __name__ == "__main__":
    main()
