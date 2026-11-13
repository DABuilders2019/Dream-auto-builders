"""Nearby repair shops from Google Maps for orders with parts that need installing.

Shown on the order confirmation page, in the confirmation email and on the install page that the follow-up
email links to. Shops are independent businesses found on Google Maps: every list carries the disclaimer
from config/store.json and Google's attribution.

Google's rules followed here (Places API policies):
  * results shown without a Google map carry the "Google Maps" attribution;
  * only a shop's place ID is stored (allowed indefinitely); names and addresses are looked up again
    whenever they're shown, never kept;
  * the site's Terms of Use and Privacy Policy must mention Google's (see docs/INSTALLERS.md).

Setup: GOOGLE_MAPS_API_KEY in Render (Places API (New) enabled). Without it, nothing is shown.
Cost: one Text Search (Pro) call per list shown; Google's free allowance covers 5,000 a month.
"""
import datetime as dt
import functools
import logging
import math
import os
import secrets
import threading

import httpx
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import mailer, web
from . import orders as O

router = APIRouter()
log = logging.getLogger("store.installers")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{}"
FIELDS = ("places.id,places.displayName,places.formattedAddress,places.googleMapsUri,"     # Pro fields only
          "places.location,places.businessStatus")
DETAIL_FIELDS = "id,displayName,formattedAddress,googleMapsUri"
DEFAULT_CATEGORIES = ["suspension", "exhaust", "brakes", "tires", "wheels", "intake", "engine", "drivetrain",
                      "exterior", "lighting", "performance"]
DEFAULT_DISCLAIMER = ("These shops are independent businesses found on Google Maps. They are not affiliated with, "
                      "endorsed by, or partners of Dream Auto Builders. Please confirm pricing, availability and that "
                      "they install this type of part before booking.")
_transport = None          # tests swap in a fake Google


def api_key():
    return os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()


def configured():
    return bool(api_key())


def cfg():
    c = O.store_config().get("installers") or {}
    return {"categories": c.get("categories") or DEFAULT_CATEGORIES, "disclaimer": c.get("disclaimer") or DEFAULT_DISCLAIMER,
            "count": int(c.get("count") or 5), "survey_days": int(c.get("survey_days") or 10)}


def _client():
    return httpx.Client(timeout=8, transport=_transport)


CLOSED = {"CLOSED_TEMPORARILY": "Temporarily closed",
          "CLOSED_PERMANENTLY": "Permanently closed"}


@functools.lru_cache(maxsize=4096)
def zip_point(zip_):
    """(lat, lng) of a US ZIP code's center from the bundled zipcodes data, or None. Used as the starting point
    for shop distances, so no extra Google lookup is needed."""
    z = (zip_ or "").strip()[:5]
    if len(z) != 5 or not z.isdigit():
        return None
    try:
        import zipcodes
        m = zipcodes.matching(z)
    except Exception:            # package missing or bad data: just no distances
        return None
    try:
        return (float(m[0]["lat"]), float(m[0]["long"])) if m else None
    except (KeyError, TypeError, ValueError):
        return None


def miles(a, b):
    """Straight-line distance in miles between two (lat, lng) points."""
    (la1, lo1), (la2, lo2) = a, b
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _shop(p, origin=None):
    loc = p.get("location") or {}
    dist = None
    if origin and loc.get("latitude") is not None and loc.get("longitude") is not None:
        dist = round(miles(origin, (loc["latitude"], loc["longitude"])), 1)
    closed = CLOSED.get(p.get("businessStatus") or "")
    return {"id": p.get("id"), "name": (p.get("displayName") or {}).get("text") or "Repair shop",
            "address": p.get("formattedAddress") or "", "url": p.get("googleMapsUri") or
            f"https://www.google.com/maps/search/?api=1&query=Google&query_place_id={p.get('id')}",
            "miles": dist, "closed": closed,
            "why": f"Google Maps lists this shop as {closed.lower()}, so it can't take your install right now." if closed else None}


