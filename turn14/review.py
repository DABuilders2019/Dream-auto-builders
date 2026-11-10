"""Ready for Turn 14 review: checks the last 3 days of the day-to-day schedule against what Turn 14 (Mark Eder,
Nov 2026) asked to see before verifying the data integration, and emails once when everything first passes.

Checks: automatic schedule on; First full load finished; Daily sync every night after 1:30 AM Eastern; Stock changes
about every hour; no scheduled run stopped; every request answered 200; under Turn 14's limits; paged (no
single-item) requests only; no quote or order calls.
"""
import datetime as dt
import os
import re
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DAYS = 3
SCHEDULED = ("changes", "daily", "full-items", "setup")
SINGLE_ITEM = re.compile(r"^/v1/(items|pricing|inventory)/(data/)?\d")
ORDERING = re.compile(r"^/v1/(quote|order)")
NOTIFY_KEY = "t14_review_ready_sent"
DEFAULT_TO = "admin@dreamautobuilders.com"


def _state(conn, key):
    import json
    r = conn.execute("SELECT value FROM t14_state WHERE key = %s", (key,)).fetchone()
    if not r:
        return {}
    v = r[0]
    return v if isinstance(v, dict) else json.loads(v)


def _item(key, name, ok, text, fix=None):
    return {"key": key, "name": name, "status": "ok" if ok else "bad", "text": text, "fix": None if ok else fix}


def nights(now):
    """The last DAYS nights whose Daily sync should have run (today counts once it's past 2:30 AM Eastern)."""
    et = now.astimezone(ET)
    last = et.date() if (et.hour, et.minute) >= (2, 30) else et.date() - dt.timedelta(days=1)
    return [last - dt.timedelta(days=i) for i in range(DAYS)][::-1]


