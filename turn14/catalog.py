"""Products for a brand that Turn 14 approves for us but SEMA doesn't (yet): built from Turn 14's item data.

  python -m turn14 catalog:<turn 14 brand id>

Steps: the brand's items, their descriptions and photos (/v1/items/data/brand), pricing and stock from Turn 14,
written out as a PIES file and loaded with the same importer as the SEMA brands. There is no vehicle fitment,
so the products show "Fitment not listed" and stay out of vehicle-filtered results.

The brand is created with the brand code (AAIA) Turn 14 gives, and each product with the manufacturer's part
number. When SEMA approves the brand later and it is loaded from the SEMA Data page, the SEMA load finds the
same brand and the same part numbers, so the same products (same web addresses) get SEMA's data and fitment,
and Turn 14 stops rebuilding the brand.
"""
import datetime as dt
import html
import os
import pathlib
import re
import tempfile
from xml.sax import saxutils as su

from psycopg.types.json import Jsonb

from .client import StopSync
from .sync import norm


class CatalogError(Exception):
    pass


# ------------------------------------------------------------------ Turn 14 item data (descriptions, photos)

def fetch_data(s, t14_id, name):
    n = 0
    with s.conn.transaction():
        for rec in s.c.pages(f"/v1/items/data/brand/{t14_id}"):
            a = rec.get("attributes") if isinstance(rec.get("attributes"), dict) else {}
            descs = rec.get("descriptions") or a.get("descriptions") or []
            files = rec.get("files") or a.get("files") or []
            s.conn.execute("""INSERT INTO t14_item_data (item_id, descriptions, files, updated_at) VALUES (%s, %s, %s, now())
                              ON CONFLICT (item_id) DO UPDATE SET descriptions = EXCLUDED.descriptions,
                              files = EXCLUDED.files, updated_at = now()""",
                           (str(rec["id"]), Jsonb(descs if isinstance(descs, list) else []),
                            Jsonb(files if isinstance(files, list) else [])))
            n += 1
    s.stats["item_data"] += n
    s.log(f"  {name}: descriptions and photos for {n} items")


# ------------------------------------------------------------------ Turn 14 -> PIES

