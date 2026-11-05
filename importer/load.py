"""Load one brand's SEMA files into the database.

Everything for a brand happens in one transaction, so the storefront never sees a half-loaded
catalog: if anything fails, the previous data stays exactly as it was.
"""
import collections
import json
import time

from psycopg.types.json import Jsonb

from . import aces, catalog, config, pies, vehicles

PRODUCT_COLS = ["brand_id", "part_number", "slug", "status", "title", "short_name", "part_type", "part_terminology_id",
                "category_id", "price", "price_type", "map_price", "upc", "weight_lb", "package_dims", "country_of_origin",
                "marketing_copy", "features", "specs", "attributes", "descriptions", "prop65_warning", "carb_eo",
                "emissions", "source_file_date", "size_norm", "prices"]
JSON_COLS = {"features", "specs", "attributes", "descriptions", "prices"}


LOGO_RASTER = ("PNG", "JPG", "JPEG", "GIF", "WEBP", "BMP", "TIF", "TIFF")


def logo_rank_of(asset):
    """0 = PNG (keeps transparency), 1 = other photo formats, 5 = anything else (EPS, AI, PDF: can't be shown)."""
    ft = (asset.get("file_type") or "").upper() or asset["uri"].rsplit(".", 1)[-1].split("?")[0].upper()
    return 0 if ft == "PNG" else 1 if ft in LOGO_RASTER else 5


def seed_reference(conn):
    """Insert/refresh brands and categories from config."""
    cat = config.catalog()
    with conn.cursor() as cur:
        for i, c in enumerate(cat["categories"]):
            cur.execute("""INSERT INTO categories (id, parent_id, name, sort) VALUES (%s, NULL, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, sort = EXCLUDED.sort""", (c["id"], c["name"], i * 100))
            for j, (sid, sname) in enumerate(c["subs"]):
                if sid == c["id"]:
                    raise ValueError(f"catalog.json: subcategory id {sid!r} is the same as its parent's id")
                cur.execute("""INSERT INTO categories (id, parent_id, name, sort) VALUES (%s, %s, %s, %s)
                               ON CONFLICT (id) DO UPDATE SET parent_id = EXCLUDED.parent_id, name = EXCLUDED.name, sort = EXCLUDED.sort""",
                            (sid, c["id"], sname, i * 100 + j + 1))
        for b in config.brands().values():
            cur.execute("""INSERT INTO brands (id, name, aaia_id, display_price) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, aaia_id = EXCLUDED.aaia_id, display_price = EXCLUDED.display_price""",
                        (b["id"], b["name"], b.get("aaia_id"), b.get("display_price", ["RET", "RMP", "LST"])))
        for r in config.image_reviews():
            cur.execute("""INSERT INTO image_reviews (key, key_type, verdict, reason) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (key) DO UPDATE SET verdict = EXCLUDED.verdict, reason = EXCLUDED.reason""",
                        (r["key"], r["key_type"], r["verdict"], r.get("reason")))


