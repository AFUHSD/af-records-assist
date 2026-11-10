# Records Assist web app (prototype)

A browser front end on top of `records_assist.py`. The engine is **not changed**: this app imports its prompts,
its pattern pass, its exemption table and both providers (the rig and Claude) and calls them. When a prompt or a
pattern changes in `records_assist.py`, the web app uses the new one on its next start.

Fictional records only until the district decides where record content may go. Every search plan and every
redaction is a suggestion a person approves.

## Run it

From the repo root, after the setup in the main README (Python 3.11+, the `.venv`):

```
.venv/bin/pip install flask reportlab
.venv/bin/python web/app.py
```

Then open http://localhost:8000. Stop it with Ctrl+C.

Models are read from the environment, exactly as the command line reads them:

| Model | Needs | Notes |
|---|---|---|
| Rig | `DISTRICT_LLM_BASE_URL`, `DISTRICT_LLM_TOKEN` | Answers only on the district network (not over the VPN today). |
| Claude | `ANTHROPIC_API_KEY` | Never put the key in a file in this repo. |

The top bar shows which models are ready. A model that is offline is greyed out wherever you would choose it.

## The five steps

The app works as a wizard. Each step opens with one line saying what to do there, and a bar pinned to the bottom
shows Back, where you are ("Step 4 of 5 · Review · Email 2 of 6", plus what is left), and the one next thing to do.
Finishing a step moves you on: approving the plan opens Records, the redaction check opens the review, finishing an
email turns the bar green ("Press Enter for the next one"), and the last email leads to sign off. The step bar at the
top stays clickable, so you can always jump back.

| Step | Page | What happens |
|---|---|---|
| 1. Request | `/new` (`templates/intake.html`) | Paste the request or upload it (.eml or .txt). |
| 2. Search plan | `/r/<id>/plan` (`templates/plan.html`) | The AI drafts the Vault plan (`QUERY_SYSTEM`, `clean_terms`), then the page asks one question: is the request clear enough to build a good search? The AI suggests an answer; a person picks. **Yes** shows the Vault terms, dates and scope, with a handoff note for Technology, and a person approves the plan. **No** shows a ready-to-send email to the requester (Copy, or Open in Gmail) and a box for their reply; pasting the reply drafts the plan again with their answers (kept in `request.json` as `replies`). |
| 3. Records | `/r/<id>/records` (`templates/records.html`) | Record the Vault Count, upload the export (.mbox, .eml, .txt) or paste an email, then run the redaction check with the rig, Claude, or both. Uploads are kept as received in `out/requests/<id>/exports/`. |
| 4. Review | `/r/<id>/review` (`templates/review.html`, `static/review.js`) | Laid out like the usual redaction tools (Acrobat, FOIA and e-discovery review): one toolbar across the top with **Redact text** (a mode: while on, highlighting words in either pane redacts them as the chosen category; Esc turns it off), **Find a name or phrase → Redact all matches**. Below it, the original (every suggestion highlighted, still readable) and the redacted preview (gray bars for undecided suggestions, black for confirmed, plain text for kept) side by side, with **Redact all remaining** right under the preview where reading ends (only undecided ones; with a filter, only those shown), and the list of suggestions with who found each (fixed rules, rig, Claude, you) and why. Keys: J/K move, A redact, R keep, U undo. Saves as you go. |
| 5. Sign off | `/r/<id>/signoff` (`templates/signoff.html`) | Needs every record checked and every suggestion decided. Saves to `out/requests/<id>/release/`: one combined PDF of every redacted record (redactions as black bars), one redacted `.txt` per record, a zip of those for the requester, and an internal log of every decision that is never in the zip. |

## Help page

The round **?** button in the top bar, left of the model pills, opens `/help` (`templates/help.html`): what the
tool is, the ground rules, each of the five steps and why it is built that way, the two models with the benchmark
numbers, how to start the app, a start-to-finish test walkthrough, a checklist that remembers ticks in the tester's
browser, and what is not built yet. Hand a new tester that page first.

## How it builds on the engine

| From `records_assist.py` | Used for |
|---|---|
| `QUERY_SYSTEM`, `parse_json`, `clean_terms` | Drafting the search plan, with the same safe Terms shape |
| `deterministic_pass` | The exact text each model sees (patterns already masked), as on the command line |
| `PATTERNS` | Finding where each pattern match sits, so the review screen can highlight it |
| `REDACT_SYSTEM`, `EXEMPTIONS`, `chunks`, `RIG_CHUNK_CHARS` | The model's redaction pass and the category labels |
| `make_provider` (`Rig`, `Claude`) | Every model call |

