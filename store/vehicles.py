"""Vehicles: the Year / Make / Model menus, and spotting a vehicle typed into the search bar.

Spotting works on whole words, punctuation ignored, so "f150", "F-150" and "f 150" are the same.
A model can be matched by its leading words: "silverado" means every Silverado model,
"silverado 1500" means Silverado 1500 plus its Classic / HD / LD variants, and a year narrows
that to the models sold that year. Model names that are only numbers ("1500" for Ram) need the
make next to them, and single words that are common in product names (e.g. "street") never
count as a model on their own.
"""
import re
from dataclasses import dataclass, field

YEAR_RE = re.compile(r"^(19[5-9]\d|20[0-4]\d)$")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def words(s):
    return [w for w in (norm(x) for x in re.split(r"[\s/]+", s)) if w]


@dataclass
class VehicleMatch:
    year: int = None
    make: str = None
    models: list = field(default_factory=list)     # [(make, model), ...]
    consumed: set = field(default_factory=set)      # indices of query tokens used up
    exact: tuple = None                              # (year, make, model) when it's one specific vehicle
    family_name: str = None                          # e.g. "Silverado" when every Silverado version matched

    @property
    def label(self):
        if self.exact:
            return f"{self.exact[0]} {self.exact[1]} {self.exact[2]}"
        y = f"{self.year} " if self.year else ""
        if not self.models:
            return f"{y}{self.make}".strip()
        makes = sorted({m for m, _ in self.models})
        models = sorted({md for _, md in self.models})
        if makes == ["Dodge", "Ram"]:
            return f"{y}Dodge {self.family_name} (all versions)"
        if len(models) == 1:
            return f"{y}{makes[0]} {models[0]}"
        return f"{y}{' / '.join(makes)} {self.family_name} (all versions)"


class VehicleIndex:
    """In-memory list of vehicles that have parts, rebuilt after imports."""

    def __init__(self, rows, make_aliases=None, common_words=()):
        # rows: (make, model, [years])
        self.years = {}
        for make, model, years in rows:
            self.years[(make, model)] = set(years)
        self.make_words = {}                         # normalized make -> make
        for make, _ in self.years:
            self.make_words[norm(make)] = make
        for alias, make in (make_aliases or {}).items():
            if any(m == make for m in self.make_words.values()):
                self.make_words[norm(alias)] = make
        self.model_words = {(mk, md): words(md) for mk, md in self.years}
        self.common = {norm(w) for w in common_words}

    @classmethod
    def load(cls, conn, make_aliases=None, generic_words=()):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT v.make, v.model, array_agg(DISTINCT v.year)
                FROM vehicles v
                WHERE EXISTS (SELECT 1 FROM fitment f JOIN products p ON p.id = f.product_id
                              WHERE f.vehicle_id = v.id AND p.status = 'active')
                GROUP BY 1, 2""")
            rows = cur.fetchall()
            # Words used for kinds of parts or product lines ("street", "touring", "baja"...) can't be a model on their own.
            cur.execute("""SELECT DISTINCT w FROM (
                             SELECT regexp_split_to_table(lower(part_type || ' ' || coalesce(attributes->>'Series', '') || ' ' || coalesce(attributes->>'Brand Line', '')), '[^a-z0-9]+') AS w
                             FROM products WHERE status = 'active'
                             UNION SELECT regexp_split_to_table(lower(name), '[^a-z0-9]+') FROM categories) x WHERE w <> ''""")
            common = [r[0] for r in cur.fetchall()]
        return cls(rows, make_aliases, list(common) + list(generic_words))

    # ------------------------------------------------------------------ spotting in a query
    def detect(self, tokens):
        """tokens: the raw query split on spaces. Returns a VehicleMatch or None."""
        nt = [norm(t) for t in tokens]
        # "f 150" -> "f150"
        merged, idx = [], []
        i = 0
        while i < len(nt):
            if len(nt[i]) == 1 and nt[i].isalpha() and i + 1 < len(nt) and nt[i + 1].isdigit():
                merged.append(nt[i] + nt[i + 1]); idx.append({i, i + 1}); i += 2
            else:
                merged.append(nt[i]); idx.append({i}); i += 1

        year, year_pos = None, None
        for j, w in enumerate(merged):
            if YEAR_RE.match(w):
                year, year_pos = int(w), j
                break

        makes = []                                    # (make, positions)
        for j, w in enumerate(merged):
            if w in self.make_words:
                makes.append((self.make_words[w], {j}))
            if j + 1 < len(merged) and merged[j] + merged[j + 1] in self.make_words:
                makes.append((self.make_words[merged[j] + merged[j + 1]], {j, j + 1}))

        best = None                                   # (score, make_or_None, matched_words, positions)
        for make, mpos in makes + [(None, set())]:
            for (mk, md), mw in self.model_words.items():
                if make and mk != make:
                    continue
                k, start = self._longest_prefix(mw, merged)
                if not k:
                    continue
                matched = mw[:k]
                if all(w.isdigit() for w in matched) and not make:
                    continue                          # "1500" alone is ambiguous
                if k == 1 and not make and (matched[0] in self.common or len(matched[0]) < 3):
                    continue
                pos = set(range(start, start + k))
                if pos & mpos:
                    continue
                score = k + (1.5 if make else 0) + (0.25 if k == len(mw) else 0)
                if best is None or score > best[0]:
                    best = (score, make, tuple(matched), pos | mpos)

        if best is None:
            if not makes:
                return None
            make, mpos = makes[0]
            consumed = set().union(*(idx[j] for j in mpos | ({year_pos} if year is not None else set())))
            return VehicleMatch(year=year, make=make, consumed=consumed)

        _, make, matched, positions = best
        family = [(mk, md) for (mk, md), mw in self.model_words.items()
                  if (make is None or mk == make) and tuple(mw[:len(matched)]) == matched]
        # Dodge sold the Ram pickups until 2010; from 2011 they're make "Ram". "dodge ram 1500" means both.
        if make == "Dodge" and matched and matched[0] == "ram" and "Ram" in self.make_words.values():
            rest = matched[1:]
            family += [(mk, md) for (mk, md), mw in self.model_words.items()
                       if mk == "Ram" and rest and tuple(mw[:len(rest)]) == rest]
        if year is not None:
            family = [m for m in family if year in self.years[m]]
        family = sorted(set(family))
        if year_pos is not None:
            positions = positions | {year_pos}
        consumed = set().union(*(idx[j] for j in positions))
        targets = {matched}
        if make == "Dodge" and matched and matched[0] == "ram":
            targets.add(matched[1:])
        exact = None
        if year is not None and family:
            full = [m for m in family if tuple(self.model_words[m]) in targets]
            if len(full) == 1:
                exact = (year,) + full[0]
            elif len(family) == 1:
                exact = (year,) + family[0]
        family_name = None
        if family:
            sample = next((m for m in family if m[0] == make), family[0])
            family_name = " ".join(sample[1].split()[:len(matched) if sample[0] == make or make is None else max(1, len(matched) - 1)])
        return VehicleMatch(family_name=family_name, year=year, make=make or (family[0][0] if len({m for m, _ in family}) == 1 else None),
                            models=family, consumed=consumed, exact=exact)

    @staticmethod
    def _longest_prefix(model_words, q):
        """Longest k such that model_words[:k] appears consecutively in q. Returns (k, start)."""
        best = (0, None)
        for s in range(len(q)):
            k = 0
            while k < len(model_words) and s + k < len(q) and q[s + k] == model_words[k]:
                k += 1
            if k > best[0]:
                best = (k, s)
        return best


# ------------------------------------------------------------------ Year / Make / Model menus

def years(conn):
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT v.year FROM vehicles v WHERE EXISTS
                       (SELECT 1 FROM fitment f JOIN products p ON p.id = f.product_id WHERE f.vehicle_id = v.id AND p.status = 'active')
                       ORDER BY 1 DESC""")
        return [r[0] for r in cur.fetchall()]


