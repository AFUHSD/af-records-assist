"""
Where a request is kept. Two halves, on purpose:

  the request document   who asked and their words, the search plan, staff notes, each record's status, every
                         redaction decision (where it sits, its category, who found it, what was decided), the
                         cost ledger. No record content.
  the record content     the words of each email: its subject, its text, what the models said about it, and any
                         error a check raised. One file per record under the data folder, beside the uploads and
                         the release files.

On one computer both halves are files (FileStore). Hosted, the document lives in SQL Server (SqlStore) and the
content stays in the data folder, so the database never holds a word of anyone's email and the folder holding the
email can be locked down on its own. The app sees one dictionary either way: load() joins the halves, save() splits
them.

The document remembers the SHA-256 of each record's text. A content file that is missing, or whose text is not the
text that was saved, brings the record back unchecked with an error on it: it can never sign off as "nothing to
redact".

The audit trail (who did what, when) is append-only in both: a .jsonl file per request, or dbo.AuditEvents. An
audit line names a record and a position in it. It never carries record text. A save and its audit lines are one
step: hosted, they commit together or not at all.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

RECORD_CONTENT = ("name", "text", "notes", "errors")  # the parts of a record that are, or may quote, its words
SUGGESTION_CONTENT = ("text", "reasons")  # ...and of a suggestion: the words covered, and what a model said about them
Event = tuple[str, str, dict]  # (actor, action, detail)


class Conflict(Exception):
    """Someone else saved this request after it was loaded. Nothing was written."""


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def split(req: dict) -> tuple[dict, dict[str, dict]]:
    """(the document with no record content, {record id: its content})."""
    doc = {k: v for k, v in req.items() if k not in ("records", "_ver")}
    doc["records"], contents = [], {}
    for rec in req.get("records") or []:
        content = {k: rec.get(k) for k in RECORD_CONTENT}
        content["suggestions"] = {f"{s['start']}-{s['end']}": {k: s.get(k) for k in SUGGESTION_CONTENT} for s in rec.get("suggestions") or []}
        meta = {k: v for k, v in rec.items() if k not in RECORD_CONTENT and k != "suggestions"}
        meta["suggestions"] = [{k: v for k, v in s.items() if k not in SUGGESTION_CONTENT} for s in rec.get("suggestions") or []]
        meta["chars"], meta["sha256"], meta["failed"] = len(rec.get("text") or ""), text_hash(rec.get("text")), len(rec.get("errors") or [])
        doc["records"].append(meta)
        contents[rec["id"]] = content
    return doc, contents


def join(doc: dict, contents: dict[str, dict]) -> dict:
    """The request as the app uses it."""
    req = dict(doc)
    req["records"] = []
    for meta in doc.get("records") or []:
        rec = dict(meta)
        content = contents.get(rec["id"])
        if content is None and "text" in rec:  # a request saved before the halves were separated
            req["records"].append(rec)
            continue
        problem = None
        if content is None:
            problem = "This record's text could not be read from the data folder."
        elif rec.get("sha256") and text_hash(content.get("text")) != rec["sha256"]:
            problem = "This record's text in the data folder is not the text that was checked. Remove the record and upload it again."
        if problem:  # never blank and checked: an empty record would sign off as nothing to redact
            content = {"name": rec["id"], "text": "", "notes": [], "errors": [], "suggestions": {}}
            rec["checked_at"] = None
        rec.update({k: content.get(k) for k in RECORD_CONTENT})
        rec["notes"], rec["errors"] = rec["notes"] or [], (rec["errors"] or []) + ([problem] if problem else [])
        words = content.get("suggestions") or {}
        size = len(rec["text"])
        rec["suggestions"] = [{**s, "text": rec["text"][s["start"]:s["end"]], "reasons": (words.get(f"{s['start']}-{s['end']}") or {}).get("reasons") or {}}
                              for s in meta.get("suggestions") or [] if s["end"] <= size]
        req["records"].append(rec)
    return req


class ContentFiles:
    """Record content, one JSON file per record: <data folder>/REQ-nnnn/records/Rnn.json."""

    def __init__(self, root: Callable[[], Path]):
        self.root = root

    def _dir(self, rid: str) -> Path:
        return self.root() / rid / "records"

    def exists(self, rid: str) -> bool:
        return (self.root() / rid).exists()

    def read(self, rid: str) -> dict[str, dict]:
        out = {}
        for p in self._dir(rid).glob("R*.json"):
            try:
                out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue  # unreadable is missing: join() brings the record back unchecked with an error
        return out

    def write(self, rid: str, contents: dict[str, dict]) -> None:
        folder = self._dir(rid)
        folder.mkdir(parents=True, exist_ok=True)
        for rec_id, content in contents.items():
            path, body = folder / f"{rec_id}.json", json.dumps(content, indent=2)
            if path.exists() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(body.encode("utf-8")).digest():
                continue
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)

    def prune(self, rid: str, keep: set[str]) -> None:
        """Remove the content of records that are no longer on the request."""
        for p in self._dir(rid).glob("R*.json"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)


class FileStore:
    """One computer, one process: the document is <data folder>/REQ-nnnn/request.json."""

    name = "files"

    def __init__(self, root: Callable[[], Path]):
        self.root, self.content = root, ContentFiles(root)

    def new_id(self) -> str:
        nums = [int(p.name[4:]) for p in self.root().glob("REQ-*") if p.name[4:].isdigit()]
        return f"REQ-{max(nums, default=0) + 1:04d}"

    def load(self, rid: str, content: bool = True) -> dict | None:
        path = self.root() / rid / "request.json"
        if not path.exists():
            return None
        doc = json.loads(path.read_text(encoding="utf-8"))
        return join(doc, self.content.read(rid)) if content else doc

    def all(self) -> list[dict]:
        """Every request's document, newest first, without record content (the list of requests does not need it)."""
        docs = [json.loads(p.read_text(encoding="utf-8")) for p in self.root().glob("REQ-*/request.json")]
        return sorted(docs, key=lambda r: r["id"], reverse=True)

    def save(self, req: dict, events: list[Event] | None = None) -> None:
        doc, contents = split(req)
        self.content.write(req["id"], contents)
        path = self.root() / req["id"] / "request.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        tmp.replace(path)
        self.content.prune(req["id"], set(contents))
        for actor, action, detail in events or []:
            self.audit(req["id"], actor, action, detail)

    def audit(self, rid: str | None, actor: str, action: str, detail: dict | None = None) -> None:
        folder = self.root() / (rid or "")
        folder.mkdir(parents=True, exist_ok=True)
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "request": rid, "actor": actor, "action": action, "detail": detail or {}}
        with open(folder / "audit.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")

    def audit_trail(self, rid: str) -> list[dict]:
        path = self.root() / rid / "audit.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []

    def role(self, email: str) -> str | None:
        """The access list on one computer is RECORDS_ALLOWED_USERS: comma-separated addresses, all reviewers."""
        allowed = {a.strip().lower() for a in os.environ.get("RECORDS_ALLOWED_USERS", "").split(",") if a.strip()}
        return "reviewer" if email.lower() in allowed else None

    def ping(self) -> str:
        self.root().mkdir(parents=True, exist_ok=True)
        if not os.access(self.root(), os.W_OK):
            raise OSError("data folder is not writable")
        return "storage: ok"


