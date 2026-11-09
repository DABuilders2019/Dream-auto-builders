"""Shipping prices at checkout: live UPS rates (the exact UPS price) for the cart, to the shopper's ZIP code.

Orders ship straight from suppliers, so each part is priced from its supplier's warehouse ZIP
(config/store.json > shipping: from_zip, and brand_from_zip for brands that ship from somewhere else).
Every unit is its own package, using the part's shipping weight and box size from the catalog.

If UPS isn't set up or doesn't answer, checkout still works with the fallback prices in the same settings,
and the order notes say so. Parts too big or heavy for UPS (over 150 lb, or longer than 108 in, or more than
165 in length + girth) can't be priced: the cart asks the shopper to contact us for a freight quote.
"""
import hashlib
import json
import logging
import re
import threading
import time

from . import orders as O
from . import ups

log = logging.getLogger("store.shipping")
CACHE_MINUTES = 30
_cache, _lock = {}, threading.Lock()


def settings(cfg=None):
    cfg = cfg or O.store_config()
    sh = dict(cfg.get("shipping") or {})
    sh.setdefault("carrier", "flat")
    sh.setdefault("from_zip", "")
    sh.setdefault("brand_from_zip", {})
    sh.setdefault("services", ["03", "12", "02", "13", "01"])
    sh.setdefault("default_weight_lb", 10)
    sh.setdefault("default_dims", [20, 12, 8])
    sh.setdefault("fallback", [{"name": "Standard shipping", "amount": float(cfg.get("flat_shipping", 14.95)), "min_days": 3, "max_days": 7}])
    return sh


def live(cfg=None):
    """True when checkout prices shipping with live UPS rates."""
    sh = settings(cfg)
    return sh["carrier"] == "ups" and bool(sh["from_zip"]) and ups.configured()


def clean_zip(z):
    d = "".join(ch for ch in str(z or "") if ch.isdigit())[:5]
    return d if len(d) == 5 and ups.state_for_zip(d) else ""


def parse_dims(text, default):
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text or "")][:3]
    return tuple(sorted(nums, reverse=True)) if len(nums) == 3 and all(n > 0 for n in nums) else tuple(sorted(default, reverse=True))


def too_big(weight, dims):
    l, w, h = dims
    return weight > 150 or l > 108 or l + 2 * (w + h) > 165


def packages_for(conn, lines, sh):
    """{origin_zip: [(weight, dims), ...]} and the lines that are too big for UPS."""
    ids = [l["product_id"] for l in lines]
    rows = {r[0]: r[1:] for r in conn.execute("SELECT id, weight_lb, package_dims, brand_id FROM products WHERE id = ANY(%s)", (ids,)).fetchall()}
    by_origin, big = {}, []
    for l in lines:
        weight, dims, brand = rows.get(l["product_id"], (None, None, None))
        weight = float(weight) if weight else float(sh["default_weight_lb"])
        dims = parse_dims(dims, sh["default_dims"])
        if too_big(weight, dims):
            big.append(l)
            continue
        origin = sh["brand_from_zip"].get(brand) or sh["from_zip"]
        by_origin.setdefault(origin, []).extend([(weight, dims)] * int(l["qty"]))
    return by_origin, big


def quote(conn, lines, to_zip, cfg=None):
    """Shipping choices for the cart to this ZIP: {"options": [{"code", "name", "amount", "days"}], "big": [lines],
    "error": str or None, "live": bool}. Cached for 30 minutes so the cart and checkout show the same prices."""
    sh = settings(cfg)
    key = hashlib.md5(json.dumps([sorted((l["product_id"], l["qty"]) for l in lines), to_zip, sh], sort_keys=True,
                                 default=str).encode()).hexdigest()
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_MINUTES * 60:
            return hit[1]
    by_origin, big = packages_for(conn, lines, sh)
    result = {"options": [], "big": big, "error": None, "live": True}
    if big:
        return result
    try:
        totals = None
        for origin, pkgs in by_origin.items():
            got = {r["code"]: r for r in ups.rates(origin, to_zip, packages=pkgs)}
            if totals is None:
                totals = {c: {"code": c, "name": r["service"], "amount": r["price"], "days": r["days"]} for c, r in got.items()}
            else:     # several warehouses: a service counts only if every one offers it, and the prices add up
                totals = {c: {**t, "amount": t["amount"] + got[c]["price"],
                              "days": max(filter(None, [t["days"], got[c]["days"]]), default=None, key=lambda d: int(d))}
                          for c, t in totals.items() if c in got}
        wanted = [c for c in sh["services"] if c in (totals or {})]
        result["options"] = [dict(totals[c], amount=round(totals[c]["amount"], 2)) for c in wanted][:5]
        if not result["options"]:
            raise ups.UPSError("UPS listed none of the services we offer for that ZIP.")
    except ups.UPSError as e:
        log.warning("UPS quote failed: %s", e)
        result.update(live=False, error=str(e),
                      options=[{"code": "", "name": f["name"], "amount": float(f["amount"]),
                                "days": f.get("max_days")} for f in sh["fallback"]][:5])
        return result                  # not cached: try UPS again next time
    with _lock:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[key] = (time.time(), result)
    return result


def stripe_options(q, currency, sh_fallback_days=(3, 7)):
    out = []
    for o in q["options"]:
        rate = {"type": "fixed_amount", "display_name": o["name"][:100],
                "fixed_amount": {"amount": round(o["amount"] * 100), "currency": currency},
                "metadata": {"ups_service": o.get("code") or ""}}
        if o.get("days"):
            d = int(o["days"])
            rate["delivery_estimate"] = {"minimum": {"unit": "business_day", "value": d}, "maximum": {"unit": "business_day", "value": d}}
        out.append({"shipping_rate_data": rate})
    return out
