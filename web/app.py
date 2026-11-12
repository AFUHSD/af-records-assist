#!/usr/bin/env python
"""
Records Assist web app (prototype, 2026-10-26): a browser front end on top of records_assist.py.

The engine's prompts, patterns and providers are not changed (its one addition is the token count each call
reports). This app imports its prompts, pattern pass, exemption table and both providers (rig and Claude) and calls
them, so a change to a prompt or pattern there shows up here on the next start.

  .venv/bin/python web/app.py        then open http://localhost:8000

Pages, in order: new request -> search plan -> records (the Vault export) -> review -> sign off. Each request is a
JSON file under out/requests/REQ-nnnn/ with its uploads and its outputs beside it (git ignores out/).

Advisory only, like the engine: a person approves every search plan and every redaction. Fictional records only
until the district decides where record content may go (RECORDS_ALLOW_REAL=true lifts the confirmation).
"""
from __future__ import annotations

import email
import email.policy
import json
import mailbox
import os
import re
import sys
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import records_assist as ra  # noqa: E402  the engine: imported and called, never edited from here

DATA = ROOT / "out" / "requests"
SAMPLE_REQUEST = ROOT / "samples" / "request.txt"
SAMPLE_RECORDS = [ROOT / "samples" / "email-export.txt", *sorted((ROOT / "samples" / "benchmark").glob("*.txt"))]
ALLOW_REAL_RECORDS = os.environ.get("RECORDS_ALLOW_REAL", "") == "true"
MODELS = {"rig": "Rig", "claude": "Claude"}
STEP_NAMES = {"plan": "Search plan", "redact": "Redaction check"}

# Cost estimate. The $1 per page is district policy (decisions of 2026-10-26, item 6: whether to enforce it is the
# cabinet's call; the workflow shows it either way). Pages are what the release PDF would print: 10pt Helvetica on
# letter paper with one-inch margins fits about 3,200 characters, and each record starts on its own page.
PAGE_RATE_USD = 1.00
CHARS_PER_PAGE = 3200
# Fallbacks for a request that has no measurements of its own yet. These come from calibrate_costs.py, which runs the
# six fictional sample emails and the sample request through Claude for real and records what Anthropic reported.
# Re-run it after a prompt or model change and paste the numbers it prints here.
MEASURED = {  # calibrate_costs.py, 2026-11-09, claude-opus-5-5: 7 calls, $0.20 in total (out/calibration/20261109-1913.json)
    "claude_usd_per_record": 0.0227,
    "claude_plan_usd": 0.0669,
    "pages_per_record": 1.0,
    "records": 6,
    "measured_on": "2026-11-09",
}
DECISIONS = ("pending", "approve", "reject")

