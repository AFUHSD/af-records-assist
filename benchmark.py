#!/usr/bin/env python
"""
Benchmark the two providers on the fictional samples (prototype, 2026-10-26).

Runs records_assist.py on every sample, several times per provider, and scores each run against
samples/benchmark/answer-key.json. Everything it reads is fictional; never point it at a real record.

  redact  Per record: did each planted item disappear (caught / partial / missed), did the public items stay
          visible, and what else did the model black out that the key does not list (for a person to judge).
  query   Per run: searches a small fictional mailbox the way Vault's Terms line does and counts the responsive
          messages found and the unrelated ones pulled in, plus whether the date range reaches today.

  python benchmark.py                          # both providers, 3 runs each
  python benchmark.py --providers rig --runs 1

Same environment as records_assist.py: the rig needs DISTRICT_LLM_BASE_URL and DISTRICT_LLM_TOKEN, Claude needs
ANTHROPIC_API_KEY. Results land in out/benchmark/<timestamp>/ (git ignores out/).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import records_assist  # deterministic_pass: tells a pattern false positive from a model one

HERE = Path(__file__).resolve().parent
KEY = HERE / "samples" / "benchmark" / "answer-key.json"
WORKERS = {"rig": 2, "claude": 4}  # the rig is one shared 8 GB card; keep the load on it light
POINTS = {"caught": 1.0, "partial": 0.5, "missed": 0.0}


# ---------------------------------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------------------------------
def run_one(job: dict, out_dir: Path) -> dict:
    stem = f"{job['cmd']}-{job['provider']}-{Path(job['file']).stem}-{job['n']}"
    redacted_path = out_dir / "runs" / f"{stem}.redacted.txt"
    argv = [sys.executable, str(HERE / "records_assist.py"), "--provider", job["provider"], job["cmd"], str(HERE / job["file"])]
    if job["cmd"] == "redact":
        argv += ["--out", str(redacted_path)]
    start = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=900)
        stdout, stderr, ok = p.stdout, p.stderr, p.returncode == 0
    except subprocess.TimeoutExpired:
        stdout, stderr, ok = "", "timed out after 900 s", False
    secs = round(time.monotonic() - start, 1)
    (out_dir / "runs" / f"{stem}.md").write_text(stdout + (f"\n\n## stderr\n\n{stderr}" if stderr.strip() else ""), encoding="utf-8")
    redacted = redacted_path.read_text(encoding="utf-8") if redacted_path.exists() else ""
    return {**job, "ok": ok, "secs": secs, "stdout": stdout, "stderr": stderr.strip()[-300:], "redacted": redacted}


def run_all(jobs: list[dict], providers: list[str], out_dir: Path) -> list[dict]:
    """One pool per provider, side by side, so the slow one does not hold up the other."""
    pools = {p: ThreadPoolExecutor(max_workers=WORKERS[p]) for p in providers}
    futures = [pools[j["provider"]].submit(run_one, j, out_dir) for j in jobs]
    results = []
    for f in futures:
        r = f.result()
        print(f"  {r['provider']:6} {r['cmd']:6} {Path(r['file']).stem:28} run {r['n']}  {'ok' if r['ok'] else 'FAILED'}  {r['secs']} s", flush=True)
        results.append(r)
    for pool in pools.values():
        pool.shutdown()
    return results


# ---------------------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------------------
def model_spans(stdout: str) -> list[str]:
    """The model's spans, read back from the redaction log table (the text is the last column)."""
    spans = []
    for line in stdout.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and cells[0].isdigit() and cells[1] != "pattern":
            spans.append(cells[-1])
    return spans


def score_redact(run: dict, rec: dict) -> dict:
    original = (HERE / rec["file"]).read_text(encoding="utf-8")
    pattern_only, _ = records_assist.deterministic_pass(original)
    text = run["redacted"]
    items = {}
    for item in rec["hide"]:
        hidden = [frag not in text for frag in item["fragments"]]
        items[item["id"]] = "caught" if all(hidden) else "partial" if any(hidden) else "missed"
    removed = {}
    for k in rec["keep"]:
        if k["text"] not in text:
            removed[k["text"]] = "pattern" if k["text"] not in pattern_only else "model"
    known = [f for i in rec["hide"] for f in i["fragments"]] + [k["text"] for k in rec["keep"]]
    extra = [s for s in model_spans(run["stdout"]) if not any(s in f or f in s for f in known)]
    model_items = [i["id"] for i in rec["hide"] if i["by"] == "model"]
    return {
        "items": items,
        "model_points": sum(POINTS[items[i]] for i in model_items),
        "model_total": len(model_items),
        "pattern_ok": all(items[i["id"]] == "caught" for i in rec["hide"] if i["by"] == "pattern"),
        "keep_removed": removed,
        "extra": extra,
        "dropped": run["stdout"].count("not found verbatim"),
        "unparsed": run["stdout"].count("was not parseable"),
    }


