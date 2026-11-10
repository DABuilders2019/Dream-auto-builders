"""Download, check and store product photos.

For each product, photos are tried in feed order (primary P04 first). Each file is:
  1. fetched from the brand's SEMA Digital Assets zip if provided, otherwise from its link;
  2. matched against image_reviews (known logos/placeholders are blocked, truck shots marked 'vehicle');
  3. decoded, flattened onto white, resized to max 1200 px and stored once per unique file.
Products whose first photos all fail get their remaining photos tried as a fallback.
"""
import collections
import concurrent.futures as cf
import hashlib
import io
import threading
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from PIL import Image, ImageChops, ImageFilter
from psycopg.types.json import Jsonb

from .storage import get_storage

MAX_SIDE = 1200
UA = "Mozilla/5.0 (compatible; DreamAutoBuildersImporter/1.0)"


def _fetch(url, timeout=60, attempts=3):
    if "dropbox.com" in url:
        url = url.replace("dl=0", "dl=1") if "dl=0" in url else url + ("&" if "?" in url else "?") + "dl=1"
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~")     # links sometimes contain spaces
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            deadline = time.monotonic() + MAX_SECONDS
            with urllib.request.urlopen(req, timeout=timeout) as r:
                # read in pieces with an overall time limit: a server that sends a trickle of bytes would otherwise
                # never hit the per-read timeout and could hold up the whole photo run
                parts, size = [], 0
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    parts.append(chunk)
                    size += len(chunk)
                    if size > MAX_DOWNLOAD:
                        raise ValueError("file over 60 MB")
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"download took over {MAX_SECONDS} seconds")
                data = b"".join(parts)
            return data
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError, http.client.HTTPException, OSError):
            if attempt == attempts - 1:      # busy image servers drop connections: wait and try again
                raise
        time.sleep(2 * 2 ** attempt)


# Some brands send huge original images (several thousand pixels on a side). Decoding many at once can use
# gigabytes of memory, so they are decoded one at a time, JPEGs are decoded at reduced size, and
# absurdly large images are refused.
_DECODE = threading.BoundedSemaphore(1)   # the server has one CPU, so decoding one at a time costs no speed
Image.MAX_IMAGE_PIXELS = 60_000_000
MAX_DOWNLOAD = 60 * 1024 * 1024
MAX_SECONDS = 120          # one photo download, start to finish


PHOTO_VERSION = "-t"     # in stored file names: photos processed with trimming (older ones have no suffix)
TRIM_WHITE = 238         # a border pixel lighter than this in every colour counts as plain white background
TRIM_MARGIN = 0.04       # margin put back around the part: 4% of its longer side


def trim_white(im, thresh=TRIM_WHITE, margin=TRIM_MARGIN):
    """Cut the plain white border off a product photo and put back the same small margin on every photo, so parts
    fill their box about the same amount whatever spacing the brand used. Photos on grey or coloured backgrounds,
    and photos that already fill the frame, are left as they are."""
    im = im.convert("RGB")
    darkest = ImageChops.darker(ImageChops.darker(*im.split()[:2]), im.split()[2])     # min of R, G, B per pixel
    mask = darkest.point(lambda v: 255 if v < thresh else 0).filter(ImageFilter.MedianFilter(3))  # ignore stray specks
    box = mask.getbbox()
    W, H = im.size
    if not box:
        return im
    w, h = box[2] - box[0], box[3] - box[1]
    if w >= W * 0.97 and h >= H * 0.97:          # already fills the frame (or isn't on white)
        return im
    if w * h < W * H * 0.002:                   # only a speck: not a real photo of a part
        return im
    pad = int(max(w, h) * margin)
    out = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (255, 255, 255))
    out.paste(im.crop(box), (pad, pad))
    return out


def _process(raw):
    with _DECODE:
        return _process_one(raw)


def _process_one(raw):
    im = Image.open(io.BytesIO(raw))
    if im.format == "JPEG":
        im.draft("RGB", (MAX_SIDE, MAX_SIDE))     # decode at 1/2, 1/4 or 1/8 size when that's still big enough
    im.load()
    if min(im.size) < 80:
        raise ValueError(f"image too small {im.size}")
    if im.mode == "P":
        im = im.convert("RGBA")
    if im.mode not in ("RGB", "RGBA", "LA", "L"):
        im = im.convert("RGB")
    im.thumbnail((MAX_SIDE * 2, MAX_SIDE * 2))   # shrink first: the colour work below is then on a smaller image
    if im.mode in ("RGBA", "LA"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im = trim_white(im)                          # trimmed before the final shrink, so the part keeps its detail
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85, optimize=True, progressive=True)
    return out.getvalue(), im.size


