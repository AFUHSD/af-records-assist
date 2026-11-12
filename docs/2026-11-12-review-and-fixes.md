# Review of the web app, and the fixes on this branch (2026-11-12)

Written for Mike and for the agent working with him. It covers what two independent reviews found in the web app
at commit `69bf5a4`, what this branch (`luke/web-app-hardening`) changes, how to check each change yourself, and
what is still open. Every name in the examples and tests is invented.

## How to review this branch

1. Install and run both test files. All 29 should pass:

   ```
   .venv/bin/pip install -r requirements.txt
   .venv/bin/python -m unittest tests/test_costs.py tests/test_release_rules.py
   ```

2. To see that the new tests describe real defects, copy `tests/test_release_rules.py` onto `69bf5a4` and run it
   there. The 14 tests written first all fail on that commit (12 failures, 2 errors on Windows).
3. Read the diff of `web/app.py` and `web/static/review.js` next to the table below. Each row names the function.
4. **One fix has no automated test: the review screen waiting for a save before leaving the page.** Please try it
   in a browser: add a redaction and click "Continue to sign off" at once, then confirm the redaction is in the
   release files. Try it again with the app stopped, and confirm the page stays open and says the change is not
   saved.

Do not treat the reviewers as right because they are reviewers. If a row is wrong, say so on the pull request.

## What was wrong, and what changed

"Released" below means it reaches the requester.

| # | Defect at `69bf5a4` | Change on this branch | Test |
|---|---|---|---|
| 1 | **The Subject line was released unredacted.** A record's name is its email Subject. Sign-off wrote that name into each `.txt` file name in the zip and into each PDF heading, and no redaction touches the name. | `write_outputs`, `write_release_pdf`: file names and headings carry the record number only. The Subject the requester sees is the one in the body, with the reviewer's redactions applied. | `test_no_release_file_or_file_name_carries_a_redacted_name` |
| 2 | **"Check again" deleted redactions.** `merge()` kept a decision only when the new run returned the identical span. | `merge()`: a re-check only adds. Every earlier suggestion stays with its decision. | three `test_a_recheck_*` tests |
| 3 | **A cut-off model reply marked the record checked.** The failure became a note; with no pattern hits the record signed off untouched. One model of two failing also counted as checked, and the page listed both. | `model_spans` raises on an unreadable reply. `check_record` marks a record checked only when every chosen model read all of it. What the other model found is kept. | `test_a_cut_off_reply_*`, `test_a_refusal_*`, `test_a_failed_check_keeps_the_decisions_already_made` |
| 4 | **A signed-off request could still change**, and still said Done. Reopening left the old release downloadable. | `frozen()` guards upload, samples, delete, re-check and a second sign-off. `reopen` empties `outputs`. | `test_a_signed_off_request_refuses_*`, `test_reopening_withdraws_the_release_files` |
| 5 | **A redaction made just before leaving the review page was never saved.** Saves waited 300 ms; the "Continue" link and the Enter key did not wait for them. | `review.js`: `flush()` and `leave()`. Every link on the page waits for the server's answer and stays put if the save failed. | none; see step 4 above |
| 6 | **A second reviewer's open page undid the first reviewer's decisions**, because the screen sends its whole list. | Each record has a `rev`. `save_decisions` answers 409 to a save made from an older page. | `test_a_stale_page_cannot_undo_another_reviewers_decision` |
| 7 | **Any other web page open in the browser could post to the app**: sign off, reopen, delete, or start a paid model call. A page using DNS rebinding could read record text. | `same_site_only()` refuses a cross-site POST (it reads `Origin` and `Sec-Fetch-Site`). `TRUSTED_HOSTS` refuses unknown host names; set `RECORDS_TRUSTED_HOSTS` on a hosted copy. | `test_a_post_from_another_site_is_refused`, `test_a_request_for_another_host_name_is_refused` |
| 8 | **A model's span quoted around a masked value was dropped.** The model reads the text after the pattern pass, so it quotes `[REDACTED: PERSONNEL]` where the record has a phone number, and the quote could not be found. | `locate()` matches across the placeholder. | `test_a_span_quoted_around_a_masked_value_is_still_found` |
| 9 | **A `.zip` or PDF upload became one garbled "record".** | `upload_records` accepts `.mbox`, `.eml`, `.txt` and says what to do with a Vault `.zip`. | `test_a_file_the_app_cannot_read_is_refused_not_reviewed_as_text` |
| 10 | **Sign-off crashed on Windows.** `%-d` and `%-I` are macOS and Linux only. | `when()` formats the date without them. | `test_dates_format_on_every_platform` |
| 11 | **The feedback button could copy record text to the feedback board** (it sends up to 200 characters of whatever is clicked). | `base.html`: the widget loads only while the app is in fictional-records mode. | read the template |
| 12 | **Cost lines were lost** for a refusal and for a plan whose answer could not be read; a failed call could bill the previous call again. "Marked as sent" showed when nothing was saved. | `complete()` records usage in `finally` and clears `last_usage` first. `draft_plan` and `model_spans` take the ledger from the caller. `estimate_sent` tells the truth. | `test_a_refusal_is_billed_*`, `test_a_plan_call_whose_answer_is_unreadable_is_still_on_the_ledger` |

Also on this branch, because the repository is public: `REPO-MAP.md` no longer carries a personal folder path or
staff first names; the help page no longer links the demo; the docs no longer point at a launcher script that is
not in the repo; `NOTICE` says the district's name and logo are not covered by the MIT licence.

## What the reviews found to be sound

Record text and model output are shown with `textContent` or escaped by the templates, so an email cannot inject
markup. Request and record numbers are checked before any file path is built. Downloads are limited to the listed
outputs. The internal log is never in the zip. The release PDF is built from the redacted text, not from boxes
drawn over the original.

## Still open

These are not fixed here. The first is yours to design; the rest come with hosting and are being built separately.

1. **Attachments.** An email's attachments are listed by name and never reviewed or released, and nothing at
   sign-off says so. Nothing compares the number of emails loaded with Vault's Count either. A reviewer should
   have to see and acknowledge both before sign-off.
2. **The per-email cost estimate** still divides by distinct emails, so re-checks push it up.
3. **Sign-in and a named access list.** The reviewer's name is typed. On a host it comes from the sign-in, and
   every decision is recorded with who made it.
4. **Storage.** One JSON file per request works for one person on one computer. The hosted copy keeps requests,
   decisions and the audit trail in a database, and record text in a protected folder.
5. **Which model reads record content** is a district decision that has not been made. Do not change the default
   on the strength of the benchmark alone.

## If you change these files later

Keep these four rules; each has a test that will tell you if one breaks.

- A re-check never removes a suggestion.
- A record is checked only when every chosen model read all of it.
- Nothing derived from a record (its name, its Subject) appears in a release file name or heading.
- A signed-off request does not change until it is reopened, and reopening withdraws its release files.