def checks(conn, now=None, env=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    env = os.environ if env is None else env
    since = now - dt.timedelta(days=DAYS)
    items = []

    auto = env.get("TURN14_AUTO") == "1"
    items.append(_item("auto", "Automatic schedule is on", auto,
                       "TURN14_AUTO is 1 in Render, so the hourly, nightly and twice-monthly tasks run by themselves."
                       if auto else "TURN14_AUTO isn't set to 1.",
                       "Add TURN14_AUTO = 1 in Render's Environment settings (after First full load has finished)."))

    setup = _state(conn, "last_setup")
    ok = setup.get("status") == "ok"
    items.append(_item("setup", "First full load finished", ok,
                       f"Last run {setup.get('finished', '')[:16].replace('T', ' ')} UTC." if ok
                       else ("It hasn't been run yet." if not setup else f"Last run ended: {setup.get('status')}."),
                       "Run First full load on this page and let it finish."))

    runs = conn.execute("""SELECT task, started, status, error FROM t14_runs WHERE started >= %s ORDER BY started""",
                        (since - dt.timedelta(hours=2),)).fetchall()

    # Daily sync each of the last 3 nights, after 1:30 AM Eastern
    good = {r[1].astimezone(ET).date() for r in runs if r[0] == "daily" and r[2] == "ok"
            and (r[1].astimezone(ET).hour, r[1].astimezone(ET).minute) >= (1, 30)}
    want = nights(now)
    missing = [d for d in want if d not in good]
    items.append(_item("daily", "Daily sync every night after 1:30 AM Eastern", not missing,
                       f"Ran cleanly on {', '.join(d.strftime('%b %d') for d in want)}." if not missing
                       else f"No clean Daily sync on {', '.join(d.strftime('%b %d') for d in missing)}.",
                       "Leave the automatic schedule on; this passes after three nights in a row."))

    # Stock changes about every hour: no gap over 2 hours between scheduled runs across the 3 days
    starts = [r[1] for r in runs if r[0] in SCHEDULED and r[2] == "ok"]
    points = [since] + [t for t in starts if t >= since] + [now]
    gap = max((b - a for a, b in zip(points, points[1:])), default=now - since)
    earlier = any(t < since for t in starts)       # the schedule was already running when the 3 days began
    ok = gap <= dt.timedelta(hours=2) and earlier
    hours = gap.total_seconds() / 3600
    items.append(_item("hourly", "Stock changes about every hour", ok,
                       f"{sum(1 for r in runs if r[0] == 'changes' and r[1] >= since)} runs in 3 days; longest gap {hours:.1f} hours."
                       if earlier else "The schedule hasn't been running for 3 days yet.",
                       "Leave the automatic schedule on. A gap usually means the server restarted or was asleep."))

    bad_runs = [r for r in runs if r[0] in SCHEDULED and r[1] >= since and r[2] != "ok"]
    items.append(_item("stops", "No scheduled run stopped", not bad_runs,
                       "Every hourly and nightly run finished." if not bad_runs else
                       f"{len(bad_runs)} stopped, last: {bad_runs[-1][0]} at {bad_runs[-1][1].astimezone(ET):%b %d %I:%M %p} ET: "
                       f"{(bad_runs[-1][3] or '')[:160]}",
                       "Send a screenshot of the error. After it's fixed, the 3 days start counting again."))

    reqs = conn.execute("""SELECT count(*), count(*) FILTER (WHERE status IS DISTINCT FROM 200)
                           FROM t14_requests WHERE at >= %s""", (since,)).fetchone()
    errs = conn.execute("""SELECT at, path, status FROM t14_requests WHERE at >= %s AND status IS DISTINCT FROM 200
                           ORDER BY at DESC LIMIT 3""", (since,)).fetchall()
    items.append(_item("non200", "Every request answered 200", reqs[1] == 0,
                       f"{reqs[0]:,} requests, all answered 200." if reqs[1] == 0 else
                       f"{reqs[1]} of {reqs[0]:,} weren't: " + "; ".join(f"{p} → {st or 'no answer'}" for _, p, st in errs),
                       "Each one stopped its run, as Turn 14 asks. Send a screenshot so it can be fixed."))

    hour = conn.execute("""SELECT coalesce(max(n), 0) FROM (SELECT count(*) n FROM t14_requests WHERE at >= %s
                           GROUP BY date_trunc('hour', at)) x""", (since,)).fetchone()[0]
    day = conn.execute("""SELECT coalesce(max(n), 0) FROM (SELECT count(*) n FROM t14_requests WHERE at >= %s
                          GROUP BY date_trunc('day', at AT TIME ZONE 'UTC')) x""", (since,)).fetchone()[0]
    ok = hour < 5000 and day < 30000
    items.append(_item("limits", "Within Turn 14's limits", ok,
                       f"Busiest hour {hour:,} requests (limit 5,000); busiest day {day:,} (limit 30,000).",
                       "The sync stops itself at 4,000 an hour and 24,000 a day, so this shouldn't happen: send a screenshot."))

    paths = [r[0] for r in conn.execute("SELECT DISTINCT path FROM t14_requests WHERE at >= %s", (since,)).fetchall()]
    single = sorted({p for p in paths if SINGLE_ITEM.match(p)})
    items.append(_item("paging", "Paged requests only (no single items)", not single,
                       "Every list was read by brand, 1,000 per page." if not single else f"Single-item requests: {', '.join(single[:3])}",
                       "Send a screenshot so it can be fixed."))
    ordering = sorted({p for p in paths if ORDERING.match(p)})
    items.append(_item("ordering", "No quote or order requests", not ordering,
                       "None, as Turn 14 asked until the data integration is verified." if not ordering
                       else f"Found: {', '.join(ordering[:3])}",
                       "Nothing should call quotes or orders yet: send a screenshot."))

    return {"items": items, "ready": all(i["status"] == "ok" for i in items),
            "server": env.get("TURN14_ENV", "test") or "test", "since": since, "now": now}


def summary(conn, result=None):
    """Plain text for the email to Turn 14."""
    r = result or checks(conn)
    lines = [f"Turn 14 data integration, last {DAYS} days ({r['since'].astimezone(ET):%b %d} to {r['now'].astimezone(ET):%b %d}, "
             f"{'testing' if r['server'] != 'production' else 'production'} server):", ""]
    for i in r["items"]:
        lines.append(f"- {'OK' if i['status'] == 'ok' else 'NOT YET'}: {i['name']}. {i['text']}")
    return "\n".join(lines)


def maybe_notify(conn, log=print):
    """Email once, the first time every check passes."""
    if conn.execute("SELECT 1 FROM app_state WHERE key = %s", (NOTIFY_KEY,)).fetchone():
        return False
    r = checks(conn)
    if not r["ready"]:
        return False
    from store import mailer
    to = os.environ.get("TURN14_REVIEW_EMAIL") or DEFAULT_TO
    text = ("Every check on the \"Ready for Turn 14 review\" card has passed.\n\n"
            "Next step: send Mark Eder at Turn 14 the email asking him to verify the data integration.\n"
            "The card is on the admin's Turn 14 page.\n\n" + summary(conn, r) + "\n")
    if not mailer.send(to, "Turn 14: ready for review", text, wait=True):
        log("  Ready for Turn 14 review, but email isn't set up, so no email was sent")
        return False
    conn.execute("""INSERT INTO app_state (key, value) VALUES (%s, %s)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                 (NOTIFY_KEY, r["now"].isoformat()))
    log(f"  Ready for Turn 14 review: email sent to {to}")
    return True