def clean(text):
    """Turn 14 descriptions can hold HTML: keep the words, one line break per paragraph or list item."""
    t = re.sub(r"(?i)<\s*(br|/p|/li|/div)\s*/?>", "\n", str(text or ""))
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    lines = [re.sub(r"[ \t ]+", " ", ln).strip(" -•*\t") for ln in t.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def desc_code(kind):
    k = str(kind or "").lower()
    if "market" in k:
        return "MKT"
    if "feature" in k or "benefit" in k:
        return "FAB"
    if "extended" in k:
        return "EXT"
    if "short" in k:
        return "SHO"
    if "long" in k or k.strip() in ("description", "product description"):
        return "DES"
    return None


def descriptions(item):
    """{PIES code: [texts]} from Turn 14's description list."""
    out = {}
    for d in item.get("descriptions") or []:
        if not isinstance(d, dict):
            continue
        code = desc_code(d.get("type") or d.get("description_type"))
        text = clean(d.get("description") or d.get("text") or d.get("value"))
        if not code or not text:
            continue
        if code == "FAB":    # one bullet per line
            out.setdefault(code, []).extend(text.splitlines())
        else:
            out.setdefault(code, []).append(text.replace("\n", " ") if code in ("SHO", "EXT") else text)
    if "MKT" not in out:    # the product page's description: the longest text Turn 14 has
        longest = max((t for c in ("DES", "EXT") for t in out.get(c, [])), key=len, default=None)
        if longest and len(longest) > 60:
            out["MKT"] = [longest]
    return out


SIZE_ORDER = {"O": 5, "XL": 4, "L": 3, "M": 2, "S": 1, "T": 0}


def best_link(f):
    links = [x for x in (f.get("links") or []) if isinstance(x, dict) and x.get("url")]
    if not links:
        return str(f["url"]) if f.get("url") else None
    links.sort(key=lambda x: ((_num(x.get("width")) or 0) * (_num(x.get("height")) or 0),
                              SIZE_ORDER.get(str(x.get("size") or x.get("asset_size") or "").upper(), 0)), reverse=True)
    return str(links[0]["url"])


def _ext(url, f):
    e = str(f.get("file_extension") or f.get("extension") or "").strip(".").upper()
    if e:
        return e
    m = re.search(r"\.([A-Za-z0-9]{2,4})(?:$|\?)", url or "")
    return m.group(1).upper() if m else ""


def assets(files, thumbnail=None):
    """[(asset type, file type, file name, url)]: primary photo P04, other photos P01, instructions INS."""
    out, primary = [], False
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url = best_link(f)
        if not url:
            continue
        ft = _ext(url, f)
        kind = f"{f.get('type') or ''} {f.get('media_content') or ''}".lower()
        if "instruction" in kind or (ft == "PDF" and "install" in kind):
            out.append(("INS", ft or "PDF", url.rsplit("/", 1)[-1].split("?")[0], url))
        elif "image" in kind or "photo" in kind or ft in ("JPG", "JPEG", "PNG", "WEBP", "GIF"):
            if "logo" in kind:
                continue
            is_primary = "primary" in kind and not primary
            primary = primary or is_primary
            out.append(("P04" if is_primary else "P01", ft or "JPG", url.rsplit("/", 1)[-1].split("?")[0], url))
    photos = [a for a in out if a[0] in ("P04", "P01")]
    if photos and not primary:      # no photo marked primary: the first one is
        i = out.index(photos[0])
        out[i] = ("P04",) + out[i][1:]
    if not photos and thumbnail:
        out.append(("P04", _ext(thumbnail, {}) or "JPG", thumbnail.rsplit("/", 1)[-1].split("?")[0], thumbnail))
    return out


def product_name(row, brand_name):
    name = clean(row.get("product_name") or row.get("part_description") or "").replace("\n", " ")
    words = name.split(" ")
    for n in range(min(4, len(words)), 0, -1):     # "K&N Air Filter" -> "Air Filter" (the brand is added back on the page)
        if norm(" ".join(words[:n])) == norm(brand_name):
            name = " ".join(words[n:])
            break
    return name.strip(" -") or row["pn"]


def _e(v):
    return su.escape(str(v))


def _a(v):
    return su.quoteattr(str(v))


def _num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def item_xml(row, aaia, brand_name):
    pn = row["pn"]
    x = [f"<Item MaintenanceType={_a('A' if row['active'] else 'D')}>", f"<PartNumber>{_e(pn)}</PartNumber>",
         f"<BrandAAIAID>{_e(aaia)}</BrandAAIAID>", f"<BrandLabel>{_e(brand_name)}</BrandLabel>"]
    bc = re.sub(r"\D", "", row.get("barcode") or "")
    if len(bc) in (12, 13, 14):
        x.append(f"<ItemLevelGTIN>{_e(bc.zfill(14))}</ItemLevelGTIN>")
    d = descriptions(row)
    part_type = row.get("subcategory") or row.get("category")
    if part_type:
        d["DEF"] = [part_type]
    if row.get("part_description"):
        d.setdefault("SHO", []).insert(0, clean(row["part_description"]).replace("\n", " "))
    x.append("<Descriptions>")
    for code, texts in d.items():
        for t in texts:
            x.append(f"<Description DescriptionCode={_a(code)}>{_e(t)}</Description>")
    x.append("</Descriptions>")
    prices = [("RET", row.get("retail_price")), ("RMP", row.get("map_price") if row.get("has_map") is not False else None),
              ("JBR", row.get("jobber_price"))]
    prices = [(t, _num(v)) for t, v in prices if _num(v)]
    if prices:
        x.append("<Prices>")
        for t, v in prices:
            x.append(f"<Pricing PriceType={_a(t)}><Price UOM=\"PE\">{v:.2f}</Price><CurrencyCode>USD</CurrencyCode></Pricing>")
        x.append("</Prices>")
    attrs = {"Title": product_name(row, brand_name)}
    if row.get("carb_eo_number"):
        attrs["CARB Executive Order Number"] = row["carb_eo_number"]
    if str(row.get("prop_65") or "").strip().lower().startswith("y"):
        attrs["Prop 65"] = "Yes"
    if row.get("ltl_freight_required"):
        attrs["Ships By"] = "Truck freight"
    if (row.get("units_per_sku") or 1) > 1:
        attrs["Quantity"] = str(row["units_per_sku"])
    x.append("<ProductAttributes>")
    for k, v in attrs.items():
        x.append(f"<ProductAttribute AttributeID={_a(k)} PADBAttribute=\"N\">{_e(v)}</ProductAttribute>")
    x.append("</ProductAttributes>")
    boxes = [b for b in (row.get("dimensions") or []) if isinstance(b, dict)]
    if boxes:
        weight = sum(_num(b.get("weight")) or 0 for b in boxes)
        dims = [_num(boxes[0].get(k)) for k in ("length", "width", "height")]
        x.append("<Packages><Package>")
        if weight:
            x.append(f"<Weights UOM=\"PG\"><Weight>{weight:g}</Weight></Weights>")
        if all(dims) and len(boxes) == 1:    # several boxes: one box's size would understate the shipment
            x.append(f"<Dimensions UOM=\"IN\"><Length>{dims[0]:g}</Length><Width>{dims[1]:g}</Width>"
                     f"<Height>{dims[2]:g}</Height></Dimensions>")
        x.append("</Package></Packages>")
    files = assets(row.get("files"), row.get("thumbnail"))
    if files:
        x.append("<DigitalAssets>")
        for kind, ft, fname, url in files:
            x.append(f"<DigitalFileInformation><AssetType>{kind}</AssetType><FileName>{_e(fname)}</FileName>"
                     f"<FileType>{_e(ft)}</FileType><URI>{_e(url)}</URI></DigitalFileInformation>")
        x.append("</DigitalAssets>")
    x.append("</Item>\n")
    return "".join(x)


def rows_for_brand(conn, t14_id):
    """One row per manufacturer part number: an active "G" (kit) item wins, as in matching."""
    cur = conn.execute("""
        SELECT i.item_id, coalesce(nullif(i.mfr_part_number, ''), i.part_number) AS pn, i.barcode, i.product_name,
               i.part_description, i.category, i.subcategory, i.dimensions, i.thumbnail, i.units_per_sku, i.active,
               i.regular_stock, i.carb_eo_number, i.prop_65, i.ltl_freight_required,
               p.retail_price, p.map_price, p.has_map, p.jobber_price, d.descriptions, d.files
        FROM t14_items i LEFT JOIN t14_pricing p ON p.item_id = i.item_id LEFT JOIN t14_item_data d ON d.item_id = i.item_id
        WHERE i.t14_brand_id = %s AND coalesce(nullif(i.mfr_part_number, ''), i.part_number) IS NOT NULL""", (t14_id,))
    cols = [c.name for c in cur.description]
    best = {}
    for r in cur.fetchall():
        row = dict(zip(cols, r))
        key = norm(row["pn"])
        if not key:
            continue
        rank = (bool(row["active"]), row["item_id"].upper().endswith("G"), row["regular_stock"] is not False)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, row)
    return [row for _, row in sorted(best.values(), key=lambda v: v[1]["pn"])]