def load_brand(conn, brand, pies_path, aces_path=None, vehicles_path=None, log=print):
    """brand: an id from config/brands.json, or the settings dict from importer.brands.resolve()."""
    if isinstance(brand, str):
        brand = config.brands()[brand]
    brand_id = brand["id"]
    stats = collections.Counter()
    t0 = time.time()
    seed_reference(conn)
    with conn.cursor() as cur:   # brands added from their files (not in config/brands.json)
        cur.execute("""INSERT INTO brands (id, name, aaia_id, display_price) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (brand_id, brand["name"], brand.get("aaia_id"), brand.get("display_price", ["RET", "RMP", "LST"])))
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO import_runs (brand_id, step) VALUES (%s, 'catalog') RETURNING id", (brand_id,))
        run_id = cur.fetchone()[0]
    conn.commit()
    try:
        with conn.transaction():
            _load(conn, brand, pies_path, aces_path, vehicles_path, stats, log)
        refresh_derived(conn, brand_id)
        stats["seconds"] = round(time.time() - t0, 1)
        _finish(conn, run_id, "ok", stats)
    except Exception as e:
        conn.rollback()
        _finish(conn, run_id, "failed", stats, repr(e))
        raise
    return dict(stats)


def refresh_derived(conn, brand_id):
    """Rebuild data derived from the catalog: card photos and the search word list."""
    from .photos import refresh_main_images
    refresh_main_images(conn, brand_id)
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_matviews WHERE matviewname = 'search_lexicon'")
        if cur.fetchone():
            cur.execute("REFRESH MATERIALIZED VIEW search_lexicon")
    conn.commit()


def _finish(conn, run_id, status, stats, error=None):
    with conn.cursor() as cur:
        cur.execute("UPDATE import_runs SET finished_at = now(), status = %s, stats = %s, error = %s WHERE id = %s",
                    (status, Jsonb(dict(stats)), error, run_id))
    conn.commit()


def _load(conn, brand, pies_path, aces_path, vehicles_path, stats, log):
    categorizer = catalog.Categorizer()
    header = pies.read_header(pies_path)
    file_date = header.get("BlanketEffectiveDate") or None
    full_file = header.get("SubmissionType", "FULL").upper() == "FULL"

    # ---------- products (PIES)
    rows, prices, docs, photos = [], {}, {}, {}
    logo_url, logo_rank = None, 9
    uncategorized = collections.Counter()
    extra = [e for e in config.extra_images() if e["brand"] == brand["id"]]
    for item in pies.read_items(pies_path):
        if not item.get("part_number"):
            stats["pies_items_without_part_number"] += 1
            continue
        if brand.get("aaia_id") and item.get("brand_aaia") and item["brand_aaia"] != brand["aaia_id"]:
            stats["pies_items_other_brand"] += 1
            continue
        p = catalog.build_product(item, brand, categorizer, file_date)
        if logo_rank:
            for a in item["assets"]:
                if a["asset_type"] == "LGO" and a["uri"]:
                    rank = logo_rank_of(a)
                    if rank < logo_rank:
                        logo_url, logo_rank = a["uri"], rank
        rows.append(p)
        prices[p["part_number"]] = item["prices"]
        docs[p["part_number"]] = catalog.install_docs(item)
        photos[p["part_number"]] = catalog.photo_assets(item)
        if p["category_id"] == "uncategorized":
            uncategorized[p["part_type"] or "(no part type)"] += 1
    stats["products_in_file"] = len(rows)
    for p in rows:
        stats["products_" + p["status"]] += 1
    if uncategorized:
        stats["uncategorized_part_types"] = dict(uncategorized.most_common())
    log(f"  PIES: {len(rows)} products read")

    with conn.cursor() as cur:
        cols = ", ".join(PRODUCT_COLS)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in PRODUCT_COLS if c not in ("brand_id", "part_number"))
        cur.executemany(
            f"""INSERT INTO products ({cols}) VALUES ({", ".join(["%s"] * len(PRODUCT_COLS))})
                ON CONFLICT (brand_id, part_number) DO UPDATE SET {updates}, updated_at = now()""",
            ([Jsonb(p[c]) if c in JSON_COLS else p[c] for c in PRODUCT_COLS] for p in rows))
        if logo_url:
            cur.execute("UPDATE brands SET logo_url = %s WHERE id = %s", (logo_url, brand["id"]))
        cur.execute("SELECT part_number, id FROM products WHERE brand_id = %s", (brand["id"],))
        ids = dict(cur.fetchall())

        if full_file:
            # A FULL file lists the brand's whole catalog: anything not in it has been discontinued.
            cur.execute("""UPDATE products SET status = 'discontinued', updated_at = now()
                           WHERE brand_id = %s AND status <> 'discontinued' AND NOT (part_number = ANY(%s))""",
                        (brand["id"], [p["part_number"] for p in rows]))
            stats["products_discontinued_since_last_file"] = cur.rowcount

        pids = [ids[p["part_number"]] for p in rows]
        cur.execute("DELETE FROM product_prices WHERE product_id = ANY(%s)", (pids,))
        cur.executemany("INSERT INTO product_prices (product_id, price_type, amount, currency) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        [(ids[pn], x["type"], x["amount"], x["currency"]) for pn, lst in prices.items() for x in lst if x["type"]])
        cur.execute("DELETE FROM product_documents WHERE product_id = ANY(%s)", (pids,))
        cur.executemany("INSERT INTO product_documents (product_id, kind, url) VALUES (%s, 'install', %s) ON CONFLICT DO NOTHING",
                        [(ids[pn], u) for pn, lst in docs.items() for u in lst])

        # Photos: keep rows that are still in the feed (and their check results), add new ones, drop the rest.
        wanted = [(ids[pn], a["asset_type"], a["file_name"], a["uri"], i) for pn, lst in photos.items() for i, a in enumerate(lst)]
        wanted += [(ids[e["part_number"]], "WEB", e.get("file_name") or "", e["url"], 50 + i)
                   for i, e in enumerate(extra) if e["part_number"] in ids]
        seen, uniq = set(), []
        for w in wanted:
            if (w[0], w[2], w[3]) not in seen:
                seen.add((w[0], w[2], w[3])); uniq.append(w)
        wanted = uniq
        cur.execute("CREATE TEMP TABLE _img (product_id bigint, asset_type text, file_name text, source_url text, sort int) ON COMMIT DROP")
        with cur.copy("COPY _img FROM STDIN") as cp:
            for w in wanted:
                cp.write_row(w)
        cur.execute("""DELETE FROM product_images i WHERE i.product_id = ANY(%s) AND NOT EXISTS
                       (SELECT 1 FROM _img n WHERE n.product_id = i.product_id AND n.source_url IS NOT DISTINCT FROM i.source_url
                        AND n.file_name IS NOT DISTINCT FROM i.file_name)""", (pids,))
        cur.execute("""INSERT INTO product_images (product_id, asset_type, file_name, source_url, sort)
                       SELECT product_id, asset_type, file_name, source_url, sort FROM _img
                       ON CONFLICT (product_id, source_url, file_name) DO UPDATE SET asset_type = EXCLUDED.asset_type, sort = EXCLUDED.sort""")
        stats["photo_links"] = len(wanted)

        # ---------- raw ACES applications
        if aces_path:
            unknown = collections.Counter()
            cur.execute("DELETE FROM aces_applications WHERE product_id = ANY(%s)", (list(ids.values()),))
            n = 0
            with cur.copy("""COPY aces_applications (product_id, app_id, base_vehicle_id, year_from, year_to, make_id, model_id,
                             vcdb, qualifiers, notes, qty, position_id, part_type_id, mfr_label) FROM STDIN""") as cp:
                for a in aces.read_apps(aces_path):
                    if a["action"] == "D":
                        continue
                    pid = ids.get(a.get("part"))
                    if pid is None:
                        unknown[a.get("part")] += 1
                        continue
                    cp.write_row((pid, a["app_id"], a.get("base_vehicle_id"), a.get("year_from"), a.get("year_to"),
                                  a.get("make_id"), a.get("model_id"), json.dumps(a["vcdb"]), a["qualifiers"] or None,
                                  a["notes"] or None, a.get("qty"), a.get("position_id"), a.get("part_type_id"), a.get("mfr_label")))
                    n += 1
            stats["aces_applications"] = n
            if unknown:
                stats["aces_parts_not_in_pies"] = len(unknown)
            log(f"  ACES: {n} applications")

        # ---------- named fitment (Admark vehicle sheet)
        if vehicles_path:
            # The sheet is read twice (vehicles first, then fitment rows) instead of being held in memory.
            keys = sorted({(r["year"], r["make"], r["model"]) for r in vehicles.read_rows(vehicles_path)})
            cur.executemany("INSERT INTO vehicles (year, make, model) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", keys)
            cur.execute("SELECT year, make, model, id FROM vehicles")
            vid = {(y, mk, md): i for y, mk, md, i in cur.fetchall()}
            cur.execute("DELETE FROM fitment WHERE product_id = ANY(%s)", (list(ids.values()),))
            n, unknown = 0, set()
            with cur.copy("""COPY fitment (product_id, vehicle_id, submodel, engine, body, transmission, bed, wheelbase,
                             position, notes, mfr_label) FROM STDIN""") as cp:
                for r in vehicles.read_rows(vehicles_path):
                    pid = ids.get(r["sku"])
                    if pid is None:
                        unknown.add(r["sku"])
                        continue
                    sub = ", ".join(x for x in (r.get("submodel"), r.get("submodel2"), r.get("submodel3")) if x) or None
                    cp.write_row((pid, vid[(r["year"], r["make"], r["model"])], sub, r.get("engine"), r.get("body"),
                                  r.get("transmission"), r.get("bed"), r.get("wheelbase"), r.get("position"), r.get("notes"),
                                  r.get("mfr_label")))
                    n += 1
            stats["fitment_rows"] = n
            stats["vehicles_covered"] = len(keys)
            if unknown:
                stats["fitment_parts_not_in_pies"] = len(unknown)
            log(f"  Vehicles: {n} fitment rows across {len(keys)} year/make/models")

        # Fitment status. No vehicle rows does NOT mean the part fits everything: it is 'universal' only when the
        # brand says so in its attributes, or the part type is universal by nature (mufflers, tips, pipes). Otherwise
        # the brand simply hasn't sent fitment, and the part is kept out of vehicle-filtered results.
        cat_cfg = config.catalog()
        universal_cats = cat_cfg.get("universal_categories", [])
        size_cats = []
        for c in cat_cfg["categories"]:
            if c["id"] in cat_cfg.get("size_fit_categories", []):
                size_cats += [c["id"]] + [sid for sid, _ in c["subs"]]
        cur.execute("""
            UPDATE products p SET fitment_status = CASE
                WHEN EXISTS (SELECT 1 FROM fitment f WHERE f.product_id = p.id) THEN 'vehicle_specific'
                WHEN p.category_id = ANY(%s) THEN 'by_size'
                WHEN p.category_id = ANY(%s)
                  OR EXISTS (SELECT 1 FROM jsonb_each_text(p.attributes) a WHERE lower(a.value) LIKE '%%universal%%') THEN 'universal'
                ELSE 'missing' END
            WHERE p.brand_id = %s""", (size_cats, universal_cats, brand["id"]))
        # Searchable extras: category + parent category names and key specs.
        cur.execute("""
            UPDATE products p SET search_extra = concat_ws(' ', c.name, pc.name,
                   p.attributes->>'Brand Line', p.attributes->>'Series', p.attributes->>'Tire Size',
                   p.attributes->>'Bolt Pattern', p.attributes->>'Product Size - Bolt Pattern')
            FROM categories c LEFT JOIN categories pc ON pc.id = c.parent_id
            WHERE c.id = p.category_id AND p.brand_id = %s""", (brand["id"],))
        cur.execute("""SELECT fitment_status, count(*) FROM products WHERE brand_id = %s AND status = 'active' GROUP BY 1""", (brand["id"],))
        for st, n in cur.fetchall():
            stats["fitment_" + st] = n
        cur.execute("""SELECT part_number FROM products WHERE brand_id = %s AND status = 'active' AND fitment_status = 'missing'
                       ORDER BY part_number""", (brand["id"],))
        missing = [r[0] for r in cur.fetchall()]
        if missing:
            stats["parts_missing_fitment"] = missing
