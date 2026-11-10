"""Runs the Turn 14 sync on Turn 14's recommended schedule (only when TURN14_AUTO=1).

  every hour              changes     (stock changes, warehouses)
  daily after 1:30 AM ET  daily       (brands, changed items, pricing, full stock count)
  1st and 15th, 3 AM ET   full-items  (every item again; catches discontinued items)

The first full load ("setup") is always started by hand from the admin page. One Turn 14 task runs
at a time, and nothing is started while one is running.
"""
import datetime as dt
import logging
import os
import threading
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
log = logging.getLogger("store.turn14")


def enabled():
    return os.environ.get("TURN14_AUTO") == "1"


def _last(conn, task):
    r = conn.execute("SELECT value FROM t14_state WHERE key = %s", (f"last_{task}",)).fetchone()
    v = r[0] if r else None
    if v and v.get("finished"):
        return dt.datetime.fromisoformat(v["finished"]), v.get("status")
    return None, None


def due(conn, now=None):
    """Which task should run now, or None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    et = now.astimezone(ET)
    setup_at, setup_status = _last(conn, "setup")
    if not setup_at or setup_status != "ok":
        return None
    daily_at, _ = _last(conn, "daily")
    if (et.hour, et.minute) >= (1, 30) and (not daily_at or daily_at.astimezone(ET).date() < et.date()):
        return "daily"
    full_at, _ = _last(conn, "full-items")
    if et.day in (1, 15) and et.hour >= 3 and (not full_at or full_at.astimezone(ET).date() < et.date()):
        return "full-items"
    changes_at, _ = _last(conn, "changes")
    last_any = max([t for t in (changes_at, daily_at, setup_at) if t])
    if now - last_any >= dt.timedelta(minutes=58):
        return "changes"
    return None


LABELS = {"test": "Connection test", "setup": "First full load", "daily": "Daily sync",
          "changes": "Stock changes", "full-items": "Full item check"}


def start(state, task, label=None):
    label = label or LABELS.get(task, task)
    return state.t14.start(f"Turn 14: {label}", [(label, [task])])


def loop(state, stop):
    while not stop.wait(60):
        try:
            if state.t14.running():
                continue
            with state.pool.connection() as conn:
                task = due(conn)
            if task:
                log.info("starting Turn 14 %s", task)
                start(state, task)
        except Exception:      # noqa: BLE001 - keep the scheduler alive
            log.exception("Turn 14 scheduler")


def launch(state):
    stop = threading.Event()
    if enabled():
        threading.Thread(target=loop, args=(state, stop), daemon=True, name="turn14-schedule").start()
    return stop