def write_pies(conn, t14_id, aaia, brand_name, path):
    rows = rows_for_brand(conn, t14_id)
    skipped = []
    with open(path, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="utf-8"?>\n<PIES xmlns="http://www.aftermarket.org">\n'
                  '<Header><SubmissionType>FULL</SubmissionType>'
                  f'<BlanketEffectiveDate>{dt.date.today().isoformat()}</BlanketEffectiveDate></Header>\n<Items>\n')
        for row in rows:
            try:
                out.write(item_xml(row, aaia, brand_name))
            except Exception as e:      # noqa: BLE001 - one odd item shouldn't stop the brand
                skipped.append(f"{row['pn']} ({e.__class__.__name__}: {e})")
        out.write("</Items>\n</PIES>\n")
    return len(rows) - len(skipped), skipped


# ------------------------------------------------------------------ the load

def brand_row(conn, t14_id):
    r = conn.execute("""SELECT t.id, t.name, t.aaia, t.approved, t.brand_id, t.load_catalog,
                               EXISTS (SELECT 1 FROM sema_brands s WHERE s.brand_id = t.brand_id AND s.status = 'loaded')
                        FROM t14_brands t WHERE t.id = %s""", (t14_id,)).fetchone()
    if not r:
        raise CatalogError("Turn 14 doesn't list this brand. Run Test connection first.")
    keys = ["id", "name", "aaia", "approved", "brand_id", "load_catalog", "from_sema"]
    return dict(zip(keys, r))