def _process_logo(raw):
    """Logo as a PNG: transparency kept, empty margins trimmed, at most 600 x 240."""
    im = Image.open(io.BytesIO(raw))
    im.load()
    im = im.convert("RGBA")
    alpha = im.split()[-1].point(lambda a: 255 if a > 10 else 0)
    ink = ImageChops.difference(im.convert("RGB"), Image.new("RGB", im.size, (255, 255, 255))).convert("L")
    box = ImageChops.multiply(ink.point(lambda v: 255 if v > 18 else 0), alpha).getbbox()
    if box:
        im = im.crop(box)
    im.thumbnail((600, 240))
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    return out.getvalue()


def save_logo(conn, brand_id, raw, source, storage=None):
    """Store a logo given as file bytes. source: 'upload' (kept until replaced) or the link it came from."""
    data = _process_logo(raw)
    storage = storage or get_storage()
    key = f"logos/{brand_id}-{hashlib.md5(data).hexdigest()[:10]}.png"
    storage.put(key, data, "image/png")
    with conn.cursor() as cur:
        cur.execute("UPDATE brands SET logo_key = %s, logo_src = %s WHERE id = %s", (key, source, brand_id))
    conn.commit()
    return key


def fetch_url(url):
    return _fetch(url)


def fetch_logo(conn, brand_id, storage=None, log=print):
    """Download the brand logo named in the PIES file (if it changed since last time)."""
    with conn.cursor() as cur:
        cur.execute("SELECT logo_url, logo_src, logo_key FROM brands WHERE id = %s", (brand_id,))
        r = cur.fetchone()
    if not r or not r[0] or (r[0] == r[1] and r[2]) or (r[1] == "upload" and r[2]):
        return r[2] if r else None      # nothing new, or a logo uploaded on the admin page (kept)
    storage = storage or get_storage()
    try:
        data = _process_logo(_fetch(r[0]))
        key = f"logos/{brand_id}-{hashlib.md5(data).hexdigest()[:10]}.png"
        storage.put(key, data, "image/png")
        with conn.cursor() as cur:
            cur.execute("UPDATE brands SET logo_key = %s, logo_src = %s WHERE id = %s", (key, r[0], brand_id))
        conn.commit()
        log("  brand logo saved")
        return key
    except Exception as e:
        conn.rollback()
        log(f"  brand logo not saved: {e!r}")
        return None


# Failures worth trying again later (not: missing link, tiny image, file not found / forbidden).
RETRY_SQL = """UPDATE product_images i SET status = 'pending' FROM products p
               WHERE p.id = i.product_id AND p.brand_id = %s AND i.status = 'failed'
                 AND coalesce(i.note, '') NOT LIKE 'no link%%' AND coalesce(i.note, '') NOT LIKE '%%too small%%'
                 AND coalesce(i.note, '') NOT LIKE '%%HTTP Error 404%%' AND coalesce(i.note, '') NOT LIKE '%%HTTP Error 403%%'
                 AND coalesce(i.note, '') NOT LIKE '%%HTTP Error 410%%' AND coalesce(i.note, '') NOT LIKE '%%cannot identify image%%'"""


