#!/usr/bin/env python
"""
Measure what Records Assist's model calls really cost, so the web app's first estimate on a new request rests on
measured numbers rather than guesses.

Runs the fictional sample request through the search-plan step and each fictional sample email through the redaction
check, with Claude, using the web app's own functions. Records the token counts Anthropic reports for every call,
prices them with the table in records_assist.py, and prints the MEASURED block to paste into web/app.py.

    ./run-claude.sh python calibrate_costs.py      (or: ANTHROPIC_API_KEY=... .venv/bin/python calibrate_costs.py)

Costs a few cents (seven calls). Results are also saved under out/calibration/.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "web"))

import records_assist as ra  # noqa: E402
import app as web  # noqa: E402


def main() -> int:
    if web.model_status()["claude"]:
        sys.exit("Claude is not available: " + web.model_status()["claude"])
    usage: list[dict] = []
    print("Search plan from samples/request.txt ...", flush=True)
    plan, ledger = web.draft_plan(web.SAMPLE_REQUEST.read_text(encoding="utf-8"), "claude")
    usage += ledger
    print(f"  {ledger[0]['input_tokens']:,} in / {ledger[0]['output_tokens']:,} out  ${ledger[0]['cost_usd']:.4f}")
    pages = []
    for i, path in enumerate(web.SAMPLE_RECORDS, 1):
        text = path.read_text(encoding="utf-8")
        masked, _ = ra.deterministic_pass(text)
        print(f"Redaction check {i}/{len(web.SAMPLE_RECORDS)}: {path.name} ({len(text):,} chars) ...", flush=True)
        spans, notes, ledger = web.model_spans("claude", masked, text, record=f"R{i:02d}")
        usage += ledger
        pages.append(web.pages_for(text))
        print(f"  {len(spans)} spans, {sum(u['input_tokens'] for u in ledger):,} in / {sum(u['output_tokens'] for u in ledger):,} out  ${sum(u['cost_usd'] for u in ledger):.4f}")
    t = web.usage_totals(usage)
    measured = {
        "claude_usd_per_record": round(t["claude_usd_per_record"], 4),
        "claude_plan_usd": round(t["by_step"]["plan"]["cost_usd"], 4),
        "pages_per_record": round(sum(pages) / len(pages), 2),
        "records": len(web.SAMPLE_RECORDS),
        "measured_on": date.today().isoformat(),
    }
    out_dir = HERE / "out" / "calibration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps({"model": usage[0]["model_id"], "pricing": ra.PRICING, "measured": measured, "totals": t, "usage": usage}, indent=2), encoding="utf-8")
    print(f"\nTotal for this run: ${t['total_usd']:.4f} ({t['calls']} calls). Saved to {out.relative_to(HERE)}")
    print("\nPaste into web/app.py:\n\nMEASURED = " + json.dumps(measured, indent=4).replace('"', '"'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
