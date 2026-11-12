"""Release rules: nothing a reviewer redacted, and nothing nobody read, can reach the requester.

Run:  .venv/bin/python -m unittest tests/test_release_rules.py
No model is called here; a fake provider stands in for Claude and the rig. Every name below is invented.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import records_assist as ra  # noqa: E402
import app as web  # noqa: E402

RECORD = "From: a@aguafria.org\nSubject: Suspension of Jordan Vale\n\nJordan Vale was suspended on Tuesday."


class Fake(ra.Provider):
    """Answers with a canned reply, or raises it when the reply is an exception."""

    def __init__(self, reply):
        super().__init__("claude")
        self.reply = reply

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        self.last_usage = {"model_id": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 500}
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class ReleaseRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = (web.DATA, web.provider, web.model_status)
        self.addCleanup(lambda: setattr(web, "DATA", self.saved[0]) or setattr(web, "provider", self.saved[1]) or setattr(web, "model_status", self.saved[2]))
        web.DATA = Path(self.tmp.name) / "requests"
        web.DATA.mkdir(parents=True)
        web.model_status = lambda: {"rig": "off", "claude": None}
        self.client = web.app.test_client()

    def request(self, rid="REQ-0001", records=(("Suspension of Jordan Vale", RECORD),)):
        req = {"id": rid, "created": web.now(), "updated": web.now(), "request_text": "r", "requester": {"name": "", "contact": ""},
               "received": "2026-11-01", "plan": {"_model": "claude"}, "plan_error": None, "plan_approved_at": web.now(),
               "vault": {"count": None, "notes": ""}, "records": [], "signed_off_at": None, "signed_off_by": None,
               "outputs": [], "usage": [], "estimate": None}
        web.add_records(req, list(records), "upload")
        web.save(req)
        return req

    def check(self, reply, rid="REQ-0001"):
        web.provider = lambda name: Fake(reply)
        return self.client.post(f"/api/r/{rid}/records/R01/check", json={"models": ["claude"]})

    def approve_all(self, rid="REQ-0001"):
        req = web.load(rid)
        for s in req["records"][0]["suggestions"]:
            s["decision"] = "approve"
        web.save(req)

    # --- a re-check only ever adds -----------------------------------------------------------------------------------
    def test_a_recheck_keeps_an_approved_span_the_model_does_not_find_again(self):
        first = web.merge([{"start": 10, "end": 20, "category": "FERPA", "by": "claude", "reason": "student"}], [], "x" * 40)
        first[0]["decision"] = "approve"
        again = web.merge([], first, "x" * 40)
        self.assertEqual([(s["start"], s["end"], s["decision"], s["found_by"]) for s in again], [(10, 20, "approve", ["claude"])])

    def test_a_recheck_keeps_rejected_and_undecided_spans_too(self):
        first = web.merge([{"start": 1, "end": 5, "category": "FERPA", "by": "claude", "reason": ""},
                           {"start": 10, "end": 20, "category": "MEDICAL", "by": "claude", "reason": ""}], [], "x" * 40)
        first[0]["decision"] = "reject"
        again = web.merge([{"start": 30, "end": 35, "category": "FERPA", "by": "rig", "reason": ""}], first, "x" * 40)
        self.assertEqual([(s["start"], s["decision"]) for s in again], [(1, "reject"), (10, "pending"), (30, "pending")])

    def test_a_span_found_by_a_second_model_lists_both_and_keeps_its_decision(self):
        first = web.merge([{"start": 10, "end": 20, "category": "FERPA", "by": "claude", "reason": "a"}], [], "x" * 40)
        first[0]["decision"] = "approve"
        again = web.merge([{"start": 10, "end": 20, "category": "MINOR", "by": "rig", "reason": "b"}], first, "x" * 40)
        self.assertEqual((again[0]["found_by"], again[0]["decision"], again[0]["category"]), (["claude", "rig"], "approve", "FERPA"))

    # --- checked means read ------------------------------------------------------------------------------------------
    def test_a_cut_off_reply_leaves_the_record_unchecked_and_sign_off_closed(self):
        self.request(records=(("Note", "Jordan Vale has an IEP."),))
        body = self.check('{"spans": [{"text": "Jordan Va').get_json()
        self.assertFalse(body["checked"])
        self.assertTrue(body["errors"])
        self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Tester"})
        req = web.load("REQ-0001")
        self.assertIsNone(req["signed_off_at"])
        self.assertEqual(len(req["usage"]), 1, "the call was made, so it is on the ledger")

    def test_a_refusal_is_billed_and_leaves_the_record_unchecked(self):
        self.request()
        body = self.check(SystemExit("claude: the model declined this request.")).get_json()
        self.assertFalse(body["checked"])
        self.assertEqual(len(web.load("REQ-0001")["usage"]), 1)

    def test_a_failed_check_keeps_the_decisions_already_made(self):
        self.request()
        self.check('{"spans": [{"text": "Jordan Vale", "category": "FERPA", "reason": "student"}]}')
        self.approve_all()
        self.check("not json at all")
        self.assertEqual([s["decision"] for s in web.load("REQ-0001")["records"][0]["suggestions"]], ["approve", "approve"])

    def test_a_span_quoted_around_a_masked_value_is_still_found(self):
        original = "Call Dana Whitlock at 602-555-0142 about her son."
        masked, _ = ra.deterministic_pass(original)
        self.assertEqual(web.locate(original, masked[5:-1]), [(5, len(original) - 1)])
        self.assertEqual(web.locate(original, "[REDACTED: PERSONNEL]"), [])

    # --- what the requester receives ---------------------------------------------------------------------------------
    def signed_off(self):
        self.request()
        self.check('{"spans": [{"text": "Jordan Vale", "category": "FERPA", "reason": "student"}]}')
        self.approve_all()
        self.client.post("/r/REQ-0001/signoff", data={"reviewer": "Tester"})
        return web.load("REQ-0001")

    def test_no_release_file_or_file_name_carries_a_redacted_name(self):
        req = self.signed_off()
        self.assertTrue(req["signed_off_at"])
        release = web.DATA / "REQ-0001" / "release"
        with zipfile.ZipFile(release / "REQ-0001-RELEASE.zip") as z:
            names = z.namelist()
            self.assertFalse([n for n in names if "Jordan" in n or "Vale" in n or "INTERNAL" in n], names)
            for n in names:
                if n.endswith(".txt"):
                    self.assertNotIn("Jordan Vale", z.read(n).decode("utf-8"))
        pdf = (release / "REQ-0001-RELEASE-all-records-REDACTED.pdf").read_bytes()
        self.assertNotIn(b"Jordan", pdf)  # the page streams are compressed, so this covers the metadata and outline
        try:
            from pypdf import PdfReader
        except ImportError:
            return
        self.assertNotIn("Jordan Vale", " ".join(page.extract_text() for page in PdfReader(release / "REQ-0001-RELEASE-all-records-REDACTED.pdf").pages))

    # --- signed off means frozen -------------------------------------------------------------------------------------
    def test_a_signed_off_request_refuses_every_change_to_its_records(self):
        self.signed_off()
        before = web.load("REQ-0001")
        self.client.post("/r/REQ-0001/records/upload", data={"pasted": "Subject: late\n\nNever checked", "fictional": "yes"})
        self.client.post("/r/REQ-0001/records/samples")
        self.client.post("/r/REQ-0001/records/R01/delete")
        self.assertEqual(self.check('{"spans": []}').status_code, 409)
        self.assertEqual(self.client.post("/api/r/REQ-0001/records/R01/decisions", json={"suggestions": []}).status_code, 409)
        after = web.load("REQ-0001")
        self.assertEqual(after["records"], before["records"])
        self.assertEqual(web.status(after)[0], "Done")

    def test_reopening_withdraws_the_release_files(self):
        req = self.signed_off()
        name = req["outputs"][0]
        with self.client.get(f"/r/REQ-0001/files/{name}") as response:
            self.assertEqual(response.status_code, 200)
        self.client.post("/r/REQ-0001/reopen")
        self.assertEqual(self.client.get(f"/r/REQ-0001/files/{name}").status_code, 404)

    # --- two reviewers -----------------------------------------------------------------------------------------------
    def test_a_stale_page_cannot_undo_another_reviewers_decision(self):
        self.request()
        self.check('{"spans": [{"text": "Jordan Vale", "category": "FERPA", "reason": "student"}]}')
        url = "/api/r/REQ-0001/records/R01/decisions"
        rec = web.load("REQ-0001")["records"][0]
        opened_at, stale = rec["rev"], [{**s} for s in rec["suggestions"]]  # both reviewers open the page here
        mine = [{**s, "decision": "approve"} for s in stale]
        self.assertEqual(self.client.post(url, json={"rev": opened_at, "suggestions": mine}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"rev": opened_at, "suggestions": stale}).status_code, 409)
        self.assertEqual([s["decision"] for s in web.load("REQ-0001")["records"][0]["suggestions"]], ["approve", "approve"])

    def test_a_plan_call_whose_answer_is_unreadable_is_still_on_the_ledger(self):
        self.request()
        web.provider = lambda name: Fake("not a plan")
        self.client.post("/r/REQ-0001/plan/draft", data={"model": "claude"})
        req = web.load("REQ-0001")
        self.assertTrue(req["plan_error"])
        self.assertEqual([u["step"] for u in req["usage"]], ["plan"])

    # --- who may ask -------------------------------------------------------------------------------------------------
    def test_a_post_from_another_site_is_refused(self):
        self.signed_off()
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            self.assertEqual(self.client.post("/r/REQ-0001/reopen", headers=headers).status_code, 403, headers)
        self.assertTrue(web.load("REQ-0001")["signed_off_at"])
        self.assertEqual(self.client.post("/r/REQ-0001/reopen", headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"}).status_code, 302)

    def test_a_request_for_another_host_name_is_refused(self):
        self.request()
        self.assertEqual(self.client.get("/", headers={"Host": "rebind.evil.example:8000"}).status_code, 400)
        self.assertEqual(self.client.get("/", headers={"Host": "127.0.0.1:8000"}).status_code, 200)

    # --- uploads -----------------------------------------------------------------------------------------------------
    def test_a_file_the_app_cannot_read_is_refused_not_reviewed_as_text(self):
        import io
        self.request()
        self.client.post("/r/REQ-0001/records/upload", data={"fictional": "yes", "files": (io.BytesIO(b"PK\x03\x04binary"), "export.zip")},
                         content_type="multipart/form-data")
        self.assertEqual(len(web.load("REQ-0001")["records"]), 1)

    def test_dates_format_on_every_platform(self):
        self.assertEqual(web.when("2026-11-02T09:05:00"), "Nov 2, 2026 9:05 AM")
        self.assertEqual(web.when("2026-11-12T00:30:00"), "Nov 12, 2026 12:30 AM")
        self.assertEqual(web.when("2026-11-12T15:00:00"), "Nov 12, 2026 3:00 PM")


if __name__ == "__main__":
    unittest.main()
