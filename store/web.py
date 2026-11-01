"""Storefront pages (server-rendered HTML with Jinja2 templates).

Shopper state lives in a signed session cookie:
  garage  list of vehicle ids (newest last, at most 8)
  active  the vehicle being shopped for, or None
  cart    {product id: quantity}
"""
import os
import pathlib
import re
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import queries as Q
from . import vehicles as veh
from .icons import icon
from .search import PRICE_BUCKETS, SORTS

HERE = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
PER_PAGE = 24
MAX_GARAGE = 8
MAX_QTY = 99
CACHE_SECONDS = 300
SORT_LABELS = [("relevance", "Best match"), ("price_asc", "Price: low to high"), ("price_desc", "Price: high to low"), ("name", "Name: A to Z")]


def money(v):
    return "" if v is None else f"${v:,.2f}"


def is_preview():
    return os.environ.get("SITE_PREVIEW", "1") != "0"


templates.env.globals.update(icon=icon, preview=is_preview, year=time.strftime("%Y"),
                             checkout_test=lambda: os.environ.get("STRIPE_SECRET_KEY", "").startswith("sk_test_"))
templates.env.filters["money"] = money


def vname(v):
    """Short vehicle name for badges: "Silverado 1500", or "Ram 1500" when the model alone is just a number."""
    return v["model"] if re.search(r"[A-Za-z]{2}", v["model"]) else f"{v['make']} {v['model']}"


_YEAR_PAIR = re.compile(r"\b((?:19|20)\d\d)((?:19|20)\d\d)\b")


def fix_text(text):
    """Repair year ranges that lost their dash in the source data ("20192026" -> "2019-2026")."""
    return _YEAR_PAIR.sub(lambda m: f"{m.group(1)}-{m.group(2)}" if m.group(2) > m.group(1) else m.group(0), text or "")


def tire_size(size):
    return re.sub(r"(?<=\d)X(?=\d)", "x", size or "")


templates.env.filters["vname"] = vname
templates.env.filters["fixtext"] = fix_text
templates.env.filters["tiresize"] = tire_size
templates.env.filters["num"] = lambda n: f"{n:,}"

router = APIRouter()


# ------------------------------------------------------------------ helpers

def _state(request):
    return request.app.state.shop


def cached(request, key, fn):
    st = _state(request)
    store = st.__dict__.setdefault("page_cache", {})
    hit = store.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    store[key] = (time.time(), value)
    return value


