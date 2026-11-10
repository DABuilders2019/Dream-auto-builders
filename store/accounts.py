"""Customer accounts.

Sign in with a password, or with a 6-digit code emailed to you (always available, and the way to get back in after
forgetting a password). An account can also be made at checkout: the shopper ticks "save my name and email" in the
cart, and after paying the account is made from what they entered on Stripe's page; it has no password and signs in
with codes.

What an account keeps: order history (orders placed while signed in, plus past orders with the same email once the
email is confirmed by a code), the garage vehicles and saved builds (on every device), and a Stripe customer so
saved cards and addresses come back on Stripe's payment page.

Codes and passwords: codes are stored hashed, last 10 minutes, allow 5 tries, and at most 3 are sent per email per
15 minutes (and 12 per IP address an hour). Passwords are hashed with scrypt. Failed password sign-ins are limited
per email and per IP address.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg.types.json import Jsonb

from . import mailer, web

log = logging.getLogger("store.accounts")
router = APIRouter()

CODE_MINUTES = 10
CODE_TRIES = 5
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


# ------------------------------------------------------------------ helpers

def norm_email(e):
    return (e or "").strip().lower()[:254]


def valid_email(e):
    return bool(EMAIL_RE.match(e or ""))


def hash_password(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def check_password(pw, stored):
    try:
        _, n, r, p, salt, h = (stored or "").split("$")
        got = hashlib.scrypt((pw or "").encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


def _code_hash(email, code):
    key = (os.environ.get("SECRET_KEY") or "dev").encode()
    return hmac.new(key, f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


class Limiter:
    """Counts events per key in a sliding window (in memory: enough for one server)."""

    def __init__(self, limit, seconds):
        self.limit, self.seconds, self.hits, self.lock = limit, seconds, {}, threading.Lock()

    def hit(self, key):
        now = time.time()
        with self.lock:
            xs = [t for t in self.hits.get(key, []) if now - t < self.seconds] + [now]
            self.hits[key] = xs
            if len(self.hits) > 20000:
                self.hits = {k: v for k, v in self.hits.items() if now - v[-1] < self.seconds}
            return len(xs) <= self.limit

    def clear(self, key):
        with self.lock:
            self.hits.pop(key, None)


login_fail_email = Limiter(8, 15 * 60)
login_fail_ip = Limiter(30, 15 * 60)
signup_ip = Limiter(10, 60 * 60)


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


# ------------------------------------------------------------------ the signed-in account

def current_id(request):
    v = request.session.get("acct")
    return int(v) if isinstance(v, int) or (isinstance(v, str) and v.isdigit()) else None


def get(conn, account_id=None, email=None):
    if account_id is None and not email:
        return None
    cur = conn.execute("""SELECT id, email, name, password_hash IS NOT NULL, email_verified, stripe_customer_id, garage,
                                 active_vehicle, builds, source, created_at
                          FROM accounts WHERE """ + ("id = %s" if account_id is not None else "email = %s"),
                       (account_id if account_id is not None else norm_email(email),))
    r = cur.fetchone()
    if not r:
        return None
    keys = ["id", "email", "name", "has_password", "email_verified", "stripe_customer_id", "garage", "active_vehicle",
            "builds", "source", "created_at"]
    return dict(zip(keys, r))


def current(request, conn):
    aid = current_id(request)
    return get(conn, aid) if aid else None


def _merge_into_session(request, a):
    """Bring the account's garage and builds into this browser (keeping anything added before signing in)."""
    from .builds import MAX_BUILDS
    g = [int(v) for v in (a["garage"] or []) if str(v).isdigit()]
    for v in web.garage_ids(request):
        if v in g:
            g.remove(v)
        g.append(v)
    request.session["garage"] = g[-web.MAX_GARAGE:]
    if request.session.get("active") is None and a["active_vehicle"] in g:
        request.session["active"] = a["active_vehicle"]
    have = {b.get("id") for b in request.session.get("builds") or [] if isinstance(b, dict)}
    builds = [b for b in (a["builds"] or []) if isinstance(b, dict) and b.get("id") not in have] + list(request.session.get("builds") or [])
    request.session["builds"] = builds[-MAX_BUILDS:]


def sign_in(request, conn, a, by_code=False):
    request.session["acct"] = a["id"]
    if by_code:
        request.session["code_at"] = int(time.time())
    conn.execute("UPDATE accounts SET last_login_at = now()" + (", email_verified = true" if by_code else "") + " WHERE id = %s",
                 (a["id"],))
    _merge_into_session(request, a)
    save_session_state(conn, a["id"], request.session)


