"""Checks SEMA Data for updated brands every night (only when SEMA_AUTO=1).

After 3:30 AM Eastern, once a day: brands on our list that SEMA has updated (or that are new on the
list, or failed last time) are loaded again, followed by their photos. Nothing starts while another
SEMA job or a catalog upload is running, and only after a connection test has passed.
"""
import datetime as dt
import logging
import os
import threading
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

ET = ZoneInfo("America/New_York")
log = logging.getLogger("store.sema")


def enabled():
    return os.environ.get("SEMA_AUTO") == "1"


def _last(conn, key):
    r = conn.execute("SELECT value FROM sema_state WHERE key = %s", (key,)).fetchone()
    return r[0] if r else None


def due(conn, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    et = now.astimezone(ET)
    test = _last(conn, "last_test")
    if not test or test.get("status") != "ok":
        return False
    if (et.hour, et.minute) < (3, 30):
        return False
    nightly = _last(conn, "last_nightly")
    if nightly and dt.datetime.fromisoformat(nightly["at"]).astimezone(ET).date() >= et.date():
        return False
    return True


def start(state, codes=(), title="SEMA: load brands", nightly=False):
    if state.jobs.running():
        return False
    args = ["load", *codes]
    ok = state.sema.start(title, [(title.replace("SEMA: ", "").capitalize(), args)])
    if ok and nightly:
        with state.pool.connection() as conn:
            conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES ('last_nightly', %s, now())
                            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                         (Jsonb({"at": dt.datetime.now(dt.timezone.utc).isoformat()}),))
    return ok


def loop(state, stop):
    while not stop.wait(120):
        try:
            if state.sema.running() or state.jobs.running():
                continue
            with state.pool.connection() as conn:
                go = due(conn)
            if go:
                log.info("starting nightly SEMA update")
                start(state, title="SEMA: nightly update", nightly=True)
        except Exception:      # noqa: BLE001 - keep the scheduler alive
            log.exception("SEMA scheduler")


def launch(state):
    stop = threading.Event()
    if enabled():
        threading.Thread(target=loop, args=(state, stop), daemon=True, name="sema-schedule").start()
    return stop