def nearby(order):
    """Up to N repair shops near the order's shipping address, or [] (no key, no address, or Google failed)."""
    a = order.get("ship_address") or {}
    zip_, city, state = (a.get("postal_code") or "").strip(), (a.get("city") or "").strip(), (a.get("state") or "").strip()
    if not configured() or not (zip_ or city):
        return []
    origin = zip_point(zip_)
    body = {"textQuery": f"auto repair shop near {city} {state} {zip_}".strip(), "includedType": "car_repair",
            "pageSize": cfg()["count"] + 2, "regionCode": "US"}     # a couple extra in case some are closed for good
    if origin:   # keep results around the shipping address
        body["locationBias"] = {"circle": {"center": {"latitude": origin[0], "longitude": origin[1]}, "radius": 25000.0}}
    try:
        with _client() as h:
            r = h.post(SEARCH_URL, json=body, headers={"X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": FIELDS})
        if r.status_code != 200:
            log.warning("Google Places search answered %s: %s", r.status_code, r.text[:300])
            return []
        shops = [_shop(p, origin) for p in r.json().get("places") or [] if p.get("id")]
        shops = [x for x in shops if x["closed"] != "Permanently closed"][:cfg()["count"]]
        # open shops first, nearest first; temporarily closed ones last, with the reason shown
        shops.sort(key=lambda x: (bool(x["closed"]), x["miles"] if x["miles"] is not None else 9e9))
        return shops
    except httpx.HTTPError as e:
        log.warning("Google Places search failed: %s", e)
        return []


def details(place_id):
    """A shop's current name, address and map link from its place ID (for shops picked earlier)."""
    if not (configured() and place_id):
        return None
    try:
        with _client() as h:
            r = h.get(DETAILS_URL.format(place_id), headers={"X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": DETAIL_FIELDS})
        return _shop(r.json()) if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


def needs_install(conn, s, order):
    """True when the order has a part in a category that usually needs a shop (lift kits, exhaust, tires...)."""
    ids = [it["product_id"] for it in order["items"] if it.get("product_id")]
    if not ids:
        return False
    wanted = set(cfg()["categories"])
    rows = conn.execute("SELECT category_id FROM products WHERE id = ANY(%s)", (ids,)).fetchall()
    for (cat,) in rows:
        top = cat
        while top and s.categories.get(top, {}).get("parent"):
            top = s.categories[top]["parent"]
        if top in wanted or cat in wanted:
            return True
    return False


def feedback_row(conn, order_id, create=False):
    r = conn.execute("""SELECT token, place_id, other_shop, survey_sent_at, answered_at, installed, rating, recommend, comment
                        FROM install_feedback WHERE order_id = %s""", (order_id,)).fetchone()
    if not r and create:
        tok = secrets.token_urlsafe(18)
        conn.execute("INSERT INTO install_feedback (order_id, token) VALUES (%s, %s) ON CONFLICT DO NOTHING", (order_id, tok))
        return feedback_row(conn, order_id)
    keys = ["token", "place_id", "other_shop", "survey_sent_at", "answered_at", "installed", "rating", "recommend", "comment"]
    return dict(zip(keys, r)) if r else None


def for_order(conn, s, order):
    """What the order page and confirmation email show: {"shops", "picked", "token", "disclaimer"} or None."""
    if order["status"] not in ("paid", "on_hold", "shipped") or not configured() or not needs_install(conn, s, order):
        return None
    shops = nearby(order)
    if not shops:
        return None
    fb = feedback_row(conn, order["id"], create=True)
    a = order.get("ship_address") or {}
    return {"shops": shops, "picked": fb["place_id"], "token": fb["token"], "disclaimer": cfg()["disclaimer"],
            "near": ", ".join(x for x in ((a.get("city") or "").strip(),
                                          f"{(a.get('state') or '').strip()} {(a.get('postal_code') or '').strip()}".strip()) if x),
            "has_miles": any(x.get("miles") is not None for x in shops)}


# ------------------------------------------------------------------ customer pages

