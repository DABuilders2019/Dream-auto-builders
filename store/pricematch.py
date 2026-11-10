"""Price match: a customer who found a part for less sends a link or a photo of the lower price from the product page.
We review it on Admin > Price Match and reply by email. An approved match comes with a link that puts the part in the
customer's cart at the matched price for 24 hours (for up to the quantity they asked about). The matched price is
applied wherever the cart is priced (web.cart_summary), so the cart, the header total and checkout all agree, and it
is marked used once that order is paid.

Photos are kept privately in DATA_DIR/price_match (only the admin page shows them), re-saved as plain JPEG so nothing
else in the uploaded file comes along.
"""
import datetime as dt
import io
import logging
import os
import pathlib
import re
import secrets
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from . import accounts, mailer, web

router = APIRouter()
log = logging.getLogger("store.pricematch")

HOURS = 24                      # how long an approved match stays in the cart
MAX_QTY = 10
MAX_PHOTO_MB = 10
send_ip = accounts.Limiter(6, 60 * 60)          # requests per IP per hour
send_email = accounts.Limiter(10, 24 * 60 * 60)  # requests per email per day
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def photo_dir():
    d = pathlib.Path(os.environ.get("DATA_DIR", "data")) / "price_match"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _money(v):
    try:
        d = Decimal(str(v).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return d.quantize(Decimal("0.01")) if d > 0 else None


def clean_url(u):
    """A competitor link, or "" if it isn't a normal web address."""
    u = (u or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or "." not in host or len(u) > 1500 or parts.username or parts.password:
        return None
    return u


def host_of(u):
    try:
        return (urlsplit(u).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def save_photo(raw):
    """Re-save an uploaded image as a plain JPEG (at most 2000 px). Returns the file name, or raises ValueError."""
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:      # noqa: BLE001
        raise ValueError("That file isn't a photo we can open. Try a JPG or PNG screenshot.") from e
    im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((2000, 2000))
    name = secrets.token_hex(12) + ".jpg"
    im.save(photo_dir() / name, "JPEG", quality=85)
    return name


def _back(slug, **q):
    return RedirectResponse(f"/p/{slug}?" + urlencode(q) + "#pricematch", status_code=303)


# ------------------------------------------------------------------ customer: send a request

@router.post("/price-match")
async def send_request(request: Request, product_id: int = Form(...), url: str = Form(""), their_price: str = Form(""),
                       qty: str = Form("1"), email: str = Form(""), website: str = Form(""),
                       photo: UploadFile | None = File(None)):
    st = web._state(request)
    raw = await photo.read(MAX_PHOTO_MB * 1024 * 1024 + 1) if photo is not None and photo.filename else b""
    with st.pool.connection() as conn:
        row = conn.execute("SELECT slug, price, title FROM products WHERE id = %s AND status = 'active'", (product_id,)).fetchone()
        if not row:
            return RedirectResponse("/", status_code=303)
        slug, price, title = row
        form = {"url": url.strip()[:1500], "their_price": their_price.strip()[:20], "qty": qty.strip()[:3], "email": email.strip()[:200]}
        request.session["pm_form"] = form

        def fail(msg):
            return _back(slug, pm_error=msg)
        if website:                                   # filled in only by bots
            return _back(slug, pm="sent")
        link = clean_url(url)
        if link is None:
            return fail("That link doesn't look right. Copy the full address of the other store's page.")
        if not link and not raw:
            return fail("Add a link to the other store's listing, or a photo of the price.")
        if raw and len(raw) > MAX_PHOTO_MB * 1024 * 1024:
            return fail(f"That photo is too big. Please keep it under {MAX_PHOTO_MB} MB.")
        theirs = _money(their_price)
        if not theirs:
            return fail("Enter the price you found, for example 589.99.")
        if price is not None and theirs >= Decimal(str(price)):
            return fail("That price isn't lower than ours, so there's nothing to match.")
        try:
            n = int(qty or 1)
        except ValueError:
            n = 0
        if not 1 <= n <= MAX_QTY:
            return fail(f"Quantity should be between 1 and {MAX_QTY}.")
        addr = accounts.norm_email(email)
        if not EMAIL_RE.match(addr or ""):
            return fail("Enter your email so we can reply.")
        if host_of(link) and host_of(link) == host_of(str(request.base_url)):
            return fail("That's a link to our own site. Paste the other store's link.")
        ip = accounts.client_ip(request)
        if not send_ip.hit(ip) or not send_email.hit(addr):
            return fail("You've sent several requests already. We'll reply to those first; try again later.")
        try:
            pkey = save_photo(raw) if raw else None
        except ValueError as e:
            return fail(str(e))
        acct = accounts.current_id(request)
        pm_id = conn.execute("""INSERT INTO price_matches (product_id, our_price, their_price, qty, url, photo_key, email, account_id, ip)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                             (product_id, price, theirs, n, link or None, pkey, addr, acct, ip)).fetchone()[0]
        conn.commit()
    request.session.pop("pm_form", None)
    base = _site(request)
    mailer.send(addr, f"We got your price match request (#{pm_id})",
                f"Thanks for sending us a lower price on:\n{title}\n\nOur price: ${price:,.2f}\nPrice you found: ${theirs:,.2f}"
                f"{' (quantity ' + str(n) + ')' if n > 1 else ''}\n{'Link: ' + link if link else 'You sent a photo of the price.'}\n\n"
                "We'll check the seller and the price and email you back, usually within one business day.\n\n"
                f"Dream Auto Builders\n{base}")
    to = mailer.notify_address()
    if to:
        mailer.send(to, f"Price match request #{pm_id}: ${theirs:,.2f} vs our ${price:,.2f}",
                    f"{title}\nOur price: ${price:,.2f}\nTheir price: ${theirs:,.2f} (qty {n})\n"
                    f"{'Link: ' + link if link else 'Photo attached on the admin page.'}\nCustomer: {addr}\n\n"
                    f"Review it: {base}/admin/price-match")
    return _back(slug, pm="sent", n=str(pm_id))


def _site(request):
    return (os.environ.get("SITE_URL") or str(request.base_url)).rstrip("/")


def form_context(request, conn, product):
    """What the product page's price match card needs."""
    q = request.query_params
    form = request.session.pop("pm_form", None) if q.get("pm_error") else None
    acct = accounts.current(request, conn)
    form = form or {"url": "", "their_price": "", "qty": "1", "email": (acct or {}).get("email") or ""}
    return {"sent": q.get("pm") == "sent", "number": q.get("n", "")[:12], "error": q.get("pm_error", "")[:300],
            "form": form, "max_qty": MAX_QTY, "hours": HOURS}


# ------------------------------------------------------------------ customer: claim an approved match into the cart

@router.get("/price-match/claim/{token}")
def claim(request: Request, token: str):
    st = web._state(request)
    with st.pool.connection() as conn:
        r = conn.execute("""SELECT id, product_id, qty, expires_at, used_order_id, status FROM price_matches WHERE token = %s""",
                         (token[:80],)).fetchone()
    if not r or r[5] != "approved" or not r[1]:
        return RedirectResponse("/cart?" + urlencode({"error": "That price match link isn't valid."}), status_code=303)
    pm_id, pid, n, expires, used, _ = r
    if used:
        return RedirectResponse("/cart?" + urlencode({"error": "That price match was already used for an order."}), status_code=303)
    if expires <= dt.datetime.now(dt.timezone.utc):
        return RedirectResponse("/cart?" + urlencode({"error": f"That price match expired {HOURS} hours after it was approved. "
                                                      "Send us a new request and we'll take another look."}), status_code=303)
    matches = dict(request.session.get("pm") or {})
    matches[str(pid)] = token
    request.session["pm"] = matches
    cart = web.cart_dict(request)
    cart[pid] = max(int(n), 1)
    web.save_cart(request, cart)
    return RedirectResponse("/cart?pm=1", status_code=303)


def apply(conn, request, cart, prods):
    """Swap in matched prices for approved, unexpired, unused matches this visitor claimed. Units over the quantity
    the match was approved for aren't added at the matched price: the cart quantity is held to it."""
    tokens = request.session.get("pm") or {}
    if not tokens:
        return
    rows = conn.execute("""SELECT id, product_id, matched_price, qty, expires_at, token FROM price_matches
                           WHERE token = ANY(%s) AND status = 'approved' AND used_order_id IS NULL AND expires_at > now()""",
                        (list(tokens.values()),)).fetchall()
    live = {}
    for pm_id, pid, matched, n, expires, token in rows:
        if tokens.get(str(pid)) == token and pid in prods and pid in cart:
            p = dict(prods[pid])
            p.update(was_price=p["price"], price=float(matched), pm_id=pm_id, pm_qty=int(n), pm_expires=expires,
                     pm_until=expires.astimezone(ZoneInfo("America/New_York")).strftime("%b %-d, %-I:%M %p") + " ET")
            prods[pid] = p
            if cart[pid] > n:
                cart[pid] = int(n)
            live[str(pid)] = token
    if live != tokens:
        request.session["pm"] = live


def ids_in(prods):
    return [p["pm_id"] for p in prods.values() if p.get("pm_id")]


def mark_used(conn, ids, order_id):
    if ids:
        conn.execute("UPDATE price_matches SET used_order_id = %s WHERE id = ANY(%s) AND used_order_id IS NULL", (order_id, ids))


# ------------------------------------------------------------------ admin

def _admin_guard(request):
    from .admin import _guard
    return _guard(request)


@router.get("/admin/price-match", response_class=HTMLResponse)
def admin_list(request: Request, show: str = "pending"):
    stop = _admin_guard(request)
    if stop:
        return stop
    from .admin import _page
    show = show if show in ("pending", "approved", "declined") else "pending"
    st = web._state(request)
    with st.pool.connection() as conn:
        storage = st.get_searcher(conn).storage
        counts = dict(conn.execute("SELECT status, count(*) FROM price_matches GROUP BY status").fetchall())
        cur = conn.execute("""SELECT m.*, p.slug, p.title, p.part_number, p.main_image, p.price AS price_now, b.name AS brand_name,
                                     o.number AS order_number
                              FROM price_matches m LEFT JOIN products p ON p.id = m.product_id
                              LEFT JOIN brands b ON b.id = p.brand_id LEFT JOIN orders o ON o.id = m.used_order_id
                              WHERE m.status = %s ORDER BY m.created_at DESC LIMIT 200""", (show,))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    now = dt.datetime.now(dt.timezone.utc)
    for r in rows:
        r["host"] = host_of(r["url"] or "")
        r["image_url"] = storage.url(r["main_image"]) if r.get("main_image") else None
        r["claim_url"] = f"{_site(request)}/price-match/claim/{r['token']}" if r.get("token") else ""
        r["expired"] = bool(r.get("expires_at") and r["expires_at"] <= now)
        if r["our_price"] and r["their_price"]:
            r["diff"] = float(r["our_price"]) - float(r["their_price"])
            r["pct"] = 100 * r["diff"] / float(r["our_price"])
    return _page(request, "admin_price_match.html", title="Price Match", section="pricematch", rows=rows, show=show,
                 counts=counts, mail_on=mailer.configured(), hours=HOURS, done=request.query_params.get("done", ""))


@router.get("/admin/price-match/{pm_id}/photo")
def admin_photo(request: Request, pm_id: int):
    stop = _admin_guard(request)
    if stop:
        return stop
    st = web._state(request)
    with st.pool.connection() as conn:
        r = conn.execute("SELECT photo_key FROM price_matches WHERE id = %s", (pm_id,)).fetchone()
    if not r or not r[0] or not re.fullmatch(r"[0-9a-f]{24}\.jpg", r[0]):
        return web.not_found(request)
    path = photo_dir() / r[0]
    if not path.exists():
        return web.not_found(request)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@router.post("/admin/price-match/{pm_id}/approve")
def admin_approve(request: Request, pm_id: int, matched_price: str = Form(""), message: str = Form("")):
    stop = _admin_guard(request)
    if stop:
        return stop
    price = _money(matched_price)
    if not price:
        return RedirectResponse("/admin/price-match?done=badprice", status_code=303)
    st = web._state(request)
    token = secrets.token_urlsafe(24)
    with st.pool.connection() as conn:
        r = conn.execute("""UPDATE price_matches SET status = 'approved', matched_price = %s, reply = %s, decided_at = now(),
                            expires_at = now() + make_interval(hours => %s), token = %s
                            WHERE id = %s AND status = 'pending'
                            RETURNING email, qty, expires_at, (SELECT title FROM products WHERE id = product_id), our_price""",
                         (price, message.strip()[:1000] or None, HOURS, token, pm_id)).fetchone()
        conn.commit()
    if r:
        email, n, expires, title, ours = r
        link = f"{_site(request)}/price-match/claim/{token}"
        until = expires.astimezone(ZoneInfo("America/New_York")).strftime("%b %-d at %-I:%M %p") + " Eastern"
        mailer.send(email, "Your price match is approved",
                    f"Good news: we'll match the price on\n{title}\n\nYour price: ${price:,.2f}"
                    f"{' each, for up to ' + str(n) if n > 1 else ''} (was ${ours:,.2f})\n\n"
                    f"{message.strip() + chr(10) + chr(10) if message.strip() else ''}"
                    f"Use this link to put it in your cart at that price:\n{link}\n\n"
                    f"The matched price is saved for {HOURS} hours, until {until}.\n\nDream Auto Builders")
    return RedirectResponse("/admin/price-match?done=approved", status_code=303)


@router.post("/admin/price-match/{pm_id}/decline")
def admin_decline(request: Request, pm_id: int, reason: str = Form("")):
    stop = _admin_guard(request)
    if stop:
        return stop
    st = web._state(request)
    with st.pool.connection() as conn:
        r = conn.execute("""UPDATE price_matches SET status = 'declined', reply = %s, decided_at = now()
                            WHERE id = %s AND status = 'pending'
                            RETURNING email, (SELECT title FROM products WHERE id = product_id)""",
                         (reason.strip()[:1000] or None, pm_id)).fetchone()
        conn.commit()
    if r:
        email, title = r
        mailer.send(email, "About your price match request",
                    f"Thanks for sending us a lower price on\n{title}\n\nWe weren't able to match this one."
                    f"{chr(10) + chr(10) + 'Why: ' + reason.strip() if reason.strip() else ''}\n\n"
                    "If you find another lower price from an authorized seller, send it our way.\n\nDream Auto Builders")
    return RedirectResponse("/admin/price-match?done=declined", status_code=303)
