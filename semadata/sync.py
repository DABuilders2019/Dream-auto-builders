"""SEMA Data tasks.

  test     sign in, read the plan's usage, list the brands SEMA has approved for us and match our
           brand list (config/sema_brands.json, editable on the admin page) to them
  load     load brands: product data (PIES), vehicle fitment and logo, then photos
           (which brands: codes given, or every brand on our list that is new or changed at SEMA)

How a brand is loaded:
  1. /products                 every part number of the brand (1 call)
  2. product data              100 parts per call, written out as one PIES file
  3. /lookup/products          which parts have vehicle fitment (1 call)
  4. /lookup/vehiclesbyproduct the vehicles for each part with fitment (1 call per part). Answers are
                               kept on disk, so a reload only asks about new parts.
  5. the same loader as a manual upload (importer.load) puts it all in the database in one step,
     so the shop never shows a half-loaded brand
  6. photos are downloaded, checked and resized like for uploaded brands (importer.photos)
"""
import datetime as dt
import difflib
import json
import os
import pathlib
import re
import time
import xml.sax.saxutils as su

from psycopg.types.json import Jsonb

from . import client as sc

PIES_BATCH = 100
CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config" / "sema_brands.json"

# Words that say what a brand sells rather than who it is; ignored when matching our list to SEMA's names.
GENERIC = {"performance", "products", "product", "inc", "llc", "co", "company", "corp", "industries", "the", "and",
           "offroad", "off", "road", "shocks", "shock", "exhaust", "rims", "rim", "brakes", "brake", "lighting", "lights",
           "suspension", "chassis", "motorsport", "motorsports", "motor", "sports", "racing", "usa", "parts",
           "automotive", "auto", "systems", "wheels", "wheel", "alloys", "&"}


# ------------------------------------------------------------------ our brand list

def wishlist(conn=None):
    if conn is not None:
        r = conn.execute("SELECT value FROM sema_state WHERE key = 'wishlist'").fetchone()
        if r and r[0] is not None:
            return list(r[0])
    try:
        return list(json.loads(CONFIG.read_text())["brands"])
    except (FileNotFoundError, ValueError, KeyError):
        return []


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def tokens(s):
    return [t for t in re.split(r"[^a-z0-9]+", (s or "").lower().replace("hilites", "hi lites")) if t]


def core(s):
    return [t for t in tokens(s) if t not in GENERIC] or tokens(s)


def candidates(wish, sema_names):
    """SEMA brand names that could be this entry on our list, best first."""
    w, wc = norm(wish), core(wish)
    exact = [n for n in sema_names if norm(n) == w]
    if exact:
        return exact, True
    strong = []
    wj = norm("".join(wc))
    for n in sema_names:
        nn, nc = norm(n), core(n)
        if (nc == wc or nn == wj or (len(nn) >= 4 and nn in w) or nn in tokens(wish) or (w and w in nn)
                or (len(wc) >= 2 and all(t in nc or t in nn for t in wc))):
            strong.append(n)
    if len(strong) == 1:
        return strong, True
    pool = strong or sema_names
    ranked = sorted(pool, key=lambda n: -difflib.SequenceMatcher(None, wj, norm(n)).ratio())
    return ranked[:6], False