@router.post("/order/{number}/installer")
def pick_from_order(request: Request, number: str, place_id: str = Form("")):
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.get(conn, number=number.upper())
        if not o or o["number"] not in request.session.get("orders", []):
            return web.not_found(request)
        feedback_row(conn, o["id"], create=True)
        _pick(conn, o["id"], place_id)
    return RedirectResponse(f"/order/{o['number']}?picked=1#install", status_code=303)


def _pick(conn, order_id, place_id):
    place_id = (place_id or "").strip()[:300]
    conn.execute("UPDATE install_feedback SET place_id = %s, picked_at = CASE WHEN %s::text = '' THEN NULL ELSE now() END WHERE order_id = %s",
                 (place_id or None, place_id, order_id))


def _order_for_token(conn, token):
    r = conn.execute("SELECT order_id FROM install_feedback WHERE token = %s", (token,)).fetchone()
    return O.get(conn, order_id=r[0]) if r else None


@router.get("/install/{token}", response_class=HTMLResponse)
def install_page(request: Request, token: str, pick: str = "", thanks: str = ""):
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        o = _order_for_token(conn, token)
        if not o:
            return web.not_found(request)
        if pick:
            _pick(conn, o["id"], pick)
        fb = feedback_row(conn, o["id"])
        shops = nearby(o)
        if fb["place_id"] and not any(x["id"] == fb["place_id"] for x in shops):
            d = details(fb["place_id"])
            if d:
                shops.insert(0, d)
        return web.render(request, conn, s, "install.html", o=o, fb=fb, shops=shops, token=token, thanks=thanks,
                          disclaimer=cfg()["disclaimer"], title="Getting your parts installed", noindex=True)


@router.post("/install/{token}")
def install_answer(request: Request, token: str, installed: str = Form(""), place_id: str = Form(""),
                   other_shop: str = Form(""), rating: str = Form(""), recommend: str = Form(""), comment: str = Form("")):
    st = web._state(request)
    with st.pool.connection() as conn:
        o = _order_for_token(conn, token)
        if not o:
            return web.not_found(request)
        inst = installed if installed in ("yes", "not_yet", "myself", "no") else None
        rt = int(rating) if rating.isdigit() and 1 <= int(rating) <= 5 else None
        rec = {"yes": True, "no": False}.get(recommend)
        pid = place_id if place_id and place_id != "other" else None
        conn.execute("""UPDATE install_feedback SET installed = %s, place_id = COALESCE(%s, place_id),
                            picked_at = CASE WHEN %s::text IS NOT NULL THEN now() ELSE picked_at END,
                            other_shop = %s, rating = %s, recommend = %s, comment = %s, answered_at = now()
                        WHERE order_id = %s""",
                     (inst, pid, pid, (other_shop.strip()[:200] or None) if place_id == "other" else None, rt, rec,
                      comment.strip()[:2000] or None, o["id"]))
        O.add_event(conn, o["id"], "Customer answered the install survey")
    return RedirectResponse(f"/install/{token}?thanks=1", status_code=303)


# ------------------------------------------------------------------ follow-up survey email

def survey_text(site, o, token):
    return "\n".join([f"Hi {(o['name'] or '').split(' ')[0]},".replace(" ,", ","), "",
                      f"Your order {o['number']} should have arrived. Did you get it installed?",
                      "Tell us how it went (2 questions):", f"{site}/install/{token}", "",
                      "If you used a shop, your answer helps us find great local installers for other customers.", "",
                      "Dream Auto Builders"])


def survey_html(site, o, token):
    first = (o["name"] or "").split(" ")[0]
    return (f'<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto;color:#111113;line-height:1.5">'
            f'<img src="{site}/static/img/logo-word.png" alt="Dream Auto Builders" width="171" height="44">'
            f'<h1 style="font-size:21px">How did the install go{", " + first if first else ""}?</h1>'
            f'<p>Your order <b>{o["number"]}</b> should have arrived. Did you get it installed? It takes about a minute.</p>'
            f'<p><a href="{site}/install/{token}" style="background:#a8131a;color:#fff;text-decoration:none;padding:11px 18px;'
            f'font-weight:bold;display:inline-block">Tell us how it went</a></p>'
            f'<p style="color:#5e5e66;font-size:13px">If you used a shop, your answer helps us find great local installers '
            f'for other customers.</p></div>')


