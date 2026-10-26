#!/usr/bin/env python
"""
AFUHSD public-records assistant (prototype, 2026-10-26).

Two jobs, both ADVISORY: a named person reads every proposal before anything touches Vault or a release.

  query   <request.txt>   A requester's words in, a Google Vault search plan out: what is really being asked,
                          the Terms strings to paste into Vault (Gmail / Drive), scope, date range, the
                          questions to send back to the requester, and the exemptions likely to come up.
  redact  <export.txt>    Exported record text in, a proposed redaction out: a deterministic pass first
                          (SSNs, phones, non-district emails, dates of birth, ID-shaped numbers), then the
                          model's pass for what patterns cannot see (student names, health, discipline,
                          minors). Every span cites a category and a candidate exemption. Nothing is burned
                          in; the output is a proposal and a log for the reviewer.

Providers (--provider):
  rig     The district's self-hosted model (default). Student data stays in the building. Needs
          DISTRICT_LLM_BASE_URL (OpenAI-compatible /v1) and DISTRICT_LLM_TOKEN (or LLM_TOKEN) in the environment.
  claude  Anthropic Claude via the official SDK. Needs ANTHROPIC_API_KEY in the environment. Fine for the
          request text; for record content prefer the rig (district ruling 2026-10-23: preferred, not required).

The key and token are read from the environment only. This script never prints them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import urllib.request
from dataclasses import dataclass
from datetime import date

RIG_MODEL = os.environ.get("DISTRICT_LLM_MODEL", "llama3.1:8b")
CLAUDE_MODEL = "claude-opus-5-5"
RIG_CHUNK_CHARS = 6000  # 16k-token context on the rig; leave room for the prompt and the answer

# Candidate exemption labels. Labels, not legal advice: the reviewer confirms each with district counsel.
EXEMPTIONS = {
    "FERPA": "Education record of an identifiable student. FERPA, 20 U.S.C. 1232g; A.R.S. 15-141.",
    "MINOR": "Identity of a minor who is not the requester's child; privacy balancing under Arizona case law.",
    "SSN": "Social Security number. A.R.S. 44-1373 (restricted use of SSNs).",
    "MEDICAL": "Medical or health information. A.R.S. 12-2292; HIPAA where it applies.",
    "PERSONNEL": "Personal or private employee information whose release is not in the public interest (privacy balancing; home address, personal phone, dependents).",
    "PRIVILEGE": "Attorney-client communication or attorney work product.",
    "SECURITY": "Information that would compromise safety or security (building security details, credentials).",
    "DISCIPLINE-STUDENT": "Student discipline detail: an education record under FERPA.",
}

QUERY_SYSTEM = """You help a school district records officer in Arizona turn a public records request into a Google Vault search plan.
Arizona law (A.R.S. 39-121) asks for prompt production; there is no fixed day count. Student education records are protected by FERPA and A.R.S. 15-141.

Return ONLY a JSON object with these keys:
  summary: one plain sentence of what the requester wants.
  really_asking: one or two sentences on the underlying need, if it differs from the literal words.
  ambiguities: list of strings, each a point the request leaves open.
  questions_for_requester: list of strings, polite, each resolving one ambiguity. Empty if none.
  custodians: list of strings naming ROLES or offices likely to hold the records (not people, unless the request names them).
  date_range: object {start: "YYYY-MM-DD" or null, end: "YYYY-MM-DD" or null, basis: string}. Infer from the request; say so in basis.
  gmail_terms: string. Google Vault Gmail search terms, ready to paste into the Terms box. The ACCOUNTS are chosen in Vault's
      scope step, so do not write from: or to: unless an email address appears in the request itself. Write the subject matter as
      quoted phrases joined by OR inside parentheses, then after:YYYY/MM/DD before:YYYY/MM/DD. Allowed operators: subject:
      has:attachment filename: after: before:, quoted phrases, OR, parentheses, - to exclude. Never invent an address. Never use has: with a phrase.
  drive_terms: string. Google Vault Drive search terms, same idea: quoted phrases joined by OR in parentheses, then after: before:.
      Do not add type: or owner: unless the request narrows to one.
  scope: string. One of "specific accounts", "organizational unit", "all accounts", with the reason. Prefer the narrowest scope that answers the request.
  likely_exemptions: list of strings drawn from this set: """ + ", ".join(EXEMPTIONS) + """. Only those the records are likely to contain.
  notes: list of strings. Anything else the records officer should know before searching.
