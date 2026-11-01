"""Admin page: upload a brand's SEMA files, run the import and photo download, see catalog status.

Turned on by setting ADMIN_PASSWORD. Sign-in is kept in the same signed cookie as the cart.
"""
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
def admin_home(request: Request, error: str = "", started: str = ""):
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
                        WHERE p2.brand_id = b.id AND p2.status = 'active' AND i.status = 'pending')
                FROM brands b LEFT JOIN products p ON p.brand_id = b.id
                GROUP BY b.id ORDER BY b.name""")
            cols = ["id", "name", "aaia", "logo_key", "active", "hidden", "discontinued", "with_photo", "with_fitment",
                    "last_import", "photos_pending"]
            brands = [dict(zip(cols, r)) for r in cur.fetchall()]
            cur.execute("""SELECT r.id, r.brand_id, r.step, r.status, r.started_at, r.finished_at, r.error
                           FROM import_runs r ORDER BY r.id DESC LIMIT 12""")
            runs = [dict(zip(["id", "brand", "step", "status", "started", "finished", "error"], r)) for r in cur.fetchall()]
    for b in brands:
        b["logo"] = s.storage.url(b["logo_key"]) if b["logo_key"] else None
    job = st.jobs.current()
    return _page(request, "admin.html", title="Catalog admin", brands=brands, runs=runs, job=job, error=error,
                 started=started, data_dir=str(st.data_dir), section="catalog")


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
    if st.jobs.running():
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
    if not st.jobs.start(f"{name[0]} photos", [(f"Download {name[0]} photos", args)], brand_id):
        return _back(error="An import is already running.")
    return _back(started=name[0] + " photos")


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


def _back(error="", started=""):
    from urllib.parse import urlencode
    q = urlencode({k: v for k, v in (("error", error), ("started", started)) if v})
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
