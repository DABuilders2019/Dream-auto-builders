"""Ready for Turn 14 review: the last 3 days of the day-to-day schedule checked against what Turn 14 asked for."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from turn14 import review

ET = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 11, 20, 15, 0, tzinfo=ET).astimezone(dt.timezone.utc)
ENV = {"TURN14_AUTO": "1"}


@pytest.fixture
def clean(conn):
    """Four days of a clean schedule: hourly stock changes, Daily sync at 1:30 AM ET, every request answered 200."""
    conn.execute("DELETE FROM t14_runs"); conn.execute("DELETE FROM t14_requests"); conn.execute("DELETE FROM app_state")
    conn.execute("INSERT INTO t14_state (key, value) VALUES ('last_setup', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                 (Jsonb({"status": "ok", "finished": "2026-11-15T10:00:00"}),))
    t = NOW - dt.timedelta(days=4, minutes=30)          # runs at :30 past each hour
    while t < NOW:
        et = t.astimezone(ET)
        task = "daily" if (et.hour, et.minute) == (1, 30) else "changes"
        conn.execute("INSERT INTO t14_runs (task, started, finished, status, requests) VALUES (%s, %s, %s, 'ok', 3)",
                     (task, t, t + dt.timedelta(minutes=2)))
        for path in (["/v1/brands", "/v1/items/updates?page=1", "/v1/pricing/brand/1?page=1", "/v1/inventory/brand/1?page=1"]
                     if task == "daily" else ["/v1/locations", "/v1/inventory/updates?page=1"]):
            conn.execute("INSERT INTO t14_requests (at, method, path, status, task) VALUES (%s, 'GET', %s, 200, %s)", (t, path, task))
        t += dt.timedelta(minutes=60)
    conn.commit()
    return conn


def status(r):
    return {i["key"]: i["status"] for i in r["items"]}


def test_clean_three_days_is_ready(clean):
    r = review.checks(clean, now=NOW, env=ENV)
    assert r["ready"], [i for i in r["items"] if i["status"] != "ok"]
    assert "OK: Every request answered 200" in review.summary(clean, r)


def test_each_problem_shows(clean):
    conn = clean
    assert status(review.checks(conn, now=NOW, env={}))["auto"] == "bad"
    conn.execute("INSERT INTO t14_requests (at, method, path, status, task) VALUES (%s, 'GET', '/v1/inventory/updates', 429, 'changes')",
                 (NOW - dt.timedelta(hours=5),))
    conn.execute("INSERT INTO t14_requests (at, method, path, status, task) VALUES (%s, 'GET', '/v1/items/12345', 200, 'manual')",
                 (NOW - dt.timedelta(hours=4),))
    conn.execute("INSERT INTO t14_requests (at, method, path, status, task) VALUES (%s, 'POST', '/v1/quote', 200, 'manual')",
                 (NOW - dt.timedelta(hours=4),))
    conn.execute("INSERT INTO t14_runs (task, started, status, error) VALUES ('changes', %s, 'stopped', 'Turn 14 answered 429')",
                 (NOW - dt.timedelta(hours=5),))
    # a night without the Daily sync, and a 6-hour gap in the hourly runs
    night = dt.datetime(2026, 11, 19, 1, 30, tzinfo=ET)
    conn.execute("DELETE FROM t14_runs WHERE task = 'daily' AND started = %s", (night,))
    conn.execute("DELETE FROM t14_runs WHERE status = 'ok' AND started BETWEEN %s AND %s",
                 (NOW - dt.timedelta(hours=40), NOW - dt.timedelta(hours=34)))
    s = status(review.checks(conn, now=NOW, env=ENV))
    assert s == {**s, "non200": "bad", "paging": "bad", "ordering": "bad", "stops": "bad", "daily": "bad", "hourly": "bad"}
    assert s["limits"] == "ok" and s["setup"] == "ok"


def test_not_three_days_yet(conn):
    conn.execute("DELETE FROM t14_runs")
    conn.execute("INSERT INTO t14_runs (task, started, status) VALUES ('changes', %s, 'ok')", (NOW - dt.timedelta(hours=1),))
    r = review.checks(conn, now=NOW, env=ENV)
    assert not r["ready"] and "hasn't been running for 3 days" in next(i["text"] for i in r["items"] if i["key"] == "hourly")


def test_email_once_when_ready(clean, monkeypatch):
    from store import mailer
    sent = []
    monkeypatch.setattr(mailer, "send", lambda to, subject, text, html=None, wait=False: sent.append((to, subject, text)) or True)
    real = review.checks
    monkeypatch.setattr(review, "checks", lambda conn, now=None, env=None: real(conn, now=NOW, env=ENV))
    assert review.maybe_notify(clean, log=lambda *a: None)
    assert sent[0][0] == "admin@dreamautobuilders.com" and sent[0][1] == "Turn 14: ready for review"
    assert "send Mark Eder" in sent[0][2]
    assert not review.maybe_notify(clean, log=lambda *a: None) and len(sent) == 1      # only once
