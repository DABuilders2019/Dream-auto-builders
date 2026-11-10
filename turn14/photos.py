"""Photos from Turn 14 for parts whose brand data has none (SEMA has no photos for some brands, e.g. Brembo).

For a brand on the site, the parts matched to a Turn 14 item but without a usable photo get that item's Turn 14
photos (asset type T14, after any of the brand's own). The brand's item data is read only when some part needs a
photo, and the normal photo download then fetches, checks, trims and stores them. A later SEMA reload keeps them.
"""
import os

from .catalog import assets, fetch_data

NEEDS_PHOTO = """
    SELECT p.id, i.item_id FROM products p JOIN t14_items i ON i.product_id = p.id
    WHERE p.brand_id = %s AND p.status <> 'discontinued' AND i.t14_brand_id = %s
      AND NOT EXISTS (SELECT 1 FROM product_images x WHERE x.product_id = p.id
                      AND x.status IN ('ok', 'pending'))"""     # has a photo, or one waiting to be downloaded


def fill(s, t14_id, our_brand, name, download=True):
    """Add Turn 14 photos for our parts that have none. Returns how many parts got photo links."""
    conn, log = s.conn, s.log
    need = conn.execute(NEEDS_PHOTO, (our_brand, t14_id)).fetchall()
    if not need:
        return 0
    have = {r[0] for r in conn.execute("""SELECT d.item_id FROM t14_item_data d JOIN t14_items i ON i.item_id = d.item_id
                                          WHERE i.t14_brand_id = %s AND d.updated_at > now() - interval '14 days'""",
                                       (t14_id,)).fetchall()}
    if not all(item in have for _, item in need):
        log(f"  {name}: {len(need)} parts have no photo; reading Turn 14's photos for the brand")
        fetch_data(s, t14_id, name)
        # items Turn 14 has no photo data for: noted as checked, so they don't trigger another read for 14 days
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany("""INSERT INTO t14_item_data (item_id) VALUES (%s)
                               ON CONFLICT (item_id) DO UPDATE SET updated_at = now()
                               WHERE t14_item_data.updated_at < now() - interval '14 days'""",
                            [(item,) for _, item in need])
    files = dict(conn.execute("""SELECT d.item_id, d.files FROM t14_item_data d JOIN t14_items i ON i.item_id = d.item_id
                                 WHERE i.t14_brand_id = %s""", (t14_id,)).fetchall())
    thumbs = dict(conn.execute("SELECT item_id, thumbnail FROM t14_items WHERE t14_brand_id = %s", (t14_id,)).fetchall())
    rows = []
    for pid, item in need:
        photos = sorted((a for a in assets(files.get(item), thumbs.get(item)) if a[0] in ("P04", "P01")),
                        key=lambda a: a[0] != "P04")[:4]          # Turn 14's primary photo first
        rows += [(pid, "T14", fname, url, 100 + n) for n, (_, _, fname, url) in enumerate(photos)]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("""INSERT INTO product_images (product_id, asset_type, file_name, source_url, sort)
                           VALUES (%s, %s, %s, %s, %s)
                           ON CONFLICT (product_id, source_url, file_name) DO UPDATE SET
                             status = CASE WHEN product_images.status = 'failed' THEN 'pending' ELSE product_images.status END""",
                        rows)
    parts = len({r[0] for r in rows})
    s.stats["parts_given_turn14_photos"] += parts
    log(f"  {name}: Turn 14 photos found for {parts} of the {len(need)} parts without one")
    if parts and download and os.environ.get("T14_SKIP_PHOTOS") != "1":
        from semadata.sync import run_photos
        log(f"--- {name}: photos ---")
        run_photos(conn, our_brand, log)
    return parts


def fill_all(s, brands):
    """brands: [(turn 14 brand id, our brand id, name)]."""
    for t14_id, ours, name in brands:
        fill(s, t14_id, ours, name)
