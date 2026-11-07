"""First-party visit and click tracking, and the admin Analytics page.

Anonymous: a random visitor id lives in the shopper's session cookie; no names, emails or IP addresses are
stored with events. Browsers sending Global Privacy Control (Sec-GPC: 1) or Do Not Track aren't tracked, and
neither is anyone signed in to the admin. Events older than 180 days are deleted.

Server side: page views (product pages, searches, brand and category pages...), add to cart, checkout started,
purchase. Browser side: clicks on anything marked data-track="..." (slides, brand tiles, category cards,
header buttons, installer links) are sent to /api/t.
"""
import logging
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

router = APIRouter()
log = logging.getLogger("store.tracking")
KEEP_DAYS = 180
SKIP = ("/admin", "/static", "/photos", "/api", "/stripe", "/robots.txt", "/favicon", "/login", "/install/")


def _ok_to_track(request):
    if request.headers.get("sec-gpc") == "1" or request.headers.get("dnt") == "1":
        return False
    try:
        return not request.session.get("admin")
    except AssertionError:
        return False


def visitor(request):
    v = request.session.get("v")
    if not v:
        v = request.session["v"] = secrets.token_urlsafe(12)
    return v


def record(request, kind, target=None, path=None, conn=None, visitor_id=None):
    """Save one event. Never raises: tracking must not break the shop."""
    try:
        if visitor_id is None:
            if not _ok_to_track(request):
                return
            visitor_id = visitor(request)
        ref = request.headers.get("referer") if request is not None else None
        host = urlparse(ref).hostname if ref else None
        own = request.url.hostname if request is not None else None
        args = (visitor_id, kind, (path or (request.url.path if request is not None else None) or "")[:300],
                (str(target)[:300] if target is not None else None), host if host and host != own else None)
        sql = "INSERT INTO site_events (visitor, kind, path, target, referrer) VALUES (%s, %s, %s, %s, %s)"
        if conn is not None:
            conn.execute(sql, args)
        else:
            from . import web
            with web._state(request).pool.connection() as c:
                c.execute(sql, args)
    except Exception:      # noqa: BLE001
        log.debug("tracking failed", exc_info=True)


def install(app):
    """Page views: recorded after a page is sent (inside the session middleware, so the visitor id persists)."""
    @app.middleware("http")
    async def track_pages(request, call_next):
        try:
            if request.method == "GET" and not request.url.path.startswith(SKIP) and _ok_to_track(request):
                visitor(request)          # set before the page is sent, so the session cookie carries it
        except Exception:      # noqa: BLE001
            pass
        response = await call_next(request)
        try:
            path = request.url.path
            if (request.method == "GET" and response.status_code == 200 and not path.startswith(SKIP)
                    and "text/html" in response.headers.get("content-type", "")):
                if path.startswith("/p/"):
                    kind, target = "product_view", path[3:]
                elif path == "/search" and request.query_params.get("q"):
                    kind, target = "search", request.query_params.get("q", "").strip().lower()[:120]
                else:
                    kind, target = "page_view", None
                record(request, kind, target)
        except Exception:      # noqa: BLE001
            pass
        return response


@router.post("/api/t")
async def click(request: Request):
    """Clicks from the browser (navigator.sendBeacon): {"t": "slide:2", "p": "/"}."""
    try:
        body = await request.json()
    except Exception:      # noqa: BLE001
        return Response(status_code=204)
    t = str(body.get("t") or "")[:120]
    p = str(body.get("p") or "")[:300]
    if re.fullmatch(r"[\w:./ &'()+-]{1,120}", t):
        record(request, "click", t, path=p or None)
    return Response(status_code=204)


def cleanup_loop(state):
    stop = threading.Event()

    def loop():
        while True:
            try:
                with state.pool.connection() as conn:
                    conn.execute("DELETE FROM site_events WHERE at < now() - make_interval(days => %s)", (KEEP_DAYS,))
            except Exception:      # noqa: BLE001
                log.debug("cleanup failed", exc_info=True)
            if stop.wait(24 * 3600):
                return
    threading.Thread(target=loop, daemon=True, name="tracking-cleanup").start()
    return stop