Never include names of students in any field unless they appear in the request itself. No prose outside the JSON."""

REDACT_SYSTEM = """You help a school district records officer in Arizona review a record before public release.
You receive the text of one record (an email or document). Some spans are already marked [REDACTED: ...] by a pattern pass; leave those alone.
Find the REMAINING spans that should be withheld, and only those. Categories, use these exact labels: """ + ", ".join(EXEMPTIONS) + """.
Typical finds: a student's name or initials tied to grades, attendance, discipline, health or special-education status; a minor's name;
a health condition; an employee's home address or personal circumstances; a line that is advice from counsel.
Do NOT flag: names of district employees acting in their official capacity, school names, public meeting content, or generic words.

Return ONLY a JSON object: {"spans": [{"text": "<exact substring as it appears>", "category": "<label>", "reason": "<one sentence>"}], "note": "<one sentence on anything the reviewer should look at by hand>"}
The text field must be copied exactly from the record so it can be located. If nothing should be withheld, return {"spans": [], "note": "..."}."""


# ---------------------------------------------------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Provider:
    name: str

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:  # pragma: no cover - dispatch
        raise NotImplementedError


class Rig(Provider):
    def __init__(self) -> None:
        super().__init__("rig")
        self.base = os.environ.get("DISTRICT_LLM_BASE_URL", "").rstrip("/")
        self.token = os.environ.get("DISTRICT_LLM_TOKEN") or os.environ.get("LLM_TOKEN")
        if not self.base or not self.token:
            sys.exit("rig: set DISTRICT_LLM_BASE_URL (an OpenAI-compatible /v1 endpoint) and DISTRICT_LLM_TOKEN. Nothing was sent.")
        # The rig is inside the district network; never go through an outbound proxy to reach it (400s otherwise).
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        body = {
            "model": RIG_MODEL,
            "stream": False,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        req = urllib.request.Request(
            self.base + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
            method="POST",
        )
        with self._opener.open(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


class Claude(Provider):
    def __init__(self) -> None:
        super().__init__("claude")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            sys.exit("claude: set ANTHROPIC_API_KEY (platform.claude.com -> Settings -> API keys). Nothing was sent.")
        import anthropic  # official SDK; imported here so the rig path has no dependency on it

        self._client = anthropic.Anthropic()

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        with self._client.messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens + 4000,  # adaptive thinking shares the budget with the answer
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            msg = stream.get_final_message()
        if msg.stop_reason == "refusal":
            sys.exit("claude: the model declined this request. Review the record by hand or try the rig.")
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def make_provider(name: str) -> Provider:
    return Claude() if name == "claude" else Rig()


def parse_json(raw: str) -> dict:
    """The models are asked for bare JSON; tolerate a code fence or a sentence around it."""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in the model's reply")
    return json.loads(m.group(0))


# ---------------------------------------------------------------------------------------------------------------------
# query: request text -> Vault search plan
# ---------------------------------------------------------------------------------------------------------------------
def cmd_query(args: argparse.Namespace) -> int:
    request = read_text(args.request)
    provider = make_provider(args.provider)
    user = f"Today is {date.today().isoformat()}. The public records request reads:\n\n{request.strip()}"
    raw = provider.complete(QUERY_SYSTEM, user)
    try:
        plan = parse_json(raw)
    except (ValueError, json.JSONDecodeError):
        print("The model did not return a parseable plan. Its reply, for the record:\n")
        print(raw)
        return 2
    for key in ("gmail_terms", "drive_terms"):
        plan[key] = clean_terms(str(plan.get(key) or ""), request)
    print(render_plan(plan, provider.name))
    return 0


_ADDRESS_OP = re.compile(r"\b(?:from|to|cc|bcc|owner):\s*\S+@\S+", re.I)
_HAS_PHRASE = re.compile(r"\bhas:\s*(\"[^\"]*\")", re.I)


def clean_terms(terms: str, request: str) -> str:
    """Small models invent addresses and misuse has:. Keep only addresses the requester wrote; accounts belong to Vault's scope step."""
    def keep(m: re.Match) -> str:
        addr = m.group(0).split(":", 1)[1].strip()
        return m.group(0) if addr.lower() in request.lower() else ""
    terms = _ADDRESS_OP.sub(keep, terms)
    terms = _HAS_PHRASE.sub(r"\1", terms)
    # A records search casts a wide net and a person narrows it: one OR group of phrases, then the dates. A small
    # model will AND the phrases or exclude a word that is the point of the request (seen: -"Flagstaff"), and an
    # over-narrow search silently loses responsive records, the worse error.
    dates = " ".join(re.findall(r"\b(?:after|before):\s*\d{4}/\d{2}/\d{2}", terms))
    body = re.sub(r"\b(?:after|before):\s*\d{4}/\d{2}/\d{2}", "", terms)
    phrases = re.findall(r"\"[^\"]+\"", body)
    rest = re.sub(r"\b\w+:", " ", re.sub(r"\"[^\"]+\"", " ", body))  # drop operators (subject:, type:) and the phrases
    bare = [w for w in re.findall(r"(?<![:\"-])\b[A-Za-z][\w'-]{2,}\b", rest) if w.upper() not in {"AND", "OR", "NOT"}]
    group = " OR ".join(dict.fromkeys(phrases + [f'"{w}"' for w in bare]))
    return (f"({group}) {dates}" if group else dates).strip()


