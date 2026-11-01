"""Product search.

A query goes through these steps:
  1. A vehicle typed into the query ("2019 silverado 1500 lift kit") is taken out and used as a fitment filter.
  2. Words that match a real part number ("69-3485", "693485") become a part-number match.
  3. Synonyms add alternatives ("catback" also searches "cat-back").
  4. Misspelled words are corrected to the closest word used in the catalog ("levling" -> "leveling").
  5. Every remaining word must match (prefix matching, so "level" finds "leveling"). If nothing matches
     all words, the search is relaxed to any word, and the response says so.
Results that fit the shopper's vehicle come first; parts with no vehicle data are left out when
filtering by vehicle. Postgres full-text search does the matching (no separate search service).
"""
import re
from dataclasses import dataclass, field

from psycopg import sql

from importer.catalog import SIZE_RE, normalize_size
from importer.storage import get_storage
from . import vehicles as veh

PRICE_BUCKETS = [(0, 100), (100, 300), (300, 750), (750, 1500), (1500, None)]
SORTS = {
    "relevance": None,  # built below (depends on whether a vehicle is selected)
    "price_asc": "m.price ASC, m.title",
    "price_desc": "m.price DESC, m.title",
    "name": "m.title",
}
_TOKEN_RE = re.compile(r'[^\w\s\-./]')


@dataclass
class ParsedQuery:
    raw: str
    vehicle: veh.VehicleMatch = None
    part_numbers: list = field(default_factory=list)   # normalized part numbers found in the query
    sizes: list = field(default_factory=list)          # normalized tire sizes found in the query
    terms: list = field(default_factory=list)          # [[alternative phrase words, ...], ...]
    corrections: dict = field(default_factory=dict)    # typo -> corrected word
    text_words: list = field(default_factory=list)     # the words actually searched

    def tsquery(self, mode="and"):
        groups = []
        for alts in self.required_terms():
            parts = [_phrase(words, strict=(mode == "strict")) for words in alts]
            parts = [p for p in parts if p]
            if parts:
                groups.append("(" + " | ".join(parts) + ")")
        return (" | " if mode == "or" else " & ").join(groups)

    def required_terms(self):
        req = [t for t in self.terms if not _is_size(t)]
        return req if req else self.terms

    def size_query(self):
        """Numbers like 4 or 2.5 as an any-of query for ranking, also matching '4in' style tokens."""
        if len(self.required_terms()) == len(self.terms):
            return ""
        atoms = []
        for t in self.terms:
            if _is_size(t):
                n = _clean(t[0][0])
                atoms += [n, n + "in"]
        return " | ".join(atoms)


def _is_size(alts):
    return len(alts) == 1 and len(alts[0]) == 1 and re.fullmatch(r"\d+(\.\d+)?", _clean(alts[0][0]) or "x") is not None


def _clean(word):
    return re.sub(r"[^a-z0-9.]", "", word.lower()).strip(".")


def _phrase(words, strict=False):
    """One phrase (list of words) as a tsquery fragment. Hyphenated words become phrases: cat-back -> cat <-> back.
    strict: only match the product name, part number and part type (weights A and B), not the marketing text."""
    wt = "AB" if strict else ""
    atoms = []
    for w in words:
        pieces = [_clean(p) for p in re.split(r"[-/]", w)]
        pieces = [p for p in pieces if p]
        if not pieces:
            continue
        if len(pieces) > 1:
            atoms.append("(" + " <-> ".join(p + (":" + wt if wt else "") for p in pieces) + ")")
        else:
            p = pieces[0]
            if len(p) >= 3 and len(words) == 1:
                atoms.append(p + ":*" + wt)
            else:
                atoms.append(p + (":" + wt if wt else ""))
    if not atoms:
        return ""
    return atoms[0] if len(atoms) == 1 else "(" + " <-> ".join(atoms) + ")"