# ------------------------------------------------------------------ admin: Analytics

def report(conn, days):
    since = f"now() - interval '{int(days)} days'"
    q = lambda sql, *a: conn.execute(sql.replace("{since}", since), a).fetchall()   # noqa: E731
    funnel = q("""SELECT count(DISTINCT visitor),
                         count(DISTINCT visitor) FILTER (WHERE kind = 'product_view'),
                         count(DISTINCT visitor) FILTER (WHERE kind = 'cart_add'),
                         count(DISTINCT visitor) FILTER (WHERE kind = 'checkout'),
                         count(DISTINCT visitor) FILTER (WHERE kind = 'purchase'),
                         count(*) FILTER (WHERE kind IN ('page_view', 'product_view', 'search'))
                  FROM site_events WHERE at > {since}""")[0]
    products = q("""SELECT e.target, p.title, b.name, count(*), count(DISTINCT e.visitor)
                    FROM site_events e LEFT JOIN products p ON p.slug = e.target LEFT JOIN brands b ON b.id = p.brand_id
                    WHERE e.kind = 'product_view' AND e.at > {since} GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 25""")
    searches = q("""SELECT target, count(*), count(DISTINCT visitor) FROM site_events
                    WHERE kind = 'search' AND at > {since} GROUP BY 1 ORDER BY 2 DESC LIMIT 25""")
    clicks = q("""SELECT target, count(*), count(DISTINCT visitor) FROM site_events
                  WHERE kind = 'click' AND at > {since} GROUP BY 1 ORDER BY 2 DESC LIMIT 40""")
    referrers = q("""SELECT referrer, count(DISTINCT visitor) FROM site_events
                     WHERE referrer IS NOT NULL AND at > {since} GROUP BY 1 ORDER BY 2 DESC LIMIT 15""")
    pages = q("""SELECT path, count(*) FROM site_events WHERE kind = 'page_view' AND at > {since}
                 GROUP BY 1 ORDER BY 2 DESC LIMIT 15""")
    # carts: visitors who added to cart and didn't buy (anonymous: no way to contact them)
    abandoned = q("""SELECT count(DISTINCT a.visitor) FROM site_events a
                     WHERE a.kind = 'cart_add' AND a.at > {since}
                       AND NOT EXISTS (SELECT 1 FROM site_events b WHERE b.visitor = a.visitor AND b.kind = 'purchase' AND b.at > a.at)""")[0][0]
    left_items = q("""SELECT p.title, b.name, count(DISTINCT a.visitor) FROM site_events a
                      JOIN products p ON p.id::text = a.target JOIN brands b ON b.id = p.brand_id
                      WHERE a.kind = 'cart_add' AND a.at > {since}
                        AND NOT EXISTS (SELECT 1 FROM site_events x WHERE x.visitor = a.visitor AND x.kind = 'purchase' AND x.at > a.at)
                      GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 15""")
    checkouts = q("""SELECT o.number, r.email, r.name, o.subtotal, r.consent, r.emailed_at, r.recovered_at, o.abandoned_at,
                            (SELECT string_agg(i.brand_name || ' ' || i.part_number, ', ') FROM order_items i WHERE i.order_id = o.id)
                     FROM checkout_recovery r JOIN orders o ON o.id = r.order_id
                     WHERE o.abandoned_at > {since} ORDER BY o.abandoned_at DESC LIMIT 100""")
    return dict(funnel=funnel, products=products, searches=searches, clicks=clicks, referrers=referrers, pages=pages,
                abandoned=abandoned, left_items=left_items, checkouts=checkouts)


@router.get("/admin/analytics", response_class=HTMLResponse)
def analytics(request: Request, days: int = 30):
    from . import admin, web
    stop = admin._guard(request)
    if stop:
        return stop
    days = days if days in (1, 7, 30, 90, 180) else 30
    with web._state(request).pool.connection() as conn:
        r = report(conn, days)
    return admin._page(request, "admin_analytics.html", title="Analytics", section="analytics", days=days, r=r)
