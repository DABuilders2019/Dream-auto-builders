"""Read-only catalog queries for the storefront pages (product page, cards, home page numbers)."""
import json
import pathlib
from collections import defaultdict

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config"

CARD_COLS = """p.id, p.slug, p.part_number, p.brand_id, p.title, p.short_name, p.price, p.category_id,
               p.main_image, p.fitment_status, p.emissions, p.carb_eo"""


def home_config():
    return json.loads((CONFIG / "home.json").read_text())


def _rows(cur):
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def decorate(items, searcher):
    """Same fields the search results carry: brand/category names, photo URL, float price."""
    for it in items:
        it["brand_name"] = searcher.brands.get(it["brand_id"], it["brand_id"])
        it["category_name"] = (searcher.categories.get(it["category_id"]) or {}).get("name")
        if "main_image" in it:
            key = it.pop("main_image")
            it["image_url"] = searcher.storage.url(key) if key else None
        it["price"] = float(it["price"]) if it.get("price") is not None else None
    return items


def cards_by_slugs(conn, searcher, slugs, vehicle_id=None):
    """Active products in the order given (missing or inactive slugs are skipped)."""
    if not slugs:
        return []
    with conn.cursor() as cur:
        cur.execute(f"SELECT {CARD_COLS} FROM products p WHERE p.slug = ANY(%s) AND p.status = 'active' AND p.price IS NOT NULL",
                    (list(slugs),))
        found = {r["slug"]: r for r in _rows(cur)}
    items = decorate([found[s] for s in slugs if s in found], searcher)
    add_fit(conn, items, vehicle_id)
    return items


def add_fit(conn, items, vehicle_id=None):
    """Set each card's fit badge: how it fits the selected vehicle, or a short fitment summary."""
    if not items:
        return items
    ids = [it["id"] for it in items]
    if vehicle_id:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT product_id FROM fitment WHERE vehicle_id = %s AND product_id = ANY(%s)", (vehicle_id, ids))
            fits = {r[0] for r in cur.fetchall()}
        for it in items:
            if it.get("fit") is None:
                it["fit"] = fit_label(it["fitment_status"], it["id"] in fits)
    summaries = fit_summaries(conn, [it["id"] for it in items if it["fitment_status"] == "vehicle_specific"])
    for it in items:
        it["fit_summary"] = summaries.get(it["id"])
    add_stock(conn, items)
    return items


def fit_label(fitment_status, fits):
    if fits:
        return "fits"
    return {"universal": "universal", "by_size": "size", "missing": "unknown"}.get(fitment_status, "no")


