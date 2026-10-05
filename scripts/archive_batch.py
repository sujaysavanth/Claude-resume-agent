"""
archive_batch.py - after run_batch.py, move finished jobs out of output/ into a dated,
ready-to-apply folder, and clear them from the batch state.

Usage (from the project root):
    python scripts/archive_batch.py            # archive all finished jobs
    python scripts/archive_batch.py --dry-run  # show what would move, change nothing

Result:
    applications/<YYYY-MM-DD>/
        apply_list.md                  <- company, title, score, apply link, PDF name, sorted by score
        sujay_..._<company>_<role>_resume.pdf   (the PDFs you upload)
        details/<resume name>/         <- .tex, flags, JD extract, Resume Worded files, logs
    jds/done/                          <- the JD files of archived jobs

Jobs that failed (build_failed, rw_failed, fix_failed) are left in place so
run_batch.py can retry them.
"""
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT, JDS, LOGS = ROOT / "output", ROOT / "jds", ROOT / "output" / "logs"
STATE_FILE = OUT / "batch_state.json"
SIDE_FILES = [".tex", "_flags.md", "_rw_jd.txt", "_rw_feedback.json", "_rw_rescore.json", "_rw_error.png"]


def jd_meta(jd_path):
    meta = {"company": "-", "title": "-", "apply": "-"}
    if jd_path.exists():
        text = jd_path.read_text(encoding="utf-8", errors="replace")
        for key, label in (("company", "Company"), ("title", "Title"), ("apply", "Apply link")):
            m = re.search(rf"^{label}:\s*(.+)$", text, re.M)
            if m:
                meta[key] = m.group(1).strip()
    return meta


def move(src, dst, dry):
    if not src.exists():
        return False
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        shutil.move(str(src), str(dst))
    return True


def main():
    dry = "--dry-run" in sys.argv
    if not STATE_FILE.exists():
        sys.exit("No output/batch_state.json - nothing to archive.")
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    finished = {k: v for k, v in state.items() if v.get("status") in ("done", "skipped")}
    waiting = {k: v for k, v in state.items() if k not in finished}
    if not finished:
        sys.exit("No finished jobs to archive yet.")

    day = ROOT / "applications" / date.today().isoformat()
    ready, skipped = [], []
    for jd, job in finished.items():
        jd_path = ROOT / jd
        meta = jd_meta(jd_path)
        name = Path(job.get("base", "")).name  # e.g. sujay_..._fiscalnote_swe_resume
        details = day / "details" / (name or Path(jd).stem)

        if name:
            if job["status"] == "done":
                move(OUT / f"{name}.pdf", day / f"{name}.pdf", dry)
            else:
                move(OUT / f"{name}.pdf", details / f"{name}.pdf", dry)
            for suffix in SIDE_FILES:
                move(OUT / f"{name}{suffix}", details / f"{name}{suffix}", dry)
        for log in LOGS.glob(f"{Path(jd).stem}_*.log"):
            move(log, details / "logs" / log.name, dry)
        move(jd_path, JDS / "done" / jd_path.name, dry)

        row = {**meta, "pdf": f"{name}.pdf" if name else "-", "before": job.get("score_before", "-"),
               "after": job.get("score_after", job.get("score_before", "-")), "flags": job.get("flags", "-"),
               "note": job.get("note", "")}
        (ready if job["status"] == "done" else skipped).append(row)

    ready.sort(key=lambda r: -(r["after"] if isinstance(r["after"], int) else -1))
    lines = [f"# Applications ready - {day.name}", "",
             "Review each job's flags in details/<resume>/ before applying.", "",
             "| # | Company | Title | Score | Flags | Resume | Apply |", "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ready, 1):
        score = f"{r['before']} -> {r['after']}" if r["before"] != r["after"] else f"{r['after']}"
        lines.append(f"| {i} | {r['company']} | {r['title']} | {score} | {r['flags']} | {r['pdf']} | {r['apply']} |")
    if skipped:
        lines += ["", "## Skipped (hard blockers)", ""]
        lines += [f"- {r['company']} - {r['title']}: {r['note'] or 'see flags'}" for r in skipped]

    if dry:
        print("\n".join(lines))
        print(f"\n[dry run] would archive {len(finished)} job(s) to {day}; {len(waiting)} left in output/.")
        return

    day.mkdir(parents=True, exist_ok=True)
    apply_list = day / "apply_list.md"
    if apply_list.exists():  # second batch on the same day: append instead of overwrite
        old = apply_list.read_text(encoding="utf-8")
        apply_list.write_text(old + "\n\n" + "\n".join(lines[2:]) + "\n", encoding="utf-8")
    else:
        apply_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
    STATE_FILE.write_text(json.dumps(waiting, indent=2), encoding="utf-8")
    summary = OUT / "batch_summary.md"
    if summary.exists() and not waiting:
        summary.unlink()

    print(f"Archived {len(ready)} ready + {len(skipped)} skipped job(s) to {day}")
    print(f"  Upload PDFs from: {day}")
    print(f"  Apply list:       {apply_list}")
    if waiting:
        print(f"  Left in output/ for retry: {', '.join(Path(k).name for k in waiting)}")


if __name__ == "__main__":
    main()
