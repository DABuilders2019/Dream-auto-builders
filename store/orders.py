"""Orders: saved before the shopper goes to Stripe, completed when Stripe reports the payment."""
import datetime as dt
import json
import pathlib

from psycopg.types.json import Jsonb

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config"

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
STATUS_LABELS = {"pending": "Waiting for payment", "paid": "Paid, being prepared", "on_hold": "On hold",
                 "shipped": "Shipped", "cancelled": "Cancelled", "refunded": "Refunded", "payment_failed": "Payment failed"}


def store_config():
    return json.loads((CONFIG / "store.json").read_text())


def shipping_for(subtotal, cfg=None):
    cfg = cfg or store_config()
    return 0.0 if subtotal >= cfg["free_shipping_over"] else float(cfg["flat_shipping"])


def carb_problem(items, state, cfg=None):
    """Parts that can't ship to this state (not CARB-approved catalytic converters), or []."""
    cfg = cfg or store_config()
    if (state or "").upper() not in cfg.get("carb_restricted_states", []):
        return []
    return [it for it in items if it.get("emissions") == "federal_only"]


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def create(conn, lines, vehicle=None, note=None):
    """lines: dicts with product_id, slug, brand_name, part_number, title, qty, unit_price, emissions."""
    subtotal = round(sum(l["unit_price"] * l["qty"] for l in lines), 2)
    events = [{"at": _now(), "text": "Checkout started" + (f" ({note})" if note else "")}]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("INSERT INTO orders (subtotal, vehicle, events) VALUES (%s, %s, %s) RETURNING id, number",
                    (subtotal, vehicle, Jsonb(events)))
        order_id, number = cur.fetchone()
        cur.executemany("""INSERT INTO order_items (order_id, product_id, slug, brand_name, part_number, title, qty, unit_price, emissions)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        [(order_id, l["product_id"], l["slug"], l["brand_name"], l["part_number"], l["title"], l["qty"],
                          l["unit_price"], l.get("emissions")) for l in lines])
    return order_id, number


def set_session(conn, order_id, session_id):
    conn.execute("UPDATE orders SET stripe_session_id = %s, updated_at = now() WHERE id = %s", (session_id, order_id))


def add_event(conn, order_id, text):
    conn.execute("UPDATE orders SET events = events || %s, updated_at = now() WHERE id = %s",
                 (Jsonb([{"at": _now(), "text": text}]), order_id))


def get(conn, number=None, order_id=None, session_id=None):
    where, val = ("number", number) if number else ("id", order_id) if order_id else ("stripe_session_id", session_id)
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM orders WHERE {where} = %s", (val,))
        cols = [c.name for c in cur.description]
        r = cur.fetchone()
        if not r:
            return None
        o = dict(zip(cols, r))
        cur.execute("""SELECT i.product_id, i.slug, i.brand_name, i.part_number, i.title, i.qty, i.unit_price, i.emissions,
                              p.main_image
                       FROM order_items i LEFT JOIN products p ON p.id = i.product_id WHERE i.order_id = %s ORDER BY i.id""",
                    (o["id"],))
        icols = [c.name for c in cur.description]
        o["items"] = [dict(zip(icols, x)) for x in cur.fetchall()]
    for k in ("subtotal", "shipping", "tax", "total"):
        o[k] = float(o[k]) if o[k] is not None else None
    for it in o["items"]:
        it["unit_price"] = float(it["unit_price"])
        it["line_total"] = round(it["unit_price"] * it["qty"], 2)
    o["status_label"] = STATUS_LABELS.get(o["status"], o["status"])
    return o


def lookup(conn, number, email):
    o = get(conn, number=(number or "").strip().upper())
    if o and o["email"] and o["email"].strip().lower() == (email or "").strip().lower() and o["status"] != "pending":
        return o
    return None


def recent(conn, status=None, limit=200):
    with conn.cursor() as cur:
        cur.execute(f"""SELECT o.number, o.status, o.email, o.name, o.total, o.subtotal, o.created_at, o.paid_at,
                               (SELECT sum(qty) FROM order_items i WHERE i.order_id = o.id) AS items,
                               o.ship_address->>'state' AS state
                        FROM orders o {"WHERE o.status = %s" if status else "WHERE o.status <> 'pending' OR o.created_at > now() - interval '2 days'"}
                        ORDER BY o.created_at DESC LIMIT %s""", ((status, limit) if status else (limit,)))
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
    return rows


# ------------------------------------------------------------------ Stripe results

def _cents(v):
    return round((v or 0) / 100, 2)


def _shipping_details(s):
    ci = s.get("collected_information") or {}
    sd = ci.get("shipping_details") or s.get("shipping_details") or {}
    cd = s.get("customer_details") or {}
    addr = sd.get("address") or cd.get("address") or {}
    return (sd.get("name") or cd.get("name")), addr


def complete(conn, s, cfg=None):
    """Apply a Stripe Checkout Session to its order. Safe to call more than once.
    Returns (order, newly_paid): newly_paid is True only the first time the payment is recorded."""
    cfg = cfg or store_config()
    meta = s.get("metadata") or {}
    o = get(conn, order_id=int(meta["order_id"])) if meta.get("order_id") else get(conn, session_id=s.get("id"))
    if not o:
        return None, False
    if o["status"] != "pending":
        return o, False
    paid = s.get("payment_status") in ("paid", "no_payment_required")
    if not paid:
        if s.get("status") == "complete":
            add_event(conn, o["id"], "Checkout finished, payment still processing")
        return get(conn, order_id=o["id"]), False

    name, addr = _shipping_details(s)
    cd = s.get("customer_details") or {}
    totals = s.get("total_details") or {}
    ship = (s.get("shipping_cost") or {}).get("amount_total")
    blocked = carb_problem(o["items"], addr.get("state"), cfg)
    status, reason = "paid", None
    if blocked:
        status = "on_hold"
        reason = (f"Ships to {addr.get('state')}, where these catalytic converters aren't legal: "
                  + ", ".join(f"{b['brand_name']} {b['part_number']}" for b in blocked) + ". Refund or contact the customer.")
    with conn.transaction():
        cur = conn.execute("""UPDATE orders SET status = %s, hold_reason = %s, payment_intent = %s, email = %s, name = %s,
                                  phone = %s, ship_address = %s, shipping = %s, tax = %s, total = %s, currency = %s,
                                  paid_at = now(), updated_at = now()
                              WHERE id = %s AND status = 'pending'""",
                           (status, reason, s.get("payment_intent"), cd.get("email"), name or cd.get("name"), cd.get("phone"),
                            Jsonb({**addr, "name": name}), _cents(ship) if ship is not None else None,
                            _cents(totals.get("amount_tax")), _cents(s.get("amount_total")), s.get("currency") or "usd", o["id"]))
        if cur.rowcount == 0:      # another request recorded it first
            return get(conn, order_id=o["id"]), False
        add_event(conn, o["id"], f"Paid {_cents(s.get('amount_total')):.2f} {(s.get('currency') or 'usd').upper()}"
                  + (f". ON HOLD: {reason}" if reason else ""))
    return get(conn, order_id=o["id"]), True


def set_status(conn, order_id, status, text, **fields):
    sets = ", ".join(f"{k} = %s" for k in fields)
    conn.execute(f"UPDATE orders SET status = %s{', ' + sets if sets else ''}, updated_at = now() WHERE id = %s",
                 (status, *fields.values(), order_id))
    add_event(conn, order_id, text)
