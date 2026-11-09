"""Admin page: upload a brand's SEMA files, run the import and photo download, see catalog status.

Turned on by setting ADMIN_PASSWORD. Sign-in is kept in the same signed cookie as the cart.
"""
import io
import hmac
import os
import pathlib
import re
import shutil
import tempfile
import time
import zipfile
from typing import List

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.background import BackgroundTask

from importer import brands as brand_lookup
from importer import report
from . import queries as Q
from . import web

router = APIRouter(prefix="/admin")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp")


def admin_password():
    return os.environ.get("ADMIN_PASSWORD", "")


def is_admin(request):
    return bool(admin_password()) and request.session.get("admin") == _token()


def _token():
    """Changes when the password changes, which signs everyone out."""
    return hmac.new(admin_password().encode(), b"dab-admin", "sha256").hexdigest()[:24]


def _guard(request):
    if not admin_password():
        return web.not_found(request)
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=303)
    return None


def _page(request, name, **ctx):
    return web.templates.TemplateResponse(request, name, {"request": request, **ctx},
                                          headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"})


# ------------------------------------------------------------------ sign in

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not admin_password():
        return web.not_found(request)
    return _page(request, "login.html", title="Admin sign in", action="/admin/login", error=None, admin=True)


@router.post("/login")
def login(request: Request, password: str = Form("")):
    if not admin_password():
        return web.not_found(request)
    if hmac.compare_digest(password.encode(), admin_password().encode()):
        request.session["admin"] = _token()
        return RedirectResponse("/admin", status_code=303)
    time.sleep(1)
    return _page(request, "login.html", title="Admin sign in", action="/admin/login", error="That password isn't right.", admin=True)


@router.post("/logout")
def logout(request: Request):
    request.session.pop("admin", None)
    return RedirectResponse("/", status_code=303)


# ------------------------------------------------------------------ status page

@router.get("", response_class=HTMLResponse)
def admin_home(request: Request, error: str = "", started: str = "", saved: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        with conn.cursor() as cur:
            cur.execute("""
                SELECT b.id, b.name, b.aaia_id, b.logo_key,
                       count(p.id) FILTER (WHERE p.status = 'active'),
                       count(p.id) FILTER (WHERE p.status = 'hidden'),
                       count(p.id) FILTER (WHERE p.status = 'discontinued'),
                       count(p.id) FILTER (WHERE p.status = 'active' AND p.main_image IS NOT NULL),
                       count(p.id) FILTER (WHERE p.status = 'active' AND p.fitment_status = 'vehicle_specific'),
                       (SELECT max(finished_at) FROM import_runs r WHERE r.brand_id = b.id AND r.step = 'catalog' AND r.status = 'ok'),
                       (SELECT count(*) FROM product_images i JOIN products p2 ON p2.id = i.product_id
                        WHERE p2.brand_id = b.id AND p2.status = 'active' AND i.status = 'pending'),
                       b.display_price, b.price_markup,
                       (SELECT jsonb_object_agg(k, n) FROM (SELECT e.key AS k, count(*) AS n FROM products p3, jsonb_each(p3.prices) e
                         WHERE p3.brand_id = b.id AND p3.status <> 'discontinued' GROUP BY e.key) x),
                       count(p.id) FILTER (WHERE p.status <> 'discontinued' AND p.prices IS NULL),
                       b.banner_key
                FROM brands b LEFT JOIN products p ON p.brand_id = b.id
                GROUP BY b.id ORDER BY b.name""")
            cols = ["id", "name", "aaia", "logo_key", "active", "hidden", "discontinued", "with_photo", "with_fitment",
                    "last_import", "photos_pending", "display_price", "markup", "price_types", "no_price_data", "banner_key"]
            brands = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("""SELECT r.id, r.brand_id, r.step, r.status, r.started_at, r.finished_at, r.error
                           FROM import_runs r ORDER BY r.id DESC LIMIT 12""")
            runs = [dict(zip(["id", "brand", "step", "status", "started", "finished", "error"], r)) for r in cur.fetchall()]
    from importer.catalog import RETAIL_TYPES
    for b in brands:
        b["logo"] = s.storage.url(b["logo_key"]) if b["logo_key"] else None
        b["banner"] = s.storage.url(b["banner_key"]) if b["banner_key"] else None
        extra = [t for t in (b["display_price"] or []) if t not in RETAIL_TYPES]
        b["price_from"] = extra[0] if extra else "retail"
        b["cost_types"] = sorted(t for t in (b["price_types"] or {}) if t not in RETAIL_TYPES)
    job = st.jobs.current()
    return _page(request, "admin.html", title="Catalog admin", brands=brands, runs=runs, job=job, error=error, saved=saved,
                 started=started, data_dir=str(st.data_dir), section="catalog", price_names=PRICE_NAMES)


PRICE_NAMES = {"RET": "Retail", "RMP": "MAP (minimum advertised)", "LST": "List", "JBR": "Jobber", "WD1": "Warehouse distributor",
               "WD2": "Warehouse distributor 2", "DLR": "Dealer", "NET": "Net", "EXP": "Export", "SUG": "Suggested",
               "BAS": "Base", "CST": "Cost", "DST": "Distributor", "RES": "Reseller", "MSRP": "MSRP"}


@router.post("/pricing/{brand_id}")
def brand_pricing(request: Request, brand_id: str, price_from: str = Form("retail"), markup: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from importer.catalog import RETAIL_TYPES, choose_price
    st = web._state(request)
    try:
        pct = float(markup) if markup.strip() else None
    except ValueError:
        return _back(error="The markup has to be a number, like 35.")
    if price_from != "retail" and (pct is None or not 0 <= pct <= 300):
        return _back(error="Enter a markup between 0 and 300 percent for a dealer/jobber price.")
    pref = list(RETAIL_TYPES) + ([price_from.strip().upper()[:6]] if price_from != "retail" else [])
    with st.pool.connection() as conn:
        name = conn.execute("SELECT name FROM brands WHERE id = %s", (brand_id,)).fetchone()
        if not name:
            return _back(error="Unknown brand.")
        rows = conn.execute("SELECT id, prices, status FROM products WHERE brand_id = %s AND status <> 'discontinued' AND prices IS NOT NULL",
                            (brand_id,)).fetchall()
        changed, shown = [], 0
        for pid, prices, status in rows:
            price, ptype = choose_price(prices or {}, pref, pct if price_from != "retail" else None)
            new_status = "active" if price else "hidden"
            shown += new_status == "active"
            changed.append((price, ptype, new_status, pid))
        with conn.transaction():
            conn.execute("UPDATE brands SET display_price = %s, price_markup = %s WHERE id = %s",
                         (pref, pct if price_from != "retail" else None, brand_id))
            with conn.cursor() as cur:
                cur.executemany("UPDATE products SET price = %s, price_type = %s, status = %s, updated_at = now() WHERE id = %s", changed)
    st.reset()
    rule = "retail prices only" if price_from == "retail" else f"retail price when given, otherwise {PRICE_NAMES.get(pref[-1], pref[-1])} + {pct:g}%"
    return _back(saved=f"{name[0]} pricing saved ({rule}): {shown:,} of {len(rows):,} parts now have a price and show on the site.")


# ------------------------------------------------------------------ uploads

def _head(f, n=4096):
    return f.read(n).decode("utf-8", "ignore")


def _xml_kind(text):
    if "<PIES" in text:
        return "pies"
    if "<ACES" in text:
        return "aces"
    return None


def classify(path):
    """What a SEMA download is: pies, aces, vehicles (Admark sheet), assets (photo zip) or None."""
    name = path.name.lower()
    if name.endswith(".xml"):
        with open(path, "rb") as f:
            return _xml_kind(_head(f))
    if name.endswith(".xlsx"):
        return "vehicles"
    if name.endswith(".zip"):
        try:
            z = zipfile.ZipFile(path)
        except zipfile.BadZipFile:
            return None
        names = [n for n in z.namelist() if not n.endswith("/")]
        xml = [n for n in names if n.lower().endswith(".xml")]
        if len(xml) == 1:
            with z.open(xml[0]) as f:
                return _xml_kind(_head(f))
        if any(n.lower().endswith(".xlsx") for n in names) and not xml:
            return "vehicles"
        if names and sum(n.lower().endswith(IMAGE_EXT) for n in names) >= len(names) / 2:
            return "assets"
    return None


def _extract_xml(zip_path, dest_dir):
    z = zipfile.ZipFile(zip_path)
    member = next(n for n in z.namelist() if n.lower().endswith(".xml"))
    out = pathlib.Path(dest_dir) / pathlib.Path(member).name
    with z.open(member) as src, open(out, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    return out


def _safe_name(name):
    name = pathlib.Path(name or "upload").name
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:150] or "upload"


@router.post("/upload")
def upload(request: Request, files: List[UploadFile] = File(...), brand_name: str = Form(""), photos: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    st = web._state(request)
    if st.jobs.running() or st.sema.running():
        return _back(error="An import is already running. Wait for it to finish, then upload again.")
    work = pathlib.Path(tempfile.mkdtemp(prefix="upload-", dir=st.uploads_dir))
    found = {}
    try:
        for f in files:
            if not f.filename:
                continue
            path = work / _safe_name(f.filename)
            with open(path, "wb") as out:
                shutil.copyfileobj(f.file, out, 1 << 20)
            kind = classify(path)
            if kind is None:
                raise ValueError(f"“{f.filename}” isn't a SEMA PIES, ACES, Admark vehicle sheet or digital assets file.")
            if kind in found:
                raise ValueError(f"Two {kind.upper()} files were uploaded. Upload one brand at a time.")
            found[kind] = path
        if "pies" not in found:
            raise ValueError("Include the brand's PIES file (the product data). ACES, the Admark vehicle sheet and photos are optional.")
        pies_xml = found["pies"] if found["pies"].suffix.lower() == ".xml" else _extract_xml(found["pies"], work)
        aaia, label = brand_lookup.peek(str(pies_xml))
        vehicles = found.get("vehicles")
        m = re.search(r"SDC_([A-Z0-9]{4})_", vehicles.name) if vehicles else None
        if m and aaia and m.group(1) != aaia:
            raise ValueError(f"The Admark vehicle sheet is for brand code {m.group(1)}, but the PIES file is for {aaia}.")
        with st.pool.connection() as conn:
            brand = brand_lookup.resolve(conn, aaia, label, brand_name.strip() or None)
    except ValueError as e:
        shutil.rmtree(work, ignore_errors=True)
        return _back(error=str(e))

    final = st.uploads_dir / brand["id"]          # keep only the latest files for each brand
    shutil.rmtree(final, ignore_errors=True)
    work.rename(final)
    rel = lambda p: str(final / p.relative_to(work)) if p else None   # noqa: E731
    args = ["load", "--brand", brand["id"], "--pies", rel(pies_xml)] if not brand.get("new") else \
           ["load", "--brand-name", brand["name"], "--pies", rel(pies_xml)]
    if found.get("aces"):
        args += ["--aces", rel(found["aces"])]
    if vehicles:
        args += ["--vehicles", rel(vehicles)]
    steps = [(f"Load {brand['name']} catalog", args)]
    if photos:
        p_args = ["photos", "--brand", brand["id"], "--workers", os.environ.get("PHOTO_WORKERS", "4")]
        if found.get("assets"):
            p_args += ["--zip", rel(found["assets"])]
        steps.append((f"Download {brand['name']} photos", p_args))
    title = f"{'New brand: ' if brand.get('new') else ''}{brand['name']}"
    if not st.jobs.start(title, steps, brand["id"]):
        return _back(error="An import is already running.")
    return _back(started=brand["name"])


@router.post("/photos/{brand_id}")
def photos(request: Request, brand_id: str):
    stop = _guard(request)
    if stop:
        return stop
    st = web._state(request)
    with st.pool.connection() as conn:
        name = conn.execute("SELECT name FROM brands WHERE id = %s", (brand_id,)).fetchone()
    if not name:
        return _back(error="Unknown brand.")
    assets = next((p for p in (st.uploads_dir / brand_id).glob("*") if classify(p) == "assets"), None) \
        if (st.uploads_dir / brand_id).exists() else None
    args = ["photos", "--brand", brand_id, "--retry-failed", "--workers", os.environ.get("PHOTO_WORKERS", "4")] + \
        (["--zip", str(assets)] if assets else [])
    if st.sema.running() or not st.jobs.start(f"{name[0]} photos", [(f"Download {name[0]} photos", args)], brand_id):
        return _back(error="An import is already running.")
    return _back(started=name[0] + " photos")


@router.post("/logo/{brand_id}")
def brand_logo(request: Request, brand_id: str, file: UploadFile = File(None), url: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from importer import photos as ph
    st = web._state(request)
    with st.pool.connection() as conn:
        name = conn.execute("SELECT name FROM brands WHERE id = %s", (brand_id,)).fetchone()
        if not name:
            return _back(error="Unknown brand.")
        try:
            if file is not None and file.filename:
                raw = file.file.read(10 * 1024 * 1024 + 1)
                if len(raw) > 10 * 1024 * 1024:
                    raise ValueError("that file is over 10 MB")
                source = "upload"
            elif url.strip().lower().startswith(("http://", "https://")):
                raw, source = ph.fetch_url(url.strip()), "upload"
            else:
                raise ValueError("choose a logo file or paste a link to one")
            conn.autocommit = False
            ph.save_logo(conn, brand_id, raw, source)
        except Exception as e:      # noqa: BLE001 - bad image, unreachable link: say so on the page
            conn.rollback()
            msg = str(e) if isinstance(e, ValueError) else "that isn't an image the site can read (use PNG, JPG or WebP)"
            return _back(error=f"{name[0]} logo not saved: {msg}.")
        finally:
            conn.autocommit = True
    st.reset()
    return _back(saved=f"{name[0]} logo saved. It shows in Featured Brands within a few minutes.")


@router.get("/report/{brand_id}")
def photo_report(request: Request, brand_id: str):
    stop = _guard(request)
    if stop:
        return stop
    st = web._state(request)
    out = pathlib.Path(tempfile.mkdtemp()) / f"{brand_id}-photo-report.xlsx"
    with st.pool.connection() as conn:
        if not conn.execute("SELECT 1 FROM brands WHERE id = %s", (brand_id,)).fetchone():
            return web.not_found(request)
        report.photo_report(conn, brand_id, str(out))
    return FileResponse(out, filename=out.name, background=BackgroundTask(shutil.rmtree, out.parent, ignore_errors=True),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _back(error="", started="", saved=""):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("error", error), ("started", started), ("saved", saved)) if v})
    return RedirectResponse("/admin" + ("?" + q if q else ""), status_code=303)


# ------------------------------------------------------------------ orders

ORDER_FILTERS = [("", "Recent"), ("paid", "To ship"), ("on_hold", "On hold"), ("shipped", "Shipped"),
                 ("refunded", "Refunded"), ("cancelled", "Cancelled")]
CARRIERS = ["UPS", "FedEx", "USPS", "DHL", "Freight", "Other"]


@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, status: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from . import orders as O
    st = web._state(request)
    with st.pool.connection() as conn:
        rows = O.recent(conn, status if status in O.STATUS_LABELS else None)
    return _page(request, "admin_orders.html", title="Orders", rows=rows, status=status, filters=ORDER_FILTERS,
                 money=web.money, section="orders")


@router.get("/orders/{number}", response_class=HTMLResponse)
def order_detail(request: Request, number: str, error: str = "", done: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from . import orders as O
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.get(conn, number=number)
    if not o:
        return web.not_found(request)
    return _page(request, "admin_order.html", title=f"Order {o['number']}", o=o, money=web.money, carriers=CARRIERS,
                 error=error, done=done, section="orders", states=O.US_STATES)


def _order_back(number, error="", done=""):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("error", error), ("done", done)) if v})
    return RedirectResponse(f"/admin/orders/{number}" + ("?" + q if q else ""), status_code=303)


@router.post("/orders/{number}/ship")
def order_ship(request: Request, number: str, carrier: str = Form(""), tracking: str = Form(""), notify: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from . import checkout, orders as O
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.get(conn, number=number)
        if not o:
            return web.not_found(request)
        if o["status"] not in ("paid", "shipped"):
            return _order_back(number, error="Only paid orders can be marked shipped.")
        if not tracking.strip():
            return _order_back(number, error="Enter the tracking number.")
        O.set_status(conn, o["id"], "shipped", f"Marked shipped: {carrier} {tracking.strip()}", carrier=carrier[:40],
                     tracking_number=tracking.strip()[:80], shipped_at=time.strftime("%Y-%m-%d %H:%M:%S%z"))
        o = O.get(conn, number=number)
        if notify and checkout.send_shipped_email(request, o):
            O.add_event(conn, o["id"], f"Shipping email sent to {o['email']}")
    return _order_back(number, done="Marked as shipped.")


@router.post("/orders/{number}/release")
def order_release(request: Request, number: str):
    stop = _guard(request)
    if stop:
        return stop
    from . import orders as O
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.get(conn, number=number)
        if o and o["status"] == "on_hold":
            O.set_status(conn, o["id"], "paid", "Hold released by admin")
    return _order_back(number, done="Hold released.")


@router.post("/orders/{number}/refund")
def order_refund(request: Request, number: str, confirm: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    import stripe
    from . import checkout, orders as O
    st = web._state(request)
    with st.pool.connection() as conn:
        o = O.get(conn, number=number)
        if not o:
            return web.not_found(request)
        if not confirm:
            return _order_back(number, error="Tick the box to confirm the refund.")
        if o["status"] not in ("paid", "on_hold", "shipped") or not o["payment_intent"]:
            return _order_back(number, error="This order has no payment to refund.")
        try:
            stripe.Refund.create(payment_intent=o["payment_intent"], api_key=checkout.stripe_key(),
                                 metadata={"order_number": o["number"]})
        except stripe.StripeError as e:
            return _order_back(number, error=f"Stripe couldn't refund it: {e.user_message or e}")
        O.set_status(conn, o["id"], "refunded", f"Refunded in full ({web.money(o['total'])})")
    return _order_back(number, done="Refunded in full. The customer sees it on their card in 5 to 10 days.")


# ------------------------------------------------------------------ Turn 14

T14_TASKS = [("test", "Test connection", "Sign in and read the brand and warehouse lists (2-3 requests)."),
             ("setup", "First full load", "All items for our approved brands, matching to our parts, pricing and stock."),
             ("daily", "Daily sync", "Brands, changed items, pricing and a full stock count. Runs by itself after 1:30 AM Eastern."),
             ("changes", "Stock changes", "Stock that changed since the last run. Runs by itself every hour."),
             ("full-items", "Full item check", "Every item again, to catch discontinued ones. Runs by itself on the 1st and 15th.")]


@router.get("/turn14", response_class=HTMLResponse)
def turn14_page(request: Request, error: str = "", started: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from turn14 import client as t14
    from . import turn14_schedule
    st = web._state(request)
    with st.pool.connection() as conn:
        hour, day = conn.execute("""SELECT count(*) FILTER (WHERE at > now() - interval '1 hour'),
                                           count(*) FILTER (WHERE at > now() - interval '24 hours') FROM t14_requests""").fetchone()
        runs = {k[5:]: v for k, v in conn.execute("SELECT key, value FROM t14_state WHERE key LIKE 'last\\_%'").fetchall()}
        brands = conn.execute("""
            SELECT b.id, b.name, b.aaia_id, t.id, t.name, t.approved, t.restricted,
                   (SELECT count(*) FROM products p WHERE p.brand_id = b.id AND p.status = 'active'),
                   (SELECT count(*) FROM t14_items i WHERE i.t14_brand_id = t.id AND i.active),
                   (SELECT count(DISTINCT i.product_id) FROM t14_items i JOIN products p ON p.id = i.product_id
                     WHERE i.t14_brand_id = t.id AND p.status = 'active'),
                   (SELECT count(*) FROM t14_items i JOIN t14_pricing pr ON pr.item_id = i.item_id
                     WHERE i.t14_brand_id = t.id AND i.product_id IS NOT NULL AND pr.can_purchase),
                   (SELECT count(*) FROM t14_items i JOIN t14_inventory v ON v.item_id = i.item_id
                     WHERE i.t14_brand_id = t.id AND i.product_id IS NOT NULL AND v.total > 0)
            FROM brands b LEFT JOIN t14_brands t ON t.brand_id = b.id
            WHERE EXISTS (SELECT 1 FROM products p WHERE p.brand_id = b.id)
            ORDER BY b.name""").fetchall()
        cols = ["id", "name", "aaia", "t14_id", "t14_name", "approved", "restricted", "parts", "t14_items", "matched",
                "buyable", "in_stock"]
        brands = [dict(zip(cols, r)) for r in brands]
        recent = conn.execute("""SELECT at, method, path, status, ms, task FROM t14_requests ORDER BY id DESC LIMIT 25""").fetchall()
    return _page(request, "admin_turn14.html", title="Turn 14", section="turn14", env=t14.environment(),
                 configured=t14.configured(), auto=turn14_schedule.enabled(), hour=hour, day=day, runs=runs,
                 tasks=T14_TASKS, labels=turn14_schedule.LABELS, brands=brands, recent=recent, job=st.t14.current(),
                 error=error, started=started, caps=(t14.HOURLY_CAP, t14.DAILY_CAP))


@router.post("/turn14/run/{task}")
def turn14_run(request: Request, task: str):
    stop = _guard(request)
    if stop:
        return stop
    from turn14 import client as t14
    from . import turn14_schedule
    from urllib.parse import urlencode
    st = web._state(request)
    back = lambda **q: RedirectResponse("/admin/turn14?" + urlencode(q), status_code=303)   # noqa: E731
    if task not in dict((t, l) for t, l, _ in T14_TASKS):
        return back(error="Unknown task.")
    if not t14.configured():
        return back(error="Add TURN14_CLIENT_ID and TURN14_CLIENT_SECRET in Render's Environment settings first.")
    if not turn14_schedule.start(st, task):
        return back(error="A Turn 14 task is already running.")
    return back(started=turn14_schedule.LABELS[task])


@router.get("/turn14/unmatched/{brand_id}.csv")
def turn14_unmatched(request: Request, brand_id: str):
    """Our parts with no Turn 14 match, and Turn 14 items for the brand that match none of our parts."""
    stop = _guard(request)
    if stop:
        return stop
    import csv
    import io
    from fastapi.responses import Response
    st = web._state(request)
    with st.pool.connection() as conn:
        t = conn.execute("SELECT id FROM t14_brands WHERE brand_id = %s", (brand_id,)).fetchone()
        ours = conn.execute("""SELECT p.part_number, p.upc, p.title FROM products p
                               WHERE p.brand_id = %s AND p.status = 'active'
                                 AND NOT EXISTS (SELECT 1 FROM t14_items i WHERE i.product_id = p.id)
                               ORDER BY p.part_number""", (brand_id,)).fetchall()
        theirs = conn.execute("""SELECT item_id, mfr_part_number, barcode, product_name FROM t14_items
                                 WHERE t14_brand_id = %s AND active AND product_id IS NULL ORDER BY mfr_part_number""",
                              (t[0] if t else -1,)).fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["list", "part number", "UPC / barcode", "Turn 14 item id", "name"])
    for pn, u, title in ours:
        w.writerow(["our part, not found at Turn 14", pn, u or "", "", title])
    for iid, pn, bc, name in theirs:
        w.writerow(["Turn 14 item, not on our site", pn or "", bc or "", iid, name or ""])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{brand_id}-turn14-unmatched.csv"'})


# ------------------------------------------------------------------ SEMA Data

def _sema_back(**q):
    from urllib.parse import urlencode
    return RedirectResponse("/admin/sema" + ("?" + urlencode(q) if q else ""), status_code=303)


@router.get("/sema", response_class=HTMLResponse)
def sema_page(request: Request, error: str = "", started: str = "", saved: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from semadata import client as sc
    from semadata import sync as ss
    from . import sema_schedule
    st = web._state(request)
    with st.pool.connection() as conn:
        state = dict(conn.execute("SELECT key, value FROM sema_state WHERE key <> 'token'").fetchall())
        rows = conn.execute("""SELECT s.aaia, s.name, s.wanted, s.wish_name, s.status, s.last_loaded, s.last_error, s.parts,
                                      s.fitted_parts, s.sema_updated, s.loaded_updated, s.brand_id, s.approved,
                                      (SELECT count(*) FROM products p WHERE p.brand_id = s.brand_id AND p.status = 'active'),
                                      (SELECT count(*) FROM products p WHERE p.brand_id = s.brand_id AND p.status = 'active'
                                         AND p.main_image IS NOT NULL),
                                      s.est_parts, s.est_calls
                               FROM sema_brands s ORDER BY s.name""").fetchall()
        hour = conn.execute("SELECT count(*) FROM sema_requests WHERE at > now() - interval '1 hour'").fetchone()[0]
        recent = conn.execute("SELECT at, method, path, status, ms, task FROM sema_requests ORDER BY id DESC LIMIT 25").fetchall()
        wish = ss.wishlist(conn)
    cols = ["aaia", "name", "wanted", "wish", "status", "last_loaded", "error", "parts", "fitted", "sema_updated",
            "loaded_updated", "brand_id", "approved", "live", "with_photo", "est_parts", "est_calls"]
    brands = {r[0]: dict(zip(cols, r)) for r in rows}
    matches = state.get("matches") or {}
    listed = []
    for w in dict.fromkeys(x.strip() for x in wish if x.strip()):
        m = matches.get(w) or {"aaia": None, "choices": []}
        listed.append({"wish": w, **m, "brand": brands.get(m.get("aaia"))})
    approved = sorted(((b["aaia"], b["name"]) for b in brands.values() if b["approved"]), key=lambda x: x[1].lower())
    to_load = sum((l["brand"] or {}).get("est_calls") or 0 for l in listed
                  if l["brand"] and l["brand"]["status"] != "loaded")
    return _page(request, "admin_sema.html", title="SEMA Data", section="sema", configured=sc.configured(),
                 auto=sema_schedule.enabled(), receiver=state.get("receiver"), last_test=state.get("last_test"),
                 last_load=state.get("last_load"), listed=listed, approved=approved, wish_text="\n".join(wish),
                 hour=hour, recent=recent, job=st.sema.current(), error=error, started=started, saved=saved,
                 tested=bool(brands), to_load=to_load)


@router.post("/sema/run/{task}")
def sema_run(request: Request, task: str, aaia: str = Form(""), refresh: str = Form(""),
             pick: list[str] = Form([])):
    stop = _guard(request)
    if stop:
        return stop
    from semadata import client as sc
    from . import sema_schedule
    st = web._state(request)
    if not sc.configured():
        return _sema_back(error="Add SEMA_USERNAME and SEMA_PASSWORD in Render's Environment settings first.")
    if st.jobs.running():
        return _sema_back(error="A catalog upload is running on the Catalog page. Wait for it to finish.")
    if task == "test":
        ok = st.sema.start("SEMA: connection test", [("Connection test", ["test"])])
        label = "Connection test"
    elif task == "load":
        picked = list(dict.fromkeys(c.strip().upper() for c in pick if c.strip() and re.fullmatch(r"[A-Za-z0-9]+", c.strip())))
        if pick and not picked:
            return _sema_back(error="Tick at least one brand to load.")
        codes = picked or ([aaia.strip().upper()] if aaia.strip() else [])
        if codes and refresh:
            codes.append("--refresh-fitment")
        if picked:
            label = f"Load {len(picked)} selected brand{'s' if len(picked) != 1 else ''}"
        else:
            label = f"Load {aaia.strip().upper()}" if codes else "Load the brands on our list"
        ok = sema_schedule.start(st, codes, title="SEMA: " + label.lower())
    else:
        return _sema_back(error="Unknown task.")
    if not ok:
        return _sema_back(error="A SEMA job is already running.")
    return _sema_back(started=label)


@router.post("/sema/list")
def sema_list(request: Request, names: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from psycopg.types.json import Jsonb
    from semadata import sync as ss
    st = web._state(request)
    wish = list(dict.fromkeys(n.strip() for n in names.splitlines() if n.strip()))
    with st.pool.connection() as conn:
        conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES ('wishlist', %s, now())
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (Jsonb(wish),))
        conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES ('config_names_seen', %s, now())
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                     (Jsonb(sorted(ss._config_names())),))     # names removed here stay removed
        ss.match_wishlist(conn, log=lambda m: None)
    return _sema_back(saved=f"Brand list saved ({len(wish)} brands).")


@router.post("/sema/pick")
def sema_pick(request: Request, wish: str = Form(...), aaia: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from psycopg.types.json import Jsonb
    from semadata import sync as ss
    st = web._state(request)
    with st.pool.connection() as conn:
        r = conn.execute("SELECT value FROM sema_state WHERE key = 'picks'").fetchone()
        picks = dict((r[0] if r else None) or {})
        if aaia:
            picks[wish] = aaia.strip().upper() if aaia != "-" else "-"
        else:
            picks.pop(wish, None)
        conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES ('picks', %s, now())
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (Jsonb(picks),))
        ss.match_wishlist(conn, log=lambda m: None)
    return _sema_back(saved=f"Saved the choice for “{wish}”.")


@router.get("/sema/log.txt")
def sema_log(request: Request):
    stop = _guard(request)
    if stop:
        return stop
    from fastapi.responses import PlainTextResponse
    job = web._state(request).sema.current()
    if not job or not pathlib.Path(job["log"]).exists():
        return web.not_found(request)
    return PlainTextResponse(pathlib.Path(job["log"]).read_text(errors="replace"), headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ installer leads

@router.get("/installers", response_class=HTMLResponse)
def installer_leads(request: Request):
    stop = _guard(request)
    if stop:
        return stop
    from . import installers
    st = web._state(request)
    with st.pool.connection() as conn:
        rows, others, stats = installers.leads(conn)
    return _page(request, "admin_installers.html", title="Installer leads", section="installers", rows=rows, others=others,
                 stats=stats, configured=installers.configured(), cfg=installers.cfg())


# ------------------------------------------------------------------ brand page banners

@router.get("/banner/{brand_id}", response_class=HTMLResponse)
def banner_page(request: Request, brand_id: str, error: str = "", saved: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from importer import banners as bn
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        r = conn.execute("SELECT name, banner_src, banner_product_id, banner_blurb FROM brands WHERE id = %s", (brand_id,)).fetchone()
        if not r:
            return _back(error="Unknown brand.")
        name, src, pid, blurb = r
        banner = bn.for_page(conn, brand_id, s.storage, Q.brand_logos(conn, s).get(brand_id))
        cands = bn.candidates(conn, brand_id, s.storage, limit=12)
        default = bn.default_blurb(conn, brand_id, name)
    for c in cands:
        c["url"] = s.storage.url(c["key"])
    with st.pool.connection() as conn:
        logo = Q.brand_logos(conn, s).get(brand_id)
    return _page(request, "admin_banner.html", title=f"{name} banner", section="catalog", brand_id=brand_id, brand_name=name,
                 banner=banner, brand_logo=logo, src=src, pid=pid, blurb=blurb or "", default_blurb=default, cands=cands,
                 error=error, saved=saved)


def _banner_back(brand_id, **kw):
    from urllib.parse import urlencode
    return RedirectResponse(f"/admin/banner/{brand_id}?" + urlencode({k: v for k, v in kw.items() if v}), status_code=303)


@router.post("/banner/{brand_id}/text")
def banner_text(request: Request, brand_id: str, blurb: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    text = " ".join(blurb.split())[:600]
    with web._state(request).pool.connection() as conn:
        conn.execute("UPDATE brands SET banner_blurb = %s WHERE id = %s", (text or None, brand_id))
    return _banner_back(brand_id, saved="Summary saved." if text else "Summary set back to the standard one.")


@router.post("/banner/{brand_id}/pick")
def banner_pick(request: Request, brand_id: str, product_id: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    from importer import banners as bn
    st = web._state(request)
    with st.pool.connection() as conn:
        try:
            if product_id == "auto":
                key = bn.make(conn, brand_id, storage=st.get_searcher(conn).storage)
                msg = "The site picked the best photo again." if key else "This brand has no photos to use yet."
            else:
                bn.make(conn, brand_id, product_id=int(product_id), source="pick", storage=st.get_searcher(conn).storage)
                msg = "Banner photo changed."
        except (ValueError, OSError) as e:
            return _banner_back(brand_id, error=f"Not changed: {e}.")
    return _banner_back(brand_id, saved=msg)


@router.post("/banner/{brand_id}/upload")
def banner_upload(request: Request, brand_id: str, file: UploadFile = File(None)):
    stop = _guard(request)
    if stop:
        return stop
    from importer import banners as bn
    st = web._state(request)
    if file is None or not file.filename:
        return _banner_back(brand_id, error="Choose a picture to upload.")
    raw = file.file.read(15 * 1024 * 1024 + 1)
    if len(raw) > 15 * 1024 * 1024:
        return _banner_back(brand_id, error="That file is over 15 MB.")
    with st.pool.connection() as conn:
        try:
            bn.make(conn, brand_id, raw=raw, source="upload", storage=st.get_searcher(conn).storage)
        except Exception:       # noqa: BLE001
            return _banner_back(brand_id, error="That isn't a picture the site can read (use PNG, JPG or WebP).")
    return _banner_back(brand_id, saved="Your picture is on the banner. It's kept until you choose another.")


# ------------------------------------------------------------------ banner logos: check them all on the red

@router.get("/logos", response_class=HTMLResponse)
def logos_page(request: Request, saved: str = ""):
    stop = _guard(request)
    if stop:
        return stop
    from importer import banners as bn
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        logos = Q.brand_logos(conn, s)
        rows = conn.execute("""SELECT b.id, b.name, b.banner_keep_holes, b.banner_logo_key, b.banner_logo_custom_key IS NOT NULL,
                                      (SELECT count(*) FROM products p WHERE p.brand_id = b.id AND p.status = 'active' AND p.price IS NOT NULL)
                               FROM brands b WHERE b.active ORDER BY lower(b.name)""").fetchall()
    brands = []
    for bid, name, keep, lkey, custom, parts in rows:
        if not parts:
            continue
        tw = bn._tweaks(bid, name)
        std = "keep_holes" in tw
        brands.append({"id": bid, "name": name, "logo": logos.get(bid), "parts": parts,
                       "keep": std if keep is None else keep, "chosen": keep is not None,
                       "outline": bool(lkey and "-logo-d-" in lkey) and not custom, "custom": custom})
    return _page(request, "admin_logos.html", title="Banner logos", section="logos", brands=brands, saved=saved)


@router.get("/logos/{brand_id}/img")
def logo_image(request: Request, brand_id: str):
    """The brand's banner logo (made now if it's missing or out of date)."""
    stop = _guard(request)
    if stop:
        return stop
    from importer import banners as bn
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        url = bn.logo_for(conn, brand_id, Q.brand_logos(conn, s).get(brand_id), s.storage)
    if not url:
        return web.not_found(request)
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


@router.post("/logos/{brand_id}/upload")
def logo_upload(request: Request, brand_id: str, file: UploadFile = File(None)):
    """Use an uploaded logo on the banner exactly as it is (a PNG with a see-through background works best)."""
    stop = _guard(request)
    if stop:
        return stop
    import hashlib
    from urllib.parse import quote
    from PIL import Image
    raw = file.file.read(8 * 1024 * 1024 + 1) if file is not None and file.filename else b""
    if not raw or len(raw) > 8 * 1024 * 1024:
        return RedirectResponse(f"/admin/logos?saved={quote('Choose a PNG, WebP or JPG under 8 MB.')}#b-{brand_id}", status_code=303)
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
        im = im.convert("RGBA")
        im.thumbnail((900, 360))
        box = im.split()[-1].point(lambda v: 255 if v > 10 else 0).getbbox()
        im = im.crop(box) if box else im
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
    except Exception:       # noqa: BLE001
        return RedirectResponse(f"/admin/logos?saved={quote('That file is not a picture the site can read.')}#b-{brand_id}", status_code=303)
    st = web._state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        key = f"banners/{brand_id}-custom-{hashlib.md5(buf.getvalue()).hexdigest()[:10]}.png"
        s.storage.put(key, buf.getvalue(), "image/png")
        name = conn.execute("UPDATE brands SET banner_logo_custom_key = %s WHERE id = %s RETURNING name", (key, brand_id)).fetchone()
    return RedirectResponse(f"/admin/logos?saved={quote((name[0] if name else brand_id) + ': your logo is on the banner now, exactly as uploaded.')}#b-{brand_id}",
                            status_code=303)


@router.post("/logos/{brand_id}/reset")
def logo_reset(request: Request, brand_id: str):
    stop = _guard(request)
    if stop:
        return stop
    from urllib.parse import quote
    with web._state(request).pool.connection() as conn:
        name = conn.execute("UPDATE brands SET banner_logo_custom_key = NULL WHERE id = %s RETURNING name", (brand_id,)).fetchone()
    return RedirectResponse(f"/admin/logos?saved={quote((name[0] if name else brand_id) + ': back to the site-made logo.')}#b-{brand_id}",
                            status_code=303)


@router.post("/logos/{brand_id}")
def logo_choice(request: Request, brand_id: str, keep: str = Form("")):
    stop = _guard(request)
    if stop:
        return stop
    value = {"keep": True, "clear": False}.get(keep)
    with web._state(request).pool.connection() as conn:
        name = conn.execute("UPDATE brands SET banner_keep_holes = %s WHERE id = %s RETURNING name", (value, brand_id)).fetchone()
    msg = f"{name[0] if name else brand_id}: " + ("white inside the letters kept." if value else "white inside the letters cleared." if value is False else "back to the standard.")
    from urllib.parse import quote
    return RedirectResponse(f"/admin/logos?saved={quote(msg)}#b-{brand_id}", status_code=303)


# ------------------------------------------------------------------ UPS: check the connection and see sample rates

@router.get("/ups", response_class=HTMLResponse)
def ups_page(request: Request):
    stop = _guard(request)
    if stop:
        return stop
    from . import ups
    cfg = ups.settings()
    return _page(request, "admin_ups.html", title="UPS", section="ups", cfg=cfg, base=ups.base_url(cfg),
                 has={"id": bool(cfg["client_id"]), "secret": bool(cfg["client_secret"]), "account": bool(cfg["account"])},
                 form={"from_zip": "", "to_zip": "", "from_state": "", "to_state": "", "weight": "10", "l": "20", "w": "12", "h": "8"})


@router.post("/ups/test", response_class=HTMLResponse)
def ups_test(request: Request, from_zip: str = Form(""), to_zip: str = Form(""), from_state: str = Form(""),
             to_state: str = Form(""), weight: str = Form("10"),
             l: str = Form("20"), w: str = Form("12"), h: str = Form("8")):
    stop = _guard(request)
    if stop:
        return stop
    from . import ups
    cfg = ups.settings()
    form = {"from_zip": from_zip.strip(), "to_zip": to_zip.strip(), "weight": weight, "l": l, "w": w, "h": h,
            "from_state": (from_state.strip().upper() or ups.state_for_zip(from_zip))[:2],
            "to_state": (to_state.strip().upper() or ups.state_for_zip(to_zip))[:2]}
    ctx = dict(title="UPS", section="ups", cfg=cfg, base=ups.base_url(cfg), form=form,
               has={"id": bool(cfg["client_id"]), "secret": bool(cfg["client_secret"]), "account": bool(cfg["account"])})
    try:
        ups.token()
        ctx["signed_in"] = True
        if form["from_zip"] and form["to_zip"]:
            ctx["results"] = ups.rates(form["from_zip"], form["to_zip"], float(weight or 10),
                                       (float(l or 20), float(w or 12), float(h or 8)),
                                       from_state=form["from_state"], to_state=form["to_state"])
    except ups.UPSError as e:
        ctx["error"] = str(e)
    except ValueError:
        ctx["error"] = "Weight and box size must be numbers."
    return _page(request, "admin_ups.html", **ctx)
