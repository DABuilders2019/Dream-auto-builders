"""Which brand a SEMA file belongs to.

Brands listed in config/brands.json keep their special settings (price types, name clean-up).
A brand that isn't listed there is added automatically the first time its PIES file is loaded,
using the brand code (AAIA ID) and name inside the file, with default settings.
"""
import re

from . import config, pies

DEFAULT_PRICES = ["RET", "RMP", "LST"]


def peek(pies_path):
    """(AAIA brand ID, brand label) from the first item in a PIES file."""
    for item in pies.read_items(pies_path):
        if item.get("brand_aaia"):
            return item["brand_aaia"].strip(), (item.get("brand_label") or "").strip()
    return None, ""


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower()) or "brand"


def _from_db(conn, where, value):
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, name, aaia_id, display_price, price_markup FROM brands WHERE {where} = %s", (value,))
        r = cur.fetchone()
    return {"id": r[0], "name": r[1], "aaia_id": r[2], "display_price": list(r[3] or DEFAULT_PRICES),
            "price_markup": float(r[4]) if r[4] is not None else None} if r else None


def _from_turn14(conn, aaia, name):
    """A brand first added from Turn 14's data, now loaded from SEMA under a different brand code: same brand, so
    the same products (matched by part number) are upgraded instead of duplicated. Its code becomes SEMA's."""
    with conn.cursor() as cur:
        cur.execute("""SELECT t.brand_id FROM t14_brands t WHERE t.load_catalog AND t.brand_id IS NOT NULL
                         AND (%s = ANY(t.aaia) OR lower(regexp_replace(t.name, '[^A-Za-z0-9]', '', 'g')) = %s)
                       ORDER BY (%s = ANY(t.aaia)) DESC LIMIT 1""", (aaia, re.sub(r"[^a-z0-9]", "", (name or "").lower()), aaia))
        r = cur.fetchone()
        if not r:
            return None
        cur.execute("UPDATE brands SET aaia_id = %s WHERE id = %s", (aaia, r[0]))
    return _from_db(conn, "id", r[0])


def resolve(conn, aaia, label="", name=None, brand_id=None):
    """Brand settings for a file with this AAIA brand ID. Raises ValueError if the file doesn't match brand_id."""
    cfg = config.brands()
    if brand_id:
        b = cfg.get(brand_id) or _from_db(conn, "id", brand_id)
        if not b:
            raise ValueError(f"unknown brand {brand_id!r}")
        if aaia and b.get("aaia_id") and b["aaia_id"] != aaia:
            raise ValueError(f"this file is for brand code {aaia}, not {b['name']} ({b['aaia_id']})")
        return b
    if not aaia:
        raise ValueError("the PIES file has no BrandAAIAID, so its brand can't be identified")
    b = config.brand_by_aaia(aaia) or _from_db(conn, "aaia_id", aaia)
    if b:
        return b
    b = _from_turn14(conn, aaia, name or label)
    if b:
        return b
    name = (name or label or aaia).strip()
    new_id = slugify(name)
    taken = _from_db(conn, "id", new_id) or cfg.get(new_id)
    if taken:
        new_id = f"{new_id}{aaia.lower()}"
    return {"id": new_id, "name": name, "aaia_id": aaia, "display_price": DEFAULT_PRICES, "new": True}
