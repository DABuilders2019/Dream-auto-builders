"""Turn 14 sync tasks. Run with:  python -m turn14 <task>

  test        sign in, read brands and warehouses (a quick connection check)
  setup       first full load: brands, warehouses, then for each of our brands that Turn 14 has approved
              for this account: all items, matching to our parts, pricing, stock
  daily       brands, items changed since the last daily run, matching, pricing, full stock count
              (Turn 14: pricing once a day after 1:00 AM Eastern)
  changes     warehouses, plus stock that changed since the last run (Turn 14's inventory updates feed)
  full-items  all items for each brand again (Turn 14: 1-2 times a month); items no longer listed are
              marked inactive
  catalog:<id>  add a brand Turn 14 approves but SEMA doesn't, from Turn 14's own item data (turn14/catalog.py)

Brands added from Turn 14 are rebuilt by daily (from the local copy) and full-items (descriptions and photos again).

Only brands we sell (matched by brand code) and that Turn 14 approves for this account are loaded.
"""
import collections
import datetime as dt
import json
import re
import time

from psycopg.types.json import Jsonb

from .client import Client, StopSync, utcnow

ALLOWED_MINUTES = (15, 30, 60, 240, 480, 1440)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def upc(s):
    d = re.sub(r"\D", "", s or "")
    return d.lstrip("0") or None


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _state(conn, key, default=None):
    r = conn.execute("SELECT value FROM t14_state WHERE key = %s", (key,)).fetchone()
    return r[0] if r else default


def _set_state(conn, key, value):
    conn.execute("""INSERT INTO t14_state (key, value, updated_at) VALUES (%s, %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""", (key, Jsonb(value)))


