# Hosting Records Assist

How the hosted copy differs from the one that runs on your own computer, what each setting does, and what has
and has not been proven. Nothing here names a server; the district's own runbook does.

## What changes when it is hosted

| | On your own computer | Hosted |
|---|---|---|
| Start it with | `python web/app.py` | `python run_waitress.py` (one process, loopback only) |
| Who gets in | whoever is at the keyboard | a signed-in district account that is on the access list |
| The request document | a file | a row in SQL Server (`dbo.Requests`) |
| Record content | files in `out/requests/` | files in a protected data folder (`RECORDS_DATA_DIR`) |
| Audit trail | `audit.jsonl` per request | `dbo.AuditEvents`, append-only |
| Request numbers | next free folder number | a database sequence |

## Two halves, kept apart

`web/store.py` splits every request in two when it saves and joins it when it loads.

- **The request document**: who asked and what for, the search plan, each record's status, every redaction
  decision (where it sits in the record, its category, who found it, what was decided), the cost ledger.
- **The record content**: each email's subject and text, the words each suggestion covers, what the models said.

The document goes to the database. The content goes to the data folder, with the uploads as received and the
release files. So the database never holds a word of anyone's email, and the people who can read the database and
the people who can read the folder do not have to be the same people. `tests/test_hosting.py` fails if a record's
words turn up in the document or in the audit trail.

What still goes to the database, on purpose: the requester's own words and their replies, the search plan built
from them, and anything staff type into the Vault notes box. None of that is record content, but it is free text.
Do not paste record text into notes.

A content file is written once and never changed. Its name carries a hash of what is in it
(`R01.<hash>.json`), and the document names the file it means and the SHA-256 of the record's text. A record whose
file is missing, unreadable, or not the bytes its name promises comes back empty and unchecked with an error on
it, and cannot be checked again; it has to be removed and uploaded again. It cannot sign off as "nothing to
redact".

An unreadable record is never saved over. Whatever the request is saved for afterwards, that record's entry in the
document goes back exactly as it was stored and its file is left in place, so a damaged file can still be
recovered by hand and the loss cannot be made to look like an intact empty record. Removing the record is the only
thing that changes it.

Hosted, a save holds a lock on that request and goes in this order: confirm nobody else saved first; write any new
content files under new names; update the row and write the audit lines in one transaction; commit; only then
remove the files no document points at. If anything fails before the commit, the row is as it was and every file
it points at is untouched. A save that loses writes nothing. Each sign-off writes its release files into a folder
of its own, so a sign-off that loses or is interrupted cannot touch the files a saved request points at.

## Sign-in

The app does not sign anybody in itself. The district's front door does, then passes each request on with the
person's address in a header and a shared secret beside it. `who_is_asking()` in `web/app.py`:

1. refuses any request without the right secret (and refuses everyone if no secret is configured);
2. refuses a header that is not exactly one district address;
3. refuses an address that is not on the access list, with a page that says so;
4. records the address as the actor on every audit line, and as the reviewer at sign-off (a typed name cannot
   sign for somebody else).

`/health` answers without sign-in and says only whether storage is reachable.

## Settings (environment variables; none belongs in a file in this repo)

| Setting | What it does |
|---|---|
| `RECORDS_PLATFORM_AUTH=1` | Believe the front door's identity header. `run_waitress.py` refuses to start without it. |
| `FRONT_DOOR_SHARED_SECRET` | Must equal what the front door sends. A secret: it lives in the host's configuration only. |
| `RECORDS_SQL` | ODBC connection string with `Trusted_Connection=yes`. A string carrying a password is refused. |
| `RECORDS_DATA_DIR` | The protected folder for record content, uploads and release files. |
| `RECORDS_PATH_BASE` | The path the app is published under, for example `/records`. |
| `RECORDS_TRUSTED_HOSTS` | The public host name(s). Requests for any other host name are refused. |
| `RECORDS_MAX_UPLOAD_MB` | Largest upload accepted. |
| `RECORDS_SECRET_KEY` | Signs the short messages shown after a redirect. |
| `RECORDS_ALLOW_REAL=true` | Real records allowed. Also turns the feedback widget off. |
| `ANTHROPIC_API_KEY`, `DISTRICT_LLM_BASE_URL`, `DISTRICT_LLM_TOKEN` | The models, as on your own computer. |

