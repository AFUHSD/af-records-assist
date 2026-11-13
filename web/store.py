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

A content file is never changed once written. Its name carries the hash of what is in it (R01.<hash>.json) and the
document names the file it means, so a save that fails part-way leaves the files the last good save pointed at
exactly as they were. Files no document points at are removed after the save has been kept.

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


def content_body(content: dict) -> str:
    return json.dumps(content, indent=2, sort_keys=True)


def content_hash(content: dict) -> str:
    return hashlib.sha256(content_body(content).encode("utf-8")).hexdigest()[:32]


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
        meta["content"] = content_hash(content)  # which content file this record means
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
    """Record content, one JSON file per record and per version of it: <data folder>/REQ-nnnn/records/Rnn.<hash>.json.
    A file is written once and never changed."""

    def __init__(self, root: Callable[[], Path]):
        self.root = root

    def _dir(self, rid: str) -> Path:
        return self.root() / rid / "records"

    def exists(self, rid: str) -> bool:
        return (self.root() / rid).exists()

    @staticmethod
    def _name(rec_id: str, digest: str | None) -> str:
        return f"{rec_id}.{digest}.json" if digest else f"{rec_id}.json"  # no hash: saved before files carried one

    def read(self, rid: str, doc: dict) -> dict[str, dict]:
        """{record id: its content} for the files this document points at. Unreadable, or not the bytes the name
        promises, is missing: join() brings the record back unchecked with an error."""
        out = {}
        for meta in doc.get("records") or []:
            digest = meta.get("content")
            try:
                raw = (self._dir(rid) / self._name(meta["id"], digest)).read_bytes()
                if digest and hashlib.sha256(raw).hexdigest()[:32] != digest:
                    continue
                out[meta["id"]] = json.loads(raw.decode("utf-8"))
            except (ValueError, OSError):
                continue
        return out

    def write(self, rid: str, contents: dict[str, dict]) -> list[Path]:
        """Write the files that are not there yet; return them, so a save that is not kept can take them back."""
        folder, made = self._dir(rid), []
        folder.mkdir(parents=True, exist_ok=True)
        for rec_id, content in contents.items():
            path = folder / self._name(rec_id, content_hash(content))
            if path.exists():
                continue
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(content_body(content).encode("utf-8"))
            tmp.replace(path)
            made.append(path)
        return made

    @staticmethod
    def discard(made: list[Path]) -> None:
        for p in made:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass  # an unused file no document points at; the next save's prune takes it

    def prune(self, rid: str, contents: dict[str, dict]) -> None:
        """Remove every content file the saved document does not point at. Called only after the save is kept; a
        file it cannot remove is tried again by the next save."""
        keep = {self._name(rec_id, content_hash(content)) for rec_id, content in contents.items()}
        for p in self._dir(rid).glob("R*.json"):
            if p.name not in keep:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


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
        return join(doc, self.content.read(rid, doc)) if content else doc

    def all(self) -> list[dict]:
        """Every request's document, newest first, without record content (the list of requests does not need it)."""
        docs = [json.loads(p.read_text(encoding="utf-8")) for p in self.root().glob("REQ-*/request.json")]
        return sorted(docs, key=lambda r: r["id"], reverse=True)

    def save(self, req: dict, events: list[Event] | None = None) -> None:
        doc, contents = split(req)
        made = self.content.write(req["id"], contents)
        try:
            path = self.root() / req["id"] / "request.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
            tmp.replace(path)
        except BaseException:
            self.content.discard(made)
            raise
        self.content.prune(req["id"], contents)
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
        return {**join(doc, self.content.read(rid, doc)), "_ver": doc["_ver"]} if content else doc

    def all(self) -> list[dict]:
        with self._connect() as c:
            rows = c.execute("SELECT Doc, RowVer FROM dbo.Requests ORDER BY RequestId DESC;").fetchall()
        return [self._doc(r) for r in rows]

    def save(self, req: dict, events: list[Event] | None = None) -> None:
        """One request at a time (a lock held by the connection, so it outlasts the commit), and in this order: check
        the version, write the new content files (new names, nothing existing is touched), then the row and its
        audit lines in one transaction. Only after the commit are the files nobody points at removed. If anything
        fails before the commit, the row is as it was and so is every file it points at."""
        doc, contents = split(req)
        body, rid = json.dumps(doc), req["id"]
        resource = "records-assist:" + rid
        with self._connect() as c:
            c.execute("DECLARE @r int; EXEC @r = sys.sp_getapplock @Resource = ?, @LockMode = 'Exclusive', @LockOwner = 'Session', "
                      "@LockTimeout = 15000; IF @r < 0 THROW 51001, N'Could not lock the request for saving.', 1;", resource)
            made, committing = [], False
            try:
                current = c.execute("SELECT RowVer FROM dbo.Requests WHERE RequestId = ?;", rid).fetchval()
                if req.get("_ver"):
                    if current is None or bytes(current).hex() != req["_ver"]:
                        raise Conflict(rid)
                elif current is not None or self.content.exists(rid):
                    # a new request whose number is already a row, or already a folder in the data folder (left by an
                    # earlier database): never write into somebody else's request
                    raise Conflict(rid)
                made = self.content.write(rid, contents)  # a row never points at words that are not there
                if req.get("_ver"):
                    row = c.execute("UPDATE dbo.Requests SET Doc = ?, UpdatedAt = SYSUTCDATETIME() OUTPUT inserted.RowVer WHERE RequestId = ?;", body, rid).fetchone()
                else:
                    row = c.execute("INSERT INTO dbo.Requests (RequestId, Doc) OUTPUT inserted.RowVer VALUES (?, ?);", rid, body).fetchone()
                for actor, action, detail in events or []:
                    c.execute("INSERT INTO dbo.AuditEvents (RequestId, Actor, Action, Detail) VALUES (?, ?, ?, ?);", rid, actor[:256], action[:40], json.dumps(detail or {}))
                committing = True
                c.commit()
                self.content.prune(rid, contents)  # still under the lock: no other save of this request is part-way
            except BaseException:
                if not committing:
                    # Not kept, so take the new files back. A commit that raised may still have been kept (a dropped
                    # connection does not say), so then the files stay: unused ones go at the next save.
                    self.content.discard(made)
                raise
            finally:
                try:
                    c.rollback()
                    c.execute("EXEC sys.sp_releaseapplock @Resource = ?, @LockOwner = 'Session';", resource)
                    c.commit()
                except self._pyodbc.Error:
                    pass  # the connection is gone, and the lock went with it
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