def safe_next(url, default="/"):
    """Only redirect back to pages on this site."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return default


def back(request, default="/"):
    return RedirectResponse(safe_next(request.query_params.get("next") or "", default), status_code=303)


def garage_ids(request):
    return [int(v) for v in request.session.get("garage", []) if str(v).isdigit()]


def active_id(request):
    a = request.session.get("active")
    return int(a) if a is not None and int(a) in garage_ids(request) else None


def set_active(request, vid):
    g = [v for v in garage_ids(request) if v != vid]
    g.append(vid)
    request.session["garage"] = g[-MAX_GARAGE:]
    request.session["active"] = vid


def cart_dict(request):
    out = {}
    for k, q in (request.session.get("cart") or {}).items():
        if str(k).isdigit() and isinstance(q, int) and q > 0:
            out[int(k)] = min(q, MAX_QTY)
    return out


def save_cart(request, cart):
    request.session["cart"] = {str(k): v for k, v in cart.items() if v > 0}


def cart_summary(conn, searcher, request):
    cart = cart_dict(request)
    prods = Q.cart_products(conn, searcher, cart.keys())
    count = sum(q for pid, q in cart.items() if pid in prods)
    subtotal = sum(prods[pid]["price"] * q for pid, q in cart.items() if pid in prods)
    return cart, prods, count, subtotal


def garage_vehicles(conn, request):
    ids = garage_ids(request)
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute("SELECT id, year, make, model FROM vehicles WHERE id = ANY(%s)", (ids,))
        found = {r[0]: {"id": r[0], "year": r[1], "make": r[2], "model": r[3], "label": f"{r[1]} {r[2]} {r[3]}"}
                 for r in cur.fetchall()}
    return [found[i] for i in reversed(ids) if i in found]


def render(request, conn, searcher, name, status_code=200, **ctx):
    garage = garage_vehicles(conn, request)
    aid = active_id(request)
    active = next((v for v in garage if v["id"] == aid), None)
    _, _, count, subtotal = cart_summary(conn, searcher, request)
    top = cached(request, "top_cats", lambda: _top_categories(conn, searcher))
    years = cached(request, "years", lambda: veh.years(conn))
    here = request.url.path + (("?" + request.url.query) if request.url.query else "")
    from .orders import store_config
    builder = bool((store_config().get("interactive_garage") or {}).get("url"))
    base = dict(request=request, garage=garage, active=active, cart_count=count, cart_subtotal=subtotal, garage_builder=builder,
                years=years, footer_cats=top[:6], here=here, q=ctx.pop("q", ""), money=money)
    resp = templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)
    if is_preview():
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


def _top_categories(conn, searcher):
    counts = Q.category_counts(conn)
    return [{"id": c, "name": v["name"], "count": counts[c]} for c, v in searcher.categories.items()
            if not v["parent"] and counts.get(c) and c != "uncategorized"]


def _price_param(value):
    m = re.fullmatch(r"(\d+)-(\d*)", value or "")
    if not m:
        return None, None
    return float(m.group(1)), (float(m.group(2)) if m.group(2) else None)


def _bucket_key(lo, hi):
    return f"{lo}-{hi if hi else ''}"


def _bucket_label(lo, hi):
    if lo == 0:
        return f"Under ${hi:,}"
    if hi is None:
        return f"${lo:,} and up"
    return f"${lo:,} to ${hi:,}"


# ------------------------------------------------------------------ home

@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        cfg = Q.home_config()
        vid = active_id(request)
        slides = _slides(conn, s, cfg)
        counts = Q.category_counts(conn, vid)
        subs = cached(request, "sub_counts", lambda: Q.sub_counts(conn))
        cards = []
        for cid in cfg["categories"]:
            if cid not in s.categories:
                continue
            all_subs = [(c, v["name"]) for c, v in sorted(s.categories.items(), key=lambda kv: kv[1].get("sort", 0))
                        if v["parent"] == cid]
            with_parts = [x for x in all_subs if subs.get(x[0])]
            img = cfg.get("category_images", {}).get(cid)
            img_url = Q.image_of(conn, s, img.split(":", 1)[1]) if img and img.startswith("product:") else \
                (f"/static/img/cat/{img}" if img else None)
            cards.append({"id": cid, "name": s.categories[cid]["name"], "count": counts.get(cid, 0),
                          "subs": (with_parts or all_subs)[:4], "image": img_url})
        if vid:
            popular = Q.top_for_vehicle(conn, s, vid, 8)
        else:
            popular = Q.cards_by_slugs(conn, s, cfg.get("popular", [])[:8])
        logos = cached(request, "logos", lambda: Q.brand_logos(conn, s))
        brands = [dict(b, logo=logos.get(b["id"])) for b in cached(request, "brands", lambda: Q.brands_with_counts(conn))]
        catalog_empty = not any(cached(request, "sub_counts", lambda: Q.sub_counts(conn)).values())
        return render(request, conn, s, "home.html", cfg=cfg, slides=slides, cat_cards=cards, popular=popular, brands=brands,
                      catalog_empty=catalog_empty)


def _slides(conn, s, cfg):
    out = []
    for sl in cfg.get("slides", []):
        text = sl.get("text", "")
        if "{price_from}" in text:
            p = Q.min_price(conn, s, sl.get("price_from"))
            if p is None:
                continue
            text = text.replace("{price_from}", money(p))
        ok = True
        for slug in re.findall(r"\{price:([\w-]+)\}", text):
            p = Q.price_of(conn, slug)
            if p is None:
                ok = False
            text = text.replace("{price:%s}" % slug, money(p))
        img = sl.get("image", "")
        if img.startswith("product:"):
            img = Q.image_of(conn, s, img.split(":", 1)[1])
            ok = ok and bool(img)
        elif img:
            img = f"/static/img/slides/{img}"
        if ok:
            out.append(dict(sl, text=text, image=img))
    return out


# ------------------------------------------------------------------ results (search, category, brand)

@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    return _results(request, kind="search", q=q.strip()[:200])


@router.get("/c/{cat_id}", response_class=HTMLResponse)
def category_page(request: Request, cat_id: str):
    return _results(request, kind="category", category=cat_id)


@router.get("/brand/{brand_id}", response_class=HTMLResponse)
def brand_page(request: Request, brand_id: str):
    return _results(request, kind="brand", brand=brand_id)


def _results(request, kind, q="", category=None, brand=None):
    st = _state(request)
    params = request.query_params
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        if kind == "category" and category not in s.categories:
            return not_found(request)
        if kind == "brand" and brand not in s.brands:
            return not_found(request)
        if kind != "category":
            category = params.get("category") if params.get("category") in s.categories else None
        brands = [brand] if brand else [b for b in params.getlist("brand") if b in s.brands]
        price = params.get("price", "")
        lo, hi = _price_param(price)
        carb = params.get("carb") == "1"
        show_all = params.get("all") == "1"
        sort = params.get("sort") if params.get("sort") in SORTS else "relevance"
        try:
            page = max(1, min(int(params.get("page", 1)), 500))
        except ValueError:
            page = 1
        vid = active_id(request)
        res = s.search(conn, q, vehicle_id=vid, category=category, brands=brands, price_min=lo, price_max=hi,
                       carb_only=carb, fit_only=not show_all, sort=sort, page=page, per_page=PER_PAGE)

        v = res.get("vehicle")
        added = None
        if v and v.get("source") == "query" and v.get("id"):
            if v["id"] != vid:
                set_active(request, v["id"])
                added = v["label"]
            vid = v["id"]
        if (kind == "search" and res["part_number_match"] and res["total"] == 1 and page == 1
                and not (brands or category or lo is not None or carb)):
            return RedirectResponse("/p/" + res["items"][0]["slug"], status_code=303)
        Q.add_fit(conn, res["items"], None)

        fit_counts = res.get("facets", {}).get("fit") or {}
        filtering_by_vehicle = bool(res.get("vehicle")) and not show_all
        hidden = sum(n for f, n in fit_counts.items() if f in ("no", "unknown")) if filtering_by_vehicle else 0
        all_total = sum(fit_counts.values()) if fit_counts else res["total"]

        base_path = {"search": "/search", "category": f"/c/{category}", "brand": f"/brand/{brand}"}[kind]
        keep = {k: params.getlist(k) for k in ("q", "brand", "price", "carb", "all", "sort", "category") if params.getlist(k)}
        if kind == "search":
            keep["q"] = [q]
        if kind == "brand":
            keep.pop("brand", None)
        if kind == "category":
            keep.pop("category", None)

        def url(path=None, **changes):
            d = {k: list(v) for k, v in keep.items()}
            for k, v in changes.items():
                if v is None:
                    d.pop(k, None)
                else:
                    d[k] = [v] if not isinstance(v, list) else v
            if "all" in d:
                d["all"] = [d["all"][-1]]
            qs = urlencode([(k, x) for k, vs in d.items() for x in vs if x not in ("", None)])
            return (path or base_path) + ("?" + qs if qs else "")

        def cat_url(cid):
            if kind == "category":
                return url(path=f"/c/{cid}")
            return url(category=cid)

        # sidebar category block
        facets = res.get("facets", {})
        cats = facets.get("categories", [])
        top_id = None
        if category:
            top_id = s.categories[category]["parent"] or category
        cat_block = {"top": None, "options": []}
        if top_id:
            top = next((c for c in cats if c["id"] == top_id), None)
            sub_counts = {x["id"]: x["count"] for x in (top or {}).get("subs", [])}
            subs = [(c, v["name"]) for c, v in sorted(s.categories.items(), key=lambda kv: kv[1].get("sort", 0)) if v["parent"] == top_id]
            cat_block = {"top": {"id": top_id, "name": s.categories[top_id]["name"], "url": cat_url(top_id),
                                 "on": category == top_id, "count": (top or {}).get("count", 0)},
                         "options": [{"id": c, "name": n, "url": cat_url(c), "count": sub_counts.get(c, 0), "on": c == category}
                                     for c, n in subs if sub_counts.get(c) or c == category],
                         "clear_url": url(path="/search", category=None) if kind == "category" and q else (url(category=None) if kind != "category" else None)}
        else:
            cat_block["options"] = [{"id": c["id"], "name": c["name"], "url": cat_url(c["id"]), "count": c["count"], "on": False}
                                    for c in cats if c["id"] != "uncategorized"]

        price_opts = [{"key": "", "label": "Any price", "on": not price, "count": None}] + \
                     [{"key": _bucket_key(b["min"], b["max"]), "label": _bucket_label(b["min"], b["max"]), "count": b["count"],
                       "on": price == _bucket_key(b["min"], b["max"])} for b in facets.get("prices", []) if b["count"] or
                      price == _bucket_key(b["min"], b["max"])]

        # headings
        if kind == "search":
            title = f"Results for “{q}”" if q else "All Parts"
            crumbs = [("Search", None)]
        elif kind == "brand":
            title = s.brands[brand]
            crumbs = [("Brands", "/brands"), (title, None)]
        else:
            title = s.categories[category]["name"]
            parent = s.categories[category]["parent"]
            crumbs = ([(s.categories[parent]["name"], f"/c/{parent}")] if parent else []) + [(title, None)]
        if kind != "category" and category:
            crumbs.append((s.categories[category]["name"], None))

        pages = _pager(page, res["pages"])
        return render(request, conn, s, "results.html", q=q, kind=kind, res=res, title=title, crumbs=crumbs,
                      category=category, brand=brand, brands_sel=brands, price=price, carb=carb, show_all=show_all,
                      sort=sort, sort_labels=SORT_LABELS, cat_block=cat_block, price_opts=price_opts, hidden=hidden,
                      all_total=all_total, filtering_by_vehicle=filtering_by_vehicle, added=added,
                      pages=[(p, url(page=str(p)) if p else None) for p in pages], page=page,
                      prev_url=url(page=str(page - 1)) if page > 1 else None,
                      next_url=url(page=str(page + 1)) if page < res["pages"] else None,
                      show_all_url=url(all="1"), fit_only_url=url(all=None), clear_url=_clear_url(kind, base_path, q, category),
                      brand_logo=cached(request, "logos", lambda: Q.brand_logos(conn, s)).get(brand) if brand else None)


def _clear_url(kind, base_path, q, category):
    if kind == "search":
        return "/search?" + urlencode({"q": q}) if q else "/search"
    return base_path


def _pager(page, pages):
    if pages <= 1:
        return []
    want = {1, pages, page - 1, page, page + 1}
    if page <= 3:
        want |= {2, 3, 4}
    if page >= pages - 2:
        want |= {pages - 1, pages - 2, pages - 3}
    out, last = [], 0
    for p in sorted(x for x in want if 1 <= x <= pages):
        if p - last > 1:
            out.append(0)   # gap
        out.append(p)
        last = p
    return out


@router.get("/brands", response_class=HTMLResponse)
def brands_page(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        logos = Q.brand_logos(conn, s)
        brands = [dict(b, logo=logos.get(b["id"])) for b in Q.brands_with_counts(conn)]
        return render(request, conn, s, "brands.html", brands=brands)


# ------------------------------------------------------------------ product page

@router.get("/p/{slug}", response_class=HTMLResponse)
def product_page(request: Request, slug: str):
    st = _state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        p = Q.product(conn, s, slug)
        if not p:
            return not_found(request)
        vid = active_id(request)
        v = veh.get(conn, vid) if vid else None
        fit_rows = Q.vehicle_fit(conn, p["id"], vid) if vid else []
        if v:
            p["fit"] = Q.fit_label(p["fitment_status"], bool(fit_rows))
        else:
            p["fit"] = None
        summary = Q.fit_summaries(conn, [p["id"]]).get(p["id"])
        related = Q.related(conn, s, p, vid)
        in_cart = cart_dict(request).get(p["id"], 0)
        fit_info = {
            "trims": sorted({t.strip() for r in fit_rows for t in r["submodel"].split(",") if t.strip()}),
            "engines": sorted({r["engine"] for r in fit_rows if r["engine"]}),
            "notes": sorted({r["notes"] for r in fit_rows if r["notes"]}),
        }
        specs = [(k, val) for k, val in (p["specs"] or {}).items() if val not in (None, "")]
        size = (p["attributes"] or {}).get("Tire Size")
        return render(request, conn, s, "product.html", p=p, v=v, fit_info=fit_info, summary=summary, related=related,
                      in_cart=in_cart, specs=specs, size=size, title=p["short_name"])


# ------------------------------------------------------------------ cart

def _wants_json(request):
    return "application/json" in request.headers.get("accept", "")


def _cart_json(request):
    st = _state(request)
    with st.pool.connection() as conn:
        _, _, count, subtotal = cart_summary(conn, st.get_searcher(conn), request)
    return JSONResponse({"count": count, "subtotal": subtotal, "subtotal_text": money(subtotal)})


@router.post("/cart/add")
def cart_add(request: Request, product_id: int = Form(...), qty: int = Form(1)):
    st = _state(request)
    with st.pool.connection() as conn:
        ok = conn.execute("SELECT 1 FROM products WHERE id = %s AND status = 'active' AND price IS NOT NULL",
                          (product_id,)).fetchone()
    if ok:
        cart = cart_dict(request)
        cart[product_id] = min(MAX_QTY, cart.get(product_id, 0) + max(1, min(qty, MAX_QTY)))
        if len(cart) <= 60:
            save_cart(request, cart)
    if _wants_json(request):
        return _cart_json(request) if ok else JSONResponse({"error": "That part isn't available."}, status_code=404)
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/update")
def cart_update(request: Request, product_id: int = Form(...), qty: int = Form(...)):
    cart = cart_dict(request)
    if product_id in cart:
        cart[product_id] = max(0, min(qty, MAX_QTY))
        save_cart(request, cart)
    return RedirectResponse("/cart", status_code=303)


@router.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        s = st.get_searcher(conn)
        cart, prods, count, subtotal = cart_summary(conn, s, request)
        lines = [dict(prods[pid], qty=q, line_total=prods[pid]["price"] * q) for pid, q in cart.items() if pid in prods]
        Q.add_fit(conn, lines, active_id(request))
        from . import checkout, orders as O
        cfg = O.store_config()
        restricted = cfg.get("carb_restricted_states", [])
        return render(request, conn, s, "cart.html", lines=lines, subtotal=subtotal, free_over=cfg["free_shipping_over"],
                      flat_shipping=float(cfg["flat_shipping"]), title="Cart", checkout_ready=checkout.checkout_ready(),
                      needs_state=any(l.get("emissions") == "federal_only" for l in lines), states=O.US_STATES,
                      restricted_names=", ".join(O.US_STATES[c] for c in restricted[:-1]) + (" or " if len(restricted) > 1 else "")
                      + (O.US_STATES[restricted[-1]] if restricted else ""),
                      error=request.query_params.get("error", "")[:300], cancelled=request.query_params.get("cancelled") == "1",
                      ship_state=request.query_params.get("state", ""))


# ------------------------------------------------------------------ garage

@router.get("/garage", response_class=HTMLResponse)
def garage_page(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        from .builds import builds_for_page
        from .orders import store_config
        s = st.get_searcher(conn)
        ig = store_config().get("interactive_garage") or {}
        return render(request, conn, s, "garage.html", title="My Garage", ig=ig, builds=builds_for_page(conn, s, request),
                      build_error=request.query_params.get("build_error", "")[:200])


@router.post("/garage/add")
def garage_add(request: Request, vehicle: int = Form(...)):
    st = _state(request)
    with st.pool.connection() as conn:
        v = veh.get(conn, vehicle)
    if v:
        set_active(request, v["id"])
    return back(request)


@router.post("/garage/use")
def garage_use(request: Request, vehicle: int = Form(...)):
    if vehicle in garage_ids(request):
        set_active(request, vehicle)
    return back(request)


@router.post("/garage/remove")
def garage_remove(request: Request, vehicle: int = Form(...)):
    request.session["garage"] = [v for v in garage_ids(request) if v != vehicle]
    if request.session.get("active") == vehicle:
        request.session["active"] = None
    return back(request, "/garage")


@router.post("/garage/shop-all")
def garage_shop_all(request: Request):
    request.session["active"] = None
    return back(request)


# ------------------------------------------------------------------ simple pages

INFO_PAGES = {
    "shipping-returns": ("Shipping & Returns", [
        "In-stock orders usually ship within 1-2 business days directly from the distributor's warehouse. "
        "You'll get a tracking number by email as soon as it ships.",
        "Oversized items such as lift kits, bumpers and tonneau covers may ship by freight. Freight orders need a phone "
        "number for delivery scheduling.",
        "Unused parts in original packaging can be returned within 30 days. Parts that have been installed can't be "
        "returned unless they're defective."]),
    "fitment-guarantee": ("Fitment Guarantee", [
        "Every part page checks the vehicle in your garage against the manufacturer's own fitment data."]),
    "contact": ("Contact Us", []),
    "about": ("About Dream Auto Builders", [
        "Parts and accessories matched to your exact vehicle, shipped from distributor warehouses across the U.S."]),
    "privacy": ("Privacy Policy", []),
    "terms": ("Terms of Sale", []),
}


@router.get("/info/{page}", response_class=HTMLResponse)
def info_page(request: Request, page: str):
    if page not in INFO_PAGES:
        return not_found(request)
    title, paras = INFO_PAGES[page]
    st = _state(request)
    with st.pool.connection() as conn:
        return render(request, conn, st.get_searcher(conn), "page.html", title=title, paras=paras, page=page)


@router.get("/track", response_class=HTMLResponse)
def track_page(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        return render(request, conn, st.get_searcher(conn), "page.html", title="Track Your Order", page="track",
                      paras=["Enter the order number from your confirmation email and the email address you ordered with."],
                      track_error=request.query_params.get("error") == "1", track_number=request.query_params.get("number", "")[:20])


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request):
    st = _state(request)
    with st.pool.connection() as conn:
        return render(request, conn, st.get_searcher(conn), "page.html", title="Sign In", page="account",
                      paras=["Customer accounts will let you see past orders, reorder parts and keep your garage on every device."])


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    if is_preview():
        return "User-agent: *\nDisallow: /\n"
    return "User-agent: *\nDisallow: /cart\nDisallow: /garage\nDisallow: /api/\n"


# ------------------------------------------------------------------ private test site

GATE_OPEN = ("/login", "/static/", "/photos/", "/api/health", "/robots.txt", "/admin", "/stripe/webhook")


def site_password():
    return os.environ.get("SITE_PASSWORD", "")


def _site_token():
    import hmac
    return hmac.new(site_password().encode(), b"dab-site", "sha256").hexdigest()[:24]


def install_site_gate(app):
    """With SITE_PASSWORD set, visitors sign in once (remembered in their cookie) before seeing the shop."""
    from . import admin

    @app.middleware("http")
    async def gate(request, call_next):
        path = request.url.path
        if site_password() and not path.startswith(GATE_OPEN) and request.session.get("site") != _site_token() \
                and not admin.is_admin(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "This test site is private."}, status_code=401)
            nxt = path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/login?" + urlencode({"next": nxt}), status_code=303)
        return await call_next(request)


@router.get("/login", response_class=HTMLResponse)
def site_login_page(request: Request):
    if not site_password():
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"request": request, "title": "Private test site", "error": None,
                                                              "action": "/login?" + urlencode({"next": request.query_params.get("next", "/")})})


@router.post("/login")
def site_login(request: Request, password: str = Form("")):
    import hmac
    if site_password() and hmac.compare_digest(password.encode(), site_password().encode()):
        request.session["site"] = _site_token()
        return back(request)
    time.sleep(1)
    return templates.TemplateResponse(request, "login.html", {"request": request, "title": "Private test site",
                                                              "error": "That password isn't right.",
                                                              "action": "/login?" + urlencode({"next": request.query_params.get("next", "/")})})


def not_found(request):
    st = _state(request)
    with st.pool.connection() as conn:
        return render(request, conn, st.get_searcher(conn), "404.html", status_code=404, title="Page not found")


def install(app):
    """Attach the page routes and the HTML 404 page to the app."""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exception_handlers import http_exception_handler

    app.include_router(router)

    @app.exception_handler(StarletteHTTPException)
    async def _html_404(request, exc):
        if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/photos/", "/static/")):
            return await run_in_threadpool(not_found, request)
        return await http_exception_handler(request, exc)
