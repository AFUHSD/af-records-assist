# What we learned and decided, 2026-10-26

Notes from the first working session on the public records workflow (Director of Innovation, Director of
Technology, the records staff who carry the work, and a cabinet member). Written up for the people who will
build the next version. No requester, staff or student is named here on purpose.

## The problem, as the people doing the work described it

- The last large request covered about three years of email for several named staff. The Vault export came
  back as 21 unfiltered folders, every message those people ever sent or received. One folder took hours to
  review and held about one responsive email. Work stopped there.
- The flow today: a non-fillable PDF on the website (or an email) reaches the front office, passes to the
  records clerk, Technology runs the Google Vault export, then staff print, review, redact by hand, and scan.
- Policy allows a charge of $1 per page produced, paid before release. Nobody can remember it being
  charged. When an attorney does the redaction, that cost lands on the district.

## Decisions

1. **Two technical lifts, in this order: build the query well, then help with redaction.** Getting the
   query right is the bigger win (50 records to review instead of 2,000). Redaction help is second.
2. **The query step carries no student data.** The requester's words and the resulting Vault terms contain
   no record content, so a hosted frontier model (Claude, through the district's API credits) is fine there.
3. **Record content may need to stay in-district.** Technology raised the concern of putting exported email
   into an AI platform. The district trusts its enterprise agreements, but if there is any red line the
   redaction pass runs on the self-hosted model instead. This tool defaults to the self-hosted model for
   `redact` for that reason. The self-hosted model is less capable, so measure it.
4. **Benchmark both models on both steps** before choosing: the self-hosted model against Claude Opus, for
   query generation and for redaction. If the self-hosted model is good enough, use it; if the frontier
   model is dramatically better, spend the API credits. Owner: the Technology side of the project.
5. **Add an estimate step.** A cursory pass predicts the record or page count before any review starts, and
   the requester confirms the cost ("this looks like 80 to 130 pages, do you want to proceed?"). It gives
   requesters a reason to narrow ("every mention of my child" versus "this one day") and gives the district a
   reasonable barrier to nuisance requests. Vault's **Count** before Export is the raw number for this.
6. **Whether to enforce the $1 per page policy is a cabinet decision**, and the cabinet member in the room
   took it. The workflow should include the cost check either way.
7. **Vault access stays with Technology** (two people today). The Director of Innovation stays out of the
   Vault and out of records requests by design. The tool hands Technology either exact search parameters for
   the Vault UI or, later, a GAM command line to run; it does not hold Vault access itself.
8. **Vault is the e-discovery platform.** Technology's note that "AI won't integrate with e-discovery"
   refers to Vault's search UI. Vault exports (.pst, .mbox) can be parsed downstream, and the Peninsula
   recipe drives Vault through its API; whether we use the API, GAM, or the UI is the next question for
   Technology.
9. **Second-pass review is human in the loop.** The Director of Technology has a records request assistant
   (acknowledgment letter, clarification drafter, redaction flagger). Proposed use: this tool produces the
   narrowed, pre-redacted set; that assistant reviews the redacted output and flags what slipped through.
   If the first pass proves reliable over a few requests, the second pass can be dropped.
10. **Review UX: unredacted and redacted side by side**, every redaction marked loudly in the text so the
    reviewer scrolls both at the same pace. Measure false positives and false negatives over time.
11. **Test case: rerun the last large request** with this tool, using the exports already in hand, and
    compare the record count the reviewer would have faced.
12. **Reconvene in two weeks, 30 minutes, with Technology's Vault administrators in the room.**

## Why AI and not find-and-replace

A find-and-replace on the names in the request catches the people the request is about. It cannot catch the
unrelated student who happens to be mentioned in a responsive email. That is the case that makes a model
necessary for redaction, and the case the reviewer must still check by hand.

## Open questions

- Can Technology run Vault searches from a command line (GAM or the Vault API) so the tool's query can be
  executed as written? If not, the tool's output stays "the parameters to click".
- Which export formats will Technology produce (.pst, .mbox, PDF), and what does the ingest step need?
- Does the district enforce the per-page charge, and at what point is the estimate shown to the requester?

## What this changes in the tool

- `query` already produces one broad OR net plus the date range and says to run Count before Export; an
  `estimate` step that turns the Count into a page and cost estimate is the next subcommand.
- `redact` already marks every span loudly in the text and logs it; the side-by-side review UI is the next
  build on top of it.
- A benchmark harness that runs both providers on the same request and the same record is the first thing
  to add, so decision 4 can be made on evidence.