def run(conn, brand_id, zip_path=None, max_per_product=4, limit_products=None, workers=12, log=print, retry_failed=False):
    storage = get_storage()
    fetch_logo(conn, brand_id, storage, log)
    if retry_failed:
        with conn.cursor() as cur:
            cur.execute(RETRY_SQL, (brand_id,))
            if cur.rowcount:
                log(f"  trying {cur.rowcount} failed downloads again")
        conn.commit()
    zf = zipfile.ZipFile(zip_path) if zip_path else None
    zip_names = set(zf.namelist()) if zf else set()
    stats = collections.Counter()
    t0 = time.time()

    with conn.cursor() as cur:
        cur.execute("INSERT INTO import_runs (brand_id, step) VALUES (%s, 'photos') RETURNING id", (brand_id,))
        run_id = cur.fetchone()[0]
        cur.execute("SELECT key, verdict, reason FROM image_reviews")
        reviews = {k: (v, r) for k, v, r in cur.fetchall()}
    conn.commit()

    def work(row):
        img_id, url, file_name = row
        sources = []
        if zf and file_name in zip_names:
            sources.append(("zip", file_name))
        if url:
            sources.append(("link", url))
        if not sources:
            return img_id, {"status": "failed", "note": "no link and not in zip"}
        result = None
        for kind_src, ref in sources:          # zip copy first; if it's unusable, try the link (often a different file)
            try:
                raw = zf.read(ref) if kind_src == "zip" else _fetch(ref)
                md5 = hashlib.md5(raw).hexdigest()
                verdict = reviews.get(md5) or reviews.get(url)
                if verdict and verdict[0] == "blocked":
                    result = {"status": "blocked", "md5": md5, "note": f"{verdict[1]} [{kind_src}]"}
                    continue
                data, (w, h) = _process(raw)
                key = f"products/{brand_id}/{md5[:2]}/{md5}{PHOTO_VERSION}.jpg"
                if not storage.exists(key):
                    storage.put(key, data)
                kind = verdict[0] if verdict and verdict[0] in ("vehicle", "packaging") else "part"
                return img_id, {"status": "ok", "md5": md5, "storage_key": key, "width": w, "height": h, "kind": kind,
                                "note": "from link" if kind_src == "link" else None}
            except Exception as e:
                result = result if result and result["status"] == "blocked" else {"status": "failed", "note": f"{repr(e)[:180]} [{kind_src}]"}
        return img_id, result

    def batch(where_sql, params):
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT i.id, i.source_url, i.file_name FROM (
                    SELECT i.*, row_number() OVER (PARTITION BY i.product_id ORDER BY i.sort, i.id) AS rank
                    FROM product_images i JOIN products p ON p.id = i.product_id
                    WHERE p.brand_id = %s AND p.status = 'active'
                ) i WHERE i.status = 'pending' AND {where_sql}
                ORDER BY i.product_id, i.rank""", [brand_id] + params)
            rows = cur.fetchall()
        if limit_products:
            keep = set()
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM products WHERE brand_id = %s AND status = 'active' ORDER BY id LIMIT %s", (brand_id, limit_products))
                keep = {r[0] for r in cur.fetchall()}
            with conn.cursor() as cur:
                cur.execute("SELECT id, product_id FROM product_images WHERE id = ANY(%s)", ([r[0] for r in rows],))
                owner = dict(cur.fetchall())
            rows = [r for r in rows if owner[r[0]] in keep]
        log(f"  checking {len(rows)} photos")
        done = 0
        with cf.ThreadPoolExecutor(workers) as ex, conn.cursor() as cur:
            for img_id, res in ex.map(work, rows):
                stats[res["status"]] += 1
                cur.execute("""UPDATE product_images SET status = %s, md5 = %s, storage_key = %s, width = %s, height = %s,
                               kind = %s, note = %s, checked_at = now() WHERE id = %s""",
                            (res["status"], res.get("md5"), res.get("storage_key"), res.get("width"), res.get("height"),
                             res.get("kind"), res.get("note"), img_id))
                done += 1
                if done % 500 == 0:
                    conn.commit()
                    log(f"    {done}/{len(rows)}")
        conn.commit()

    try:
        batch("i.rank <= %s", [max_per_product])
        # Fallback: products with no usable part photo yet get their remaining photos tried.
        batch("""NOT EXISTS (SELECT 1 FROM product_images o WHERE o.product_id = i.product_id
                 AND o.status = 'ok' AND o.kind = 'part')""", [])
        refresh_main_images(conn, brand_id)
        conn.commit()
        summary = summarize(conn, brand_id)
        stats.update({"seconds": round(time.time() - t0, 1)})
        with conn.cursor() as cur:
            cur.execute("UPDATE import_runs SET finished_at = now(), status = 'ok', stats = %s WHERE id = %s",
                        (Jsonb({**stats, **summary["counts"]}), run_id))
        conn.commit()
        try:                                    # the brand page banner (first one, or its part lost its photo)
            from . import banners
            banners.ensure(conn, brand_id, storage, log)
            conn.commit()
        except Exception as e:                  # noqa: BLE001 - never fail a photo run over the banner
            conn.rollback()
            log(f"  banner not made: {e!r}")
        return dict(stats), summary
    except Exception as e:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("UPDATE import_runs SET finished_at = now(), status = 'failed', error = %s, stats = %s WHERE id = %s",
                        (repr(e), Jsonb(dict(stats)), run_id))
        conn.commit()
        raise


def trim_existing(conn, log=print, storage=None):
    """One time: trim the photos already on the site, from the stored copies (nothing is downloaded again)."""
    storage = storage or get_storage()
    with conn.cursor() as cur:
        cur.execute(f"""SELECT i.id, i.storage_key, p.brand_id FROM product_images i JOIN products p ON p.id = i.product_id
                        WHERE i.status = 'ok' AND i.storage_key IS NOT NULL AND i.storage_key NOT LIKE '%%{PHOTO_VERSION}.jpg'
                        ORDER BY p.brand_id, i.id""")
        rows = cur.fetchall()
    log(f"  {len(rows)} photos to even out")
    done, brands, changed = {}, set(), 0
    for n, (img_id, key, brand_id) in enumerate(rows, 1):
        new_key = key[:-4] + PHOTO_VERSION + ".jpg"
        try:
            if new_key not in done:
                if not storage.exists(new_key):
                    im = Image.open(io.BytesIO(storage.get(key)))
                    im.load()
                    im = trim_white(im)
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=88, optimize=True, progressive=True)
                    storage.put(new_key, buf.getvalue())
                    done[new_key] = im.size
                else:
                    done[new_key] = Image.open(io.BytesIO(storage.get(new_key))).size
            w, h = done[new_key]
            with conn.cursor() as cur:
                cur.execute("UPDATE product_images SET storage_key = %s, width = %s, height = %s WHERE id = %s",
                            (new_key, w, h, img_id))
            brands.add(brand_id)
            changed += 1
        except Exception as e:      # noqa: BLE001 - a missing or unreadable file keeps its old photo
            log(f"  skipped {key}: {e!r}"[:200])
        if n % 500 == 0:
            conn.commit()
            log(f"    {n}/{len(rows)}")
    conn.commit()
    for b in sorted(brands):         # cards point at the trimmed main photo
        refresh_main_images(conn, b)
    conn.commit()
    log(f"  done: {changed} photos evened out across {len(brands)} brands")
    return changed


def refresh_main_images(conn, brand_id):
    """Set products.main_image to the best photo: a part photo before truck/box shots, then feed order."""
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE products p SET main_image = m.storage_key
            FROM (SELECT p2.id, (SELECT i.storage_key FROM product_images i
                                 WHERE i.product_id = p2.id AND i.status = 'ok'
                                 ORDER BY (i.kind = 'part') DESC, i.sort, i.id LIMIT 1) AS storage_key
                  FROM products p2 WHERE p2.brand_id = %s) m
            WHERE p.id = m.id AND p.main_image IS DISTINCT FROM m.storage_key""", (brand_id,))
        return cur.rowcount