class SqlStore:
    """Hosted: the document is a row in dbo.Requests (deploy/sql/01-schema.sql). The app's identity can read and
    write rows and nothing else: no DDL, and no UPDATE or DELETE on the audit table.

    A save is one transaction holding a lock on that request's number (sp_getapplock), so two saves of the same
    request, from any number of processes, run one after the other. Inside it, in order: confirm the row is still
    the version that was loaded (if not, Conflict, and nothing at all has been written); write the content files;
    update the row; write the audit lines; remove the content of records taken off the request; commit. A save that
    loses never touches a content file. Request numbers come from a sequence, so two people starting a request at
    the same moment cannot be given the same one."""

    name = "sql"

    def __init__(self, connection_string: str, root: Callable[[], Path]):
        import pyodbc  # imported here so one computer needs neither the driver nor the package

        self._pyodbc, self._cs, self.content = pyodbc, connection_string, ContentFiles(root)

    @contextmanager
    def _connect(self):
        c = self._pyodbc.connect(self._cs, autocommit=False, timeout=15)
        try:
            yield c
        finally:
            c.close()  # uncommitted work is rolled back

    def new_id(self) -> str:
        with self._connect() as c:
            n = c.execute("SELECT NEXT VALUE FOR dbo.RequestNumber;").fetchval()
            c.commit()
        return f"REQ-{n:04d}"

    @staticmethod
    def _doc(row) -> dict:
        doc = json.loads(row.Doc)
        doc["_ver"] = bytes(row.RowVer).hex()
        return doc

    def load(self, rid: str, content: bool = True) -> dict | None:
        with self._connect() as c:
            row = c.execute("SELECT Doc, RowVer FROM dbo.Requests WHERE RequestId = ?;", rid).fetchone()
        if row is None:
            return None
        doc = self._doc(row)
        return {**join(doc, self.content.read(rid)), "_ver": doc["_ver"]} if content else doc

    def all(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT Doc, RowVer FROM dbo.Requests ORDER BY RequestId DESC;").fetchall()
        return [self._doc(r) for r in rows]

    def save(self, req: dict, events: list[Event] | None = None) -> None:
        doc, contents = split(req)
        body, rid = json.dumps(doc), req["id"]
        with self._connect() as c:
            c.execute("DECLARE @r int; EXEC @r = sys.sp_getapplock @Resource = ?, @LockMode = 'Exclusive', @LockOwner = 'Transaction', "
                      "@LockTimeout = 15000; IF @r < 0 THROW 51001, N'Could not lock the request for saving.', 1;", "records-assist:" + rid)
            current = c.execute("SELECT RowVer FROM dbo.Requests WHERE RequestId = ?;", rid).fetchval()
            if req.get("_ver"):
                if current is None or bytes(current).hex() != req["_ver"]:
                    raise Conflict(rid)
            elif current is not None or self.content.exists(rid):
                # a new request whose number is already a row, or already a folder in the data folder (left by an
                # earlier database): never write into somebody else's request
                raise Conflict(rid)
            self.content.write(rid, contents)  # a row never points at words that are not there
            if req.get("_ver"):
                row = c.execute("UPDATE dbo.Requests SET Doc = ?, UpdatedAt = SYSUTCDATETIME() OUTPUT inserted.RowVer WHERE RequestId = ?;", body, rid).fetchone()
            else:
                row = c.execute("INSERT INTO dbo.Requests (RequestId, Doc) OUTPUT inserted.RowVer VALUES (?, ?);", rid, body).fetchone()
            for actor, action, detail in events or []:
                c.execute("INSERT INTO dbo.AuditEvents (RequestId, Actor, Action, Detail) VALUES (?, ?, ?, ?);", rid, actor[:256], action[:40], json.dumps(detail or {}))
            self.content.prune(rid, set(contents))
            c.commit()
        req["_ver"] = bytes(row.RowVer).hex()

    def audit(self, rid: str | None, actor: str, action: str, detail: dict | None = None) -> None:
        """An audit line on its own, for things that change nothing (a download, a refusal at the door)."""
        with self._connect() as c:
            c.execute("INSERT INTO dbo.AuditEvents (RequestId, Actor, Action, Detail) VALUES (?, ?, ?, ?);", rid, actor[:256], action[:40], json.dumps(detail or {}))
            c.commit()

    def audit_trail(self, rid: str) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT At, Actor, Action, Detail FROM dbo.AuditEvents WHERE RequestId = ? ORDER BY EventId;", rid).fetchall()
        return [{"at": r.At.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds"), "request": rid, "actor": r.Actor, "action": r.Action,
                 "detail": json.loads(r.Detail or "{}")} for r in rows]

    def role(self, email: str) -> str | None:
        with self._connect() as c:
            return c.execute("SELECT Role FROM dbo.Access WHERE Email = ?;", email.lower()).fetchval()

    def database(self) -> str:
        with self._connect() as c:
            return c.execute("SELECT DB_NAME();").fetchval()

    def ping(self) -> str:
        self.database()
        return "storage: ok"


def make_store(root: Callable[[], Path]):
    """RECORDS_SQL (an ODBC connection string, Trusted_Connection, no password) selects SQL Server; otherwise files."""
    cs = os.environ.get("RECORDS_SQL", "").strip()
    if not cs:
        return FileStore(root)
    if "pwd=" in cs.lower().replace(" ", "") or "password=" in cs.lower().replace(" ", ""):
        raise SystemExit("RECORDS_SQL carries a password. This app signs in to SQL Server as its own Windows identity; remove it.")
    return SqlStore(cs, root)