def render_plan(p: dict, provider: str) -> str:
    dr = p.get("date_range") or {}
    out = [
        "# Vault search plan (PROPOSED; a records officer confirms every line)",
        "",
        f"Prepared by the {provider} model on {date.today().isoformat()}. Nothing has been searched.",
        "",
        "## What is being asked",
        f"- Summary: {p.get('summary', '')}",
        f"- Really asking: {p.get('really_asking', '')}",
        bullets("Ambiguities", p.get("ambiguities")),
        bullets("Questions to send the requester", p.get("questions_for_requester")),
        "",
        "## Where the records live",
        bullets("Likely custodians (roles)", p.get("custodians")),
        f"- Scope in Vault: {p.get('scope', '')}",
        f"- Date range: {dr.get('start') or 'open'} to {dr.get('end') or 'open'} ({dr.get('basis', '')})",
        "",
        "## Paste into Vault",
        "Vault -> Matters -> (new matter named for the request number) -> Search -> choose the service, scope and dates below,",
        "then paste the Terms line. Run Count before Export.",
        "",
        "Gmail terms:",
        "",
        "    " + (p.get("gmail_terms") or "(none proposed)"),
        "",
        "Drive terms:",
        "",
        "    " + (p.get("drive_terms") or "(none proposed)"),
        "",
        "## Before release",
        bullets("Exemptions likely to come up", [f"{e}: {EXEMPTIONS.get(e, 'see counsel')}" for e in p.get("likely_exemptions") or []]),
        bullets("Notes", p.get("notes")),
    ]
    return "\n".join(line for line in out if line is not None)


def bullets(title: str, items) -> str:
    items = [str(i) for i in (items or []) if str(i).strip()]
    if not items:
        return f"- {title}: none"
    return f"- {title}:\n" + "\n".join(f"    - {i}" for i in items)


