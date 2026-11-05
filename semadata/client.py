"""SEMA Data API client (https://apps.semadata.org/sdapi/v2) that keeps to SEMA's limits.

- Sign-in: SEMA_USERNAME / SEMA_PASSWORD (the SEMA Data website login) are exchanged for a token,
  which is kept in the database and reused. If SEMA says the token is no longer valid, one new
  token is requested and the call is tried once more.
- Speed: SEMA allows 120 calls a minute to /products endpoints and 60 a minute to everything else,
  counted separately per endpoint. We stay a little under that.
- Allowance: the subscription includes a fixed number of API calls. Before a load the remaining
  allowance is read from /receiver (that call is free) and the job stops itself before it runs out.
- Errors: 429 (too many calls) waits as long as SEMA asks and tries once more. Any other error
  answer, or SEMA reporting that its data is being updated, stops the job.
- The login is sent only to SEMA, never logged: request logs keep the endpoint path only.
"""
import os
import time

import httpx
from psycopg.types.json import Jsonb

BASE = "https://apps.semadata.org/sdapi/v2"
INTERVAL = {"products": 0.55, "other": 1.1}     # seconds between calls -> ~109 and ~55 a minute
FREE = {"/receiver", "/brand/datachanges", "/token/get"}   # don't count toward the allowance
RESERVE_FRACTION = 0.05                          # always leave 5% of the allowance unused
RESERVE_MIN = 1000
MAX_429_WAIT = 120


class StopSync(Exception):
    """Something that means: stop the whole job now."""


class BrandError(Exception):
    """A problem with one brand only: record it and carry on with the next brand."""


class _TokenRejected(Exception):
    pass


class RequestTooLong(Exception):
    """SEMA's web server refused the request because its address was too long: send fewer parts at once."""


def _short(text):
    """An error page as one short line (SEMA's server sends HTML error pages)."""
    import re
    m = re.search(r"<title>(.*?)</title>", text or "", re.S | re.I)
    if m:
        return " ".join(m.group(1).split())
    return " ".join(re.sub(r"<[^>]+>", " ", text or "").split())[:300]


def credentials():
    clean = lambda k: os.environ.get(k, "").strip().strip('"').strip()   # noqa: E731
    return clean("SEMA_USERNAME"), clean("SEMA_PASSWORD")


def configured():
    u, p = credentials()
    return bool(u and p)


def _ok(body):
    if not isinstance(body, dict):
        return True
    for k in ("success", "Success"):
        if k in body:
            return bool(body[k])
    return True


def _msg(body):
    if isinstance(body, dict):
        return str(body.get("message") or body.get("Message") or "").strip()
    return ""