## The database

Release scripts in `deploy/sql/`, run by a DBA in this order. Each is pinned to its database, refuses to run in
the wrong one, and is safe to run twice.

1. `00-databases.sql` creates two empty databases: `RecordsAssist` and `RecordsAssistTest`.
2. `01-schema.sql` and `01-schema.test.sql` create the same three tables, sequence and trigger in each. The two
   files differ only in the database name. So do `02-app-identity.sql` and `02-app-identity.test.sql`.
3. `02-app-identity.sql` creates the app's login from its Windows service account and grants it exactly what
   `SqlStore` uses: read and insert on requests, update on two columns of it (`Doc`, `UpdatedAt`); read and insert
   on the audit trail; read on the access list; the sequence. It is in no role and has no DDL rights. The script
   fails loudly if the identity holds a role, any other grant in the database, or anything on the server beyond
   connecting. It cannot see rights inherited from a Windows group; the account should be in none.

The access list (`dbo.Access`) is operator state. A deploy never writes to it. A DBA adds a row per person:

```sql
INSERT INTO dbo.Access (Email, Role) VALUES (N'first.last@aguafria.org', 'reviewer');
```

## What has been proven, and what has not

Proven by tests that run anywhere (`python -m unittest discover -s tests`): the two halves, the audit lines, every
refusal at the front door, links staying under a published path, a conflict writing nothing, a failed save
leaving the last good save readable.

Proven against SQL Server (the tests in `SqlServer`, which run when `RECORDS_TEST_SQL` points at
`RecordsAssistTest`; they were run there as the database owner):

- `00-databases.sql` and `01-schema.test.sql` ran to the end, and the schema's verify step passed.
- A request round-trips with no record words in the row; a stale save and a duplicate number are refused.
- The trigger refuses UPDATE and DELETE on the audit trail, even for the database owner.
- A save that fails after its content file and row are written rolls back: the row, the audit trail and the
  content the row points at are as they were, and the request is not left locked.
- Two saves of the same request released at the same moment, twenty rounds: exactly one is kept each time. With
  the lock weakened the same test fails in the first round.
- All release scripts have been applied. The app identity's effective rights on the live database were read back
  from SQL Server while impersonating it and match the list above.
- The six SQL Server tests also pass run as the app identity on the test database (`02-app-identity.test.sql`
  applied, `RECORDS_TEST_SQL_AS` set to the identity's name), by impersonation from a DBA's connection. That
  covers the sequence, the lock, the column-level update, the audit insert and the refused audit change under the
  identity's own grants. It is not the app's own process signing in as that account.
- A process running as the service account itself (its own Windows sign-in to SQL Server, no impersonation) drove
  `SqlStore` on the test database: number, save, load, stale save refused, audit lines. It was refused everything
  it should be: changing or deleting an audit line, deleting a request, changing a request's number, adding to
  the access list, creating a table, disabling the audit trigger. It can open its own two databases and no other.

**Not proven yet:**

- Nothing has run behind the real front door or under IIS. The path prefix and the forwarded host name are tested
  with a stand-in.
- The database and the data folder are not one transaction. What that leaves: if the process dies between the
  commit and the clean-up, or a commit's outcome is unknown, unused content files stay in the folder until the
  next save of that request removes them. They hold record text that no request points at.
- An upload is kept "as received" before it is parsed, and a sign-off writes its release folder before the save
  that points at it. If that save fails, the raw upload or the release folder is left in the data folder with no
  audit line. Nothing reads them, and nothing removes them.

`run_waitress.py` refuses to start unless sign-in, the shared secret, SQL Server, the data folder and the trusted
host names are all configured, the database it reaches is the one expected, and the data folder already exists.

## Not built

- The front door itself and the host provisioning (service account, site, data folder permissions). The app does
  not check the data folder's permissions; whoever can read that folder can read every record in it.
- Delivery: copying a signed-off release to the shared drive the requester's files are sent from.
- A retention rule for uploads and record content.
- A screen for the access list and for reading the audit trail.