def send_due_surveys(conn, s, site, now=None):
    """Email the install survey to customers whose order shipped `survey_days` ago. Returns how many were sent."""
    if not mailer.configured():
        return 0
    days = cfg()["survey_days"]
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = conn.execute("""SELECT o.id FROM orders o LEFT JOIN install_feedback f ON f.order_id = o.id
                           WHERE o.status = 'shipped' AND o.shipped_at < %s AND o.shipped_at > %s
                             AND f.survey_sent_at IS NULL AND f.answered_at IS NULL""",
                        (now - dt.timedelta(days=days), now - dt.timedelta(days=days + 30))).fetchall()
    sent = 0
    for (oid,) in rows:
        o = O.get(conn, order_id=oid)
        if not o or not o["email"] or not needs_install(conn, s, o):
            continue
        fb = feedback_row(conn, oid, create=True)
        if mailer.send(o["email"], f"How did the install go? (order {o['number']})", survey_text(site, o, fb["token"]),
                       survey_html(site, o, fb["token"])):
            conn.execute("UPDATE install_feedback SET survey_sent_at = now() WHERE order_id = %s", (oid,))
            O.add_event(conn, oid, f"Install survey emailed to {o['email']}")
            sent += 1
    return sent


def launch(state):
    """Hourly: send install surveys that are due (needs SITE_URL so email links point at the live site)."""
    stop = threading.Event()
    site = (os.environ.get("SITE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")   # Render sets the latter

    def loop():
        while not stop.wait(3600):
            if not site:
                continue
            try:
                with state.pool.connection() as conn:
                    send_due_surveys(conn, state.get_searcher(conn), site)
            except Exception:      # noqa: BLE001 - keep the scheduler alive
                log.exception("install surveys")
    threading.Thread(target=loop, daemon=True, name="install-surveys").start()
    return stop


# ------------------------------------------------------------------ admin: installer leads

def leads(conn):
    rows = conn.execute("""SELECT place_id, count(*) AS picks,
                                  count(*) FILTER (WHERE installed = 'yes') AS installs,
                                  round(avg(rating), 1) AS rating, count(rating) AS ratings,
                                  count(*) FILTER (WHERE recommend) AS recommend_yes,
                                  count(recommend) AS recommend_answers,
                                  array_remove(array_agg(comment ORDER BY answered_at DESC), NULL) AS comments,
                                  max(coalesce(answered_at, picked_at)) AS last
                           FROM install_feedback WHERE place_id IS NOT NULL
                           GROUP BY place_id ORDER BY count(*) FILTER (WHERE installed = 'yes') DESC, avg(rating) DESC NULLS LAST,
                                    count(*) DESC LIMIT 100""").fetchall()
    keys = ["place_id", "picks", "installs", "rating", "ratings", "recommend_yes", "recommend_answers", "comments", "last"]
    out = [dict(zip(keys, r)) for r in rows]
    for x in out:
        x["shop"] = details(x["place_id"])          # looked up fresh: Google's details aren't stored
    others = conn.execute("""SELECT f.other_shop, f.rating, f.recommend, f.comment, f.answered_at, o.ship_address->>'city',
                                    o.ship_address->>'state'
                             FROM install_feedback f JOIN orders o ON o.id = f.order_id
                             WHERE f.other_shop IS NOT NULL ORDER BY f.answered_at DESC LIMIT 100""").fetchall()
    stats = conn.execute("""SELECT count(*) FILTER (WHERE survey_sent_at IS NOT NULL), count(answered_at),
                                   count(*) FILTER (WHERE installed = 'myself'), count(place_id)
                            FROM install_feedback""").fetchone()
    return out, others, stats