class Client:
    def __init__(self, conn, task="manual", transport=None, sleep=time.sleep, log=print):
        self.conn = conn            # autocommit connection
        self.task = task
        self.sleep = sleep
        self.log = log
        self.http = httpx.Client(timeout=180, transport=transport, headers={"Accept": "application/json"})
        self._last = {"products": 0.0, "other": 0.0}
        self._token = None
        self.calls = 0              # calls this run that count toward the allowance
        self._budget = None         # calls this run may still make

    def close(self):
        self.http.close()

    # ------------------------------------------------------------------ bookkeeping
    def _record(self, method, path, status, ms):
        self.conn.execute("INSERT INTO sema_requests (method, path, status, ms, task) VALUES (%s, %s, %s, %s, %s)",
                          (method, path[:200], status, ms, self.task))

    def _throttle(self, path):
        group = "products" if path.startswith("/products") else "other"
        wait = self._last[group] + INTERVAL[group] - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        self._last[group] = time.monotonic()

    def state(self, key):
        r = self.conn.execute("SELECT value FROM sema_state WHERE key = %s", (key,)).fetchone()
        return r[0] if r else None

    def set_state(self, key, value):
        self.conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES (%s, %s, now())
                             ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (key, Jsonb(value)))

    # ------------------------------------------------------------------ token
    def token(self, fresh=False):
        if self._token and not fresh:
            return self._token
        saved = None if fresh else self.state("token")
        if saved and saved.get("token") and saved.get("at", 0) > time.time() - 6 * 3600:
            self._token = saved["token"]
            return self._token
        user, password = credentials()
        if not (user and password):
            raise StopSync("SEMA_USERNAME and SEMA_PASSWORD aren't set in Render's Environment settings.")
        body = self._send("GET", "/token/get", {"userName": user, "password": password}, sign=False)
        tok = body.get("token") or body.get("Token") if isinstance(body, dict) else None
        if not tok:
            raise StopSync(f"SEMA refused the sign-in: {_msg(body) or 'no token returned'}. "
                           "Check SEMA_USERNAME and SEMA_PASSWORD in Render (the SEMA Data website login).")
        self._token = tok
        self.set_state("token", {"token": tok, "at": time.time()})
        return tok

    # ------------------------------------------------------------------ allowance
    def receiver(self):
        body = self.call("GET", "/receiver")
        keep = {k: body.get(k) for k in ("Name", "IsSubscriber", "HasAPIAccess", "HasExports", "APICount", "APIMaxCount",
                                         "HasAPIUnitsLeft", "ExportCount", "ExportMaxCount", "HasExportUnitsLeft",
                                         "DownloadCount", "DownloadMaxCount", "MembershipExpirationDate")}
        keep["checked"] = time.time()
        self.set_state("receiver", keep)
        return keep

    def reserve_budget(self, needed=None):
        """Read the remaining allowance and set how many calls this run may make. Stops if not enough."""
        r = self.receiver()
        if r.get("HasAPIAccess") is False:
            raise StopSync("SEMA says this account doesn't have API access. Check the subscription on semadata.org.")
        used, cap = r.get("APICount"), r.get("APIMaxCount")
        if used is None or not cap:
            self._budget = None      # SEMA didn't report it: rely on HasAPIUnitsLeft checks
            if r.get("HasAPIUnitsLeft") is False:
                raise StopSync("SEMA says the account's API allowance is used up.")
            return None
        left = cap - used - max(RESERVE_MIN, int(cap * RESERVE_FRACTION))
        if left <= 0 or (needed is not None and needed > left):
            raise StopSync(f"Not enough SEMA API allowance left: {cap - used:,} of {cap:,} calls remain, this needs about "
                           f"{needed or 0:,}, and we keep {max(RESERVE_MIN, int(cap * RESERVE_FRACTION)):,} in reserve.")
        self._budget = left
        return left

    # ------------------------------------------------------------------ requests
    def call(self, method, path, params=None, data=None):
        """One API call with the token. Returns the decoded JSON body. For POST, the fields go in the body."""
        if method == "POST" and data is None:
            params, data = None, params
        if path not in FREE:
            if self._budget is not None and self.calls >= self._budget:
                raise StopSync("Stopped before using up the SEMA API allowance (the reserve is kept for later).")
        for attempt in (1, 2):
            try:
                body = self._send(method, path, params, data)
            except _TokenRejected:
                if attempt == 2:
                    raise StopSync("SEMA rejected a new sign-in token. Check SEMA_USERNAME and SEMA_PASSWORD in Render.") from None
                self.token(fresh=True)
                continue
            if not _ok(body):
                msg = _msg(body)
                if body.get("IsDataUpdating"):
                    raise StopSync("SEMA is updating its data right now; try again later.")
                if attempt == 1 and "token" in msg.lower():
                    self.token(fresh=True)
                    continue
                if "not authorized" in msg.lower() or "authoriz" in msg.lower() or "access" in msg.lower():
                    raise BrandError(f"SEMA: {msg}")
                raise BrandError(f"SEMA answered with an error on {path}: {msg or 'no message'}")
            if path not in FREE:
                self.calls += 1
            return body

    def _send(self, method, path, params=None, data=None, sign=True):
        params = list(params) if isinstance(params, list) else dict(params or {})
        if data is not None and not isinstance(data, list):
            data = dict(data)
        if sign:
            if method == "GET":
                if isinstance(params, list):
                    params = [("token", self.token())] + params
                else:
                    params["token"] = self.token()
            elif isinstance(data, list):
                data = [("token", self.token())] + data
            else:
                data = {"token": self.token(), **(data or {})}
        for attempt in (1, 2):
            self._throttle(path)
            t0 = time.monotonic()
            try:
                if method == "GET":
                    r = self.http.get(BASE + path, params=params)
                else:
                    r = self.http.post(BASE + path, data=data if isinstance(data, dict) else None,
                                       content=_form(data) if isinstance(data, list) else None,
                                       headers={"Content-Type": "application/x-www-form-urlencoded"} if isinstance(data, list) else None)
            except httpx.HTTPError as e:
                self._record(method, path, None, int((time.monotonic() - t0) * 1000))
                raise StopSync(f"Couldn't reach SEMA ({e.__class__.__name__}).") from None
            self._record(method, path, r.status_code, int((time.monotonic() - t0) * 1000))
            if r.status_code == 429 and attempt == 1:
                wait = min(MAX_429_WAIT, int(r.headers.get("Retry-After", "60") or 60))
                self.log(f"  SEMA asked us to slow down; waiting {wait} seconds")
                self.sleep(wait)
                continue
            if r.status_code in (401, 403) and path == "/token/get":
                raise StopSync(f"SEMA refused the sign-in ({r.status_code}). Check SEMA_USERNAME and SEMA_PASSWORD in Render.")
            if r.status_code == 401 and sign:
                raise _TokenRejected()
            if r.status_code == 414 or (r.status_code == 404 and "404.15" in r.text):
                raise RequestTooLong()
            if r.status_code != 200:
                raise StopSync(f"SEMA answered {r.status_code} on {path}: {_short(r.text)}")
            try:
                return r.json()
            except ValueError:
                raise StopSync(f"SEMA sent something that isn't JSON on {path}.") from None
        raise StopSync("SEMA kept asking us to slow down; stopped.")


def _form(pairs):
    from urllib.parse import urlencode
    return urlencode(pairs)