# ---------------------------------------------------------------------------------------------------------------------
# redact: record text -> proposed redaction + log
# ---------------------------------------------------------------------------------------------------------------------
PATTERNS = [
    ("SSN", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("PERSONNEL", re.compile(r"(?<![\w.])(?:\+?1[ .-]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[ .-]\d{3}[ .-]\d{4}\b")),  # phone numbers
    ("PERSONNEL", re.compile(r"\b[\w.+-]+@(?!aguafria\.org\b)[\w-]+\.[\w.-]+\b")),  # non-district email addresses
    ("MINOR", re.compile(r"\b(?:DOB|date of birth|born)\s*[:\-]?\s*\d{1,2}/\d{1,2}/\d{2,4}\b", re.I)),
    ("FERPA", re.compile(r"\b(?:student|stu|SAIS)\s*(?:id|#|number|num)?\s*[:#]?\s*\d{5,9}\b", re.I)),  # ID-shaped, labelled
]


def deterministic_pass(text: str) -> tuple[str, list[dict]]:
    log: list[dict] = []
    for category, rx in PATTERNS:
        def sub(m: re.Match, category=category) -> str:
            log.append({"text": m.group(0), "category": category, "reason": "pattern match", "pass": "pattern"})
            return f"[REDACTED: {category}]"
        text = rx.sub(sub, text)
    return text, log


def chunks(text: str, size: int):
    if len(text) <= size:
        yield text
        return
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        cut = text.rfind("\n", start, end)
        if cut <= start:
            cut = end
        yield text[start:cut]
        start = cut


def cmd_redact(args: argparse.Namespace) -> int:
    original = read_text(args.record)
    provider = make_provider(args.provider)
    if provider.name == "claude":
        print("NOTE: record content is going to a hosted model. District preference is the rig for anything with student data.\n", file=sys.stderr)
    text, log = deterministic_pass(original)
    size = RIG_CHUNK_CHARS if provider.name == "rig" else 60000
    notes: list[str] = []
    for piece in chunks(text, size):
        raw = provider.complete(REDACT_SYSTEM, "Record text:\n\n" + piece, max_tokens=1500)
        try:
            found = parse_json(raw)
        except (ValueError, json.JSONDecodeError):
            notes.append("The model's reply for one chunk was not parseable; review that part by hand.")
            continue
        if found.get("note"):
            notes.append(str(found["note"]))
        for span in found.get("spans") or []:
            s = str(span.get("text", "")).strip()
            cat = str(span.get("category", "")).strip() or "FERPA"
            if cat not in EXEMPTIONS:
                cat = "FERPA" if "student" in cat.lower() else "PERSONNEL"
            if len(s) < 2 or s not in text:
                notes.append(f"Model proposed a span not found verbatim in the text (ignored): {s[:60]!r}")
                continue
            text = text.replace(s, f"[REDACTED: {cat}]")
            log.append({"text": s, "category": cat, "reason": str(span.get("reason", "")), "pass": provider.name})
    print(render_redaction(text, log, notes, provider.name))
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        print(f"\nProposed redacted text written to {args.out}", file=sys.stderr)
    return 0


def render_redaction(text: str, log: list[dict], notes: list[str], provider: str) -> str:
    out = [
        "# Proposed redaction (nothing is burned in; a reviewer applies each line or rejects it)",
        "",
        f"Pattern pass first, then the {provider} model. {len(log)} span(s) proposed.",
        "",
        "## Redaction log",
        "",
        "| # | Pass | Category | Candidate exemption | Reason | Text (reviewer eyes only) |",
        "|---|------|----------|---------------------|--------|---------------------------|",
    ]
    for i, e in enumerate(log, 1):
        out.append(f"| {i} | {e['pass']} | {e['category']} | {EXEMPTIONS.get(e['category'], '')} | {e['reason']} | {e['text']} |")
    if notes:
        out += ["", "## Reviewer notes", *[f"- {n}" for n in notes]]
    out += ["", "## Redacted text", "", text]
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------------------------------
def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=textwrap.dedent(__doc__), formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["rig", "claude"], default="rig", help="which model answers (default rig)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("query", help="request text -> Vault search plan")
    q.add_argument("request", help="file with the requester's words, or - for stdin")
    r = sub.add_parser("redact", help="record text -> proposed redaction and log")
    r.add_argument("record", help="file with the exported record text, or - for stdin")
    r.add_argument("--out", help="also write the proposed redacted text to this file")
    args = ap.parse_args(argv)
    return cmd_query(args) if args.cmd == "query" else cmd_redact(args)


if __name__ == "__main__":
    sys.exit(main())