def _state_of(session):
    return {"garage": [int(v) for v in session.get("garage") or [] if str(v).isdigit()],
            "active": session.get("active"), "builds": session.get("builds") or []}


_synced = {}


def save_session_state(conn, account_id, session):
    st = _state_of(session)
    digest = hashlib.md5(json.dumps(st, sort_keys=True, default=str).encode()).hexdigest()
    if _synced.get(account_id) == digest:
        return
    active = st["active"] if isinstance(st["active"], int) else None
    conn.execute("UPDATE accounts SET garage = %s, active_vehicle = %s, builds = %s WHERE id = %s",
                 (Jsonb(st["garage"]), active, Jsonb(st["builds"]), account_id))
    _synced[account_id] = digest


def install(app):
    """Keep a signed-in shopper's garage and builds saved on their account as they change them."""
    @app.middleware("http")
    async def keep_account_in_sync(request, call_next):
        resp = await call_next(request)
        try:
            sess = request.scope.get("session")
            aid = current_id(request) if sess is not None else None
            if aid and request.method == "POST":
                st = web._state(request)
                with st.pool.connection() as conn:
                    save_session_state(conn, aid, sess)
        except Exception:       # noqa: BLE001 - never break a page over this
            log.warning("account sync failed", exc_info=True)
        return resp


def create(conn, email, name=None, password=None, source="signup", verified=False):
    r = conn.execute("""INSERT INTO accounts (email, name, password_hash, source, email_verified) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (email) DO NOTHING RETURNING id""",
                     (norm_email(email), (name or "").strip()[:120] or None, hash_password(password) if password else None,
                      source, verified)).fetchone()
    return get(conn, r[0]) if r else None


# ------------------------------------------------------------------ email codes

def code_email(code, name):
    """(subject, text, html) of the sign-in code email."""
    return (f"{code} is your Dream Auto Builders sign-in code",
            f"Your Dream Auto Builders sign-in code is {code}\n\nIt works for {CODE_MINUTES} minutes. "
            "If you didn't ask for it, you can ignore this email; nobody can sign in without the code.",
            web.templates.get_template("email_code.html").render(code=code, minutes=CODE_MINUTES, name=name))


def send_code(request, conn, email):
    """Email a sign-in code. Returns (sent, message for the shopper)."""
    email = norm_email(email)
    if not valid_email(email):
        return False, "Enter a valid email address."
    if not mailer.configured():
        return False, "Email sign-in codes aren't switched on yet. Sign in with your password, or try again later."
    n_email = conn.execute("SELECT count(*) FROM account_codes WHERE email = %s AND created_at > now() - interval '15 minutes'",
                           (email,)).fetchone()[0]
    n_ip = conn.execute("SELECT count(*) FROM account_codes WHERE ip = %s AND created_at > now() - interval '1 hour'",
                        (client_ip(request),)).fetchone()[0]
    if n_email >= 3 or n_ip >= 12:
        return False, "Too many codes asked for. Wait a few minutes, then try again."
    a = get(conn, email=email)
    if not a:
        # Don't say whether an account exists; just don't send anything.
        return True, None
    code = f"{secrets.randbelow(10 ** 6):06d}"
    conn.execute("UPDATE account_codes SET used_at = now() WHERE email = %s AND used_at IS NULL", (email,))
    conn.execute("INSERT INTO account_codes (email, code_hash, expires_at, ip) VALUES (%s, %s, now() + %s * interval '1 minute', %s)",
                 (email, _code_hash(email, code), CODE_MINUTES, client_ip(request)))
    mailer.send(email, *code_email(code, a["name"]))
    return True, None


def check_code(conn, email, code):
    """The account if the code is right (and uses it up), else (None, message)."""
    email, code = norm_email(email), re.sub(r"\D", "", code or "")
    r = conn.execute("""SELECT id, code_hash, attempts FROM account_codes
                        WHERE email = %s AND used_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT 1""",
                     (email,)).fetchone()
    if not r:
        return None, "That code has expired. Ask for a new one."
    cid, h, attempts = r
    if attempts >= CODE_TRIES:
        return None, "Too many wrong tries. Ask for a new code."
    if len(code) != 6 or not hmac.compare_digest(h, _code_hash(email, code)):
        conn.execute("UPDATE account_codes SET attempts = attempts + 1 WHERE id = %s", (cid,))
        left = CODE_TRIES - attempts - 1
        return None, "That code isn't right." + (f" {left} tr{'y' if left == 1 else 'ies'} left." if left else " Ask for a new one.")
    conn.execute("UPDATE account_codes SET used_at = now() WHERE id = %s", (cid,))
    return get(conn, email=email), None


