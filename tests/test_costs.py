"""Cost tally: every model call's real token counts are kept and priced, and the estimate is built from them.

Run:  .venv/bin/python -m unittest tests/test_costs.py
No model is called here; a fake provider stands in for Claude and the rig.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "web"))

import records_assist as ra  # noqa: E402


class FakeProvider(ra.Provider):
    """Answers with a canned reply and reports token counts the way the real providers do."""

    def __init__(self, name: str, reply: str, model_id: str, tokens=(1000, 500)):
        super().__init__(name)
        self.reply, self.model_id, self.tokens = reply, model_id, tokens

    def complete(self, system: str, user: str, max_tokens: int = 2000) -> str:
        self.last_usage = {"model_id": self.model_id, "input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}
        return self.reply


class EnginePricing(unittest.TestCase):
    def test_opus_price_is_list_price(self):
        # $4 per million in, $20 per million out (Anthropic list price, checked 2026-11-09)
        self.assertAlmostEqual(ra.cost_usd("claude-opus-5-5", 1_000_000, 0), 4.00)
        self.assertAlmostEqual(ra.cost_usd("claude-opus-5-5", 0, 1_000_000), 20.00)
        self.assertAlmostEqual(ra.cost_usd("claude-opus-5-5", 1000, 500), 0.004 + 0.010)

    def test_rig_is_free(self):
        self.assertEqual(ra.cost_usd(ra.RIG_MODEL, 50_000, 50_000), 0.0)

    def test_unknown_model_is_priced_at_the_dearest_known_rate_not_zero(self):
        # A new model id must never silently show as free; it is priced at the highest known rate until the table is updated.
        self.assertGreater(ra.cost_usd("claude-something-new", 1000, 1000), 0)

    def test_provider_starts_with_no_usage(self):
        self.assertIsNone(ra.Provider("x").last_usage)


class WebTally(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ANTHROPIC_API_KEY"] = "test-key-never-used"
        import app as web  # noqa: WPS433  imported after the env is set

        web.DATA = Path(self.tmp.name)
        self.web = web
        self.web.app.config["TESTING"] = True
        self.client = web.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_appends_a_priced_usage_line(self):
        ledger = []
        p = FakeProvider("claude", "{}", "claude-opus-5-5", (2000, 1000))
        out = self.web.complete(p, "sys", "user", ledger=ledger, step="plan")
        self.assertEqual(out, "{}")
        self.assertEqual(len(ledger), 1)
        line = ledger[0]
        self.assertEqual((line["step"], line["model"], line["model_id"]), ("plan", "claude", "claude-opus-5-5"))
        self.assertEqual((line["input_tokens"], line["output_tokens"]), (2000, 1000))
        self.assertAlmostEqual(line["cost_usd"], 0.008 + 0.020)
        self.assertIn("at", line)

    def test_usage_totals_groups_by_step_and_model(self):
        usage = [
            {"step": "plan", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 1000, "cost_usd": 0.024, "record": None},
            {"step": "redact", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.014, "record": "R001"},
            {"step": "redact", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.014, "record": "R002"},
            {"step": "redact", "model": "rig", "model_id": "llama3.1:8b", "input_tokens": 900, "output_tokens": 300, "cost_usd": 0.0, "record": "R001"},
            {"step": "redact", "model": "rig", "model_id": "llama3.1:8b", "input_tokens": 900, "output_tokens": 300, "cost_usd": 0.0, "record": "R001"},  # 2nd chunk
        ]
        t = self.web.usage_totals(usage)
        self.assertAlmostEqual(t["total_usd"], 0.052)
        self.assertEqual(t["calls"], 5)
        self.assertEqual(t["by_step"]["plan"]["calls"], 1)
        self.assertEqual(t["by_step"]["redact"]["calls"], 4)
        self.assertAlmostEqual(t["by_step"]["redact"]["cost_usd"], 0.028)
        self.assertEqual(t["by_model"]["claude"]["input_tokens"], 3000)
        self.assertEqual(t["by_model"]["rig"]["cost_usd"], 0.0)
        # Claude checked two distinct records for $0.028: $0.014 each. Chunks of one record count once.
        self.assertEqual(t["claude_records"], 2)
        self.assertAlmostEqual(t["claude_usd_per_record"], 0.014)

    def test_usage_totals_handles_old_requests_with_no_usage(self):
        t = self.web.usage_totals([])
        self.assertEqual(t["total_usd"], 0.0)
        self.assertIsNone(t["claude_usd_per_record"])

    def _req(self, count=None, usage=(), records=()):
        return {"id": "REQ-0001", "vault": {"count": count, "notes": ""}, "usage": list(usage),
                "records": [{"id": f"R{i:03d}", "text": "x" * n, "suggestions": [], "checked_at": None} for i, n in enumerate(records, 1)],
                "estimate": None}

    def test_no_estimate_without_a_vault_count(self):
        self.assertIsNone(self.web.estimate(self._req(count=None)))

    def test_estimate_falls_back_to_measured_defaults(self):
        est = self.web.estimate(self._req(count=100))
        m = self.web.MEASURED
        self.assertEqual(est["count"], 100)
        self.assertAlmostEqual(est["api_usd"], 100 * m["claude_usd_per_record"] + m["claude_plan_usd"])
        self.assertEqual(est["pages"], round(100 * m["pages_per_record"]))
        self.assertAlmostEqual(est["page_usd"], est["pages"] * self.web.PAGE_RATE_USD)
        self.assertAlmostEqual(est["total_usd"], est["api_usd"] + est["page_usd"])
        self.assertIn("fictional sample", est["basis"])
        self.assertIn(m["measured_on"], est["basis"])

    def test_estimate_prefers_this_requests_own_measurements(self):
        usage = [
            {"step": "plan", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.05, "record": None},
            {"step": "redact", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.10, "record": "R001"},
            {"step": "redact", "model": "claude", "model_id": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.20, "record": "R002"},
        ]
        # two records of 3200 and 6400 characters: 1 page and 2 pages, so 1.5 pages per record
        est = self.web.estimate(self._req(count=10, usage=usage, records=(3200, 6400)))
        # spent so far ($0.35) plus 8 more records at the measured $0.15 each
        self.assertAlmostEqual(est["api_usd"], 0.35 + 8 * 0.15)
        self.assertEqual(est["pages"], 15)
        self.assertIn("2 emails in this request", est["basis"])

    def test_pages_for_a_record(self):
        self.assertEqual(self.web.pages_for(""), 1)
        self.assertEqual(self.web.pages_for("x" * self.web.CHARS_PER_PAGE), 1)
        self.assertEqual(self.web.pages_for("x" * (self.web.CHARS_PER_PAGE + 1)), 2)

    def test_check_record_keeps_claude_usage_on_the_request(self):
        web = self.web
        web.DATA.mkdir(exist_ok=True)
        req = {"id": "REQ-0001", "created": "", "updated": "", "request_text": "r", "requester": {"name": "", "contact": ""},
               "received": "2026-11-09", "plan": {"summary": "s"}, "plan_error": None, "plan_approved_at": "x",
               "vault": {"count": None, "notes": ""}, "records": [], "signed_off_at": None, "signed_off_by": None, "outputs": []}
        web.add_records(req, [("One", "Hello Jordan Smith, your SAIS 1234567 is on file.")], "sample")
        web.save(req)
        fake = FakeProvider("claude", json.dumps({"spans": [{"text": "Jordan Smith", "category": "FERPA", "reason": "student"}]}), "claude-opus-5-5", (1200, 300))
        web.provider = lambda name: fake  # noqa: E731
        web.model_status = lambda: {"rig": "off", "claude": None}  # noqa: E731
        r = self.client.post("/api/r/REQ-0001/records/R01/check", json={"models": ["claude"]})
        self.assertEqual(r.status_code, 200, r.data)
        saved = web.load("REQ-0001")
        self.assertEqual(len(saved["usage"]), 1)
        self.assertEqual(saved["usage"][0]["record"], "R01")
        self.assertAlmostEqual(saved["usage"][0]["cost_usd"], 1200 * 4 / 1e6 + 300 * 20 / 1e6)
        # and the cost shows on the page
        page = self.client.get("/r/REQ-0001/records").get_data(as_text=True)
        self.assertIn("AI cost so far", page)
        self.assertIn("$0.01", page)

    def test_estimate_card_and_email_render_after_a_count(self):
        web = self.web
        web.DATA.mkdir(exist_ok=True)
        req = {"id": "REQ-0002", "created": "", "updated": "", "request_text": "r", "requester": {"name": "Pat Lee", "contact": "pat@example.com"},
               "received": "2026-11-09", "plan": {"summary": "s"}, "plan_error": None, "plan_approved_at": "x",
               "vault": {"count": None, "notes": ""}, "records": [], "signed_off_at": None, "signed_off_by": None, "outputs": []}
        web.save(req)
        self.client.post("/r/REQ-0002/vault", data={"count": "1,250", "notes": ""})
        page = self.client.get("/r/REQ-0002/records").get_data(as_text=True)
        self.assertIn("Cost estimate", page)
        self.assertIn("1,250", page)
        self.assertIn("Hello Pat Lee", page)
        self.assertIn("pat@example.com", page)
        # mark it sent, then record the requester's answer
        self.client.post("/r/REQ-0002/estimate/sent")
        saved = web.load("REQ-0002")
        self.assertIsNotNone(saved["estimate"]["sent_at"])
        self.assertEqual(saved["estimate"]["quoted"]["count"], 1250)
        self.assertEqual(web.status(saved)[0], "Estimate sent, waiting on the requester")
        self.client.post("/r/REQ-0002/estimate/decision", data={"decision": "proceed"})
        saved = web.load("REQ-0002")
        self.assertEqual(saved["estimate"]["decision"], "proceed")
        self.assertEqual(web.status(saved)[0], "Waiting for Vault export")


if __name__ == "__main__":
    unittest.main()