def score_query(run: dict, q: dict) -> dict:
    m = re.search(r"Gmail terms:\s*\n\s*\n\s*(.+)", run["stdout"])
    terms = m.group(1).strip() if m else ""
    phrases = [p.lower() for p in re.findall(r"\"([^\"]+)\"", terms)]
    hits = [msg for msg in q["mailbox"] if any(p in msg["text"].lower() for p in phrases)]
    after = re.search(r"after:(\d{4}/\d{2}/\d{2})", terms)
    before = re.search(r"before:(\d{4}/\d{2}/\d{2})", terms)
    lines = run["stdout"].splitlines()
    questions = 0
    for i, line in enumerate(lines):
        if line.startswith("- Questions to send the requester:"):
            questions = sum(1 for _ in _takewhile_indented(lines[i + 1:]))
    return {
        "terms": terms,
        "found": [h["id"] for h in hits if h["responsive"]],
        "noise": [h["id"] for h in hits if not h["responsive"]],
        "responsive_total": sum(1 for msg in q["mailbox"] if msg["responsive"]),
        "noise_total": sum(1 for msg in q["mailbox"] if not msg["responsive"]),
        "after": after.group(1) if after else None,
        "before": before.group(1) if before else None,
        "reaches_today": bool(before) and before.group(1) > q["today"],  # Vault's before: is exclusive
        "questions": questions,
    }


def _takewhile_indented(lines: list[str]):
    for line in lines:
        if not line.startswith("    - "):
            return
        yield line


# ---------------------------------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------------------------------
def tally(statuses: list[str]) -> str:
    c, p = statuses.count("caught"), statuses.count("partial")
    return f"{c}/{len(statuses)}" + (f" (+{p} partial)" if p else "")