# ------------------------------------------------------------------ orders

def orders_for(conn, a, limit=50):
    """The account's orders: placed while signed in, plus (once the email is confirmed) past orders with its email."""
    cur = conn.execute("""
        SELECT o.number, o.status, o.total, o.created_at, o.paid_at, o.tracking_number, o.carrier,
               (SELECT count(*) FROM order_items i WHERE i.order_id = o.id), (SELECT sum(qty) FROM order_items i WHERE i.order_id = o.id)
        FROM orders o
        WHERE o.status NOT IN ('pending', 'cancelled')
          AND (o.account_id = %s OR (%s AND lower(o.email) = %s))
        ORDER BY o.created_at DESC LIMIT %s""", (a["id"], a["email_verified"], a["email"], limit))
    keys = ["number", "status", "total", "created_at", "paid_at", "tracking_number", "carrier", "lines", "qty"]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def owns_order(conn, request, number):
    a = current(request, conn)
    if not a:
        return False
    return bool(conn.execute("""SELECT 1 FROM orders WHERE number = %s AND (account_id = %s OR (%s AND lower(email) = %s))""",
                             (number, a["id"], a["email_verified"], a["email"])).fetchone())


def save_at_checkout(conn, order, session):
    """After a paid checkout where the shopper ticked "save my name and email": make (or link) their account."""
    cd = session.get("customer_details") or {}
    email = norm_email(cd.get("email") or order.get("email"))
    if not valid_email(email):
        return None, False
    a = get(conn, email=email)
    made = False
    if not a:
        a = create(conn, email, name=cd.get("name") or order.get("name"), source="checkout")
        made = a is not None
    if a:
        conn.execute("UPDATE orders SET account_id = %s WHERE id = %s AND account_id IS NULL", (a["id"], order["id"]))
        if session.get("customer") and not a["stripe_customer_id"]:
            conn.execute("UPDATE accounts SET stripe_customer_id = %s WHERE id = %s", (session["customer"], a["id"]))
    return a, made


# ------------------------------------------------------------------ pages

def _page(request, conn, mode, status_code=200, **ctx):
    s = web._state(request).get_searcher(conn)
    titles = {"signin": "Sign in", "signup": "Create your account", "code": "Enter your code", "account": "My account"}
    return web.render(request, conn, s, "account.html", status_code=status_code, title=titles.get(mode, "My account"),
                      mode=mode, codes_on=mailer.configured(), min_password=MIN_PASSWORD, **ctx)


def _next(request):
    return web.safe_next(request.query_params.get("next") or "", "/account")


@router.get("/account", response_class=HTMLResponse)
def account_home(request: Request, msg: str = "", error: str = ""):
    st = web._state(request)
    with st.pool.connection() as conn:
        a = current(request, conn)
        if not a:
            if current_id(request):
                request.session.pop("acct", None)
            return _page(request, conn, "signin", msg=msg, error=error, email=request.query_params.get("email", ""))
        orders = orders_for(conn, a)
        recent_code = time.time() - (request.session.get("code_at") or 0) < 15 * 60
        return _page(request, conn, "account", a=a, orders=orders, msg=msg, error=error, recent_code=recent_code,
                     garage_count=len(web.garage_ids(request)), builds_count=len(request.session.get("builds") or []))


@router.post("/account/signin")
def account_signin(request: Request, email: str = Form(""), password: str = Form("")):
    email, ip = norm_email(email), client_ip(request)
    st = web._state(request)
    with st.pool.connection() as conn:
        if not login_fail_email.hit(email) or not login_fail_ip.hit(ip):
            return _page(request, conn, "signin", status_code=429, email=email,
                         error="Too many tries. Wait 15 minutes, or sign in with an emailed code instead.")
        a = get(conn, email=email)
        stored = conn.execute("SELECT password_hash FROM accounts WHERE email = %s", (email,)).fetchone() if a else None
        ok = bool(stored and stored[0] and check_password(password, stored[0]))
        if not ok:
            if a and not a["has_password"]:
                err = "This account signs in with an emailed code. Use “Email me a code” below."
            else:
                err = "That email and password don't match."
            return _page(request, conn, "signin", status_code=400, email=email, error=err)
        login_fail_email.clear(email)
        sign_in(request, conn, a)
    return RedirectResponse(_next(request), status_code=303)