def summarize(conn, brand_id):
    """Photo status for every active product, plus files shared suspiciously widely (possible logos)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.part_number, p.short_name, p.part_type, p.price,
                   count(*) FILTER (WHERE i.status = 'ok' AND i.kind = 'part') AS part_photos,
                   count(*) FILTER (WHERE i.status = 'ok' AND i.kind IN ('vehicle', 'packaging')) AS vehicle_photos,
                   count(*) FILTER (WHERE i.status = 'blocked') AS blocked,
                   count(*) FILTER (WHERE i.status = 'failed') AS failed,
                   count(*) FILTER (WHERE i.status = 'pending') AS not_checked,
                   bool_or(i.status = 'ok' AND i.asset_type = 'WEB') AS from_web,
                   min(i.source_url) FILTER (WHERE i.asset_type <> 'WEB') AS first_link
            FROM products p LEFT JOIN product_images i ON i.product_id = p.id
            WHERE p.brand_id = %s AND p.status = 'active'
            GROUP BY p.id ORDER BY p.part_number""", (brand_id,))
        cols = [c.name for c in cur.description]
        products = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.execute("""
            SELECT i.md5, count(DISTINCT i.product_id) AS products, count(DISTINCT p.part_type) AS part_types,
                   min(i.storage_key) AS storage_key
            FROM product_images i JOIN products p ON p.id = i.product_id
            WHERE p.brand_id = %s AND i.status = 'ok' AND i.md5 IS NOT NULL
            GROUP BY i.md5 HAVING count(DISTINCT i.product_id) >= 5 AND (count(DISTINCT p.part_type) >= 3
                   OR (count(DISTINCT i.product_id) >= 50 AND count(DISTINCT p.part_type) >= 2) OR count(DISTINCT i.product_id) >= 200)
            ORDER BY 2 DESC""", (brand_id,))
        suspicious = [dict(zip([c.name for c in cur.description], r)) for r in cur.fetchall()]
    for p in products:
        if p["part_photos"]:
            p["photo_status"] = "Part photo (from the web)" if p["from_web"] and p["part_photos"] == 1 else "Part photo"
        elif p["vehicle_photos"]:
            p["photo_status"] = "Vehicle or box photo only"
        elif p["not_checked"]:
            p["photo_status"] = "Not checked yet"
        else:
            p["photo_status"] = "No usable photo"
    counts = collections.Counter(p["photo_status"] for p in products)
    return {"products": products, "suspicious": suspicious, "counts": dict(counts)}
