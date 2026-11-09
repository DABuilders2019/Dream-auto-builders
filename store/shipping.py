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


def packages_for(conn, lines, sh, sizes=None):
    """{origin_zip: [(weight, dims), ...]} and the lines that are too big for UPS.
    Pass a dict as sizes to get each part's (weight lb, (l, w, h) in) by product id."""
    ids = [l["product_id"] for l in lines]
    rows = {r[0]: r[1:] for r in conn.execute("SELECT id, weight_lb, package_dims, brand_id FROM products WHERE id = ANY(%s)", (ids,)).fetchall()}
    by_origin, big = {}, []
    for l in lines:
        weight, dims, brand = rows.get(l["product_id"], (None, None, None))
        weight = float(weight) if weight else float(sh["default_weight_lb"])
        dims = parse_dims(dims, sh["default_dims"])
        if sizes is not None:
            sizes[l["product_id"]] = (weight, dims)
        if too_big(weight, dims):
            big.append(l)
            continue
        origin = sh["brand_from_zip"].get(brand) or sh["from_zip"]
        by_origin.setdefault(origin, []).extend([(weight, dims)] * int(l["qty"]))
    return by_origin, big


def quote(conn, lines, to_zip, cfg=None):
    """Shipping choices for the cart to this ZIP: {"options": [{"code", "name", "amount", "days"}], "big": [lines],
    "error": str or None, "live": bool, "free": {...}}. UPS prices are cached for 30 minutes so the cart and
    checkout show the same prices; the free-shipping rule (Admin > UPS) is applied on top each time."""
    raw = _ups_quote(conn, lines, to_zip, cfg)

    def price_of(sub):                   # UPS prices for just these parts (the ones free shipping doesn't cover)
        part = _ups_quote(conn, sub, to_zip, cfg)
        return {o["code"]: o["amount"] for o in part["options"]} if part["live"] and not part["big"] else {}
    return apply_free(raw, free_rule(conn), lines, to_zip, price_of)


def _ups_quote(conn, lines, to_zip, cfg=None):
    sh = settings(cfg)
    key = hashlib.md5(json.dumps([sorted((l["product_id"], l["qty"]) for l in lines), to_zip, sh], sort_keys=True,
                                 default=str).encode()).hexdigest()
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_MINUTES * 60:
            return hit[1]
    sizes = {}
    by_origin, big = packages_for(conn, lines, sh, sizes)
    result = {"options": [], "big": big, "error": None, "live": True, "sizes": sizes}
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


# ------------------------------------------------------------------ free shipping over an order amount (Admin > UPS)

FREE_KEY = "free_shipping"
FREE_DEFAULT = {"on": False, "over": 149.0, "max_lb": 50.0, "service": "03", "lower48": True, "oversize": True}
FREE_SERVICES = {"03": "UPS Ground", "12": "UPS 3 Day Select", "02": "UPS 2nd Day Air"}


def free_rule(conn):
    """The free-shipping settings saved on Admin > UPS (defaults if never saved)."""
    rule = dict(FREE_DEFAULT)
    try:
        row = conn.execute("SELECT value FROM app_state WHERE key = %s", (FREE_KEY,)).fetchone()
        if row and row[0]:
            rule.update({k: v for k, v in json.loads(row[0]).items() if k in FREE_DEFAULT})
    except Exception as e:               # a bad saved value must never break the cart
        log.warning("free shipping settings unreadable: %s", e)
    return rule


def save_free_rule(conn, rule):
    clean = {k: rule.get(k, v) for k, v in FREE_DEFAULT.items()}
    conn.execute("""INSERT INTO app_state (key, value, updated_at) VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                 (FREE_KEY, json.dumps(clean)))
    conn.commit()
    return clean


def _subtotal(lines):
    return round(sum(float(l.get("unit_price", l.get("price")) or 0) * int(l["qty"]) for l in lines), 2)


def ups_surcharge(weight, dims):
    """True when UPS adds a handling or large-package charge for this box: over 50 lb, longest side over 48 in,
    second-longest side over 30 in, longer than 96 in, or length + girth over 130 in."""
    l, w, h = dims
    return weight > 50 or l > 48 or w > 30 or l > 96 or l + 2 * (w + h) > 130


def excluded(rule, lines, sizes):
    """[(line, reason)] for the parts free shipping doesn't cover: over the weight limit, or (when the
    'oversized' setting is on) a box UPS charges extra for."""
    out, max_lb = [], float(rule["max_lb"] or 0)
    for l in lines:
        weight, dims = sizes.get(l["product_id"], (0, (0, 0, 0)))
        if max_lb and weight > max_lb:
            out.append((l, f"over {max_lb:g} lb"))
        elif rule.get("oversize") and ups_surcharge(weight, dims):
            out.append((l, "oversized box"))
    return out


def _label(l):
    return " ".join(filter(None, [l.get("brand_name"), l.get("part_number")])) or l.get("title", "")


def apply_free(raw, rule, lines, to_zip, price_of=None):
    """A copy of the quote with the free service applied when the order qualifies.

    The order total (all parts) must reach the minimum. Parts free shipping doesn't cover (see excluded) still pay:
    the free service then costs the UPS price of just those parts, and everything else rides free. If every part is
    excluded, nothing changes. Faster services always keep their full UPS price.

    result["free"]["why"]: None (all free), "partial" (free except `heavy`, which cost `charge`), "short" (add `short`
    more), "heavy" (every part is excluded), "area" (outside the lower 48), "service" (no price for that service),
    or "off"."""
    q = dict(raw, options=[dict(o) for o in raw["options"]])
    sizes = raw.get("sizes") or {}
    sub = _subtotal(lines)
    info = {"on": bool(rule["on"]), "ok": False, "why": "off", "over": float(rule["over"]), "short": 0.0,
            "max_lb": float(rule["max_lb"] or 0), "heavy": [], "charge": 0.0,
            "service": FREE_SERVICES.get(rule["service"], "UPS Ground")}
    q["free"] = info
    if not rule["on"] or q["big"]:
        return q
    state = ups.state_for_zip(to_zip)
    out = excluded(rule, lines, sizes)
    info["heavy"] = [{"name": _label(l), "why": why} for l, why in out]
    if rule["lower48"] and (state not in O.US_STATES or state in ("AK", "HI")):
        info["why"] = "area"
        return q
    if len(out) == len(lines):
        info["why"] = "heavy"
        return q
    if sub < info["over"]:
        info.update(why="short", short=round(info["over"] - sub, 2))
        return q
    if q["live"]:
        pick = next((o for o in q["options"] if o["code"] == rule["service"]), None)
    else:                                # UPS didn't answer: the standard fallback rate is the free one
        pick = q["options"][0] if q["options"] and not out else None
    charge = 0.0
    if pick and out:
        prices = price_of([l for l, _ in out]) if price_of else {}
        if rule["service"] not in prices:
            pick = None
        else:
            charge = round(min(prices[rule["service"]], pick["amount"]), 2)
    if not pick:
        info["why"] = "service"
        return q
    if charge:
        pick.update(amount=charge, name=pick["name"] + " (free except oversized parts)")
    else:
        pick.update(amount=0.0, name="Free " + (pick["name"] if q["live"] else "standard shipping"))
    q["options"] = [pick] + [o for o in q["options"] if o is not pick]
    info.update(ok=True, why="partial" if out else None, charge=charge)
    return q


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