class Sync:
    def __init__(self, conn, client=None, log=print, task="manual", log_conn=None):
        self.conn = conn
        self.log = log
        # Requests are recorded on their own connection so the count survives a rolled-back load.
        self.c = client or Client(log_conn or conn, task=task, log=log)
        self.stats = collections.Counter()

    # ------------------------------------------------------------------ brands and warehouses
    def brands(self):
        body = self.c.get("/v1/brands")
        rows = body.get("data") or []
        ours = self.conn.execute("SELECT id, name, aaia_id FROM brands").fetchall()
        by_aaia = {a.upper(): i for i, n, a in ours if a}
        by_name = {norm(n): i for i, n, a in ours}
        seen = []
        for b in rows:
            a = b.get("attributes") or {}
            codes = [str(x).upper() for x in (a.get("AAIA") or a.get("aaia") or []) if x]
            approved, restricted = False, False
            for pg in a.get("pricegroups") or []:
                rules = pg.get("purchase_restrictions") or []
                if not rules:
                    approved = True
                for rule in rules:
                    if str(rule.get("your_status", "")).lower() == "approved":
                        approved = True
                    else:
                        restricted = True
            if not (a.get("pricegroups") or []):
                approved = True
            ours_id = next((by_aaia[c] for c in codes if c in by_aaia), None) or by_name.get(norm(a.get("name")))
            self.conn.execute("""
                INSERT INTO t14_brands (id, name, aaia, approved, restricted, dropship, brand_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, aaia = EXCLUDED.aaia, approved = EXCLUDED.approved,
                    restricted = EXCLUDED.restricted, dropship = EXCLUDED.dropship, brand_id = EXCLUDED.brand_id, updated_at = now()""",
                (int(b["id"]), a.get("name") or "", codes, approved, restricted, a.get("dropship"), ours_id))
            seen.append(int(b["id"]))
        self.stats["turn14_brands"] = len(rows)
        mine = self.my_brands()
        self.stats["our_brands_at_turn14"] = len(mine)
        self.log(f"  Brands: {len(rows)} at Turn 14; {len(mine)} of ours are there and approved for this account")
        return mine

    def my_brands(self):
        """[(turn14 brand id, our brand id, name)] for our brands that Turn 14 approves for this account."""
        return self.conn.execute("""SELECT t.id, t.brand_id, b.name FROM t14_brands t JOIN brands b ON b.id = t.brand_id
                                    WHERE t.approved ORDER BY b.name""").fetchall()

    def locations(self):
        body = self.c.get("/v1/locations")
        for loc in body.get("data") or []:
            a = loc.get("attributes") or {}
            self.conn.execute("""INSERT INTO t14_locations (id, name, city, state, updated_at) VALUES (%s, %s, %s, %s, now())
                                 ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, city = EXCLUDED.city,
                                 state = EXCLUDED.state, updated_at = now()""",
                              (str(loc["id"]), a.get("Name") or a.get("name"), a.get("City") or a.get("city"),
                               a.get("State") or a.get("state")))

    # ------------------------------------------------------------------ items
    def _save_item(self, rec, t0):
        a = rec.get("attributes") or {}
        self.conn.execute("""
            INSERT INTO t14_items (item_id, t14_brand_id, part_number, mfr_part_number, mfr_norm, barcode, alternate_part_number,
                product_name, active, regular_stock, not_carb_approved, carb_eo_number, ltl_freight_required,
                air_freight_prohibited, clearance_item, prop_65, epa, part_description, category, subcategory, dimensions,
                thumbnail, units_per_sku, seen_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (item_id) DO UPDATE SET t14_brand_id = EXCLUDED.t14_brand_id, part_number = EXCLUDED.part_number,
                mfr_part_number = EXCLUDED.mfr_part_number, mfr_norm = EXCLUDED.mfr_norm, barcode = EXCLUDED.barcode,
                alternate_part_number = EXCLUDED.alternate_part_number, product_name = EXCLUDED.product_name,
                active = EXCLUDED.active, regular_stock = EXCLUDED.regular_stock, not_carb_approved = EXCLUDED.not_carb_approved,
                carb_eo_number = EXCLUDED.carb_eo_number, ltl_freight_required = EXCLUDED.ltl_freight_required,
                air_freight_prohibited = EXCLUDED.air_freight_prohibited, clearance_item = EXCLUDED.clearance_item,
                prop_65 = EXCLUDED.prop_65, epa = EXCLUDED.epa, part_description = EXCLUDED.part_description,
                category = EXCLUDED.category, subcategory = EXCLUDED.subcategory, dimensions = EXCLUDED.dimensions,
                thumbnail = EXCLUDED.thumbnail, units_per_sku = EXCLUDED.units_per_sku,
                seen_at = EXCLUDED.seen_at, updated_at = now()""",
            (str(rec["id"]), int(a.get("brand_id") or 0), a.get("part_number"), a.get("mfr_part_number"),
             norm(a.get("mfr_part_number")), a.get("barcode"), a.get("alternate_part_number"), a.get("product_name"),
             a.get("active", True) is not False, a.get("regular_stock"), a.get("not_carb_approved"), a.get("carb_eo_number"),
             a.get("ltl_freight_required"), a.get("air_freight_prohibited"), a.get("clearance_item"), a.get("prop_65"),
             a.get("epa"), a.get("part_description"), a.get("category"), a.get("subcategory"),
             Jsonb(a["dimensions"]) if isinstance(a.get("dimensions"), list) else None, a.get("thumbnail"),
             _int(a.get("units_per_sku")), t0))

    def items_for_brand(self, t14_id, name):
        t0 = utcnow()
        n = 0
        with self.conn.transaction():
            for rec in self.c.pages(f"/v1/items/brand/{t14_id}"):
                self._save_item(rec, t0)
                n += 1
            # A full list: anything for this brand that wasn't in it is no longer carried.
            gone = self.conn.execute("""UPDATE t14_items SET active = false, updated_at = now()
                                        WHERE t14_brand_id = %s AND seen_at < %s AND active""", (t14_id, t0)).rowcount
        self.stats["items"] += n
        self.stats["items_no_longer_listed"] += gone
        self.log(f"  {name}: {n} items" + (f", {gone} no longer listed" if gone else ""))

    def item_updates(self, days):
        mine = {t for t, _, _ in self.my_brands()}
        t0, n = utcnow(), 0
        with self.conn.transaction():
            for rec in self.c.pages("/v1/items/updates", {"days": days}):
                if int((rec.get("attributes") or {}).get("brand_id") or 0) in mine:
                    self._save_item(rec, t0)
                    n += 1
        self.stats["items_changed"] += n
        self.log(f"  Items added or changed in the last {days} day(s) for our brands: {n}")

    # ------------------------------------------------------------------ matching to our parts
    def match(self, t14_id, our_brand):
        """Map Turn 14 item ids to our products: manufacturer part number, then UPC, then alternate part number.
        One Turn 14 item per product; a "G" (group/kit) item wins over the plain one, as Turn 14 recommends."""
        prods = self.conn.execute("SELECT id, pn_norm, upc FROM products WHERE brand_id = %s AND status <> 'discontinued'",
                                  (our_brand,)).fetchall()
        by_pn = {pn: pid for pid, pn, _ in prods if pn}
        by_upc = {upc(u): pid for pid, _, u in prods if upc(u)}
        items = self.conn.execute("""SELECT item_id, mfr_norm, barcode, alternate_part_number, active, regular_stock
                                     FROM t14_items WHERE t14_brand_id = %s""", (t14_id,)).fetchall()
        best = {}
        for item_id, mfr, bc, alt, active, regular in items:
            pid, how = None, None
            if mfr and mfr in by_pn:
                pid, how = by_pn[mfr], "part_number"
            elif upc(bc) and upc(bc) in by_upc:
                pid, how = by_upc[upc(bc)], "barcode"
            elif norm(alt) and norm(alt) in by_pn:
                pid, how = by_pn[norm(alt)], "alternate"
            if pid is None:
                continue
            rank = (bool(active), item_id.upper().endswith("G"), regular is not False)
            if pid not in best or rank > best[pid][0]:
                best[pid] = (rank, item_id, how)
        with self.conn.transaction():
            self.conn.execute("UPDATE t14_items SET product_id = NULL, match_method = NULL WHERE t14_brand_id = %s", (t14_id,))
            with self.conn.cursor() as cur:
                cur.executemany("UPDATE t14_items SET product_id = %s, match_method = %s WHERE item_id = %s",
                                [(pid, how, item_id) for pid, (_, item_id, how) in best.items()])
        self.stats["parts_matched"] += len(best)
        self.stats["our_parts"] += len(prods)
        self.log(f"  Matched {len(best)} of our {len(prods)} parts to Turn 14 items")

    # ------------------------------------------------------------------ pricing
    def pricing_for_brand(self, t14_id, name):
        n = 0
        with self.conn.transaction():
            for rec in self.c.pages(f"/v1/pricing/brand/{t14_id}"):
                a = rec.get("attributes") or {}
                lists = {str(p.get("name", "")).strip().lower(): p.get("price") for p in a.get("pricelists") or []}
                self.conn.execute("""
                    INSERT INTO t14_pricing (item_id, purchase_cost, has_map, can_purchase, map_price, retail_price, jobber_price,
                                             pricelists, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (item_id) DO UPDATE SET purchase_cost = EXCLUDED.purchase_cost, has_map = EXCLUDED.has_map,
                        can_purchase = EXCLUDED.can_purchase, map_price = EXCLUDED.map_price, retail_price = EXCLUDED.retail_price,
                        jobber_price = EXCLUDED.jobber_price, pricelists = EXCLUDED.pricelists, updated_at = now()""",
                    (str(rec["id"]), a.get("purchase_cost"), a.get("has_map"), a.get("can_purchase"), lists.get("map"),
                     lists.get("retail") or lists.get("suggested retail"), lists.get("jobber"), Jsonb(a.get("pricelists") or [])))
                n += 1
        self.stats["prices"] += n
        self.log(f"  {name}: pricing for {n} items")

    # ------------------------------------------------------------------ stock
    def _save_stock(self, rec):
        a = rec.get("attributes") or {}
        inv = {str(k): int(v or 0) for k, v in (a.get("inventory") or {}).items()}
        mfr = a.get("manufacturer") or {}
        esd = mfr.get("esd")
        try:
            esd = dt.date.fromisoformat(esd) if esd else None
        except ValueError:
            esd = None
        self.conn.execute("""
            INSERT INTO t14_inventory (item_id, by_location, total, mfr_stock, mfr_esd, eta, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (item_id) DO UPDATE SET by_location = EXCLUDED.by_location, total = EXCLUDED.total,
                mfr_stock = EXCLUDED.mfr_stock, mfr_esd = EXCLUDED.mfr_esd, eta = EXCLUDED.eta, updated_at = now()""",
            (str(rec["id"]), Jsonb(inv), sum(inv.values()), mfr.get("stock"), esd, Jsonb(a.get("eta")) if a.get("eta") else None))

    def inventory_for_brand(self, t14_id, name):
        n = 0
        with self.conn.transaction():
            for rec in self.c.pages(f"/v1/inventory/brand/{t14_id}"):
                self._save_stock(rec)
                n += 1
        self.stats["stock_records"] += n
        self.log(f"  {name}: stock for {n} items")

    def inventory_changes(self, minutes):
        known = {r[0] for r in self.conn.execute("SELECT item_id FROM t14_items WHERE product_id IS NOT NULL").fetchall()}
        n = 0
        with self.conn.transaction():
            for rec in self.c.pages("/v1/inventory/updates", {"minutes": minutes}):
                if str(rec.get("id")) in known:
                    self._save_stock(rec)
                    n += 1
        self.stats["stock_changes"] += n
        self.log(f"  Stock changes in the last {minutes} minutes for our parts: {n}")