def makes(conn, year):
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT v.make FROM vehicles v WHERE v.year = %s AND EXISTS
                       (SELECT 1 FROM fitment f JOIN products p ON p.id = f.product_id WHERE f.vehicle_id = v.id AND p.status = 'active')
                       ORDER BY 1""", (year,))
        return [r[0] for r in cur.fetchall()]


def models(conn, year, make):
    with conn.cursor() as cur:
        cur.execute("""SELECT v.id, v.model FROM vehicles v WHERE v.year = %s AND v.make = %s AND EXISTS
                       (SELECT 1 FROM fitment f JOIN products p ON p.id = f.product_id WHERE f.vehicle_id = v.id AND p.status = 'active')
                       ORDER BY 2""", (year, make))
        return [{"id": i, "model": m} for i, m in cur.fetchall()]


def get(conn, vehicle_id):
    with conn.cursor() as cur:
        cur.execute("SELECT id, year, make, model FROM vehicles WHERE id = %s", (vehicle_id,))
        r = cur.fetchone()
    return {"id": r[0], "year": r[1], "make": r[2], "model": r[3], "label": f"{r[1]} {r[2]} {r[3]}"} if r else None


def find_id(conn, year, make, model):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM vehicles WHERE year = %s AND make = %s AND model = %s", (year, make, model))
        r = cur.fetchone()
    return r[0] if r else None


class Picker:
    """Year -> make -> model choices for the "Add a vehicle" bar, worked out once and kept in memory.

    Only vehicles with at least one part for sale are offered. Asking the database that question on every
    click got slow once the catalog had millions of fitment rows, so it's answered in one pass here."""

    def __init__(self, conn):
        with conn.cursor() as cur:
            cur.execute("""SELECT v.id, v.year, v.make, v.model FROM vehicles v
                           WHERE v.id IN (SELECT f.vehicle_id FROM fitment f JOIN products p ON p.id = f.product_id
                                          WHERE p.status = 'active')""")
            rows = cur.fetchall()
        tree = {}
        for vid, year, make, model in rows:
            tree.setdefault(year, {}).setdefault(make, []).append({"id": vid, "model": model})
        for makes in tree.values():
            for lst in makes.values():
                lst.sort(key=lambda m: m["model"])
        self.tree = tree

    def years(self):
        return sorted(self.tree, reverse=True)

    def makes(self, year):
        return sorted(self.tree.get(year, {}))

    def models(self, year, make):
        return list(self.tree.get(year, {}).get(make, []))