def brand_code(t):
    return (t["aaia"] or [None])[0] or f"T14{t['id']}"


def load(s, t14_id, fetch=True, photos=True):
    """Build (or rebuild) our products for one Turn 14 brand. s: a turn14.sync.Sync."""
    from importer import brands as brand_lookup
    from importer import load as loader
    conn, log = s.conn, s.log
    t = brand_row(conn, t14_id)
    if not t["approved"]:
        raise CatalogError(f"{t['name']} isn't approved for this account at Turn 14.")
    if t["brand_id"] and not t["load_catalog"]:
        raise CatalogError(f"{t['name']} is already on the site from SEMA's data; Turn 14 only adds its stock and pricing.")
    if t["from_sema"]:
        raise CatalogError(f"{t['name']} has been loaded from SEMA since; its products now come from SEMA.")
    log(f"--- {t['name']} from Turn 14 (no vehicle fitment) ---")
    if fetch:
        s.items_for_brand(t14_id, t["name"])
        fetch_data(s, t14_id, t["name"])
        s.pricing_for_brand(t14_id, t["name"])
        s.inventory_for_brand(t14_id, t["name"])
    aaia = brand_code(t)
    if t["brand_id"]:
        brand = brand_lookup.resolve(conn, None, brand_id=t["brand_id"])
    else:
        brand = brand_lookup.resolve(conn, aaia, t["name"], t["name"])
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "pies.xml"
        n, skipped = write_pies(conn, t14_id, brand.get("aaia_id") or aaia, brand["name"], path)
        if skipped:
            s.stats["catalog_items_skipped"] += len(skipped)
            log(f"  Skipped {len(skipped)} item(s) with data we couldn't read: " + "; ".join(skipped[:10])
                + (" ..." if len(skipped) > 10 else ""))
        if not n:
            raise CatalogError(f"Turn 14 lists no items for {t['name']}.")
        log(f"  {n} parts written for the importer")
        st = loader.load_brand(conn, brand, str(path), None, None, log=log)
    conn.execute("UPDATE t14_brands SET brand_id = %s, load_catalog = true, catalog_loaded = now() WHERE id = %s",
                 (brand["id"], t14_id))
    s.match(t14_id, brand["id"])
    s.stats["catalog_products"] += st.get("products_in_file", 0)
    s.stats["catalog_active"] += st.get("products_active", 0)
    s.stats["catalog_hidden_no_price"] += st.get("products_hidden", 0)
    if st.get("uncategorized_part_types"):
        log(f"  Not sorted into a category yet: {st['uncategorized_part_types']}")
    log(f"  {t['name']}: {st.get('products_active', 0)} products on the site"
        + (f", {st['products_hidden']} hidden (no price)" if st.get("products_hidden") else ""))
    if photos and os.environ.get("T14_SKIP_PHOTOS") != "1":
        from semadata.sync import run_photos
        log(f"--- {t['name']}: photos ---")
        run_photos(conn, brand["id"], log)
    return brand["id"]


def catalog_brands(conn):
    """Turn 14 brands whose products we build from Turn 14 (and that SEMA hasn't taken over)."""
    return [r[0] for r in conn.execute("""SELECT t.id FROM t14_brands t WHERE t.load_catalog AND t.approved
                                          AND NOT EXISTS (SELECT 1 FROM sema_brands s WHERE s.brand_id = t.brand_id
                                                          AND s.status = 'loaded') ORDER BY t.name""").fetchall()]


def refresh_all(s, fetch_data_too):
    """Daily / full-items: rebuild each Turn-14 brand from the local tables (descriptions again on full-items)."""
    for t14_id in catalog_brands(s.conn):
        try:
            if fetch_data_too:
                fetch_data(s, t14_id, brand_row(s.conn, t14_id)["name"])
            load(s, t14_id, fetch=False, photos=True)
        except CatalogError as e:
            s.log(f"  skipped: {e}")


__all__ = ["load", "refresh_all", "CatalogError", "StopSync"]