@router.post("/account/code")
def account_send_code(request: Request, email: str = Form("")):
    st = web._state(request)
    with st.pool.connection() as conn:
        ok, err = send_code(request, conn, email)
        if not ok:
            return _page(request, conn, "signin", status_code=400, email=norm_email(email), error=err, show_code=True)
        return _page(request, conn, "code", email=norm_email(email), nxt=request.query_params.get("next", ""))


@router.post("/account/code/verify")
def account_verify_code(request: Request, email: str = Form(""), code: str = Form("")):
    st = web._state(request)
    with st.pool.connection() as conn:
        a, err = check_code(conn, email, code)
        if not a:
            return _page(request, conn, "code", status_code=400, email=norm_email(email), error=err,
                         nxt=request.query_params.get("next", ""))
        sign_in(request, conn, a, by_code=True)
    return RedirectResponse(_next(request), status_code=303)


@router.get("/account/signup", response_class=HTMLResponse)
def account_signup_page(request: Request):
    st = web._state(request)
    with st.pool.connection() as conn:
        if current(request, conn):
            return RedirectResponse("/account", status_code=303)
        return _page(request, conn, "signup", email=request.query_params.get("email", ""))


@router.post("/account/signup")
def account_signup(request: Request, name: str = Form(""), email: str = Form(""), password: str = Form("")):
    email, name = norm_email(email), " ".join(name.split())[:120]
    st = web._state(request)
    with st.pool.connection() as conn:
        def again(err, code=400):
            return _page(request, conn, "signup", status_code=code, email=email, form_name=name, error=err)
        if not signup_ip.hit(client_ip(request)):
            return again("Too many accounts made from here. Try again later.", 429)
        if not name:
            return again("Enter your name.")
        if not valid_email(email):
            return again("Enter a valid email address.")
        if password and len(password) < MIN_PASSWORD:
            return again(f"Use at least {MIN_PASSWORD} characters for your password, or leave it empty to sign in with emailed codes.")
        if not password and not mailer.configured():
            return again("Choose a password (emailed sign-in codes aren't switched on yet).")
        a = create(conn, email, name=name, password=password or None)
        if not a:
            return again("There's already an account with that email. Sign in instead, or use “Email me a code”.")
        sign_in(request, conn, a)
    return RedirectResponse(web.safe_next(request.query_params.get("next") or "", "/account?msg=welcome"), status_code=303)


@router.post("/account/signout")
def account_signout(request: Request):
    for k in ("acct", "code_at"):
        request.session.pop(k, None)
    return RedirectResponse("/account?msg=signedout", status_code=303)


def _need(request):
    aid = current_id(request)
    return aid


@router.post("/account/profile")
def account_profile(request: Request, name: str = Form("")):
    aid = _need(request)
    if not aid:
        return RedirectResponse("/account", status_code=303)
    name = " ".join(name.split())[:120]
    if not name:
        return RedirectResponse("/account?error=name", status_code=303)
    with web._state(request).pool.connection() as conn:
        conn.execute("UPDATE accounts SET name = %s WHERE id = %s", (name, aid))
    return RedirectResponse("/account?msg=saved", status_code=303)


@router.post("/account/password")
def account_password(request: Request, current_password: str = Form(""), new_password: str = Form("")):
    aid = _need(request)
    if not aid:
        return RedirectResponse("/account", status_code=303)
    with web._state(request).pool.connection() as conn:
        stored = conn.execute("SELECT password_hash FROM accounts WHERE id = %s", (aid,)).fetchone()
        recent_code = time.time() - (request.session.get("code_at") or 0) < 15 * 60
        if stored and stored[0] and not recent_code and not check_password(current_password, stored[0]):
            return RedirectResponse("/account?error=current#security", status_code=303)
        if len(new_password) < MIN_PASSWORD:
            return RedirectResponse("/account?error=short#security", status_code=303)
        conn.execute("UPDATE accounts SET password_hash = %s WHERE id = %s", (hash_password(new_password), aid))
    return RedirectResponse("/account?msg=password#security", status_code=303)


@router.post("/account/delete")
def account_delete(request: Request, confirm: str = Form("")):
    aid = _need(request)
    if not aid:
        return RedirectResponse("/account", status_code=303)
    if confirm.strip().upper() != "DELETE":
        return RedirectResponse("/account?error=confirm#delete", status_code=303)
    with web._state(request).pool.connection() as conn:
        conn.execute("UPDATE orders SET account_id = NULL WHERE account_id = %s", (aid,))
        conn.execute("DELETE FROM accounts WHERE id = %s", (aid,))
    for k in ("acct", "code_at"):
        request.session.pop(k, None)
    _synced.pop(aid, None)
    return RedirectResponse("/account?msg=deleted", status_code=303)