Two pieces are repeated here rather than imported, because in the engine they live inside the functions that print
to Terminal: the dozen lines of `cmd_query` that build the request message, and the loop in `cmd_redact` that sends
each chunk and reads back the spans. If those move into their own functions in `records_assist.py`, this app can call
them instead.

Two things the web app does differently, both on purpose:

- **Positions, not replacement.** The command line replaces text as it goes; the web app keeps where each span sits so
  it can be highlighted, approved or rejected one by one. The release file applies only the approved ones.
- **Spans across a line break are found.** When a model returns a sentence that the record breaks across lines, the
  web app matches it across the break instead of dropping it.

## Data

Each request is `out/requests/REQ-nnnn/request.json`, with `exports/` (uploads as received) and `release/` (sign-off
outputs) beside it. Git ignores `out/`. Nothing leaves this computer except the calls to the model you choose.

## What the AI costs

Every model call keeps the token counts the model reported (`usage` on the Anthropic reply, `usage` on the rig's
OpenAI-style reply) and prices them from `PRICING` in `records_assist.py` (Anthropic list price; the rig is $0). The
lines live on the request itself (`request.json` -> `usage`: step, model, record, tokens in and out, dollars), so:

- the header of every step shows **AI cost so far** (hover for the breakdown by step);
- after the Vault Count is saved, the Records page shows a **cost estimate**: AI processing (this request's own measured
  cost per email once any email has been checked, otherwise the calibrated sample numbers in `MEASURED`) plus the
  $1-per-page policy charge, with the email that asks the requester to proceed, narrow or withdraw. "Mark as sent" keeps
  the quoted numbers; the requester's answer is recorded on the same card and shows in the request's status;
- the sign-off page shows the real total next to what was quoted, and the internal log lists every call.

`calibrate_costs.py` measures the sample numbers for real (seven Claude calls, about 20 cents) and prints the `MEASURED`
block to paste into `app.py`. Re-run it after a prompt or model change. The 2026-11-09 run: $0.0227 per email for the
redaction check, $0.0669 for a search plan, on claude-opus-5-5. Tests: `.venv/bin/python -m unittest tests/test_costs.py`.

## Not built yet

- Payment: how the requester pays (the estimate email has a placeholder), and a final invoice email at sign-off

- Vault API (search, count and export run by the app instead of by hand)
- Email notification on sign-off (needs the district mail relay, so it waits for hosting)
- Sign-in and access control (the app only listens on this computer for now)
- PDF burn-in of the release files, and PDF or attachment text extraction
- Hosting on a district server

## Show tips (product tour)

The **Show tips** switch in the top bar puts a numbered dot beside each control worth explaining on the current
screen. Hover a dot (or the control) for one sentence on what it does and what to do with it; click to pin it. A bar
at the bottom offers **Walk me through**, which rings the controls one at a time in the order you would use them.
The switch stays on until it is turned off, remembered per browser.

- `static/product-tour.js` is the shared overlay from the `product-tour` skill, copied in unchanged (copy, not link,
  so a change to the skill cannot silently break this app).
- `static/tips.js` holds the tips, loaded on every page by `base.html`. A tip belongs to a **screen key**, which each
  page puts on `<body data-screen>`. Pages whose controls depend on state carry the state in the key
  (`plan-none`, `plan-open`, `plan-search`, `plan-ask`, `plan-approved`; `records-empty`, `-check`, `-done`;
  `signoff-open`, `-done`), so a tip never points at a control the page is not showing.
- Controls are marked with `data-tip="token"` in the templates. **When a control changes, change its tip in the same
  edit; when a control goes, delete its entry.** A tip whose control is missing shows as "N tips need updating" in
  the bar and warns in the browser console.
- The overlay reads the house token names (`--ink`, `--hair`, `--sans`); the bottom of `style.css` points those at
  this app's tokens, lifts the tips bar above the wizard bar, and fixes one clash: the overlay's `--accent-ink` means
  accent-coloured text, while here it means white text on a blue button.

## Reviewer feedback

Every page loads the district Site Feedback layer (one `<script>` line at the bottom of `templates/base.html`,
project `af-records-assist`). A reviewer presses **Feedback**, clicks the part of the page they mean and types
what should change; comments collect at https://apps.aguafria.org/feedback. To silence it for one browser add
`?feedback=off` to the URL; to turn it off for everyone use the switch on the project's card on the board; to
remove it for good delete that script line.