app = Flask(__name__)
app.secret_key = os.urandom(16)  # flash messages only; nothing needs to survive a restart
_max_mb = os.environ.get("RECORDS_MAX_UPLOAD_MB", "")
# No cap by default: a Vault .mbox export can be gigabytes, and this only listens on this computer. A hosted copy sets one.
app.config["MAX_CONTENT_LENGTH"] = int(_max_mb) * 1024 * 1024 if _max_mb.isdigit() else None
app.config["MAX_FORM_MEMORY_SIZE"] = None  # pasted email text in the form has no cap either (Werkzeug 3.1 defaults to 500 KB)
# The names this app answers to. Anything else is refused, so a web page that points its own hostname at 127.0.0.1
# (DNS rebinding) cannot read record text. A hosted copy lists its public name in RECORDS_TRUSTED_HOSTS.
app.config["TRUSTED_HOSTS"] = [h.strip() for h in os.environ.get("RECORDS_TRUSTED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
_lock = threading.Lock()  # one writer at a time per request file
UPLOAD_TYPES = (".mbox", ".eml", ".txt")


@app.before_request
def same_site_only():
    """A request that changes something must come from this app's own pages. Browsers label every cross-site POST
    (Sec-Fetch-Site, Origin), so another open tab cannot sign off, reopen, delete, or start a paid model call."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    site = request.headers.get("Sec-Fetch-Site")
    origin = request.headers.get("Origin")
    if (site and site not in ("same-origin", "none")) or (origin and origin.split("://", 1)[-1] != request.host):
        abort(403)
    return None


# ---------------------------------------------------------------------------------------------------------------------
# Storage: one JSON file per request
# ---------------------------------------------------------------------------------------------------------------------
def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def folder(rid: str) -> Path:
    if not re.fullmatch(r"REQ-\d{4}", rid):
        abort(404)
    return DATA / rid


def load(rid: str) -> dict:
    path = folder(rid) / "request.json"
    if not path.exists():
        abort(404)
    return json.loads(path.read_text(encoding="utf-8"))


def save(req: dict) -> None:
    req["updated"] = now()
    path = folder(req["id"]) / "request.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(req, indent=2), encoding="utf-8")
    tmp.replace(path)


def all_requests() -> list[dict]:
    reqs = [json.loads(p.read_text(encoding="utf-8")) for p in DATA.glob("REQ-*/request.json")]
    return sorted(reqs, key=lambda r: r["id"], reverse=True)


def next_id() -> str:
    nums = [int(p.name[4:]) for p in DATA.glob("REQ-*") if p.name[4:].isdigit()]
    return f"REQ-{max(nums, default=0) + 1:04d}"


def find_record(req: dict, rec_id: str) -> dict:
    for rec in req["records"]:
        if rec["id"] == rec_id:
            return rec
    abort(404)


def status(req: dict) -> tuple[str, str]:
    """(what to show, which step to open). Worked out from the request, never stored, so it cannot go stale."""
    if req.get("signed_off_at"):
        return "Done", "signoff"
    if not req.get("plan"):
        return "Search plan needed", "plan"
    if not req.get("plan_approved_at"):
        return ("Waiting on the requester" if req.get("plan_choice") == "ask" else "Plan ready to check"), "plan"
    recs = req["records"]
    if not recs:
        est = req.get("estimate") or {}
        if est.get("sent_at") and not est.get("decision"):
            return "Estimate sent, waiting on the requester", "records"
        if est.get("decision") == "declined":
            return "Requester declined the estimate", "records"
        return "Waiting for Vault export", "records"
    if any(not r.get("checked_at") for r in recs):
        return "Redaction check needed", "records"
    pending = sum(1 for r in recs for s in r["suggestions"] if s["decision"] == "pending")
    if pending:
        return f"In review: {pending} to decide", "review"
    return "Ready to sign off", "signoff"


# ---------------------------------------------------------------------------------------------------------------------
# Models: which ones can answer right now
# ---------------------------------------------------------------------------------------------------------------------
_rig_seen = {"at": -1e9, "ok": False}


def rig_online() -> bool:
    base = os.environ.get("DISTRICT_LLM_BASE_URL", "").rstrip("/")
    if not base or not (os.environ.get("DISTRICT_LLM_TOKEN") or os.environ.get("LLM_TOKEN")):
        return False
    if time.monotonic() - _rig_seen["at"] < 60:
        return _rig_seen["ok"]
    try:  # same rule as the engine: never through an outbound proxy
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(re.sub(r"/v1$", "", base) + "/healthz", timeout=3) as resp:
            ok = resp.status == 200
    except Exception:
        ok = False
    _rig_seen.update(at=time.monotonic(), ok=ok)
    return ok


def model_status() -> dict[str, str | None]:
    """None means ready; otherwise the reason it is not."""
    return {
        "rig": None if rig_online() else "Not reachable. The rig answers only on the district network.",
        "claude": None if os.environ.get("ANTHROPIC_API_KEY") else "No API key. Start the app with ANTHROPIC_API_KEY set.",
    }


def provider(name: str):
    try:
        return ra.make_provider(name)
    except SystemExit as e:  # the engine exits with a message when a setting is missing
        raise RuntimeError(str(e)) from None


def complete(p, system: str, user: str, max_tokens: int = 2000, ledger: list | None = None, step: str = "", record: str | None = None) -> str:
    """One model call. When a ledger is given, the tokens the model reported for this call are priced and appended
    to it, so every dollar a request costs is on the request itself (request.json -> "usage")."""
    p.last_usage = None  # so a call that fails before the model answers cannot be billed as the previous call again
    try:
        return p.complete(system, user, max_tokens=max_tokens)
    except SystemExit as e:  # e.g. Claude declined
        raise RuntimeError(str(e)) from None
    finally:  # a call the model answered is billed even when the answer was a refusal
        u = getattr(p, "last_usage", None)
        if ledger is not None and u:
            ledger.append({"at": now(), "step": step, "model": p.name, "model_id": u["model_id"], "record": record,
                           "input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"],
                           "cost_usd": ra.cost_usd(u["model_id"], u["input_tokens"], u["output_tokens"])})


# ---------------------------------------------------------------------------------------------------------------------
# Cost: what the model calls have cost so far, and what the rest of the request is likely to cost
# ---------------------------------------------------------------------------------------------------------------------
def usage_totals(usage: list[dict]) -> dict:
    """Totals by step and by model, plus Claude's measured cost per record (chunks of one record count once)."""
    def bucket() -> dict:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}

    by_step, by_model = {}, {}
    claude_records = set()
    claude_redact_usd = 0.0
    for u in usage:
        for b in (by_step.setdefault(u["step"], bucket()), by_model.setdefault(u["model"], bucket())):
            b["calls"] += 1
            b["input_tokens"] += u["input_tokens"]
            b["output_tokens"] += u["output_tokens"]
            b["cost_usd"] += u["cost_usd"]
        if u["model"] == "claude" and u["step"] == "redact" and u.get("record"):
            claude_records.add(u["record"])
            claude_redact_usd += u["cost_usd"]
    return {"total_usd": sum(u["cost_usd"] for u in usage), "calls": len(usage), "by_step": by_step, "by_model": by_model,
            "input_tokens": sum(u["input_tokens"] for u in usage), "output_tokens": sum(u["output_tokens"] for u in usage),
            "claude_records": len(claude_records),
            "claude_usd_per_record": claude_redact_usd / len(claude_records) if claude_records else None}


def pages_for(text: str) -> int:
    """Pages this record takes in the release PDF: at least one, then one more per CHARS_PER_PAGE characters."""
    return max(1, -(-len(text) // CHARS_PER_PAGE))


def estimate(req: dict) -> dict | None:
    """From Vault's Count: what finishing this request will cost in model calls and what the $1 per page charge comes
    to. Uses this request's own measurements when it has any, otherwise the calibrated sample numbers; says which."""
    count = (req.get("vault") or {}).get("count")
    if not count:
        return None
    totals = usage_totals(req.get("usage") or [])
    records = req.get("records") or []
    m = MEASURED
    if totals["claude_usd_per_record"] is not None:
        per_record = totals["claude_usd_per_record"]
        remaining = max(0, count - totals["claude_records"])
        api_usd = totals["total_usd"] + remaining * per_record
        basis = f"${per_record:.3f} per email, measured on {totals['claude_records']} emails in this request"
    else:
        per_record = m["claude_usd_per_record"]
        api_usd = totals["total_usd"] + count * per_record + (0 if totals["by_step"].get("plan") else m["claude_plan_usd"])
        basis = (f"${per_record:.3f} per email, measured on the {m['records']} fictional sample emails on {m['measured_on']}; "
                 "the estimate switches to this request's own numbers as soon as its first emails are checked")
    if records:
        pages_per_record = sum(pages_for(r["text"]) for r in records) / len(records)
        basis += f". Pages: {pages_per_record:.1f} per email from the {len(records)} emails loaded so far"
    else:
        pages_per_record = m["pages_per_record"]
        basis += f". Pages: {pages_per_record:.1f} per email from the sample emails"
    pages = round(count * pages_per_record)
    page_usd = pages * PAGE_RATE_USD
    return {"count": count, "api_usd": api_usd, "api_so_far_usd": totals["total_usd"], "per_record_usd": per_record,
            "pages": pages, "pages_per_record": pages_per_record, "page_rate_usd": PAGE_RATE_USD, "page_usd": page_usd,
            "total_usd": api_usd + page_usd, "basis": basis}


# ---------------------------------------------------------------------------------------------------------------------
# Step 2: the search plan (the same calls cmd_query makes, returned instead of printed)
# ---------------------------------------------------------------------------------------------------------------------
def draft_plan(request_text: str, model: str, ledger: list | None = None) -> tuple[dict, list[dict]]:
    """The plan and the usage lines its model calls produced. Pass a ledger to keep the usage line of a call whose
    answer could not be read."""
    p = provider(model)
    ledger = [] if ledger is None else ledger
    user = f"Today is {date.today().isoformat()}. The public records request reads:\n\n{request_text.strip()}"
    raw = complete(p, ra.QUERY_SYSTEM, user, ledger=ledger, step="plan")
    try:
        plan = ra.parse_json(raw)
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError("The model did not return a plan the app could read. Try again.") from None
    for key in ("gmail_terms", "drive_terms"):
        plan[key] = ra.clean_terms(str(plan.get(key) or ""), request_text)
    plan["_model"], plan["_at"] = model, now()
    return plan, ledger


# ---------------------------------------------------------------------------------------------------------------------
# Step 6: the redaction check (the same calls cmd_redact makes, with each span's position kept for the review screen)
# ---------------------------------------------------------------------------------------------------------------------
def pattern_spans(text: str) -> list[dict]:
    """The engine's PATTERNS, run in its order on the original text; a later pattern never claims an earlier match."""
    found: list[dict] = []
    for category, rx in ra.PATTERNS:
        for m in rx.finditer(text):
            if not any(m.start() < f["end"] and f["start"] < m.end() for f in found):
                found.append({"start": m.start(), "end": m.end(), "category": category, "by": "pattern", "reason": "pattern match"})
    return found


_MASK = re.compile(r"\[REDACTED: [A-Z-]+\]")  # what the pattern pass leaves in the text the model reads


def locate(text: str, needle: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in re.finditer(re.escape(needle), text)]
    if not spans:
        # A model joins a sentence the record breaks across lines, and it quotes the pattern pass's placeholder where
        # the record has the real value. Match across the break, and across whatever the placeholder stands for.
        parts = [r"\s+".join(re.escape(w) for w in part.split()) for part in _MASK.split(needle)]
        while parts and not parts[0]:
            parts.pop(0)
        while parts and not parts[-1]:
            parts.pop()
        loose = r"\s*.{1,200}?\s*".join(parts)
        spans = [(m.start(), m.end()) for m in re.finditer(loose, text)] if loose else []
    return spans


def model_spans(model: str, masked: str, original: str, record: str | None = None, ledger: list | None = None) -> tuple[list[dict], list[str], list[dict]]:
    """Spans, reviewer notes, and the usage lines of every call made for this record. Pass a ledger to keep the
    usage lines of the calls that were made when a later one fails."""
    p = provider(model)
    size = ra.RIG_CHUNK_CHARS if model == "rig" else 60000
    found, notes = [], []
    ledger = [] if ledger is None else ledger
    for piece in ra.chunks(masked, size):
        raw = complete(p, ra.REDACT_SYSTEM, "Record text:\n\n" + piece, max_tokens=1500, ledger=ledger, step="redact", record=record)
        try:
            reply = ra.parse_json(raw)
        except (ValueError, json.JSONDecodeError):
            # Not a note: a part of the record nobody read must not count as checked.
            raise RuntimeError("the reply for part of this record was not readable (it may have been cut off). Check it again.") from None
        if reply.get("note"):
            notes.append(str(reply["note"]))
        for span in reply.get("spans") or []:
            text = str(span.get("text", "")).strip()
            category = str(span.get("category", "")).strip() or "FERPA"
            if category not in ra.EXEMPTIONS:
                category = "FERPA" if "student" in category.lower() else "PERSONNEL"
            where = locate(original, text) if len(text) >= 2 else []
            if not where and not _MASK.sub("", text).strip():
                continue  # only a placeholder: the pattern pass already holds that span
            if not where:
                notes.append(f"Proposed text that is not in the record (ignored): {text[:60]!r}")
            for start, end in where:
                found.append({"start": start, "end": end, "category": category, "by": model, "reason": str(span.get("reason", ""))})
    return found, notes, ledger


def merge(found: list[dict], previous: list[dict], text: str) -> list[dict]:
    """One suggestion per exact span, listing every pass that found it. A re-check only ever adds: every earlier
    suggestion stays with its decision, whether or not this run found it again. A model's answer varies from run to
    run and a re-check may use a different model, so "not found this time" is never a reason to drop a redaction."""
    by_span: dict[tuple[int, int], dict] = {}
    for s in previous:  # earlier suggestions first, exactly as they were
        by_span[(s["start"], s["end"])] = {**s, "found_by": list(s["found_by"]), "reasons": dict(s.get("reasons") or {}),
                                           "categories": dict(s.get("categories") or {})}
    for f in found:
        s = by_span.setdefault((f["start"], f["end"]), {"start": f["start"], "end": f["end"], "found_by": [], "reasons": {}, "categories": {},
                                                        "decision": "pending"})
        if f["by"] not in s["found_by"]:
            s["found_by"].append(f["by"])
        s["reasons"][f["by"]], s["categories"][f["by"]] = f["reason"], f["category"]
    out = list(by_span.values())
    for s in out:
        c = s["categories"]
        s["category"] = c.get("pattern") or c.get("claude") or c.get("rig") or c.get("reviewer") or s.get("category")
    out.sort(key=lambda s: (s["start"], s["end"]))
    for i, s in enumerate(out, 1):
        s["id"], s["text"] = f"S{i:03d}", text[s["start"]:s["end"]]
    return out


def redacted(text: str, suggestions: list[dict]) -> str:
    merged: list[list] = []
    for s in sorted((s for s in suggestions if s["decision"] == "approve"), key=lambda s: s["start"]):
        if merged and s["start"] < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s["end"])
        else:
            merged.append([s["start"], s["end"], s["category"]])
    out, pos = [], 0
    for start, end, category in merged:
        out += [text[pos:start], f"[REDACTED: {category}]"]
        pos = end
    return "".join(out + [text[pos:]])


# ---------------------------------------------------------------------------------------------------------------------
# Step 4: reading what Technology exports (.mbox from Vault, .eml, or plain text)
# ---------------------------------------------------------------------------------------------------------------------
class _TextOnly(HTMLParser):
    BLOCKS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _TextOnly()
    parser.feed(markup)
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def message_record(msg) -> tuple[str, str]:
    """An email as the plain text the engine reads: the headers that matter, then the body."""
    head = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    body = ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, KeyError):  # an unknown charset
            body = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if part.get_content_type() == "text/html":
            body = html_to_text(body)
    attached = [p.get_filename() for p in msg.iter_attachments() if p.get_filename()]
    text = "\n".join(head) + "\n\n" + body.replace("\r\n", "\n").strip()
    if attached:
        text += "\n\n[Attachments not included: " + ", ".join(attached) + "]"
    return str(msg["Subject"] or "").strip(), text


def records_from_file(path: Path) -> list[tuple[str, str]]:
    ext = path.suffix.lower()
    if ext == ".mbox":
        box = mailbox.mbox(path, factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default), create=False)
        return [(subject or f"{path.stem}, message {i}", text) for i, (subject, text) in enumerate((message_record(m) for m in box), 1)]
    if ext == ".eml":
        subject, text = message_record(email.message_from_bytes(path.read_bytes(), policy=email.policy.default))
        return [(subject or path.stem, text)]
    return [(path.stem, path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n"))]


def add_records(req: dict, items: list[tuple[str, str]], source: str) -> None:
    n = max((int(r["id"][1:]) for r in req["records"]), default=0)  # never reuse the id of a removed record
    for name, text in items:
        if text.strip():
            n += 1
            req["records"].append({"id": f"R{n:02d}", "name": name[:120], "source": source, "text": text,
                                   "checked_at": None, "models": [], "suggestions": [], "notes": [], "errors": []})


# ---------------------------------------------------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------------------------------------------------
@app.context_processor
def page_globals():
    return {"models": MODELS, "model_status": model_status(), "status": status, "exemptions": ra.EXEMPTIONS,
            "allow_real": ALLOW_REAL_RECORDS, "usage_totals": usage_totals, "step_names": STEP_NAMES, "page_rate": PAGE_RATE_USD}


@app.template_filter("pages")
def pages_filter(text: str) -> int:
    return pages_for(text)


@app.template_filter("when")
def when(value: str | None) -> str:
    if not value:
        return ""
    d = datetime.fromisoformat(value)  # no %-d or %-I: those flags exist on macOS and Linux only and raise on Windows
    return f"{d:%b} {d.day}, {d.year} {d.hour % 12 or 12}:{d:%M %p}"


def frozen(req: dict) -> bool:
    """A signed-off request does not change: its release files and its log describe exactly what was signed.
    Reopen it first."""
    return bool(req.get("signed_off_at"))


def refuse_frozen(rid: str):
    flash("This request is signed off. Reopen it before changing its records.", "error")
    return redirect(url_for("signoff_page", rid=rid))


def fictional_ok() -> bool:
    if ALLOW_REAL_RECORDS or request.form.get("fictional") == "yes":
        return True
    flash("Please confirm the record is fictional. Real records stay out of this prototype until the district decides where record content may go.", "error")
    return False


@app.get("/")
def home():
    return render_template("home.html", requests=all_requests())


@app.get("/help")
def help_page():
    """Everything a new tester needs: what the tool is, the five steps and why, the models, and a test walkthrough."""
    return render_template("help.html")


@app.get("/new")
def new_request():
    return render_template("intake.html", sample=SAMPLE_REQUEST.read_text(encoding="utf-8"), today=date.today().isoformat())


@app.post("/new")
def create_request():
    text = request.form.get("request_text", "").strip()
    upload = request.files.get("request_file")
    if upload and upload.filename:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = DATA / f".upload-{os.getpid()}-{secure_filename(upload.filename)}"
        upload.save(tmp)
        try:
            text = "\n\n".join(t for _, t in records_from_file(tmp)).strip() or text
        finally:
            tmp.unlink(missing_ok=True)
    if not text:
        flash("Paste the request or upload it before continuing.", "error")
        return redirect(url_for("new_request"))
    if not fictional_ok():
        return redirect(url_for("new_request"))
    with _lock:
        rid = next_id()
        req = {"id": rid, "created": now(), "updated": now(), "request_text": text,
               "requester": {"name": request.form.get("requester_name", "").strip(), "contact": request.form.get("requester_contact", "").strip()},
               "received": request.form.get("received") or date.today().isoformat(),
               "plan": None, "plan_error": None, "plan_approved_at": None, "vault": {"count": None, "notes": ""},
               "records": [], "signed_off_at": None, "signed_off_by": None, "outputs": [], "usage": [], "estimate": None}
        save(req)
    return _draft_and_go(rid, request.form.get("model", "claude"))


def request_with_replies(req: dict) -> str:
    """The request as the model reads it: the requester's original words, then each answer they sent back."""
    text = req["request_text"]
    for r in req.get("replies") or []:
        text += f"\n\nThe requester's answer to our questions ({r['at'][:10]}):\n{r['text']}"
    return text


def _draft_and_go(rid: str, model: str):
    req = load(rid)
    ledger: list[dict] = []
    try:
        if model_status().get(model):
            raise RuntimeError(f"{MODELS.get(model, model)} is not available: {model_status()[model]}")
        (plan, _), error = draft_plan(request_with_replies(req), model, ledger), None
    except Exception as e:  # shown on the page; the request itself is kept
        plan, error = None, str(e)
    with _lock:
        req = load(rid)
        if plan:  # a new plan means asking the question again: is it clear enough to search?
            req["plan"], req["plan_approved_at"], req["plan_choice"] = plan, None, None
        req["plan_error"] = error
        req.setdefault("usage", []).extend(ledger)  # a call that was made costs money even if its answer was unreadable
        save(req)
    return redirect(url_for("plan_page", rid=rid))


@app.get("/r/<rid>")
def open_request(rid):
    return redirect(url_for(f"{status(load(rid))[1]}_page", rid=rid))


@app.get("/r/<rid>/plan")
def plan_page(rid):
    return render_template("plan.html", req=load(rid), step="plan")


@app.post("/r/<rid>/plan/draft")
def plan_redraft(rid):
    load(rid)
    return _draft_and_go(rid, request.form.get("model", "claude"))


@app.post("/r/<rid>/plan/choose")
def plan_choose(rid):
    """Step 2's question: is the request clear enough to search (show the Vault search), or ask the requester first
    (show the email)? A person answers it; the model only suggests."""
    choice = request.form.get("choice")
    with _lock:
        req = load(rid)
        if req.get("plan") and choice in ("search", "ask"):
            req["plan_choice"] = choice
            if choice == "ask":
                req["plan_approved_at"] = None  # a plan waiting on the requester is not an approved plan
            save(req)
    return redirect(url_for("plan_page", rid=rid, _anchor="decide"))


@app.post("/r/<rid>/plan/reply")
def plan_reply(rid):
    """The requester answered our questions: keep their words with the request and draft the plan again with them."""
    text = request.form.get("reply", "").strip()
    if not text:
        flash("Paste the requester's answer first.", "error")
        return redirect(url_for("plan_page", rid=rid, _anchor="decide"))
    with _lock:
        req = load(rid)
        replies = req.setdefault("replies", [])
        if not replies or replies[-1]["text"] != text:  # pasting it again after a failed draft must not keep it twice
            replies.append({"at": now(), "text": text})
        save(req)
    return _draft_and_go(rid, request.form.get("model", "claude"))


@app.post("/r/<rid>/plan/approve")
def plan_approve(rid):
    with _lock:
        req = load(rid)
        if req.get("plan"):
            req["plan_approved_at"], req["plan_choice"] = now(), "search"
            save(req)
    return redirect(url_for("records_page", rid=rid))


@app.get("/r/<rid>/records")
def records_page(rid):
    req = load(rid)
    return render_template("records.html", req=req, step="records", est=estimate(req))


@app.post("/r/<rid>/estimate/sent")
def estimate_sent(rid):
    """The estimate email went to the requester: keep the numbers that were quoted, so a later re-estimate cannot
    quietly change what they were told."""
    with _lock:
        req = load(rid)
        est = estimate(req)
        if est:
            req["estimate"] = {"sent_at": now(), "quoted": est, "decision": None, "decided_at": None}
            save(req)
    if est:
        flash("Marked as sent. The numbers quoted are kept with the request.", "ok")
    else:
        flash("Nothing was marked: save the Vault count first, so there is an estimate to quote.", "error")
    return redirect(url_for("records_page", rid=rid, _anchor="estimate"))


@app.post("/r/<rid>/estimate/decision")
def estimate_decision(rid):
    decision = request.form.get("decision")
    with _lock:
        req = load(rid)
        if req.get("estimate") and decision in ("proceed", "declined"):
            req["estimate"].update(decision=decision, decided_at=now())
            save(req)
        elif decision == "reset" and req.get("estimate"):
            req["estimate"] = None
            save(req)
    return redirect(url_for("records_page", rid=rid, _anchor="estimate"))


@app.post("/r/<rid>/vault")
def save_vault(rid):
    with _lock:
        req = load(rid)
        count = request.form.get("count", "").replace(",", "").strip()
        req["vault"] = {"count": int(count) if count.isdigit() else None, "notes": request.form.get("notes", "").strip()}
        save(req)
    flash("Vault details saved.", "ok")
    return redirect(url_for("records_page", rid=rid))


@app.post("/r/<rid>/records/upload")
def upload_records(rid):
    if not fictional_ok():
        return redirect(url_for("records_page", rid=rid))
    if frozen(load(rid)):
        return refuse_frozen(rid)
    exports = folder(rid) / "exports"  # step 5: every export is kept, as received, beside the request
    exports.mkdir(parents=True, exist_ok=True)
    items: list[tuple[str, str]] = []
    try:
        for f in request.files.getlist("files"):
            if f and f.filename:
                if f.filename.lower().endswith(".pst"):
                    flash(f"{f.filename} is a PST (Outlook) export, which this app cannot read. Ask Technology to export the same Vault search again as .mbox.", "error")
                    return redirect(url_for("records_page", rid=rid))
                if not f.filename.lower().endswith(UPLOAD_TYPES):
                    # anything else would be read as text and reviewed as one garbled "record" (a Vault .zip, a PDF)
                    flash(f"{f.filename} was not added: this app reads .mbox, .eml and .txt. A Vault export arrives as a .zip; unzip it and upload the .mbox inside.", "error")
                    return redirect(url_for("records_page", rid=rid))
                path = exports / f"{datetime.now():%Y%m%d-%H%M%S}-{secure_filename(f.filename)}"
                f.save(path)
                items += records_from_file(path)
    except Exception as e:
        flash(f"Could not read that file: {e}", "error")
        return redirect(url_for("records_page", rid=rid))
    pasted = request.form.get("pasted", "").strip()
    if pasted:
        subject = re.search(r"^Subject:\s*(.+)$", pasted, re.M)
        items.append((subject.group(1).strip() if subject else "Pasted email", pasted.replace("\r\n", "\n")))
    with _lock:
        req = load(rid)
        if frozen(req):
            return refuse_frozen(rid)
        before = len(req["records"])
        add_records(req, items, "upload")
        save(req)
    added = len(req["records"]) - before
    flash(f"Added {added} record{'s' if added != 1 else ''}." if added else "Nothing to add: choose a file or paste an email.", "ok" if added else "error")
    return redirect(url_for("records_page", rid=rid))


@app.post("/r/<rid>/records/samples")
def add_samples(rid):
    with _lock:
        req = load(rid)
        if frozen(req):
            return refuse_frozen(rid)
        before = len(req["records"])
        add_records(req, [(p.stem.replace("-", " ").capitalize(), p.read_text(encoding="utf-8")) for p in SAMPLE_RECORDS], "sample")
        save(req)
    flash(f"Added {len(req['records']) - before} fictional sample emails.", "ok")
    return redirect(url_for("records_page", rid=rid))


@app.post("/r/<rid>/records/<rec_id>/delete")
def delete_record(rid, rec_id):
    with _lock:
        req = load(rid)
        if frozen(req):
            return refuse_frozen(rid)
        find_record(req, rec_id)
        req["records"] = [r for r in req["records"] if r["id"] != rec_id]
        save(req)
    return redirect(url_for("records_page", rid=rid))


@app.post("/api/r/<rid>/records/<rec_id>/check")
def check_record(rid, rec_id):
    req = load(rid)
    if frozen(req):
        return jsonify(error="This request is signed off; reopen it to check a record again."), 409
    rec = find_record(req, rec_id)
    ready = model_status()
    wanted = [m for m in (request.get_json(silent=True) or {}).get("models", []) if m in MODELS and ready[m] is None]
    if not wanted:
        return jsonify(error="No model is available to check this record."), 400
    original = rec["text"]
    masked, _ = ra.deterministic_pass(original)  # the model sees exactly what the command line sends it
    found, notes, errors, ledger = pattern_spans(original), [], [], []
    used = {m: [] for m in wanted}  # each model's usage lines, kept even when one of its calls fails
    with ThreadPoolExecutor(max_workers=len(wanted)) as pool:  # rig and Claude side by side
        futures = {m: pool.submit(model_spans, m, masked, original, rec_id, used[m]) for m in wanted}
        for m, future in futures.items():
            try:
                spans, said, _ = future.result()
                found += spans
                notes += [{"by": m, "text": n} for n in said]
            except Exception as e:
                errors.append(f"{MODELS[m]}: {e}")
            ledger += used[m]
    with _lock:
        req = load(rid)
        if frozen(req):
            return jsonify(error="This request is signed off; reopen it to check a record again."), 409
        rec = find_record(req, rec_id)
        rec["suggestions"] = merge(found, rec["suggestions"], original)
        # Checked means every model the reviewer chose read the whole record. One failure leaves it unchecked: what
        # the others found is kept, the error is shown, and sign-off stays closed until a check runs clean.
        rec.update(checked_at=None if errors else now(), models=wanted, notes=notes, errors=errors, rev=rec.get("rev", 0) + 1)
        req.setdefault("usage", []).extend(ledger)
        save(req)
    totals = usage_totals(req["usage"])
    return jsonify(id=rec_id, checked=bool(rec["checked_at"]), suggestions=len(rec["suggestions"]), errors=errors,
                   cost_usd=sum(u["cost_usd"] for u in ledger), total_usd=totals["total_usd"])


@app.get("/r/<rid>/review")
def review_page(rid):
    req = load(rid)
    checked = [r for r in req["records"] if r.get("checked_at")]
    if not checked:
        flash("Run the redaction check on at least one record first.", "error")
        return redirect(url_for("records_page", rid=rid))
    wanted = request.args.get("rec")
    undecided = lambda r: any(s["decision"] == "pending" for s in r["suggestions"])  # noqa: E731
    rec = next((r for r in checked if r["id"] == wanted), None) or next((r for r in checked if undecided(r)), checked[0])
    # the wizard's "next": the next email that still has something to decide, going round; sign off when none is left
    i = checked.index(rec)
    nxt = next((r for r in checked[i + 1:] + checked[:i] if undecided(r)), None)
    return render_template("review.html", req=req, rec=rec, checked=checked, step="review",
                           position=i + 1, nxt=nxt, prev=checked[i - 1] if i > 0 else None)


@app.post("/api/r/<rid>/records/<rec_id>/decisions")
def save_decisions(rid, rec_id):
    """The review screen sends its whole list back; check every field before keeping any of it."""
    body = request.get_json(silent=True) or {}
    incoming = body.get("suggestions", [])
    with _lock:
        req = load(rid)
        if req.get("signed_off_at"):
            return jsonify(error="This request is signed off; reopen it to change decisions."), 409
        rec = find_record(req, rec_id)
        # The screen sends its whole list, so a page opened before someone else's save would write their decisions
        # back to what it last saw. Each save and each check moves the record's rev on; an older page is refused.
        if "rev" in body and body["rev"] != rec.get("rev", 0):
            return jsonify(error="someone else changed this record while this page was open. Reload to see their decisions."), 409
        # matched by position, not id: ids are renumbered on every save and the screen may still hold old ones
        current = {(s["start"], s["end"]): s for s in rec["suggestions"]}
        seen = set()
        for s in incoming:
            try:
                key = (int(s.get("start")), int(s.get("end")))
            except (TypeError, ValueError):
                continue
            if key in current:
                if s.get("decision") in DECISIONS:
                    current[key]["decision"] = s["decision"]
                seen.add(key)
            elif str(s.get("id", "")).startswith("new") and 0 <= key[0] < key[1] <= len(rec["text"]) and s.get("category") in ra.EXEMPTIONS:
                category = s["category"]  # added by the reviewer from a text selection
                current[key] = {"start": key[0], "end": key[1], "found_by": ["reviewer"], "category": category,
                                "categories": {"reviewer": category}, "decision": "approve",
                                "reasons": {"reviewer": str(s.get("reason") or "Added by the reviewer")[:300]}}
                seen.add(key)
        # anything the models found stays in the list; only a reviewer's own addition can be removed
        kept = sorted((s for k, s in current.items() if k in seen or s["found_by"] != ["reviewer"]), key=lambda s: (s["start"], s["end"]))
        for i, s in enumerate(kept, 1):
            s["id"], s["text"] = f"S{i:03d}", rec["text"][s["start"]:s["end"]]
        rec["suggestions"] = kept
        rec["rev"] = rec.get("rev", 0) + 1
        save(req)
    return jsonify(suggestions=kept, rev=rec["rev"])


@app.get("/r/<rid>/signoff")
def signoff_page(rid):
    req = load(rid)
    return render_template("signoff.html", req=req, step="signoff", totals=usage_totals(req.get("usage") or []), est=estimate(req))


@app.post("/r/<rid>/signoff")
def sign_off(rid):
    reviewer = request.form.get("reviewer", "").strip()
    with _lock:
        req = load(rid)
        if frozen(req):
            return refuse_frozen(rid)
        pending = sum(1 for r in req["records"] for s in r["suggestions"] if s["decision"] == "pending")
        unchecked = [r for r in req["records"] if not r.get("checked_at")]
        if not reviewer or pending or unchecked or not req["records"]:
            flash("Sign-off needs a reviewer name, every record checked, and a decision on every suggestion.", "error")
            return redirect(url_for("signoff_page", rid=rid))
        req["signed_off_at"], req["signed_off_by"] = now(), reviewer
        req["outputs"] = write_outputs(req)
        save(req)
    flash(f"{rid} is signed off. The redacted records and the log are saved below.", "ok")
    return redirect(url_for("signoff_page", rid=rid))


@app.post("/r/<rid>/reopen")
def reopen(rid):
    with _lock:
        req = load(rid)
        req["signed_off_at"] = req["signed_off_by"] = None
        req["outputs"] = []  # the release files describe the request as it was signed; once it can change they are stale
        save(req)
    return redirect(url_for("review_page", rid=rid))


def write_outputs(req: dict) -> list[str]:
    """Step 8's paper trail: one redacted file per record for release, one combined PDF of all of them, a zip of
    everything that may be released, and one internal log that must not be released."""
    out = folder(req["id"]) / "release"
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for rec in req["records"]:
        # Never the record's name: it is the email's Subject line, which no redaction touches ("Suspension of <student>").
        name = f"{req['id']}-{rec['id']}-REDACTED.txt"
        (out / name).write_text(redacted(rec["text"], rec["suggestions"]), encoding="utf-8")
        names.append(name)
    pdf_name = f"{req['id']}-RELEASE-all-records-REDACTED.pdf"
    write_release_pdf(req, out / pdf_name)
    zip_name = f"{req['id']}-RELEASE.zip"
    with zipfile.ZipFile(out / zip_name, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names + [pdf_name]:
            z.write(out / name, name)
    log = [f"# {req['id']} redaction log (INTERNAL: do not release)", "",
           f"Signed off by {req['signed_off_by']} on {when(req['signed_off_at'])}. Search plan drafted by {MODELS.get((req.get('plan') or {}).get('_model', ''), 'n/a')}."
           + (f" Vault count: {req['vault']['count']:,} messages." if req["vault"].get("count") else ""), ""]
    for rec in req["records"]:
        log += [f"## {rec['id']} {rec['name']}", "", "| # | Decision | Category | Candidate exemption | Found by | Reason | Text |", "|---|---|---|---|---|---|---|"]
        for s in rec["suggestions"]:
            reason = "; ".join(f"{k}: {v}" for k, v in s["reasons"].items() if v).replace("|", "/")
            log.append(f"| {s['id']} | {s['decision']} | {s['category']} | {ra.EXEMPTIONS.get(s['category'], '')} | {', '.join(s['found_by'])} | {reason} | {s['text'].replace('|', '/')} |")
        log.append("")
    log += cost_lines(req)
    log_name = f"{req['id']}-redaction-log-INTERNAL.md"
    (out / log_name).write_text("\n".join(log), encoding="utf-8")
    # The zip first so it is the obvious thing to hand over; the internal log last and never inside the zip.
    return [zip_name, pdf_name] + names + [log_name]


def cost_lines(req: dict) -> list[str]:
    """The cost section of the internal log: every model call's real token counts, priced, and the page charge."""
    usage = req.get("usage") or []
    t = usage_totals(usage)
    pages = sum(pages_for(r["text"]) for r in req["records"])
    out = ["## Cost", "", f"Model calls: {t['calls']} ({t['input_tokens']:,} tokens in, {t['output_tokens']:,} out), ${t['total_usd']:.2f} in API fees.",
           f"Pages released: {pages} at ${PAGE_RATE_USD:.2f} per page = ${pages * PAGE_RATE_USD:.2f} (district policy; whether it is charged is the cabinet's call).",
           f"Total: ${t['total_usd'] + pages * PAGE_RATE_USD:.2f}.", ""]
    quoted = (req.get("estimate") or {}).get("quoted")
    if quoted:
        out += [f"Estimate sent to the requester on {when(req['estimate']['sent_at'])}: ${quoted['total_usd']:.2f} "
                f"(${quoted['api_usd']:.2f} API + ${quoted['page_usd']:.2f} for {quoted['pages']} pages, from a Vault count of {quoted['count']:,}). "
                f"Requester's answer: {req['estimate'].get('decision') or 'none recorded'}.", ""]
    out += ["| When | Step | Model | Record | Tokens in | Tokens out | Cost |", "|---|---|---|---|---|---|---|"]
    for u in usage:
        out.append(f"| {u['at']} | {STEP_NAMES.get(u['step'], u['step'])} | {u['model_id']} | {u.get('record') or ''} | {u['input_tokens']:,} | {u['output_tokens']:,} | ${u['cost_usd']:.4f} |")
    return out + [""]


def redacted_paragraphs(text: str, suggestions: list[dict], style: ParagraphStyle) -> list[Paragraph]:
    """The redacted text as PDF paragraphs: each approved span becomes a black bar with its category in white."""
    merged: list[list] = []
    for s in sorted((s for s in suggestions if s["decision"] == "approve"), key=lambda s: s["start"]):
        if merged and s["start"] < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], s["end"])
        else:
            merged.append([s["start"], s["end"], s["category"]])
    parts, pos = [], 0
    for start, end, category in merged:
        parts += [escape(text[pos:start]), f'<font backColor="black" color="white">&nbsp;REDACTED: {escape(category)}&nbsp;</font>']
        pos = end
    parts.append(escape(text[pos:]))
    # One paragraph per blank-line-separated block keeps email structure; single newlines become line breaks.
    blocks = re.split(r"\n\s*\n", "".join(parts))
    return [Paragraph(b.replace("\n", "<br/>"), style) for b in blocks if b.strip()]


def write_release_pdf(req: dict, path: Path) -> None:
    """One PDF for the requester: a cover sheet, then every record in order, redactions shown as black bars."""
    styles = getSampleStyleSheet()
    body = ParagraphStyle("record", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14)
    title = ParagraphStyle("cover", parent=styles["Title"], alignment=TA_CENTER)
    head = ParagraphStyle("head", parent=styles["Heading2"], spaceBefore=0)
    meta = ParagraphStyle("meta", parent=body, textColor="#555555", fontSize=9)
    records = req["records"]
    approved = sum(1 for r in records for s in r["suggestions"] if s["decision"] == "approve")

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(inch, 0.6 * inch, f"{req['id']} · Redacted for release")
        canvas.drawRightString(letter[0] - inch, 0.6 * inch, f"Page {doc.page}")
        canvas.restoreState()

    story = [Spacer(1, 2 * inch), Paragraph(f"Public records request {escape(req['id'])}", title),
             Paragraph("Responsive records, redacted for release", ParagraphStyle("sub", parent=body, alignment=TA_CENTER, fontSize=12)),
             Spacer(1, 0.5 * inch),
             Paragraph(f"Records: {len(records)}<br/>Redactions: {approved}<br/>Reviewed and signed off by {escape(req['signed_off_by'])} on {escape(when(req['signed_off_at']))}", ParagraphStyle("facts", parent=body, alignment=TA_CENTER)),
             Spacer(1, 0.3 * inch),
             Paragraph("Each black bar marks text withheld under the exemption named on it. The full list of exemptions applied is available on request.", ParagraphStyle("note", parent=meta, alignment=TA_CENTER)),
             PageBreak()]
    for i, rec in enumerate(records):
        # The heading carries the record's number only. Its name is the Subject line, unredacted; the Subject the
        # requester may see is the one in the body below, with the reviewer's redactions applied.
        story += [Paragraph(f"Record {i + 1} of {len(records)} ({escape(rec['id'])})", head),
                  Paragraph(f"{sum(1 for s in rec['suggestions'] if s['decision'] == 'approve')} redaction(s)", meta),
                  Spacer(1, 0.15 * inch)]
        story += redacted_paragraphs(rec["text"], rec["suggestions"], body)
        if i < len(records) - 1:
            story.append(PageBreak())
    SimpleDocTemplate(str(path), pagesize=letter, leftMargin=inch, rightMargin=inch, topMargin=inch, bottomMargin=inch,
                      title=f"{req['id']} redacted records", author="Records Assist").build(story, onFirstPage=footer, onLaterPages=footer)


@app.get("/r/<rid>/files/<name>")
def download(rid, name):
    if name not in load(rid).get("outputs", []):
        abort(404)
    return send_from_directory(folder(rid) / "release", name, as_attachment=True)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    print("Records Assist: open http://localhost:8000  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)
