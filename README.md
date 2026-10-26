# af-records-assist

A small, honest prototype of the two AI-assisted steps in a public records (FOIA) workflow for a K-12 district,
built by Agua Fria Union High School District (Arizona) on 2026-10-26 and modelled on Peninsula School District's
records system, shared through the Agentic Builders cookbook (recipe `public-records-request-system`).

Both steps are **advisory**. A named person reads every proposal; the tool never touches Google Vault and never
burns a redaction in.

| Step | Command | What goes in | What comes out |
|---|---|---|---|
| Prepare the search | `query` | the requester's words | a Google Vault search plan: what is really being asked, Gmail and Drive Terms strings to paste into Vault, scope, date range, questions to send back, exemptions likely to come up |
| Prepare the redaction | `redact` | exported record text | a proposed redaction: deterministic pass (SSNs, phones, non-district emails, DOBs, labelled IDs) then the model's pass (student names, health, discipline, minors, counsel's advice), each span with a category and candidate exemption, plus a reviewer log |

## Run it

Python 3.11+. One optional dependency, the official Anthropic SDK, and only for `--provider claude`.

```
python -m venv .venv
.venv/Scripts/pip install anthropic        # or .venv/bin/pip on Linux and macOS
python records_assist.py query  samples/request.txt
python records_assist.py redact samples/email-export.txt
```

Providers (choose with `--provider`, before the subcommand):

| Provider | Environment | Notes |
|---|---|---|
| `rig` (default) | `DISTRICT_LLM_BASE_URL` (any OpenAI-compatible `/v1` endpoint), `DISTRICT_LLM_TOKEN`, optional `DISTRICT_LLM_MODEL` (default `llama3.1:8b`) | A self-hosted model keeps record content inside the district. Calls bypass any outbound proxy. |
| `claude` | `ANTHROPIC_API_KEY` | Claude Opus 5.5 through the official SDK, adaptive thinking, streamed. The tool prints a note when record content goes to a hosted model. |

Keys and tokens are read from the environment only and never printed. Set them in the shell, never in a file in this repo.

## Which model for which step

- **Request text** (the `query` step) holds no student records. Either provider is fine.
- **Record content** (the `redact` step) usually does. Our ruling (2026-10-23): the self-hosted model is
  preferred, not required, for anything that reads student records. The tool defaults to it.
- The deterministic pass runs first and the model second, with both providers. The model is a second check,
  never the only check.

## What the model is and is not trusted with

- The model proposes the **words** of a Vault search. The tool fixes the **shape**: one OR group of quoted phrases
  plus the date range, any address the requester did not write removed, no `AND`, no exclusions. A small model
  will happily exclude the very word the request is about; an over-narrow search silently loses responsive
  records, which is the worse error. Accounts are chosen in Vault's scope step, by a person.
- The model proposes redaction **spans**, copied verbatim from the record so they can be located. A span the
  tool cannot find verbatim is dropped and noted. Exemption labels are candidates for counsel, not determinations.
- Arizona context in the prompts: A.R.S. 39-121 (prompt production, no fixed day count), FERPA and A.R.S. 15-141
  for student records. Swap in your state's statute and taxonomy in `EXEMPTIONS` and the two system prompts.

## What it is not, yet

- No Vault API. The plan is pasted into Vault by a person. Peninsula wired matter, hold, export and ingest
  through a delegated service account; that is the next build.
- No PDF burn-in, no OCR, no review queue, no exemption-log table. The log here is markdown.
- The samples are fictional. Never put a real record in `samples/`; run the tool on a file outside the repo.

## Owners, and where to read next

Owned by Luke Allpress and Mike Riley (AFUHSD). Start here:

- **`docs/2026-10-26-decisions.md`**: what the records staff told us about the work, the twelve decisions from
  the first working session (query first, then redaction; benchmark the self-hosted model against Claude on
  both; an estimate step before review; Vault access stays with Technology), and the open questions.
- **The Agentic Builders cookbook**, the K-12 practitioner commons this came out of:
  https://lukeallpress.github.io/agentic-builders-commons/cookbook.html. The recipe to read first is
  Peninsula School District's **`public-records-request-system`** (Kris Hagel): the full pipeline this prototype
  takes its two steps from, with the reproduction prompt, the data map, and the lessons they learned building it
  as the cases came in. Read it end to end before extending this repo; the next pieces (Vault matter, hold,
  export and ingest; the review queue; redactions that must cite an exemption; installment releases) are
  all laid out there.
- **Peninsula's public code**: https://github.com/psd401. Their records system itself is not public, but their
  CI library and Claude Code plugin system are, and the recipe describes the records system's shape.
- **This repo's two prompts and the `EXEMPTIONS` table** in `records_assist.py` are the parts to tune first;
  the Arizona statute references live there and nowhere else.

## License

MIT. See `LICENSE`.