class Searcher:
    def __init__(self, conn, cfg, vehicle_index=None):
        self.cfg = cfg
        self.vehicles = vehicle_index or veh.VehicleIndex.load(conn, cfg["make_aliases"], cfg["generic_model_words"])
        with conn.cursor() as cur:
            cur.execute("SELECT id, parent_id, name, sort FROM categories ORDER BY sort, name")
            self.categories = {i: {"id": i, "parent": p, "name": n, "sort": o} for i, p, n, o in cur.fetchall()}
            cur.execute("SELECT id, name FROM brands")
            self.brands = dict(cur.fetchall())
        self.storage = get_storage()

    # ------------------------------------------------------------------ parsing
    def parse(self, conn, q):
        q = (q or "").strip()
        tokens = [t for t in _TOKEN_RE.sub(" ", q.replace('"', " ")).split() if t]
        pq = ParsedQuery(raw=q)
        if not tokens:
            return pq
        pq.vehicle = self.vehicles.detect(tokens)
        rest = [t for i, t in enumerate(tokens) if not (pq.vehicle and i in pq.vehicle.consumed)]

        # tire sizes: "35x12.50r20", "LT285/70R17", "275/40ZR18"
        kept = []
        for t in rest:
            m = SIZE_RE.fullmatch(t.upper().removeprefix("LT").removeprefix("P").removesuffix("LT")) if t else None
            if m:
                pq.sizes.append(normalize_size(m.group(1)))
            else:
                kept.append(t)
        rest = kept

        # part numbers: only words that are real part numbers in the catalog
        candidates = {veh.norm(t) for t in rest if any(ch.isdigit() for ch in t) and len(veh.norm(t)) >= 4}
        for a in range(len(rest)):                             # "69 3485" typed with a space
            for b in range(a + 2, min(a + 4, len(rest)) + 1):
                joined = veh.norm("".join(rest[a:b]))
                if len(joined) >= 4 and any(ch.isdigit() for ch in joined):
                    candidates.add(joined)
        if candidates:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT pn_norm FROM products WHERE status = 'active' AND pn_norm = ANY(%s)", (list(candidates),))
                pq.part_numbers = [r[0] for r in cur.fetchall()]
        if pq.part_numbers:
            drop = set()
            for a in range(len(rest)):
                for b in range(a + 1, min(a + 4, len(rest)) + 1):
                    if veh.norm("".join(rest[a:b])) in pq.part_numbers:
                        drop.update(range(a, b))
            rest = [t for i, t in enumerate(rest) if i not in drop]

        words = [t.lower() for t in rest if t.lower() not in self.cfg["stop_words"]]
        syn = self.cfg["synonyms"]
        keys = sorted(syn, key=lambda k: -len(k.split()))
        i = 0
        while i < len(words):
            hit = None
            for k in keys:
                n = len(k.split())
                if " ".join(words[i:i + n]) == k:
                    hit = (k, n)
                    break
            if hit:
                k, n = hit
                pq.terms.append([k.split()] + [alt.split() for alt in syn[k]])
                pq.text_words.append(k)
                i += n
                continue
            w = words[i]
            fixed = self._correct(conn, w)
            if fixed != w:
                pq.corrections[w] = fixed
            pq.terms.append([[fixed]])
            pq.text_words.append(fixed)
            i += 1
        return pq

    def _correct(self, conn, word):
        if "-" in word or "/" in word:
            return word
        w = _clean(word)
        if len(w) < 4 or not w.isalpha():
            return word
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM search_lexicon WHERE word LIKE %s LIMIT 1", (w + "%",))
            if cur.fetchone():
                return word
            cur.execute("""SELECT word FROM search_lexicon WHERE word %% %s
                           ORDER BY similarity(word, %s) DESC, ndoc DESC LIMIT 1""", (w, w))
            r = cur.fetchone()
        return r[0] if r else word

    # ------------------------------------------------------------------ search
    def search(self, conn, q="", vehicle_id=None, category=None, brands=(), price_min=None, price_max=None,
               carb_only=False, fit_only=True, sort="relevance", page=1, per_page=24, facets=True):
        pq = self.parse(conn, q)
        vehicle = self._vehicle(conn, pq, vehicle_id)
        page, per_page = max(1, int(page)), min(max(1, int(per_page)), 96)
        result = {"query": q, "corrections": pq.corrections, "searched_words": pq.text_words,
                  "vehicle": vehicle["info"], "part_number_match": bool(pq.part_numbers), "relaxed": False}

        with conn.transaction():
            filters = {
                "category": self._category_ids(category),
                "brand": [b for b in brands if b] or None,
                "price": (price_min, price_max) if price_min is not None or price_max is not None else None,
                "carb": bool(carb_only),
                "fit": bool(fit_only and vehicle["ids_sql"] is not None),
            }
            where, params = self._where(filters)
            passes = ["strict", "and"] if pq.terms else ["and"]   # (sizes and part numbers always apply)
            if len(pq.required_terms()) > 1 and not pq.part_numbers:
                passes.append("or")
            with conn.cursor() as cur:
                for mode in passes:
                    self._build_matches(conn, pq, vehicle, mode)
                    cur.execute(sql.SQL("SELECT count(*) FROM _m m WHERE ") + where, params)
                    result["total"] = cur.fetchone()[0]
                    if result["total"]:
                        result["relaxed"] = mode == "or"
                        break
                order = SORTS.get(sort) or self._relevance_order(vehicle)
                cur.execute(sql.SQL("""
                    SELECT m.id, p.slug, p.part_number, p.brand_id, p.title, p.short_name, p.price, p.category_id,
                           p.main_image, p.fitment_status, p.emissions, p.carb_eo, m.fit
                    FROM _m m JOIN products p ON p.id = m.id WHERE """) + where +
                            sql.SQL(" ORDER BY " + order + " LIMIT %(limit)s OFFSET %(offset)s"),
                            {**params, "limit": per_page, "offset": (page - 1) * per_page})
                cols = [c.name for c in cur.description]
                items = [dict(zip(cols, r)) for r in cur.fetchall()]
            if facets:
                result["facets"] = self._facets(conn, filters, vehicle)
        self._decorate(conn, items, vehicle)
        result.update(items=items, page=page, per_page=per_page,
                      pages=(result["total"] + per_page - 1) // per_page if result["total"] else 0)
        return result

    def _vehicle(self, conn, pq, vehicle_id):
        """Vehicle filter: from the query if one was typed, else the shopper's selected vehicle."""
        m = pq.vehicle
        if m and (m.models or m.make):
            conds, params = [], {}
            if m.models:
                conds.append("(v.make || '|' || v.model) = ANY(%(vm)s)")
                params["vm"] = [f"{a}|{b}" for a, b in m.models]
            else:
                conds.append("v.make = %(vmake)s")
                params["vmake"] = m.make
            if m.year:
                conds.append("v.year = %(vyear)s")
                params["vyear"] = m.year
            exact_id = veh.find_id(conn, *m.exact) if m.exact else None
            return {"ids_sql": sql.SQL("SELECT v.id FROM vehicles v WHERE " + " AND ".join(conds)), "params": params,
                    "info": {"source": "query", "label": m.label, "id": exact_id, "exact": bool(exact_id),
                             "found": bool(m.models) or not m.year}}
        if vehicle_id:
            v = veh.get(conn, vehicle_id)
            if v:
                return {"ids_sql": sql.SQL("SELECT %(vid)s::int"), "params": {"vid": v["id"]},
                        "info": {"source": "garage", "label": v["label"], "id": v["id"], "exact": True, "found": True}}
        return {"ids_sql": None, "params": {}, "info": None}

    def _build_matches(self, conn, pq, vehicle, mode):
        tsq = pq.tsquery(mode)
        params = dict(vehicle["params"])
        conds = ["p.status = 'active'"]
        if pq.sizes:
            conds.append("p.size_norm = ANY(%(sizes)s)")
            params["sizes"] = pq.sizes
        if pq.part_numbers and tsq:
            conds.append("p.pn_norm = ANY(%(pn)s) AND p.search @@ to_tsquery('english', %(tsq)s)")
        elif pq.part_numbers:
            conds.append("p.pn_norm = ANY(%(pn)s)")
        elif tsq:
            conds.append("p.search @@ to_tsquery('english', %(tsq)s)")
        params.update(pn=pq.part_numbers, tsq=tsq or "")
        rank = ("ts_rank_cd(p.search, to_tsquery('english', %(tsq)s), 32)" if tsq else "0") + \
               " + CASE WHEN p.pn_norm = ANY(%(pn)s) THEN 10 ELSE 0 END" + \
               " + CASE WHEN p.category_id = ANY(%(boost_cats)s) THEN 1 ELSE 0 END" + \
               (" + 2 * ts_rank_cd(p.search, to_tsquery('english', %(sizeq)s), 32)" if pq.size_query() else "")
        params["sizeq"] = pq.size_query()
        params["boost_cats"] = self._named_categories(conn, pq)
        if vehicle["ids_sql"] is not None:
            fit_cte = sql.SQL("WITH vids AS (") + vehicle["ids_sql"] + sql.SQL("""),
                fitset AS (SELECT DISTINCT product_id FROM fitment WHERE vehicle_id IN (SELECT * FROM vids)) """)
            fit_expr = """CASE WHEN fs.product_id IS NOT NULL THEN 'fits' WHEN p.fitment_status = 'universal' THEN 'universal'
                               WHEN p.fitment_status = 'by_size' THEN 'size'
                               WHEN p.fitment_status = 'missing' THEN 'unknown' ELSE 'no' END"""
            join = "LEFT JOIN fitset fs ON fs.product_id = p.id"
        else:
            fit_cte, fit_expr, join = sql.SQL(""), "NULL::text", ""
        q = sql.SQL("CREATE TEMP TABLE _m ON COMMIT DROP AS ") + fit_cte + sql.SQL(
            f"""SELECT p.id, p.category_id, p.brand_id, p.price, p.emissions, p.title, p.main_image IS NOT NULL AS has_image,
                       {rank} AS rank, {fit_expr} AS fit
                FROM products p {join} WHERE {" AND ".join(conds)}""")
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS _m")
            cur.execute(q, params)
            return cur.rowcount

    def _named_categories(self, conn, pq):
        """Categories whose whole name appears in the query, e.g. "exhaust tip 4 inch" names Exhaust Tips."""
        text = " ".join(" ".join(w) for alts in pq.terms for w in alts)
        if not text:
            return []
        with conn.cursor() as cur:
            cur.execute("""SELECT id FROM categories WHERE parent_id IS NOT NULL
                           AND to_tsvector('english', %s) @@ plainto_tsquery('english', name)""", (text,))
            return [r[0] for r in cur.fetchall()]

    @staticmethod
    def _relevance_order(vehicle):
        fit = "CASE m.fit WHEN 'fits' THEN 0 WHEN 'universal' THEN 1 WHEN 'size' THEN 2 ELSE 3 END, " if vehicle["ids_sql"] is not None else ""
        return fit + "m.rank DESC, m.has_image DESC, m.title"

    def _category_ids(self, category):
        if not category or category not in self.categories:
            return None
        return [category] + [c for c, v in self.categories.items() if v["parent"] == category]

    @staticmethod
    def _where(f, skip=None):
        parts, params = [sql.SQL("TRUE")], {}
        if f["category"] and skip != "category":
            parts.append(sql.SQL("m.category_id = ANY(%(f_cat)s)")); params["f_cat"] = f["category"]
        if f["brand"] and skip != "brand":
            parts.append(sql.SQL("m.brand_id = ANY(%(f_brand)s)")); params["f_brand"] = list(f["brand"])
        if f["price"] and skip != "price":
            lo, hi = f["price"]
            if lo is not None:
                parts.append(sql.SQL("m.price >= %(f_lo)s")); params["f_lo"] = lo
            if hi is not None:
                parts.append(sql.SQL("m.price < %(f_hi)s")); params["f_hi"] = hi
        if f["carb"] and skip != "carb":
            parts.append(sql.SQL("(m.emissions IS NULL OR m.emissions = 'carb_legal')"))
        if f["fit"] and skip != "fit":
            parts.append(sql.SQL("m.fit IN ('fits', 'universal', 'size')"))
        return sql.SQL(" AND ").join(parts), params

    def _facets(self, conn, f, vehicle):
        out = {}
        with conn.cursor() as cur:
            where, p = self._where(f, "category")
            cur.execute(sql.SQL("SELECT category_id, count(*) FROM _m m WHERE ") + where + sql.SQL(" GROUP BY 1"), p)
            subs = dict(cur.fetchall())
            parents = {}
            for cid, n in subs.items():
                parent = self.categories.get(cid, {}).get("parent") or cid
                parents[parent] = parents.get(parent, 0) + n
            out["categories"] = sorted(
                [{"id": pid, "name": self.categories[pid]["name"], "count": n,
                  "subs": sorted([{"id": c, "name": self.categories[c]["name"], "count": subs[c]}
                                  for c in subs if self.categories.get(c, {}).get("parent") == pid], key=lambda x: -x["count"])}
                 for pid, n in parents.items() if pid in self.categories], key=lambda x: -x["count"])

            where, p = self._where(f, "brand")
            cur.execute(sql.SQL("SELECT brand_id, count(*) FROM _m m WHERE ") + where + sql.SQL(" GROUP BY 1 ORDER BY 2 DESC"), p)
            out["brands"] = [{"id": b, "name": self.brands.get(b, b), "count": n} for b, n in cur.fetchall()]

            where, p = self._where(f, "price")
            cases = " ".join(f"WHEN m.price >= {lo}" + (f" AND m.price < {hi}" if hi else "") + f" THEN {i}"
                             for i, (lo, hi) in enumerate(PRICE_BUCKETS))
            cur.execute(sql.SQL(f"SELECT CASE {cases} END, count(*) FROM _m m WHERE ") + where + sql.SQL(" GROUP BY 1"), p)
            counts = dict(cur.fetchall())
            out["prices"] = [{"min": lo, "max": hi, "count": counts.get(i, 0)} for i, (lo, hi) in enumerate(PRICE_BUCKETS)]

            where, p = self._where(f, "carb")
            cur.execute(sql.SQL("""SELECT count(*) FILTER (WHERE m.emissions IS NOT NULL),
                                          count(*) FILTER (WHERE m.emissions = 'carb_legal') FROM _m m WHERE """) + where, p)
            em, carb = cur.fetchone()
            out["emissions"] = {"emissions_parts": em, "carb_legal": carb}

            if vehicle["ids_sql"] is not None:
                where, p = self._where(f, "fit")
                cur.execute(sql.SQL("SELECT m.fit, count(*) FROM _m m WHERE ") + where + sql.SQL(" GROUP BY 1"), p)
                out["fit"] = dict(cur.fetchall())
        return out

    def _decorate(self, conn, items, vehicle):
        """Add brand/category names, photo URL, and how each part fits the selected vehicle."""
        for it in items:
            it["brand_name"] = self.brands.get(it["brand_id"], it["brand_id"])
            cat = self.categories.get(it["category_id"]) or {}
            it["category_name"] = cat.get("name")
            it["image_url"] = self.storage.url(it.pop("main_image")) if it.get("main_image") else None
            it["price"] = float(it["price"]) if it["price"] is not None else None
        fits = [it["id"] for it in items if it.get("fit") == "fits"]
        if fits and vehicle["ids_sql"] is not None:
            q = sql.SQL("""SELECT f.product_id,
                                  array_remove(array_agg(DISTINCT f.engine), NULL),
                                  array_remove(array_agg(DISTINCT f.submodel), NULL),
                                  array_remove(array_agg(DISTINCT f.notes), NULL)
                           FROM fitment f WHERE f.product_id = ANY(%(ids)s) AND f.vehicle_id IN (""") + vehicle["ids_sql"] + \
                sql.SQL(") GROUP BY 1")
            with conn.cursor() as cur:
                cur.execute(q, {**vehicle["params"], "ids": fits})
                detail = {r[0]: r[1:] for r in cur.fetchall()}
            for it in items:
                if it["id"] in detail:
                    eng, sub, notes = detail[it["id"]]
                    it["fit_detail"] = {"engines": sorted(eng), "submodels": sorted({s for x in sub for s in x.split(", ")}),
                                        "notes": sorted(notes)[:3]}

    # ------------------------------------------------------------------ autocomplete
    def suggest(self, conn, q, vehicle_id=None, limit=6):
        q = (q or "").strip()
        out = {"query": q, "vehicle": None, "categories": [], "brands": [], "part_numbers": [], "products": []}
        if len(q) < 2:
            return out
        pq = self.parse(conn, q)
        if pq.vehicle and (pq.vehicle.models or pq.vehicle.make):
            out["vehicle"] = {"label": pq.vehicle.label,
                              "id": veh.find_id(conn, *pq.vehicle.exact) if pq.vehicle.exact else None}
        text = " ".join(pq.text_words)
        with conn.cursor() as cur:
            if text:
                cur.execute("""SELECT c.id, c.name, c.parent_id FROM categories c
                               WHERE (lower(c.name) LIKE %(like)s OR similarity(lower(c.name), %(t)s) > 0.35)
                                 AND EXISTS (SELECT 1 FROM products p JOIN categories s ON s.id = p.category_id
                                             WHERE p.status = 'active' AND (s.id = c.id OR s.parent_id = c.id))
                               ORDER BY (lower(c.name) LIKE %(start)s) DESC, similarity(lower(c.name), %(t)s) DESC LIMIT 5""",
                            {"like": f"%{text}%", "start": f"{text}%", "t": text})
                out["categories"] = [{"id": i, "name": n, "parent": self.categories.get(p, {}).get("name") if p else None}
                                     for i, n, p in cur.fetchall()]
                cur.execute("""SELECT id, name FROM brands b WHERE (lower(name) LIKE %s OR similarity(lower(name), %s) > 0.4)
                               AND EXISTS (SELECT 1 FROM products p WHERE p.brand_id = b.id AND p.status = 'active') LIMIT 3""",
                            (f"%{text}%", text))
                out["brands"] = [{"id": i, "name": n} for i, n in cur.fetchall()]
            qn = veh.norm(q)
            if any(ch.isdigit() for ch in qn) and len(qn) >= 3:
                cur.execute("""SELECT p.slug, p.part_number, p.brand_id, p.short_name FROM products p
                               WHERE p.status = 'active' AND p.pn_norm LIKE %s ORDER BY length(p.pn_norm), p.pn_norm LIMIT 5""",
                            (qn + "%",))
                out["part_numbers"] = [{"slug": s, "part_number": n, "brand": self.brands.get(b, b), "name": nm}
                                       for s, n, b, nm in cur.fetchall()]
        if pq.text_words or pq.part_numbers or out["vehicle"]:
            res = self.search(conn, q, vehicle_id=vehicle_id, per_page=limit, facets=False)
            out["products"] = [{k: it.get(k) for k in ("slug", "title", "short_name", "brand_name", "part_number", "price",
                                                       "image_url", "fit")} for it in res["items"]]
            out["corrections"] = res["corrections"]
        return out
