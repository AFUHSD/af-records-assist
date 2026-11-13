"""Hosting rules: record content stays out of the request document, every change leaves an audit line with no
record text in it, and behind the front door only a listed, signed-in person gets in.

Run:  .venv/bin/python -m unittest tests/test_hosting.py
The SQL Server tests run only when RECORDS_TEST_SQL holds a connection string for a scratch copy of the database
(deploy/sql/01-schema.sql applied); without it they are skipped and say so. Every name below is invented.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import app as web  # noqa: E402
import store as storage  # noqa: E402

TEXT = "From: a@aguafria.org\nSubject: Suspension of Jordan Vale\n\nJordan Vale was suspended on Tuesday."
WORDS = ("Jordan", "Vale", "Suspension", "suspended")
SECRET = "test-front-door-secret"


def sample(rid="REQ-0001"):
    req = {"id": rid, "created": web.now(), "updated": web.now(), "request_text": "All email about the band trip.",
           "requester": {"name": "Pat Lee", "contact": "pat@example.com"}, "received": "2026-11-01", "plan": {"_model": "claude"},
           "plan_error": None, "plan_approved_at": web.now(), "vault": {"count": None, "notes": ""}, "records": [],
           "signed_off_at": None, "signed_off_by": None, "outputs": [], "usage": [], "estimate": None}
    web.add_records(req, [("Suspension of Jordan Vale", TEXT)], "upload")
    found = [{"start": a, "end": b, "category": "FERPA", "by": "claude", "reason": "Jordan Vale is a student"} for a, b in web.locate(TEXT, "Jordan Vale")]
    req["records"][0]["suggestions"] = web.merge(found, [], TEXT)
    req["records"][0]["notes"] = [{"by": "claude", "text": "Jordan Vale appears twice"}]
    req["records"][0]["checked_at"] = web.now()
    return req


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        saved = (web.DATA, web.STORE, web.PLATFORM_AUTH)
        self.addCleanup(lambda: (setattr(web, "DATA", saved[0]), setattr(web, "STORE", saved[1]), setattr(web, "PLATFORM_AUTH", saved[2])))
        web.DATA = Path(self.tmp.name) / "requests"
        web.DATA.mkdir(parents=True)
        web.STORE = storage.FileStore(lambda: web.DATA)
        self.client = web.app.test_client()


def content_file(rec_id="R01"):
    """The one content file the saved document points at for this record."""
    found = list((web.DATA / "REQ-0001" / "records").glob(rec_id + ".*.json"))
    assert len(found) == 1, found
    return found[0]


class TwoHalves(Base):
    def test_the_request_document_holds_no_record_content(self):
        web.save(sample())
        doc = (web.DATA / "REQ-0001" / "request.json").read_text(encoding="utf-8")
        self.assertFalse([w for w in WORDS if w in doc], "record words found in the request document")
        self.assertIn("Pat Lee", doc)  # the requester and their words belong to the request, not to a record
        content = json.loads(content_file().read_text(encoding="utf-8"))
        self.assertEqual(content["text"], TEXT)

    def test_a_request_comes_back_exactly_as_it_was_saved(self):
        req = sample()
        web.save(req)
        back = web.load("REQ-0001")
        self.assertEqual(back["records"][0]["text"], TEXT)
        self.assertEqual(back["records"][0]["name"], "Suspension of Jordan Vale")
        self.assertEqual([(s["start"], s["end"], s["text"], s["reasons"]) for s in back["records"][0]["suggestions"]],
                         [(s["start"], s["end"], s["text"], s["reasons"]) for s in req["records"][0]["suggestions"]])
        self.assertEqual(back["records"][0]["notes"], req["records"][0]["notes"])

    def test_a_record_whose_content_file_is_gone_is_unchecked_not_blank(self):
        web.save(sample())
        content_file().unlink()
        rec = web.load("REQ-0001")["records"][0]
        self.assertIsNone(rec["checked_at"])
        self.assertTrue(rec["errors"])
        self.assertEqual(web.status(web.load("REQ-0001"))[0], "Redaction check needed")

    def test_a_content_file_that_was_altered_cannot_sign_off_as_nothing_to_redact(self):
        req = sample()
        req["records"][0]["suggestions"] = []  # checked, nothing found: the case where an emptied file would sail through
        web.save(req)
        path = content_file()
        for altered in ({"name": "R01", "text": "", "notes": [], "errors": [], "suggestions": {}},
                        {"name": "R01", "text": "Nothing to see here.", "notes": [], "errors": [], "suggestions": {}}):
            path.write_text(json.dumps(altered), encoding="utf-8")
            rec = web.load("REQ-0001")["records"][0]
            self.assertIsNone(rec["checked_at"])
            self.assertEqual(rec["text"], "")
            self.assertTrue(rec["errors"])
            self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Tester"})
            self.assertIsNone(web.load("REQ-0001")["signed_off_at"])
            self.assertEqual(self.client.post("/api/r/REQ-0001/records/R01/check", json={"models": ["claude"]}).status_code, 409)
        path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(web.load("REQ-0001")["records"][0]["checked_at"])

    def test_an_unreadable_record_is_never_saved_over_whatever_the_request_is_saved_for(self):
        """Lose or damage the content file, then do everything a reviewer could do to the request. The document's
        entry for that record (its file, hash, decisions, checked state) must not move, and the damaged file must
        still be there: saving an empty record over it would make the loss permanent and look intact."""
        req = sample()
        req["records"][0]["suggestions"][0]["decision"] = "approve"
        web.add_records(req, [("Second", "Second email.")], "upload")
        web.save(req)
        stored = lambda: json.loads((web.DATA / "REQ-0001" / "request.json").read_text(encoding="utf-8"))["records"][0]
        before, path = stored(), content_file()
        for damage in ("{ not json", json.dumps({"name": "R01", "text": "", "notes": [], "errors": [], "suggestions": {}})):
            path.write_text(damage, encoding="utf-8")
            self.client.post("/r/REQ-0001/vault", data={"count": "7", "notes": "seven"})
            self.client.post("/r/REQ-0001/records/upload", data={"pasted": "Subject: third" + chr(10) + chr(10) + "Third email.", "fictional": "yes"})
            self.client.post("/r/REQ-0001/records/R02/delete")
            self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"rev": before.get("rev", 0), "suggestions": []})
            self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Tester"})
            hasty = web.load("REQ-0001")  # and the worst a handler could do: treat the empty record as real and checked
            hasty["records"][0].update(checked_at=web.now(), suggestions=[], text="", rev=99)
            web.save(hasty)
            self.assertEqual(web.load("REQ-0001")["vault"]["count"], 7)  # the unrelated saves did happen
            self.assertEqual(stored(), before)
            self.assertEqual(path.read_text(encoding="utf-8"), damage)
            rec = web.load("REQ-0001")["records"][0]
            self.assertEqual((rec["checked_at"], rec["text"], bool(rec["errors"])), (None, "", True))
            self.assertIsNone(web.load("REQ-0001")["signed_off_at"])
        self.client.post("/r/REQ-0001/records/R01/delete")  # the one way out
        self.assertNotIn("R01", [r["id"] for r in web.load("REQ-0001")["records"]])
        self.assertFalse(path.exists())

    def test_a_removed_records_number_is_never_given_out_again(self):
        req = sample()
        web.add_records(req, [("Second", "Second email.")], "upload")
        web.save(req)
        self.client.post("/r/REQ-0001/records/R02/delete")
        self.client.post("/r/REQ-0001/records/upload", data={"pasted": "Subject: third" + chr(10) + chr(10) + "Third email.", "fictional": "yes"})
        self.assertEqual([r["id"] for r in web.load("REQ-0001")["records"]], ["R01", "R03"])

    def test_a_request_from_before_numbers_were_remembered_does_not_reuse_one_either(self):
        req = sample()
        web.add_records(req, [("Second", "Second email.")], "upload")
        req.pop("last_record")
        web.save(req)
        self.client.post("/r/REQ-0001/records/R02/delete")
        self.client.post("/r/REQ-0001/records/upload", data={"pasted": "Subject: third" + chr(10) + chr(10) + "Third email.", "fictional": "yes"})
        self.assertEqual([r["id"] for r in web.load("REQ-0001")["records"]], ["R01", "R03"])

    def test_a_save_that_fails_leaves_the_last_good_save_readable(self):
        """New content goes to a new file. If the document is never written, the old document still finds its own."""
        web.save(sample())
        kept = content_file().read_bytes()
        changed = web.load("REQ-0001")
        changed["records"][0]["notes"] = [{"by": "claude", "text": "a second opinion"}]
        changed["unsaveable"] = object()  # the document cannot be written, after the content file has been
        with self.assertRaises(TypeError):
            web.STORE.save(changed)
        self.assertEqual(content_file().read_bytes(), kept)
        rec = web.load("REQ-0001")["records"][0]
        self.assertEqual((rec["text"], rec["notes"], bool(rec["checked_at"])), (TEXT, sample()["records"][0]["notes"], True))

    def test_a_decision_that_does_not_say_which_version_it_saw_is_refused(self):
        web.save(sample())
        rec = web.load("REQ-0001")["records"][0]
        r = self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"suggestions": [{**s, "decision": "reject"} for s in rec["suggestions"]]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual({s["decision"] for s in web.load("REQ-0001")["records"][0]["suggestions"]}, {s["decision"] for s in rec["suggestions"]})

    def test_a_check_error_is_kept_with_the_content_not_in_the_document(self):
        req = sample()
        req["records"][0]["errors"] = ["Claude: could not read 'Jordan Vale was suspended'"]
        web.save(req)
        self.assertNotIn("Jordan", (web.DATA / "REQ-0001" / "request.json").read_text(encoding="utf-8"))
        self.assertEqual(web.load("REQ-0001")["records"][0]["errors"], req["records"][0]["errors"])

    def test_removing_a_record_removes_its_content_file(self):
        web.save(sample())
        self.client.post("/r/REQ-0001/records/R01/delete")
        self.assertEqual(list((web.DATA / "REQ-0001" / "records").glob("R01*")), [])

    def test_a_request_saved_before_the_halves_were_separated_still_opens(self):
        req = sample()
        (web.DATA / "REQ-0001").mkdir()
        (web.DATA / "REQ-0001" / "request.json").write_text(json.dumps(req), encoding="utf-8")
        self.assertEqual(web.load("REQ-0001")["records"][0]["text"], TEXT)
        self.assertEqual(self.client.get("/r/REQ-0001/records").status_code, 200)


class AuditTrail(Base):
    def test_every_decision_is_recorded_with_who_and_where_and_never_the_words(self):
        web.save(sample())
        req = web.load("REQ-0001")
        rec = req["records"][0]
        body = {"rev": rec.get("rev", 0), "suggestions": [{**s, "decision": "approve"} for s in rec["suggestions"]]}
        self.assertEqual(self.client.post("/api/r/REQ-0001/records/R01/decisions", json=body).status_code, 200)
        self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Tester"})
        self.client.post("/r/REQ-0001/reopen")
        trail = web.STORE.audit_trail("REQ-0001")
        self.assertEqual([e["action"] for e in trail], ["decisions", "signed-off", "reopened"])
        self.assertEqual({e["actor"] for e in trail}, {"local"})
        self.assertEqual([(c["from"], c["to"]) for c in trail[0]["detail"]["changes"]], [("pending", "approve"), ("pending", "approve")])
        self.assertIn("REQ-0001-RELEASE.zip", trail[1]["detail"]["files"])  # each release file's name and SHA-256
        self.assertEqual({len(h) for h in trail[1]["detail"]["files"].values()}, {64})
        raw = (web.DATA / "REQ-0001" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertFalse([w for w in WORDS if w in raw], "record words found in the audit trail")

    def test_a_sign_off_that_loses_cannot_touch_the_release_that_won(self):
        web.save(sample())
        rec = web.load("REQ-0001")["records"][0]
        self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"rev": rec.get("rev", 0), "suggestions": [{**s, "decision": "approve"} for s in rec["suggestions"]]})
        self.client.post("/r/REQ-0001/signoff", data={"reviewer": "First"})
        won = web.load("REQ-0001")
        before = {n: (web.release_folder(won) / n).read_bytes() for n in won["outputs"]}
        loser = dict(won, signed_off_by="Second", release="20990101-000000-abcdef")  # a second sign-off that will lose the save
        web.write_outputs(loser)
        self.assertEqual({n: (web.release_folder(won) / n).read_bytes() for n in won["outputs"]}, before)
        with self.client.get(f"/r/REQ-0001/files/{won['outputs'][0]}") as r:
            self.assertEqual(r.get_data(), before[won["outputs"][0]])

    def test_the_audited_model_name_is_one_of_ours_whatever_was_posted(self):
        web.save(sample())
        self.client.post("/r/REQ-0001/plan/draft", data={"model": "Jordan Vale was suspended"})
        raw = (web.DATA / "REQ-0001" / "audit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("Jordan", raw)
        self.assertIn("unknown", raw)

    def test_a_save_that_changes_nothing_writes_no_audit_line(self):
        web.save(sample())
        rec = web.load("REQ-0001")["records"][0]
        self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"rev": rec.get("rev", 0), "suggestions": rec["suggestions"]})
        self.assertEqual(web.STORE.audit_trail("REQ-0001"), [])


class FrontDoor(Base):
    def setUp(self):
        super().setUp()
        web.PLATFORM_AUTH = True
        saved = {k: os.environ.get(k) for k in ("FRONT_DOOR_SHARED_SECRET", "RECORDS_ALLOWED_USERS")}
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in saved.items()])
        os.environ["FRONT_DOOR_SHARED_SECRET"] = SECRET
        os.environ["RECORDS_ALLOWED_USERS"] = "clerk@aguafria.org"
        web.save(sample())

    def get(self, path="/", **headers):
        return self.client.get(path, headers=headers)

    def test_a_listed_person_with_the_secret_gets_in(self):
        self.assertEqual(self.get(**{web.SECRET_HEADER: SECRET, web.EMAIL_HEADER: "Clerk@aguafria.org"}).status_code, 200)

    def test_everything_else_is_refused(self):
        ok = {web.SECRET_HEADER: SECRET, web.EMAIL_HEADER: "clerk@aguafria.org"}
        for label, headers in {
            "no headers at all": {},
            "identity without the secret": {web.EMAIL_HEADER: "clerk@aguafria.org"},
            "wrong secret": {**ok, web.SECRET_HEADER: "guess"},
            "signed in but not on the list": {**ok, web.EMAIL_HEADER: "teacher@aguafria.org"},
            "outside the district": {**ok, web.EMAIL_HEADER: "clerk@example.com"},
            "two addresses in one header": {**ok, web.EMAIL_HEADER: "x@evil.example, clerk@aguafria.org"},
        }.items():
            for path in ("/", "/r/REQ-0001/review", "/r/REQ-0001/records"):
                self.assertEqual(self.get(path, **headers).status_code, 403, f"{label}: {path}")

    def test_a_refusal_is_recorded_without_the_address_that_was_asked_for(self):
        self.get("/Suspension-of-Jordan-Vale", **{web.SECRET_HEADER: SECRET, web.EMAIL_HEADER: "teacher@aguafria.org"})
        raw = (web.DATA / "audit.jsonl").read_text(encoding="utf-8")
        self.assertIn("teacher@aguafria.org", raw)
        self.assertFalse([w for w in WORDS if w in raw])

    def test_no_secret_configured_refuses_everyone(self):
        os.environ["FRONT_DOOR_SHARED_SECRET"] = ""
        self.assertEqual(self.get(**{web.SECRET_HEADER: "", web.EMAIL_HEADER: "clerk@aguafria.org"}).status_code, 403)

    def test_the_refusal_page_says_who_and_shows_no_request(self):
        r = self.get(**{web.SECRET_HEADER: SECRET, web.EMAIL_HEADER: "teacher@aguafria.org"})
        page = r.get_data(as_text=True)
        self.assertIn("teacher@aguafria.org", page)
        self.assertNotIn("REQ-0001", page)

    def test_the_signed_in_person_is_the_actor_and_the_reviewer(self):
        ok = {web.SECRET_HEADER: SECRET, web.EMAIL_HEADER: "clerk@aguafria.org"}
        rec = web.load("REQ-0001")["records"][0]
        body = {"rev": rec.get("rev", 0), "suggestions": [{**s, "decision": "approve"} for s in rec["suggestions"]]}
        self.client.post("/api/r/REQ-0001/records/R01/decisions", json=body, headers=ok)
        self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Somebody Else"}, headers=ok)
        self.assertEqual(web.load("REQ-0001")["signed_off_by"], "clerk@aguafria.org")
        self.assertEqual({e["actor"] for e in web.STORE.audit_trail("REQ-0001")}, {"clerk@aguafria.org"})

    def test_the_health_check_needs_no_sign_in_and_shows_nothing(self):
        r = self.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("REQ-", r.get_data(as_text=True))


class UnderAPath(Base):
    def test_links_and_redirects_stay_under_the_published_path(self):
        from werkzeug.test import Client
        web.save(sample())
        for path in ("/records/", "/"):  # whether or not the front door strips the prefix first
            client = Client(web.UnderPath(web.app.wsgi_app, "/records"))
            page = client.get(path).get_data(as_text=True)
            self.assertIn('href="/records/static/style.css"', page)
            self.assertIn("/records/r/REQ-0001", page)
        self.assertEqual(Client(web.UnderPath(web.app.wsgi_app, "/records")).get("/records/r/REQ-0001").headers["Location"], "/records/r/REQ-0001/review")


class TwoPeopleAtOnce(Base):
    def test_a_conflict_tells_the_person_and_writes_nothing(self):
        web.save(sample())

        class Busy(storage.FileStore):
            def save(self, req, events=None):
                raise storage.Conflict(req["id"])

        web.STORE = Busy(lambda: web.DATA)
        rec = web.load("REQ-0001")["records"][0]
        r = self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"rev": rec.get("rev", 0), "suggestions": [{**s, "decision": "approve"} for s in rec["suggestions"]]})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.client.post("/r/REQ-0001/vault", data={"count": "12"}).status_code, 302)
        web.STORE = storage.FileStore(lambda: web.DATA)
        self.assertIsNone(web.load("REQ-0001")["vault"]["count"])

    def test_the_release_scripts_are_pinned_and_the_test_schema_is_the_same_schema(self):
        sql = ROOT / "deploy" / "sql"
        live, test = (sql / "01-schema.sql").read_text(encoding="utf-8"), (sql / "01-schema.test.sql").read_text(encoding="utf-8")
        import re
        self.assertEqual(re.sub(r"RecordsAssist(?!Test)", "RecordsAssistTest", live), test, "01-schema.test.sql has drifted from 01-schema.sql")
        live, test = (sql / "02-app-identity.sql").read_text(encoding="utf-8"), (sql / "02-app-identity.test.sql").read_text(encoding="utf-8")
        self.assertEqual(re.sub(r"RecordsAssist(?!Test)", "RecordsAssistTest", live), test, "02-app-identity.test.sql has drifted from 02-app-identity.sql")
        for name, database in (("00-databases.sql", "master"), ("01-schema.sql", "RecordsAssist"), ("01-schema.test.sql", "RecordsAssistTest"),
                               ("02-app-identity.sql", "RecordsAssist"), ("02-app-identity.test.sql", "RecordsAssistTest")):
            text = (sql / name).read_text(encoding="utf-8")
            self.assertIn(f"USE [{database}];", text, name)
            self.assertIn(f"IF @db <> N'{database}'", text, f"{name}: no exact DB_NAME() guard")
            self.assertNotRegex(text, r"(?i)(password|pwd)\s*=", name)
            self.assertNotRegex(text, r"RAISERROR\([^)]*DB_NAME\(\)", f"{name}: RAISERROR cannot take a function call")

    def test_a_connection_string_with_a_password_is_refused(self):
        os.environ["RECORDS_SQL"] = "Driver={x};Server=s;Database=d;UID=u;PWD=p"
        self.addCleanup(os.environ.pop, "RECORDS_SQL", None)
        with self.assertRaises(SystemExit):
            storage.make_store(lambda: web.DATA)


@unittest.skipUnless(os.environ.get("RECORDS_TEST_SQL"), "RECORDS_TEST_SQL is not set: the SQL Server store was NOT tested in this run")
class SqlServer(unittest.TestCase):
    """The same promises, kept by SQL Server. Point RECORDS_TEST_SQL at a scratch copy, never at the live database:
    these tests add rows and leave them (the app's identity cannot delete an audit line, and neither can this)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = storage.SqlStore(os.environ["RECORDS_TEST_SQL"], lambda: Path(self.tmp.name))
        run_as = os.environ.get("RECORDS_TEST_SQL_AS", "").strip()
        if run_as:  # every connection becomes the app's own database user, so its grants are what is being tested
            plain = self.store._connect

            @contextmanager
            def as_the_app():
                with plain() as c:
                    c.execute("EXECUTE AS USER = ?;", run_as)
                    self.assertEqual(c.execute("SELECT USER_NAME();").fetchval(), run_as)
                    try:
                        yield c
                    finally:  # a pooled connection handed back while still impersonating is killed by the server
                        try:
                            c.rollback()
                            c.execute("REVERT;")
                            c.commit()
                        except self.store._pyodbc.Error:
                            pass

            self.store._connect = as_the_app
        if self.store.database() == "RecordsAssist":
            self.fail("RECORDS_TEST_SQL points at the live database")

    def test_numbers_are_unique_and_a_request_round_trips_without_content_in_the_row(self):
        a, b = self.store.new_id(), self.store.new_id()
        self.assertNotEqual(a, b)
        req = sample(a)
        self.store.save(req)
        back = self.store.load(a)
        self.assertEqual(back["records"][0]["text"], TEXT)
        row = json.dumps(self.store.load(a, content=False))
        self.assertFalse([w for w in WORDS if w in row])
        self.assertIn(a, [d["id"] for d in self.store.all()])

    def test_a_stale_save_is_refused_and_a_duplicate_number_is_refused(self):
        rid = self.store.new_id()
        self.store.save(sample(rid))
        mine, theirs = self.store.load(rid), self.store.load(rid)
        theirs["vault"]["count"] = 5
        self.store.save(theirs)
        mine["vault"]["count"] = 9
        with self.assertRaises(storage.Conflict):
            self.store.save(mine)
        self.assertEqual(self.store.load(rid)["vault"]["count"], 5)
        with self.assertRaises(storage.Conflict):
            self.store.save(sample(rid))

    def test_a_save_sql_server_rolls_back_leaves_the_row_and_its_content_as_they_were(self):
        rid = self.store.new_id()
        self.store.save(sample(rid))
        changed = self.store.load(rid)
        changed["records"][0]["text"] = "Different words entirely."
        changed["records"][0]["suggestions"] = []
        with self.assertRaises(TypeError):  # the audit line cannot be written, after the content file and the row have been
            self.store.save(changed, [("clerk@aguafria.org", None, {})])
        back = self.store.load(rid)
        self.assertEqual((back["records"][0]["text"], bool(back["records"][0]["checked_at"]), back["records"][0]["errors"]), (TEXT, True, []))
        self.assertEqual(self.store.audit_trail(rid), [])
        self.assertEqual(len(list((Path(self.tmp.name) / rid / "records").glob("R01*"))), 1)
        back["vault"]["count"] = 3  # and the request is not left locked
        self.store.save(back)

    def test_two_saves_at_the_same_moment_keep_exactly_one(self):
        """Two connections, the same loaded version, released together. One is kept; the other is told, and neither
        its row, its audit line nor its content file survives. Twenty rounds, because a race does not lose every time."""
        import threading

        rid = self.store.new_id()
        self.store.save(sample(rid))
        for round_ in range(20):
            copies = [self.store.load(rid), self.store.load(rid)]
            gate, results = threading.Barrier(2), [None, None]

            def go(i):
                copies[i]["records"][0]["notes"] = [{"by": "claude", "text": f"round {round_} writer {i}"}]
                gate.wait()
                try:
                    self.store.save(copies[i], [(f"writer{i}@aguafria.org", "race", {"round": round_})])
                    results[i] = "kept"
                except storage.Conflict:
                    results[i] = "refused"

            threads = [threading.Thread(target=go, args=(i,)) for i in (0, 1)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual(sorted(results), ["kept", "refused"], f"round {round_}")
            winner = results.index("kept")
            back = self.store.load(rid)
            self.assertEqual(back["records"][0]["notes"][0]["text"], f"round {round_} writer {winner}")
            self.assertEqual((back["records"][0]["text"], back["records"][0]["errors"]), (TEXT, []))
            self.assertEqual(len(list((Path(self.tmp.name) / rid / "records").glob("R01*"))), 1)
            mine = [e["actor"] for e in self.store.audit_trail(rid) if e["detail"].get("round") == round_]
            self.assertEqual(mine, [f"writer{winner}@aguafria.org"])

    def test_the_audit_trail_appends_and_cannot_be_changed(self):
        rid = self.store.new_id()
        self.store.audit(rid, "clerk@aguafria.org", "decisions", {"record": "R01", "changes": [{"start": 1, "end": 5, "from": "pending", "to": "approve"}]})
        trail = self.store.audit_trail(rid)
        self.assertEqual([(e["actor"], e["action"]) for e in trail], [("clerk@aguafria.org", "decisions")])
        with self.store._connect() as c:
            for statement in ("UPDATE dbo.AuditEvents SET Actor = 'x' WHERE RequestId = ?;", "DELETE FROM dbo.AuditEvents WHERE RequestId = ?;"):
                with self.assertRaises(self.store._pyodbc.Error):
                    c.execute(statement, rid)
                c.rollback()
        self.assertEqual(len(self.store.audit_trail(rid)), 1)

    def test_someone_not_on_the_access_list_has_no_role(self):
        self.assertIsNone(self.store.role("nobody-" + os.urandom(4).hex() + "@aguafria.org"))


if __name__ == "__main__":
    unittest.main()
