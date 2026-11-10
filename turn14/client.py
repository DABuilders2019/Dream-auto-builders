"""Turn 14 API client that keeps to Turn 14's rules (docs/TURN14.md).

- Token: requested only when there's no valid one; kept in the database so every process reuses it
  until shortly before it expires (tokens last an hour).
- Speed: at most 4 requests a second (Turn 14 allows 5).
- Usage: before every request the requests already made are counted; the sync stops itself at
  4,000 an hour and 24,000 a day (Turn 14 allows 5,000 and 30,000).
- Errors: any answer other than 200 stops the sync, 429 (too many requests) included, as Turn 14 asked
  (Mark Eder, Nov 2026: "terminate any script that receives any non-200 status code (401, 403, 404, 429, 500 etc.)").
- Paging: lists are read 1,000 items per page, never one item at a time.
"""
import datetime as dt
import json
import os
import time

import httpx

BASE_URLS = {"test": "https://apitest.turn14.com", "production": "https://api.turn14.com"}
MIN_INTERVAL = 0.25          # seconds between requests -> 4 per second
HOURLY_CAP = 4000            # Turn 14 limit 5,000 per hour
DAILY_CAP = 24000            # Turn 14 limit 30,000 per 24 hours
TOKEN_PER_MINUTE_CAP = 5     # Turn 14 limit 10 token requests per minute per IP


class StopSync(Exception):
    """Something Turn 14 or the network answered that means: stop now."""


def environment():
    env = os.environ.get("TURN14_ENV", "test").strip().lower()
    return env if env in BASE_URLS else "test"


def configured():
    return bool(os.environ.get("TURN14_CLIENT_ID") and os.environ.get("TURN14_CLIENT_SECRET"))


class Client:
    def __init__(self, conn, task="manual", env=None, transport=None, sleep=time.sleep, log=print):
        self.conn = conn                 # autocommit connection
        self.task = task
        self.env = env or environment()
        self.base = BASE_URLS[self.env]
        self.sleep = sleep
        self.log = log
        self.http = httpx.Client(timeout=90, transport=transport, headers={"Accept": "application/json"})
        self._last = 0.0
        self._token = None
        self.requests = 0

    def close(self):
        self.http.close()

    # ------------------------------------------------------------------ bookkeeping
    def _record(self, method, path, status, ms):
        self.conn.execute("INSERT INTO t14_requests (method, path, status, ms, task) VALUES (%s, %s, %s, %s, %s)",
                          (method, path[:300], status, ms, self.task))

    def _count(self, interval, path=None):
        q = "SELECT count(*) FROM t14_requests WHERE at > now() - %s::interval"
        args = [interval]
        if path:
            q += " AND path = %s"
            args.append(path)
        return self.conn.execute(q, args).fetchone()[0]

    def _throttle(self):
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last = time.monotonic()

    def _check_budget(self):
        hour, day = self._count("1 hour"), self._count("24 hours")
        if hour >= HOURLY_CAP:
            raise StopSync(f"Stopped to stay under Turn 14's hourly limit ({hour} requests in the last hour).")
        if day >= DAILY_CAP:
            raise StopSync(f"Stopped to stay under Turn 14's daily limit ({day} requests in the last 24 hours).")

    # ------------------------------------------------------------------ token
    def token(self):
        now = time.time()
        if self._token and self._token["expires_at"] - 120 > now:
            return self._token["access_token"]
        row = self.conn.execute("SELECT value FROM t14_state WHERE key = 'token'").fetchone()
        if row:
            t = row[0]
            if t.get("env") == self.env and t.get("expires_at", 0) - 120 > now:
                self._token = t
                return t["access_token"]
        if self._count("1 minute", "/v1/token") >= TOKEN_PER_MINUTE_CAP:
            raise StopSync("Too many sign-in requests in the last minute; stopped to protect the account.")
        cid, secret = (os.environ.get(k, "").strip().strip('"').strip() for k in ("TURN14_CLIENT_ID", "TURN14_CLIENT_SECRET"))
        if not (cid and secret):
            raise StopSync("TURN14_CLIENT_ID and TURN14_CLIENT_SECRET aren't set in Render's Environment settings.")
        self._throttle()
        t0 = time.monotonic()
        try:
            r = self.http.post(self.base + "/v1/token", json={"grant_type": "client_credentials",
                                                                "client_id": cid, "client_secret": secret})
        except httpx.HTTPError as e:
            self._record("POST", "/v1/token", None, int((time.monotonic() - t0) * 1000))
            raise StopSync(f"Couldn't reach Turn 14 ({e.__class__.__name__}).") from None
        self._record("POST", "/v1/token", r.status_code, int((time.monotonic() - t0) * 1000))
        self.requests += 1
        if r.status_code != 200:
            raise StopSync(f"Turn 14 refused the sign-in ({r.status_code}): {_message(r)}. "
                           "Check the Client ID and Secret, and that the account has access to this server.")
        body = r.json()
        t = {"access_token": body["access_token"], "expires_at": now + float(body.get("expires_in", 3600)), "env": self.env}
        self.conn.execute("""INSERT INTO t14_state (key, value, updated_at) VALUES ('token', %s, now())
                             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (json.dumps(t),))
        self._token = t
        return t["access_token"]

    # ------------------------------------------------------------------ requests
    def get(self, path, params=None):
        for attempt in (1, 2):
            self._check_budget()
            token = self.token()
            self._throttle()
            t0 = time.monotonic()
            try:
                r = self.http.get(self.base + path, params=params or {}, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                self._record("GET", _show(path, params), None, int((time.monotonic() - t0) * 1000))
                raise StopSync(f"Couldn't reach Turn 14 for {path} ({e.__class__.__name__}).") from None
            self._record("GET", _show(path, params), r.status_code, int((time.monotonic() - t0) * 1000))
            self.requests += 1
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:     # token no longer valid (e.g. secret reset): forget it so the next run signs in again
                self.conn.execute("DELETE FROM t14_state WHERE key = 'token'")
            raise StopSync(f"Turn 14 answered {r.status_code} for {_show(path, params)}: {_message(r)}")
        raise StopSync("unreachable")

    def pages(self, path, params=None):
        """Yield each record of a paged list, 1,000 per page, following meta.total_pages."""
        page = 1
        while True:
            body = self.get(path, {**(params or {}), "page": page})
            data = body.get("data") or []
            for rec in data:
                yield rec
            total = (body.get("meta") or {}).get("total_pages") or 1
            if page >= total or not data:
                return
            page += 1


def _show(path, params):
    return path + ("?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else "")


def _message(r):
    try:
        body = r.json()
        errs = body.get("errors") if isinstance(body, dict) else None
        if errs and isinstance(errs, list):
            return "; ".join(str(e.get("detail") or e.get("title") or e) for e in errs)[:300]
        return (body.get("error_description") or body.get("message") or json.dumps(body))[:300]
    except ValueError:
        return (r.text or "")[:300]


def utcnow():
    return dt.datetime.now(dt.timezone.utc)