def fit_summaries(conn, ids):
    """{product_id: "2019-2026 Ram 1500 + 3 more"} from the vehicle each part fits most often."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("""SELECT f.product_id, v.make, v.model, min(v.year), max(v.year), count(*)
                       FROM fitment f JOIN vehicles v ON v.id = f.vehicle_id
                       WHERE f.product_id = ANY(%s) GROUP BY 1, 2, 3""", (ids,))
        by_product = defaultdict(list)
        for pid, make, model, y0, y1, n in cur.fetchall():
            by_product[pid].append((n, y1, make, model, y0))
    out = {}
    for pid, rows in by_product.items():
        rows.sort(key=lambda r: (-r[0], -r[1], r[2], r[3]))
        n, y1, make, model, y0 = rows[0]
        years = str(y0) if y0 == y1 else f"{y0}-{y1}"
        out[pid] = {"label": f"{years} {make} {model}", "more": len(rows) - 1}
    return out


def product(conn, searcher, slug):
    with conn.cursor() as cur:
        cur.execute("""SELECT p.*, c.parent_id AS category_parent FROM products p
                       LEFT JOIN categories c ON c.id = p.category_id
                       WHERE p.slug = %s AND p.status = 'active'""", (slug,))
        rows = _rows(cur)
        if not rows:
            return None
        p = rows[0]
        decorate([p], searcher)
        p["map_price"] = float(p["map_price"]) if p["map_price"] is not None else None
        p["parent_name"] = (searcher.categories.get(p["category_parent"]) or {}).get("name") if p["category_parent"] else None

        cur.execute("""SELECT storage_key, kind FROM product_images
                       WHERE product_id = %s AND status = 'ok' AND storage_key IS NOT NULL
                       ORDER BY (kind = 'part') DESC, (kind = 'vehicle') DESC, sort, id""", (p["id"],))
        seen, images = set(), []
        for key, kind in cur.fetchall():
            if key not in seen:
                seen.add(key)
                images.append({"url": searcher.storage.url(key), "kind": kind})
        p["images"] = images

        cur.execute("SELECT kind, url FROM product_documents WHERE product_id = %s ORDER BY kind, url", (p["id"],))
        p["documents"] = [{"kind": k, "url": u} for k, u in cur.fetchall()]

        cur.execute("SELECT price_type, amount FROM product_prices WHERE product_id = %s", (p["id"],))
        p["prices"] = {t: float(a) for t, a in cur.fetchall()}
    p["fitment"], p["fitment_total"] = fitment_table(conn, p["id"])
    p["stock"] = stock_for(conn, [p["id"]]).get(p["id"])
    return p


def fitment_table(conn, product_id, limit=400):
    """Fitment rows grouped into year ranges: [{years, make, model, submodel, engine, notes}], total groups."""
    with conn.cursor() as cur:
        cur.execute("""SELECT v.make, v.model, coalesce(f.submodel, ''), coalesce(f.engine, ''), coalesce(f.notes, ''), v.year
                       FROM fitment f JOIN vehicles v ON v.id = f.vehicle_id WHERE f.product_id = %s
                       GROUP BY 1, 2, 3, 4, 5, 6 ORDER BY 1, 2, 3, 4, 5, 6""", (product_id,))
        rows = cur.fetchall()
    groups = []
    for make, model, sub, eng, notes, year in rows:
        g = groups[-1] if groups else None
        if g and (g["make"], g["model"], g["submodel"], g["engine"], g["notes"]) == (make, model, sub, eng, notes) and year == g["to"] + 1:
            g["to"] = year
        else:
            groups.append({"make": make, "model": model, "submodel": sub, "engine": eng, "notes": notes, "from": year, "to": year})
    groups.sort(key=lambda g: (g["make"], g["model"], -g["to"], g["submodel"], g["engine"]))
    for g in groups:
        g["years"] = str(g["from"]) if g["from"] == g["to"] else f"{g['from']}-{g['to']}"
    return groups[:limit], len(groups)


def vehicle_fit(conn, product_id, vehicle_id):
    """Fitment rows for one vehicle (empty if the part doesn't fit it)."""
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT coalesce(submodel, ''), coalesce(engine, ''), coalesce(notes, '')
                       FROM fitment WHERE product_id = %s AND vehicle_id = %s ORDER BY 1, 2, 3""", (product_id, vehicle_id))
        return [{"submodel": s, "engine": e, "notes": n} for s, e, n in cur.fetchall()]


def related(conn, searcher, p, vehicle_id=None, limit=4):
    """Other parts in the same category: same brand first, then with photos. Hides parts that don't fit the vehicle."""
    fit_join, fit_cond = "", ""
    params = {"cat": p["category_id"], "id": p["id"], "brand": p["brand_id"], "limit": limit}
    if vehicle_id:
        fit_join = "LEFT JOIN (SELECT DISTINCT product_id FROM fitment WHERE vehicle_id = %(vid)s) fs ON fs.product_id = p.id"
        fit_cond = "AND (fs.product_id IS NOT NULL OR p.fitment_status IN ('universal', 'by_size'))"
        params["vid"] = vehicle_id
    with conn.cursor() as cur:
        cur.execute(f"""SELECT {CARD_COLS} FROM products p {fit_join}
                        WHERE p.category_id = %(cat)s AND p.id <> %(id)s AND p.status = 'active' AND p.price IS NOT NULL {fit_cond}
                        ORDER BY (p.brand_id = %(brand)s) DESC, (p.main_image IS NOT NULL) DESC,
                                 abs(p.price - %(price)s), p.title LIMIT %(limit)s""",
                    {**params, "price": p["price"] or 0})
        items = decorate(_rows(cur), searcher)
    return add_fit(conn, items, vehicle_id)


def category_ids(searcher, cat_id):
    return [cat_id] + [c for c, v in searcher.categories.items() if v["parent"] == cat_id]


def min_price(conn, searcher, cat_id):
    with conn.cursor() as cur:
        cur.execute("SELECT min(price) FROM products WHERE status = 'active' AND category_id = ANY(%s)",
                    (category_ids(searcher, cat_id),))
        v = cur.fetchone()[0]
    return float(v) if v is not None else None


def price_of(conn, slug):
    with conn.cursor() as cur:
        cur.execute("SELECT price FROM products WHERE slug = %s AND status = 'active'", (slug,))
        r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def image_of(conn, searcher, slug):
    with conn.cursor() as cur:
        cur.execute("SELECT main_image FROM products WHERE slug = %s", (slug,))
        r = cur.fetchone()
    return searcher.storage.url(r[0]) if r and r[0] else None


def category_counts(conn, vehicle_id=None):
    """{top-level category id: active parts}, or parts that fit / are universal / fit by size for the vehicle."""
    with conn.cursor() as cur:
        if vehicle_id:
            cur.execute("""SELECT coalesce(c.parent_id, c.id), count(*) FROM products p JOIN categories c ON c.id = p.category_id
                           LEFT JOIN (SELECT DISTINCT product_id FROM fitment WHERE vehicle_id = %s) fs ON fs.product_id = p.id
                           WHERE p.status = 'active' AND p.price IS NOT NULL
                             AND (fs.product_id IS NOT NULL OR p.fitment_status IN ('universal', 'by_size'))
                           GROUP BY 1""", (vehicle_id,))
        else:
            cur.execute("""SELECT coalesce(c.parent_id, c.id), count(*) FROM products p JOIN categories c ON c.id = p.category_id
                           WHERE p.status = 'active' AND p.price IS NOT NULL GROUP BY 1""")
        return dict(cur.fetchall())


def sub_counts(conn):
    """{category id: active parts} for every category that has parts."""
    with conn.cursor() as cur:
        cur.execute("SELECT category_id, count(*) FROM products WHERE status = 'active' AND price IS NOT NULL GROUP BY 1")
        return dict(cur.fetchall())


def brands_with_counts(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT b.id, b.name, b.logo_key, count(p.id) FROM brands b
                       JOIN products p ON p.brand_id = b.id AND p.status = 'active' AND p.price IS NOT NULL
                       WHERE b.active GROUP BY 1, 2, 3 ORDER BY 2""")
        return [{"id": i, "name": n, "logo_key": k, "count": c} for i, n, k, c in cur.fetchall()]


def brand_logos(conn, searcher):
    """{brand id: logo URL}: a file chosen in config/home.json, else the logo from the brand's SEMA data."""
    chosen = home_config().get("brand_logos", {})
    with conn.cursor() as cur:
        cur.execute("SELECT id, logo_key FROM brands")
        rows = cur.fetchall()
    out = {i: searcher.storage.url(k) for i, k in rows if k}
    out.update({i: f"/static/img/brands/{f}" for i, f in chosen.items()})
    return out


def cart_products(conn, searcher, ids):
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(f"SELECT {CARD_COLS} FROM products p WHERE p.id = ANY(%s) AND p.status = 'active' AND p.price IS NOT NULL",
                    (list(ids),))
        return {r["id"]: r for r in decorate(_rows(cur), searcher)}


def top_for_vehicle(conn, searcher, vehicle_id, limit=8):
    """Parts made for this vehicle, with photos, one per category at a time (bigger kits first)."""
    with conn.cursor() as cur:
        cur.execute(f"""SELECT * FROM (
                          SELECT {CARD_COLS}, row_number() OVER (PARTITION BY p.category_id ORDER BY p.price DESC) AS rn
                          FROM products p JOIN (SELECT DISTINCT product_id FROM fitment WHERE vehicle_id = %s) fs ON fs.product_id = p.id
                          WHERE p.status = 'active' AND p.price IS NOT NULL AND p.main_image IS NOT NULL) x
                        ORDER BY rn, price DESC LIMIT %s""", (vehicle_id, limit))
        items = _rows(cur)
    for it in items:
        it.pop("rn", None)
    return add_fit(conn, decorate(items, searcher), vehicle_id)


# ------------------------------------------------------------------ stock (Turn 14 feed)

def stock_for(conn, product_ids):
    """{product id: stock info} for products matched to a Turn 14 item that has a stock record."""
    if not product_ids:
        return {}
    try:
        rows = conn.execute("""
            SELECT DISTINCT ON (i.product_id) i.product_id, i.active, i.regular_stock, pr.can_purchase,
                   v.by_location, v.total, v.mfr_stock, v.eta
            FROM t14_items i JOIN t14_inventory v ON v.item_id = i.item_id
            LEFT JOIN t14_pricing pr ON pr.item_id = i.item_id
            WHERE i.product_id = ANY(%s)
            ORDER BY i.product_id, i.active DESC, (i.item_id LIKE '%%G') DESC""", (list(product_ids),)).fetchall()
        states = dict(conn.execute("SELECT id, state FROM t14_locations").fetchall())
    except Exception:          # noqa: BLE001 - tables not there yet (older database): no stock shown
        return {}
    out = {}
    for pid, active, regular, can_buy, by_loc, total, mfr, eta in rows:
        s = stock_status(active, regular, can_buy, by_loc or {}, total or 0, mfr or 0, eta, states)
        if s:
            out[pid] = s
    return out


def stock_status(active, regular, can_buy, by_loc, total, mfr, eta, states):
    if not active or can_buy is False:
        return None
    if total > 0:
        where = [(f"{states.get(k) or k} warehouse", q) for k, q in sorted(by_loc.items()) if q and q > 0]
        return {"code": "in", "label": "In stock", "detail": "Usually ships in 1-2 business days", "where": where}
    if mfr > 0:
        return {"code": "mfr", "label": "Ships from the manufacturer", "detail": "Usually takes a few extra days", "where": []}
    dates = sorted(d for d in ((eta or {}).get("estimated_availability") or {}).values() if d)
    if dates:
        return {"code": "bo", "label": "Backordered", "detail": f"Expected around {dates[0]}", "where": []}
    if regular is False:
        return {"code": "special", "label": "Special order", "detail": "Ordered for you from the manufacturer", "where": []}
    return {"code": "out", "label": "Out of stock", "detail": "", "where": []}


def add_stock(conn, items):
    stock = stock_for(conn, [it["id"] for it in items if it.get("id")])
    for it in items:
        it["stock"] = stock.get(it.get("id"))
    return items
