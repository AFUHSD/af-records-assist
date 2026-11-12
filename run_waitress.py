#!/usr/bin/env python
"""
Records Assist as a hosted service: one waitress process on loopback, behind the district front door.

  python run_waitress.py [port]        the port also comes from HTTP_PLATFORM_PORT (IIS httpPlatformHandler)

One process on purpose. Threads share the app's lock; SQL Server's row versions cover everything else. It listens
on 127.0.0.1 only, so the front door is the one way in. Settings come from the environment, never from a file here:

  RECORDS_PLATFORM_AUTH=1        believe the front door's identity header (with FRONT_DOOR_SHARED_SECRET beside it)
  FRONT_DOOR_SHARED_SECRET       the same value the front door sends; unset means every request is refused
  RECORDS_SQL                    ODBC connection string, Trusted_Connection=yes, no password
  RECORDS_DATA_DIR               the protected folder for record content, uploads and release files
  RECORDS_PATH_BASE              the path the app is published under, e.g. /records
  RECORDS_TRUSTED_HOSTS          the public host name(s) the app answers to
  RECORDS_MAX_UPLOAD_MB          largest upload accepted
  RECORDS_SECRET_KEY             signs the short messages shown after a redirect
  RECORDS_ALLOW_REAL=true        real records allowed (also turns the feedback widget off)
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "web"))


def main() -> int:
    if os.environ.get("RECORDS_PLATFORM_AUTH", "").strip().lower() not in ("1", "true", "yes"):
        sys.exit("run_waitress.py is the hosted entry point and refuses to start without RECORDS_PLATFORM_AUTH=1: "
                 "without it nobody is signed in and every page is open. On your own computer use: python web/app.py")
    missing = [k for k in ("RECORDS_SQL", "RECORDS_DATA_DIR", "FRONT_DOOR_SHARED_SECRET", "RECORDS_TRUSTED_HOSTS") if not os.environ.get(k, "").strip()]
    if missing:
        sys.exit("run_waitress.py refuses to start without " + ", ".join(missing) + ": a hosted copy that quietly fell back to "
                 "files in its own folder, or to no secret, would look healthy and be wrong.")
    from waitress import serve

    import app as web

    if web.STORE.name != "sql":
        sys.exit("run_waitress.py refuses to start: storage is not SQL Server.")
    expected = os.environ.get("RECORDS_SQL_DATABASE", "RecordsAssist")
    if web.STORE.database() != expected:  # also proves the app can sign in to SQL Server before it takes a request
        sys.exit(f"run_waitress.py refuses to start: connected to a database that is not {expected}.")

    if not web.DATA.is_dir():  # never created here: a folder made at start-up would inherit whatever its parent allows
        sys.exit("run_waitress.py refuses to start: RECORDS_DATA_DIR does not exist. It is provisioned with its own permissions, not created by the app.")
    port = int(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HTTP_PLATFORM_PORT", "8000"))
    serve(web.app, host="127.0.0.1", port=port, threads=int(os.environ.get("WAITRESS_THREADS", "8")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