# ------------------------------------------------------------------ tasks

def run(conn, task, log=print, client=None, log_conn=None):
    s = Sync(conn, client=client, log=log, task=task, log_conn=log_conn)
    conn.execute("DELETE FROM t14_requests WHERE at < now() - interval '3 days'")
    started = time.time()
    last = _state(conn, f"last_{task}", {}) or {}
    record = {"started": utcnow().isoformat(timespec="seconds"), "env": s.c.env}
    try:
        if task == "test":
            s.brands()
            s.locations()
        elif task.startswith("catalog:"):
            from . import catalog
            s.brands()
            try:
                catalog.load(s, int(task.split(":", 1)[1]))
            except catalog.CatalogError as e:
                raise StopSync(str(e)) from None
        elif task in ("setup", "full-items"):
            s.locations()
            for t14_id, ours, name in s.brands():
                s.items_for_brand(t14_id, name)
                s.match(t14_id, ours)
                if task == "setup":
                    s.pricing_for_brand(t14_id, name)
                    s.inventory_for_brand(t14_id, name)
            from . import catalog
            catalog.refresh_all(s, fetch_data_too=True)
        elif task == "daily":
            prev = _state(conn, "last_daily", {}) or {}
            days = 1
            if prev.get("finished"):
                age = utcnow() - dt.datetime.fromisoformat(prev["finished"])
                days = max(1, min(15, int(age.total_seconds() // 86400) + 1))
            mine = s.brands()
            s.item_updates(days)
            for t14_id, ours, name in mine:
                s.match(t14_id, ours)
                s.pricing_for_brand(t14_id, name)
                s.inventory_for_brand(t14_id, name)
            from . import catalog
            catalog.refresh_all(s, fetch_data_too=False)
        elif task == "changes":
            s.locations()
            minutes = 60
            if last.get("finished"):
                age = (utcnow() - dt.datetime.fromisoformat(last["finished"])).total_seconds() / 60 + 5
                minutes = next((m for m in ALLOWED_MINUTES if m >= age), 1440)
            s.inventory_changes(minutes)
        else:
            raise SystemExit(f"unknown task {task!r}")
        record.update(status="ok")
        return dict(s.stats)
    except StopSync as e:
        record.update(status="stopped", error=str(e))
        raise
    except Exception as e:
        record.update(status="failed", error=repr(e)[:300])
        raise
    finally:
        record.update(finished=utcnow().isoformat(timespec="seconds"), seconds=round(time.time() - started),
                      requests=s.c.requests, stats=dict(s.stats))
        _set_state(conn, f"last_{task}", record)
        s.c.close()
