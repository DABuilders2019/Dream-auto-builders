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
        cur.execute(f"SELECT id, name, aaia_id, display_price FROM brands WHERE {where} = %s", (value,))
        r = cur.fetchone()
    return {"id": r[0], "name": r[1], "aaia_id": r[2], "display_price": list(r[3] or DEFAULT_PRICES)} if r else None


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
    name = (name or label or aaia).strip()
    new_id = slugify(name)
    taken = _from_db(conn, "id", new_id) or cfg.get(new_id)
    if taken:
        new_id = f"{new_id}{aaia.lower()}"
    return {"id": new_id, "name": name, "aaia_id": aaia, "display_price": DEFAULT_PRICES, "new": True}
