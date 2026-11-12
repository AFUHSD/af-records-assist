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

The document remembers the SHA-256 of each record's text. A record whose content file is missing, unreadable, or
no longer holds the text that was saved comes back empty and unchecked with an error on it, and cannot be checked
again; it has to be removed and uploaded again. It cannot sign off as "nothing to redact".

Hosted, a save is one database transaction holding a lock on that request: confirm nobody else saved first, write
the content files, update the row, write the audit line, commit. A save that loses writes nothing. Each sign-off
writes its release files into a folder of its own, so a sign-off that loses or is interrupted cannot touch the
files a saved request points at.

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
   files differ only in the database name.
3. `02-app-identity.sql` creates the app's login from its Windows service account and grants it exactly what
   `SqlStore` uses: read, insert and update on requests; read and insert on the audit trail; read on the access
   list; the sequence. It is in no role and has no DDL rights.

The access list (`dbo.Access`) is operator state. A deploy never writes to it. A DBA adds a row per person:

```sql
INSERT INTO dbo.Access (Email, Role) VALUES (N'first.last@aguafria.org', 'reviewer');
```

## What has been proven, and what has not

Proven by tests that run anywhere (`python -m unittest tests/test_hosting.py`): the two halves, the audit lines,
every refusal at the front door, links staying under a published path, a conflict writing nothing.

**Not proven yet:**

- `SqlStore` has never run against SQL Server. Its four tests are skipped until `RECORDS_TEST_SQL` points at
  `RecordsAssistTest` with the schema applied. Run them before anything else touches the live database.
- The SQL scripts were compiled read-only (`SET NOEXEC ON`) on a SQL Server instance. That checks syntax and
  types. It does not check the trigger body or the grants, which are built as strings and compiled only when run.
- Nothing has run behind the real front door or under IIS. The path prefix and the forwarded host name are tested
  with a stand-in.
- No test drives two processes through a save at the same moment, or kills the process between the content write
  and the commit. The lock and the transaction are there; their behaviour under a real race is unobserved.
- The database and the data folder are not one transaction. If the process dies after a content file is written
  and before the row commits, the file can be newer than the row. The text itself never changes after upload and
  is hash-checked, so this can lose a model's stated reason, not a redaction.

`run_waitress.py` refuses to start unless sign-in, the shared secret, SQL Server, the data folder and the trusted
host names are all configured, the database it reaches is the one expected, and the data folder already exists.

## Not built

- The front door itself and the host provisioning (service account, site, data folder permissions). The app does
  not check the data folder's permissions; whoever can read that folder can read every record in it.
- Delivery: copying a signed-off release to the shared drive the requester's files are sent from.
- A retention rule for uploads and record content.
- A screen for the access list and for reading the audit trail.