def match_wishlist(conn, log=print):
    """Mark which approved SEMA brands are on our list. Returns {wish: {"aaia", "name", "choices"}}."""
    rows = conn.execute("SELECT aaia, name FROM sema_brands WHERE approved").fetchall()
    by_name = {}
    for aaia, name in rows:
        by_name.setdefault(name, aaia)
    picks = conn.execute("SELECT value FROM sema_state WHERE key = 'picks'").fetchone()
    picks = (picks[0] if picks else None) or {}
    out, wanted = {}, {}
    for wish in dict.fromkeys(w.strip() for w in wishlist(conn) if w.strip()):
        pick = picks.get(wish)
        if pick == "-":
            out[wish] = {"aaia": None, "name": None, "choices": [], "skipped": True}
            continue
        if pick and pick in dict(rows):
            out[wish] = {"aaia": pick, "name": dict(rows)[pick], "choices": [], "picked": True}
            wanted[pick] = wish
            continue
        names, sure = candidates(wish, list(by_name))
        if sure:
            aaia = by_name[names[0]]
            out[wish] = {"aaia": aaia, "name": names[0], "choices": []}
            wanted[aaia] = wish
        else:
            out[wish] = {"aaia": None, "name": None, "choices": [(by_name[n], n) for n in names]}
    conn.execute("UPDATE sema_brands SET wanted = false, wish_name = NULL")
    for aaia, wish in wanted.items():
        conn.execute("UPDATE sema_brands SET wanted = true, wish_name = %s WHERE aaia = %s", (wish, aaia))
    conn.execute("""INSERT INTO sema_state (key, value, updated_at) VALUES ('matches', %s, now())
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                 (Jsonb(out),))
    log(f"Our list: {len(out)} brands, {len(wanted)} matched to SEMA, "
        f"{sum(1 for v in out.values() if not v['aaia'] and not v.get('skipped'))} need a choice")
    return out


# ------------------------------------------------------------------ tasks

def run(conn, task, args=(), log=print, client=None, data_dir=None, photos=True):
    c = client or sc.Client(conn, task=task, log=log)
    t0 = time.time()
    stats = {"task": task, "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    status, error = "ok", None
    try:
        if task == "test":
            stats.update(test(conn, c, log))
        elif task == "load":
            stats.update(load(conn, c, list(args), log, data_dir or _data_dir(), photos))
        else:
            raise ValueError(f"unknown task {task!r}")
    except sc.StopSync as e:
        status, error = "stopped", str(e)
        raise
    except Exception as e:      # noqa: BLE001
        status, error = "failed", repr(e)
        raise
    finally:
        stats.update(status=status, error=error, calls=c.calls, seconds=round(time.time() - t0),
                     finished=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        c.set_state(f"last_{task}", stats)
        if client is None:
            c.close()
    return stats


def _data_dir():
    return pathlib.Path(os.environ.get("DATA_DIR", "data")) / "sema"


def test(conn, c, log):
    c.token(fresh=True)
    log("Signed in to SEMA Data")
    r = c.receiver()
    if r.get("APIMaxCount"):
        log(f"Plan usage: {r.get('APICount') or 0:,} of {r['APIMaxCount']:,} API calls, "
            f"{r.get('ExportCount') or 0:,} of {r.get('ExportMaxCount') or 0:,} exports. Renews {r.get('MembershipExpirationDate') or '?'}")
    n = refresh_brands(conn, c, log)
    matches = match_wishlist(conn, log)
    total = estimate(conn, c, log)
    return {"approved_brands": n, "on_our_list": sum(1 for v in matches.values() if v["aaia"]), "estimated_calls": total}


def refresh_estimates(conn, fit_mode):
    """Recount the calls each unloaded brand needs (fewer once we know SEMA answers fitment in batches)."""
    per = FIT_BATCH if fit_mode in ("repeat", "plural") else 1
    conn.execute("""UPDATE sema_brands SET est_calls = ((est_parts + 99) / 100) + 2 + ((est_fit + %s - 1) / %s)
                    WHERE est_parts IS NOT NULL AND est_fit IS NOT NULL""", (per, per))
    return conn.execute("""SELECT coalesce(sum(est_calls), 0) FROM sema_brands
                           WHERE wanted AND approved AND status IS DISTINCT FROM 'loaded'""").fetchone()[0]


def estimate(conn, c, log):
    """Count parts and parts with fitment for brands on our list that aren't loaded yet (2 calls a brand)."""
    rows = conn.execute("""SELECT aaia, name FROM sema_brands WHERE wanted AND approved AND est_fit IS NULL
                           AND status IS DISTINCT FROM 'loaded' ORDER BY name""").fetchall()
    if rows:
        log(f"Sizing {len(rows)} brand(s) on our list ...")
    for aaia, name in rows:
        try:
            parts = part_numbers(c, aaia)
            body = c.call("POST", "/lookup/products", {"aaia_brandid": aaia})
            fit = sum(1 for p in body.get("Products") or [] if p.get("FitmentCount"))
        except sc.BrandError as e:
            log(f"  {name}: {e}")
            continue
        per = FIT_BATCH if c.state("fit_mode") in ("repeat", "plural") else 1
        calls = -(-len(parts) // PIES_BATCH) + 2 + -(-fit // per)
        conn.execute("UPDATE sema_brands SET est_parts = %s, est_fit = %s, est_calls = %s WHERE aaia = %s",
                     (len(parts), fit, calls, aaia))
        log(f"  {name}: {len(parts):,} parts, {fit:,} fit specific vehicles: about {calls:,} calls to load")
    total = refresh_estimates(conn, c.state("fit_mode"))
    log(f"Loading everything on the list not loaded yet: about {total:,} API calls")
    return total


def refresh_brands(conn, c, log):
    body = c.call("GET", "/export/branddatasets")
    sets = body.get("BrandDatasets") or body.get("branddatasets") or []
    seen = {}
    for d in sets:
        aaia = (d.get("AAIABrandId") or d.get("AAIA_BrandID") or "").strip().upper()
        if aaia:
            seen[aaia] = d
    for aaia, d in seen.items():
        conn.execute("""INSERT INTO sema_brands (aaia, name, dataset_id, sema_updated, approved, updated_at)
                        VALUES (%s, %s, %s, %s, true, now())
                        ON CONFLICT (aaia) DO UPDATE SET name = EXCLUDED.name, dataset_id = EXCLUDED.dataset_id,
                            sema_updated = EXCLUDED.sema_updated, approved = true, updated_at = now()""",
                     (aaia, (d.get("BrandName") or aaia).strip(), d.get("DatasetId"), str(d.get("LastUpdated") or "")))
    conn.execute("UPDATE sema_brands SET approved = false WHERE NOT (aaia = ANY(%s))", (list(seen),))
    log(f"SEMA has approved {len(seen)} brands for us")
    return len(seen)


# ------------------------------------------------------------------ load

def load(conn, c, codes, log, data_dir, photos=True):
    from importer import brands as brand_lookup
    from importer import load as loader
    refresh = "--refresh-fitment" in codes
    codes = [x.upper() for x in codes if not x.startswith("--")]
    refresh_brands(conn, c, log)
    match_wishlist(conn, log=lambda m: None)
    if codes:
        rows = conn.execute("SELECT aaia, name, sema_updated FROM sema_brands WHERE aaia = ANY(%s) AND approved ORDER BY name",
                            (codes,)).fetchall()
        missing = set(codes) - {r[0] for r in rows}
        if missing:
            log(f"Not approved at SEMA (skipped): {', '.join(sorted(missing))}")
    else:   # our list: brands never loaded, changed at SEMA since, or that failed last time
        rows = conn.execute("""SELECT aaia, name, sema_updated FROM sema_brands WHERE wanted AND approved
                               AND (loaded_updated IS DISTINCT FROM sema_updated OR status IS DISTINCT FROM 'loaded')
                               ORDER BY name""").fetchall()
    if not rows:
        log("Nothing to load: every brand on the list is up to date.")
        return {"brands": 0}
    log(f"{len(rows)} brand(s) to load: {', '.join(r[1] for r in rows)}")
    c.reserve_budget()
    done, failed = [], []
    for aaia, name, updated in rows:
        log(f"\n--- {name} ({aaia}) ---")
        conn.execute("UPDATE sema_brands SET status = 'loading', last_error = NULL, updated_at = now() WHERE aaia = %s", (aaia,))
        try:
            work = pathlib.Path(data_dir) / aaia
            work.mkdir(parents=True, exist_ok=True)
            parts = part_numbers(c, aaia)
            if not parts:
                raise sc.BrandError("SEMA lists no parts for this brand")
            log(f"  {len(parts):,} parts")
            pies_path = fetch_pies(c, aaia, parts, work / "pies.xml", log)
            veh_path, fitted = fetch_fitment(c, aaia, parts, work, log, refresh)
            pies_aaia, label = brand_lookup.peek(str(pies_path))
            brand = brand_lookup.resolve(conn, aaia, label or name, name)
            st = loader.load_brand(conn, brand, str(pies_path), None, str(veh_path), log=log)
            conn.execute("""UPDATE sema_brands SET status = 'loaded', brand_id = %s, loaded_updated = %s, last_loaded = now(),
                            parts = %s, fitted_parts = %s, last_error = NULL, updated_at = now() WHERE aaia = %s""",
                         (brand["id"], updated, st.get("products_in_file", len(parts)), fitted, aaia))
            log(f"  Loaded: {st.get('products_in_file', 0):,} products, {st.get('fitment_rows', 0):,} vehicle fitment rows")
            done.append((aaia, brand["id"], name))
        except sc.BrandError as e:
            conn.execute("UPDATE sema_brands SET status = 'failed', last_error = %s, updated_at = now() WHERE aaia = %s", (str(e), aaia))
            log(f"  SKIPPED: {e}")
            failed.append(aaia)
        except sc.StopSync as e:
            conn.execute("UPDATE sema_brands SET status = 'failed', last_error = %s, updated_at = now() WHERE aaia = %s", (str(e), aaia))
            raise
        except Exception as e:     # noqa: BLE001 - one brand's bad file shouldn't stop the others
            conn.execute("UPDATE sema_brands SET status = 'failed', last_error = %s, updated_at = now() WHERE aaia = %s",
                         (f"{e.__class__.__name__}: {e}"[:500], aaia))
            log(f"  FAILED: {e!r}")
            failed.append(aaia)
    if photos:
        for aaia, brand_id, name in done:
            log(f"\n--- {name}: photos ---")
            run_photos(conn, brand_id, log)
    return {"brands": len(rows), "loaded": len(done), "failed": len(failed)}


def disk_free_gb():
    import shutil
    path = os.environ.get("PHOTO_DIR") or os.environ.get("DATA_DIR", "data")
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def run_photos(conn, brand_id, log):
    from importer import photos as ph
    free = disk_free_gb()
    if free is not None and free < 1.0:
        log(f"  Photos skipped: only {free:.1f} GB free on the photo disk. Make the disk bigger in Render, "
            "then use Retry photos on the Catalog page.")
        return
    try:
        stats, summary = ph.run(conn, brand_id, None, 4, None, int(os.environ.get("PHOTO_WORKERS", "4")), log=log)
        log(f"  Photos: {json.dumps(summary.get('counts', {}))}")
    except Exception as e:     # noqa: BLE001 - photos can be retried from the Catalog page
        log(f"  Photos stopped: {e!r} (retry from the Catalog page)")


def part_numbers(c, aaia):
    body = c.call("GET", "/products", {"brandaaiaid": aaia})
    pns = body.get("PartNumbers") or body.get("partnumbers") or []
    return list(dict.fromkeys(str(p).strip() for p in pns if str(p).strip()))


def fetch_pies(c, aaia, parts, path, log):
    """Product data for every part, 100 per call, written out as one PIES file for the loader."""
    tmp = path.with_suffix(".tmp")
    batches = [parts[i:i + PIES_BATCH] for i in range(0, len(parts), PIES_BATCH)]
    with open(tmp, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="utf-8"?>\n<PIES xmlns="http://www.aftermarket.org">\n'
                  '<Header><SubmissionType>FULL</SubmissionType>'
                  f'<BlanketEffectiveDate>{dt.date.today().isoformat()}</BlanketEffectiveDate></Header>\n<Items>\n')
        got = 0
        for i, batch in enumerate(batches):
            body = c.call("GET", "/products/bulk", [("brandaaiaid", aaia)] + [("partnumbers", p) for p in batch])
            for prod in body.get("Products") or []:
                out.write(product_xml(prod, aaia))
                got += 1
            if (i + 1) % 20 == 0:
                log(f"  product data: {got:,} of {len(parts):,}")
        out.write("</Items>\n</PIES>\n")
    if got < len(parts) * 0.9:
        raise sc.BrandError(f"SEMA returned product data for only {got:,} of {len(parts):,} parts")
    tmp.replace(path)
    log(f"  product data: {got:,} parts")
    return path


FIT_BATCH = 50
FIT_KEYS = {"repeat": "partNumber", "plural": "partNumbers", "single": "partNumber"}


def fitment_mode(c, aaia, todo, log):
    """Can /lookup/vehiclesbyproduct answer for several parts in one call? SEMA's documentation shows one part
    per call; asking about two parts once (and remembering the answer) can save thousands of calls."""
    saved = c.state("fit_mode")
    if saved in FIT_KEYS:
        return saved
    pair = todo[:2]
    for mode in ("repeat", "plural"):
        try:
            body = c.call("POST", "/lookup/vehiclesbyproduct", [("aaia_brandid", aaia)] + [(FIT_KEYS[mode], p) for p in pair])
        except sc.BrandError:
            continue
        got = {str(p.get("PartNumber") or "").strip() for p in body.get("Parts") or [] if p.get("Vehicles")}
        if set(pair) <= got:
            log(f"  SEMA answers fitment for several parts per call: {FIT_BATCH} parts per call")
            c.set_state("fit_mode", mode)
            refresh_estimates(c.conn, mode)
            return mode
    log("  SEMA answers fitment one part per call")
    c.set_state("fit_mode", "single")
    return "single"


def fetch_fitment(c, aaia, parts, work, log, refresh=False):
    """Vehicle fitment rows for the brand's parts. Kept in fitment.jsonl so reloads only ask about new parts."""
    cache = work / "fitment.jsonl"
    if refresh:
        cache.unlink(missing_ok=True)
    done = set()
    if cache.exists():
        for line in open(cache, encoding="utf-8"):
            r = json.loads(line)
            if r.get("done"):
                done.add(r["sku"])
    try:
        body = c.call("POST", "/lookup/products", {"aaia_brandid": aaia})
        counts = {str(p.get("PartNumber")).strip(): p.get("FitmentCount") for p in body.get("Products") or []}
    except sc.BrandError as e:
        log(f"  couldn't read which parts have fitment ({e}); asking about every part")
        counts = {}
    with_fit = [p for p in parts if counts.get(p, 1)]          # unknown -> ask
    todo = [p for p in with_fit if p not in done]
    log(f"  fitment: {len(with_fit):,} parts fit specific vehicles; {len(todo):,} to look up")
    mode = fitment_mode(c, aaia, todo, log) if len(todo) > 1 else "single"
    size = FIT_BATCH if mode != "single" else 1
    with open(cache, "a", encoding="utf-8") as out:
        for i in range(0, len(todo), size):
            batch = todo[i:i + size]
            fields = [("aaia_brandid", aaia)] + [(FIT_KEYS.get(mode, "partNumber"), pn) for pn in batch]
            try:
                body = c.call("POST", "/lookup/vehiclesbyproduct", fields)
            except sc.BrandError as e:
                log(f"  {', '.join(batch[:3])}{'…' if len(batch) > 3 else ''}: {e}")
                continue
            for part in body.get("Parts") or []:
                pn = str(part.get("PartNumber") or (batch[0] if len(batch) == 1 else "")).strip()
                if pn not in batch:
                    continue
                for v in part.get("Vehicles") or []:
                    out.write(json.dumps({"sku": pn, "year": v.get("Year"), "make": v.get("MakeName"),
                                          "model": v.get("ModelName"), "submodel": v.get("SubmodelName"),
                                          "notes": v.get("FitmentNotes")}) + "\n")
            for pn in batch:
                out.write(json.dumps({"sku": pn, "done": True}) + "\n")
            out.flush()
            if (i // size + 1) % 250 == 0:
                log(f"  fitment: {min(i + size, len(todo)):,} of {len(todo):,} looked up")
    current = set(parts)
    veh = work / "vehicles.jsonl"
    fitted = set()
    with open(cache, encoding="utf-8") as src, open(veh, "w", encoding="utf-8") as out:
        for line in src:
            r = json.loads(line)
            if r.get("done") or r["sku"] not in current:
                continue
            out.write(line)
            fitted.add(r["sku"])
    return veh, len(fitted)


# ------------------------------------------------------------------ JSON product -> PIES XML

def _g(d, *keys):
    if not isinstance(d, dict):
        return None
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:
        v = low.get(k.lower())
        if v not in (None, "", []):
            return v
    return None


def _e(v):
    return su.escape(str(v))


def _a(v):
    return su.quoteattr(str(v))


def product_xml(p, aaia):
    """One /products/bulk record as a PIES <Item> (only the parts our loader reads)."""
    seg = _g(p, "ItemSegments") or {}
    pn = _g(p, "PartNumber") or ""
    x = [f"<Item MaintenanceType={_a(_g(seg, 'MaintenanceType') or 'A')}>",
         f"<PartNumber>{_e(pn)}</PartNumber>",
         f"<BrandAAIAID>{_e(_g(p, 'BrandAAIAID') or aaia)}</BrandAAIAID>"]
    for tag, keys in (("BrandLabel", ("BrandLabel",)), ("ItemLevelGTIN", ("ItemLevelGTIN", "GTIN")),
                      ("PartTerminologyID", ("PartTerminologyID", "PartTerminologyId"))):
        v = _g(seg, *keys) or _g(p, *keys)
        if v is not None:
            x.append(f"<{tag}>{_e(v)}</{tag}>")
    descs = _g(p, "Descriptions") or []
    if descs:
        x.append("<Descriptions>")
        for d in descs:
            code = _g(d, "Code", "DescriptionCode")
            vals = _g(d, "Values", "Value") or []
            for v in vals if isinstance(vals, list) else [vals]:
                if code and v:
                    x.append(f"<Description DescriptionCode={_a(code)}>{_e(v)}</Description>")
        x.append("</Descriptions>")
    prices = _g(p, "Prices") or []
    if prices:
        x.append("<Prices>")
        for pr in prices:
            code, val = _g(pr, "Code", "PriceType"), _g(pr, "Value", "Price")
            if code and val not in (None, ""):
                x.append(f"<Pricing PriceType={_a(code)}><Price UOM={_a(_g(pr, 'UoM') or 'PE')}>{_e(val)}</Price>"
                         f"<CurrencyCode>{_e(_g(pr, 'Currency', 'CurrencyCode') or 'USD')}</CurrencyCode></Pricing>")
        x.append("</Prices>")
    ext = _g(p, "ExtendedProductInfo", "ExtendedInformation") or []
    if ext:
        x.append("<ExtendedInformation>")
        for e in ext:
            if _g(e, "Code") and _g(e, "Value") is not None:
                x.append(f"<ExtendedProductInformation EXPICode={_a(_g(e, 'Code'))}>{_e(_g(e, 'Value'))}</ExtendedProductInformation>")
        x.append("</ExtendedInformation>")
    attrs = _g(p, "Attributes") or []
    if attrs:
        x.append("<ProductAttributes>")
        for a in attrs:
            code, val = _g(a, "Code", "AttributeID", "Name"), _g(a, "Value")
            if code and val not in (None, ""):
                padb = "Y" if _g(a, "IsPADBAttribute") in (True, "true", "Y", "y", 1) else "N"
                x.append(f"<ProductAttribute AttributeID={_a(code)} PADBAttribute={_a(padb)}>{_e(val)}</ProductAttribute>")
        x.append("</ProductAttributes>")
    pk = _g(p, "Packaging") or {}
    if pk:
        x.append("<Packages><Package>")
        if _g(pk, "Weight") not in (None, ""):
            x.append(f"<Weights UOM={_a(_g(pk, 'WeightUOM') or 'PG')}><Weight>{_e(_g(pk, 'Weight'))}</Weight></Weights>")
        dims = [_g(pk, k) for k in ("Length", "Width", "Height")]
        if all(v not in (None, "") for v in dims):
            x.append(f"<Dimensions UOM={_a(_g(pk, 'DIMUOM') or 'IN')}><Length>{_e(dims[0])}</Length>"
                     f"<Width>{_e(dims[1])}</Width><Height>{_e(dims[2])}</Height></Dimensions>")
        x.append("</Package></Packages>")
    assets = _g(p, "DigitalAssets") or []
    if assets:
        x.append("<DigitalAssets>")
        for a in assets:
            uri = _g(a, "Value", "URI", "Link")
            if not uri:
                continue
            x.append(f"<DigitalFileInformation><AssetType>{_e(_g(a, 'Code', 'AssetTypeCode', 'AssetType') or '')}</AssetType>"
                     f"<FileName>{_e(_g(a, 'FileName') or '')}</FileName><FileType>{_e(_g(a, 'FileType') or _ext(uri))}</FileType>"
                     f"<URI>{_e(uri)}</URI></DigitalFileInformation>")
        x.append("</DigitalAssets>")
    x.append("</Item>\n")
    return "".join(x)


def _ext(uri):
    m = re.search(r"\.([A-Za-z0-9]{2,4})(?:$|\?)", uri or "")
    return m.group(1).upper() if m else ""