def render(results: list[dict], key: dict, providers: list[str], runs: int) -> str:
    red = [r for r in results if r["cmd"] == "redact" and r["ok"]]  # a failed run is reported below, not scored
    qry = [r for r in results if r["cmd"] == "query" and r["ok"]]
    out = [f"# Benchmark, {datetime.now():%Y-%m-%d %H:%M}", "",
           f"Providers: {', '.join(providers)}. {runs} run(s) each. All samples fictional.", ""]

    out += ["## Redaction: model items (patterns excluded, they run first for both)", "",
            "| Provider | Score, all runs | Lowest run | Highest run | Public items hidden by the model | Extra model redactions | Failed runs |",
            "|---|---|---|---|---|---|---|"]
    for p in providers:
        rs = [r for r in red if r["provider"] == p]
        per_run = {}
        for r in rs:
            pts, tot = per_run.get(r["n"], (0.0, 0))
            per_run[r["n"]] = (pts + r["score"]["model_points"], tot + r["score"]["model_total"])
        pts = sum(v[0] for v in per_run.values())
        tot = sum(v[1] for v in per_run.values())
        pcts = [v[0] / v[1] * 100 for v in per_run.values() if v[1]]
        keep_model = sum(1 for r in rs for who in r["score"]["keep_removed"].values() if who == "model")
        extra = sum(len(r["score"]["extra"]) for r in rs)
        failed = sum(1 for r in results if r["provider"] == p and r["cmd"] == "redact" and not r["ok"])
        out.append(f"| {p} | {pts:g} of {tot} ({pts / tot * 100:.0f}%) | {min(pcts):.0f}% | {max(pcts):.0f}% | {keep_model} | {extra} | {failed} |" if tot else f"| {p} | no runs | | | | | {failed} |")

    out += ["", "## Redaction: every planted item", "",
            "| Record | Item | Expected pass | " + " | ".join(providers) + " |",
            "|---|---|---|" + "---|" * len(providers)]
    for rec in key["records"]:
        for item in rec["hide"]:
            cells = []
            for p in providers:
                sts = [r["score"]["items"][item["id"]] for r in red if r["provider"] == p and r["file"] == rec["file"]]
                cells.append(tally(sts) if sts else "n/a")
            out.append(f"| {rec['title']} | {item['what']} | {item['by']} | " + " | ".join(cells) + " |")

    out += ["", "## Public items that were hidden", "", "| Provider | Record | Item | Removed by | Runs |", "|---|---|---|---|---|"]
    rows = {}
    for r in red:
        for text, who in r["score"]["keep_removed"].items():
            k = (r["provider"], r["file"], text, who)
            rows[k] = rows.get(k, 0) + 1
    titles = {rec["file"]: rec["title"] for rec in key["records"]}
    for (p, f, text, who), n in sorted(rows.items()):
        out.append(f"| {p} | {titles[f]} | {text} | {who} | {n} |")

    out += ["", "## Extra model redactions (not in the key; a person decides if each is right)", ""]
    for r in red:
        if r["score"]["extra"]:
            out.append(f"- {r['provider']}, {titles[r['file']]}, run {r['n']}: " + "; ".join(f"`{s}`" for s in r["score"]["extra"]))

    if qry:
        q = key["query"]
        out += ["", "## Search plan", "",
                "| Provider | Run | Responsive found | Unrelated pulled in | Date range | Reaches today | Questions for requester |",
                "|---|---|---|---|---|---|---|"]
        for r in sorted(qry, key=lambda r: (r["provider"], r["n"])):
            s = r["score"]
            out.append(f"| {r['provider']} | {r['n']} | {len(s['found'])}/{s['responsive_total']} | {len(s['noise'])}/{s['noise_total']} | "
                       f"{s['after']} to {s['before']} | {'yes' if s['reaches_today'] else 'no'} | {s['questions']} |")
        out += ["", "Terms each run proposed:", ""]
        for r in sorted(qry, key=lambda r: (r["provider"], r["n"])):
            out.append(f"- {r['provider']} run {r['n']}: `{r['score']['terms']}`")
        out += ["", q["note"]]

    failures = [r for r in results if not r["ok"]]
    if failures:
        out += ["", "## Failed runs", ""] + [f"- {r['provider']} {r['cmd']} {r['file']} run {r['n']}: {r['stderr']}" for r in failures]
    secs = {p: [r["secs"] for r in results if r["provider"] == p and r["ok"]] for p in providers}
    out += ["", "Average seconds per run: " + ", ".join(f"{p} {sum(v) / len(v):.0f}" for p, v in secs.items() if v)]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--providers", default="rig,claude", help="comma-separated: rig, claude (default both)")
    ap.add_argument("--runs", type=int, default=3, help="runs per provider per sample (default 3)")
    ap.add_argument("--skip-query", action="store_true", help="redaction only")
    args = ap.parse_args(argv)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    key = json.loads(KEY.read_text(encoding="utf-8"))

    out_dir = HERE / "out" / "benchmark" / datetime.now().strftime("%Y%m%d-%H%M")
    (out_dir / "runs").mkdir(parents=True, exist_ok=True)
    jobs = []
    for n in range(1, args.runs + 1):
        for p in providers:
            if not args.skip_query:
                jobs.append({"provider": p, "cmd": "query", "file": key["query"]["file"], "n": n})
            for rec in key["records"]:
                jobs.append({"provider": p, "cmd": "redact", "file": rec["file"], "n": n})
    print(f"{len(jobs)} runs; results in {out_dir}")

    results = run_all(jobs, providers, out_dir)
    recs = {rec["file"]: rec for rec in key["records"]}
    for r in results:
        if r["ok"]:
            r["score"] = score_redact(r, recs[r["file"]]) if r["cmd"] == "redact" else score_query(r, key["query"])

    (out_dir / "results.json").write_text(json.dumps([{k: v for k, v in r.items() if k not in ("stdout", "redacted")} for r in results], indent=2), encoding="utf-8")
    report = render(results, key, providers, args.runs)
    (out_dir / "scorecard.md").write_text(report, encoding="utf-8")
    print(f"\nScorecard: {out_dir / 'scorecard.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
