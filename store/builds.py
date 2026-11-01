"""Saved builds: lists of parts a shopper put together in the Interactive Garage builder.

The builder (on interactivegarage.com) sends a finished build to this site at /build/import with
the part numbers. The build is saved in My Garage (in the shopper's cookie, like the cart and
garage) and/or added to the cart. See docs/INTERACTIVE_GARAGE.md for the format.
"""
import re
import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import vehicles as veh
from . import web
from .orders import store_config

router = APIRouter()
MAX_BUILDS = 10
MAX_ITEMS = 40


def saved(request):
    out = []
    for b in request.session.get("builds") or []:
        if isinstance(b, dict) and b.get("id") and isinstance(b.get("items"), list):
            out.append(b)
    return out


def _parse_items(params):
    """[(part number, qty)] from items=PN:QTY,PN:QTY and/or repeated pn= (with optional qty= in the same order)."""
    found = []
    for chunk in params.getlist("items"):
        for part in re.split(r"[,;\n]+", chunk):
            part = part.strip()
            if not part:
                continue
            pn, _, q = part.rpartition(":") if re.search(r":\d+$", part) else (part, "", "1")
            found.append((pn.strip(), q))
    pns, qtys = params.getlist("pn"), params.getlist("qty")
    for i, pn in enumerate(pns):
        found.append((pn.strip(), qtys[i] if i < len(qtys) else "1"))
    out = []
    for pn, q in found[:MAX_ITEMS]:
        try:
            q = max(1, min(int(q), 99))
        except ValueError:
            q = 1
        if pn:
            out.append((pn[:60], q))
    return out


def _match(conn, items, brand=None):
    """Active products for the part numbers (any punctuation), preferring the given brand."""
    norms = {re.sub(r"[^a-z0-9]", "", pn.lower()): (pn, q) for pn, q in items}
    with conn.cursor() as cur:
        cur.execute("""SELECT p.pn_norm, p.id, p.brand_id, lower(b.name) FROM products p JOIN brands b ON b.id = p.brand_id
                       WHERE p.status = 'active' AND p.price IS NOT NULL AND p.pn_norm = ANY(%s)""", (list(norms),))
        rows = cur.fetchall()
    best = {}
    want = (brand or "").lower()
    for norm, pid, brand_id, brand_name in rows:
        score = 1 if want and (want == brand_id or want in brand_name) else 0
        if norm not in best or score > best[norm][1]:
            best[norm] = (pid, score)
    matched = [(best[n][0], q) for n, (pn, q) in norms.items() if n in best]
    missing = [pn for n, (pn, q) in norms.items() if n not in best]
    return matched, missing


@router.api_route("/build/import", methods=["GET", "POST"])
async def import_build(request: Request):
    params = request.query_params
    if request.method == "POST":
        form = await request.form()
        from starlette.datastructures import QueryParams
        params = QueryParams(list(params.multi_items()) + [(k, v) for k, v in form.multi_items() if isinstance(v, str)])
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_import, request, params)


def _import(request, params):
    items = _parse_items(params)
    st = web._state(request)
    with st.pool.connection() as conn:
        matched, missing = _match(conn, items, params.get("brand"))
        vehicle = (params.get("vehicle") or "").strip()[:80]
        y, mk, md = params.get("year"), params.get("make"), params.get("model")
        if y and mk and md and y.isdigit():
            vid = veh.find_id(conn, int(y), mk.strip(), md.strip())
            vehicle = vehicle or f"{y} {mk.strip()} {md.strip()}"
            if vid:
                web.set_active(request, vid)       # the builder's vehicle becomes the one being shopped for
    if not matched:
        return RedirectResponse("/garage?" + web.urlencode({"build_error": "None of the parts in that build are sold here yet."}),
                                status_code=303)
    name = (params.get("name") or "").strip()[:60] or (f"{vehicle} build" if vehicle else "My build")
    build = {"id": uuid.uuid4().hex[:10], "name": name, "vehicle": vehicle, "items": [[pid, q] for pid, q in matched],
             "at": int(time.time()), "missing": missing[:10]}
    builds = [b for b in saved(request)][-(MAX_BUILDS - 1):] + [build]
    request.session["builds"] = builds
    if params.get("action") == "cart":
        cart = web.cart_dict(request)
        for pid, q in matched:
            cart[pid] = min(99, cart.get(pid, 0) + q)
        web.save_cart(request, cart)
        return RedirectResponse("/cart", status_code=303)
    return RedirectResponse(f"/garage#build-{build['id']}", status_code=303)


@router.post("/garage/builds/{build_id}/cart")
def build_to_cart(request: Request, build_id: str):
    b = next((x for x in saved(request) if x["id"] == build_id), None)
    if b:
        cart = web.cart_dict(request)
        for pid, q in b["items"]:
            cart[int(pid)] = min(99, cart.get(int(pid), 0) + int(q))
        web.save_cart(request, cart)
        return RedirectResponse("/cart", status_code=303)
    return RedirectResponse("/garage", status_code=303)


@router.post("/garage/builds/{build_id}/remove")
def build_remove(request: Request, build_id: str):
    request.session["builds"] = [b for b in saved(request) if b["id"] != build_id]
    return RedirectResponse("/garage#builds", status_code=303)


def builds_for_page(conn, searcher, request):
    """Saved builds with their parts (current prices and photos)."""
    from . import queries as Q
    bs = saved(request)
    ids = {int(pid) for b in bs for pid, _ in b["items"]}
    prods = Q.cart_products(conn, searcher, ids)
    out = []
    for b in reversed(bs):
        lines = [dict(prods[int(pid)], qty=int(q), line_total=prods[int(pid)]["price"] * int(q))
                 for pid, q in b["items"] if int(pid) in prods]
        out.append({**b, "lines": lines, "total": sum(l["line_total"] for l in lines),
                    "date": time.strftime("%b %d, %Y", time.localtime(b.get("at", 0)))})
    return out


@router.get("/build", response_class=HTMLResponse)
def build_page(request: Request):
    st = web._state(request)
    ig = store_config().get("interactive_garage") or {}
    if not ig.get("url"):
        return RedirectResponse("/garage", status_code=303)
    with st.pool.connection() as conn:
        return web.render(request, conn, st.get_searcher(conn), "build.html", title="Build your dream ride", ig=ig,
                          n_builds=len(saved(request)))
