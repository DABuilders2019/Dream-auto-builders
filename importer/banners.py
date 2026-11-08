"""Brand page banners: the brand's best catalog photo, cut out of its white background, and a short summary.

Every brand with photos gets one automatically (after a photo download, and by a background pass in the store
that catches anything new). On the admin page a different photo can be picked or one uploaded, and the summary
edited; those choices are kept.

The photo is chosen by scoring the brand's priciest parts that have a photo (complete kits and systems first)
for: a clean white background, sharp and big, plenty of detail, the part filling the frame well.
"""
import hashlib
import io
import json
import logging
import pathlib
import re

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

from .storage import get_storage

log = logging.getLogger("banners")

CONFIG = pathlib.Path(__file__).resolve().parent.parent / "config" / "brand_blurbs.json"
POOL = 160          # parts looked at per brand (priciest first)
VERSION = 2         # bump to rebuild every automatic banner


# ------------------------------------------------------------------ reading photos

def read(storage, key):
    if hasattr(storage, "root"):
        return (storage.root / key).read_bytes()
    obj = storage.client.get_object(Bucket=storage.bucket, Key=key)
    return obj["Body"].read()


def _min_channel(im):
    r, g, b = im.split()
    return ImageChops.darker(r, ImageChops.darker(g, b))


def _frac(mask):
    """Share of 255 pixels in a 0/255 L image."""
    h = mask.histogram()
    return h[255] / max(1, sum(h))


def score(raw):
    """(score, info) for one photo, or None when it's too small to use."""
    im = Image.open(io.BytesIO(raw))
    if im.format == "JPEG":
        im.draft("RGB", (600, 600))
    im = im.convert("RGB")
    w, h = im.size
    if min(w, h) < 450:
        return None
    small = im.copy()
    small.thumbnail((240, 240))
    sw, sh = small.size
    mn = _min_channel(small)
    white = mn.point(lambda v: 255 if v > 235 else 0)
    k = 3
    border = sum(_frac(white.crop(b)) for b in [(0, 0, sw, k), (0, sh - k, sw, sh), (0, 0, k, sh), (sw - k, 0, sw, sh)]) / 4
    fg = mn.point(lambda v: 255 if v < 230 else 0)
    fill = _frac(fg)
    box = fg.getbbox()
    if not box:
        return None
    bw, bh = (box[2] - box[0]) / sw, (box[3] - box[1]) / sh
    edges = small.convert("L").filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0)
    detail = _frac(edges)
    mx = ImageChops.lighter(*small.split()[:2])
    mx = ImageChops.lighter(mx, small.split()[2])
    sat = ImageStat.Stat(ImageChops.subtract(mx, mn), mask=fg).mean[0] / 255
    contrast = ImageStat.Stat(small.convert("L"), mask=fg).stddev[0] / 80     # dark and bright parts read well on red
    s = (3 * border + (1 - abs(fill - 0.4) * 2) + min(detail * 8, 1) + 0.5 * sat + min(contrast, 1.2)
         + (0.5 if w >= h else 0) + min(w, 1600) / 1600 - (1 if bw > 0.98 or bh > 0.98 else 0))
    return round(s, 3), {"w": w, "h": h, "white": round(border, 2), "fill": round(fill, 2), "contrast": round(contrast, 2)}


def cutout(raw):
    """WebP image with the white background (connected to the edges) made transparent and trimmed.
    Returns (png bytes, boxed): boxed=True when the photo has no clean white background to remove."""
    im = Image.open(io.BytesIO(raw))
    if im.format == "JPEG":
        im.draft("RGB", (1400, 1400))
    im = im.convert("RGB")
    im.thumbnail((1200, 1200))
    w, h = im.size
    mn = _min_channel(im)
    spread = ImageChops.subtract(ImageChops.lighter(ImageChops.lighter(*im.split()[:2]), im.split()[2]), mn)
    near = ImageChops.multiply(mn.point(lambda v: 255 if v > 238 else 0), spread.point(lambda v: 255 if v < 14 else 0))
    # flood the near-white area that touches the edges (white inside the part, like a chrome highlight, stays)
    step = max(4, min(w, h) // 120)
    seeds = ([(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
             + [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)])
    for xy in seeds:
        if near.getpixel(xy) == 255:
            ImageDraw.floodfill(near, xy, 128)
    bg = near.point(lambda v: 255 if v == 128 else 0)
    boxed = _frac(bg) < 0.15
    if boxed:
        alpha = Image.new("L", im.size, 255)
    else:
        alpha = ImageChops.invert(bg).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    out = im.convert("RGBA")
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 10 else 0).getbbox()
    if box:
        out = out.crop(box)
    buf = io.BytesIO()
    out.save(buf, "WEBP", quality=86, method=5)     # keeps the transparency at a fraction of a PNG's size
    return buf.getvalue(), boxed


# ------------------------------------------------------------------ choosing

def candidates(conn, brand_id, storage=None, limit=8):
    """The brand's best banner photos, best first: [{"product_id", "key", "title", "score"}]."""
    storage = storage or get_storage()
    rows = conn.execute("""SELECT id, main_image, title FROM products
                           WHERE brand_id = %s AND status = 'active' AND price IS NOT NULL AND main_image IS NOT NULL
                           ORDER BY price DESC, id LIMIT %s""", (brand_id, POOL)).fetchall()
    seen, out, looks = set(), [], []
    for rank, (pid, key, title) in enumerate(rows):
        if key in seen:
            continue
        seen.add(key)
        try:
            r = score(read(storage, key))
        except Exception:       # noqa: BLE001 - a missing or unreadable photo just isn't a candidate
            continue
        if r and r[1] not in looks:            # the same photo filed under another part number
            looks.append(r[1])
            out.append({"product_id": pid, "key": key, "title": title,
                        "score": r[0] + 0.6 * (1 - rank / max(1, len(rows)))})   # pricier parts (kits) win ties
    out.sort(key=lambda c: -c["score"])
    return out[:limit]


def make(conn, brand_id, product_id=None, raw=None, source="auto", storage=None):
    """Build and save a brand's banner picture: from a product's photo (the best one when product_id is None),
    or from uploaded image bytes (raw). Returns the storage key, or None when the brand has no usable photo."""
    storage = storage or get_storage()
    if raw is None:
        if product_id is None:
            best = candidates(conn, brand_id, storage, limit=1)
            if not best:
                return None
            product_id, key = best[0]["product_id"], best[0]["key"]
        else:
            r = conn.execute("SELECT main_image FROM products WHERE id = %s AND brand_id = %s", (product_id, brand_id)).fetchone()
            if not r or not r[0]:
                raise ValueError("that part has no photo")
            key = r[0]
        raw = read(storage, key)
    else:
        product_id = None
    pic, boxed = cutout(raw)
    out_key = f"banners/{brand_id}-{hashlib.md5(pic).hexdigest()[:10]}.webp"
    storage.put(out_key, pic, "image/webp")
    old = conn.execute("SELECT banner_key FROM brands WHERE id = %s", (brand_id,)).fetchone()
    if old and old[0] and old[0] != out_key and hasattr(storage, "root"):
        (storage.root / old[0]).unlink(missing_ok=True)     # the replaced banner picture
    conn.execute("""UPDATE brands SET banner_key = %s, banner_product_id = %s, banner_src = %s, banner_boxed = %s,
                    banner_version = %s, banner_at = now() WHERE id = %s""",
                 (out_key, product_id, source, boxed, VERSION, brand_id))
    return out_key


def needs_banner(conn):
    """Brands with photos whose banner is missing or out of date: none yet, its part is gone or lost its photo,
    or an automatic one made by an older version."""
    return [r[0] for r in conn.execute("""
        SELECT b.id FROM brands b
        WHERE b.active AND EXISTS (SELECT 1 FROM products p WHERE p.brand_id = b.id AND p.status = 'active'
                                   AND p.price IS NOT NULL AND p.main_image IS NOT NULL)
          AND (b.banner_key IS NULL
               OR (b.banner_src <> 'upload' AND NOT EXISTS (SELECT 1 FROM products p WHERE p.id = b.banner_product_id
                                                            AND p.status = 'active' AND p.main_image IS NOT NULL))
               OR (b.banner_src = 'auto' AND b.banner_version IS DISTINCT FROM %s))
        ORDER BY b.name""", (VERSION,)).fetchall()]


def ensure(conn, brand_id=None, storage=None, log=log.info):
    """Make banners for the brands that need one (just brand_id, when given). Returns how many were made."""
    made = 0
    for b in needs_banner(conn):
        if brand_id and b != brand_id:
            continue
        try:
            if make(conn, b, storage=storage):
                made += 1
                log(f"  banner made for {b}")
        except Exception as e:      # noqa: BLE001 - one brand's bad photo shouldn't stop the others
            log(f"  banner for {b} not made: {e!r}")
    return made


# ------------------------------------------------------------------ the summary text

def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


_blurbs = {"mtime": None, "data": {}}


def written_blurbs():
    try:
        m = CONFIG.stat().st_mtime
        if m != _blurbs["mtime"]:
            data = json.loads(CONFIG.read_text())
            _blurbs.update(mtime=m, data={norm(k): v for k, v in data.get("brands", {}).items()})
    except (OSError, ValueError):
        pass
    return _blurbs["data"]


def written_blurb(brand_id, name):
    """The summary in config/brand_blurbs.json for this brand: same name, or the longest listed name the brand's
    name starts with ("Lucas Oil" for "Lucas Oil Products", "Crown Automotive" for "Crown Automotive Jeep Replacement")."""
    data = written_blurbs()
    keys = [norm(name), norm(brand_id)]
    for k in keys:
        if k in data:
            return data[k]
    best = max((d for d in data for k in keys if len(d) >= 3 and k.startswith(d)), key=len, default=None)
    return data[best] if best else None


def _join(xs):
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1] if xs else ""


def default_blurb(conn, brand_id, name):
    """Our written summary for the brand, or one put together from what the brand's parts are."""
    text = written_blurb(brand_id, name)
    if text:
        return text
    cats = [r[0] for r in conn.execute("""
        SELECT c.name FROM products p JOIN categories c ON c.id = p.category_id
        WHERE p.brand_id = %s AND p.status = 'active' AND c.id <> 'uncategorized'
        GROUP BY c.name ORDER BY count(*) DESC LIMIT 3""", (brand_id,)).fetchall()]
    if not cats:
        return f"Shop genuine {name} parts at Dream Auto Builders, with fitment checked for your vehicle."
    return (f"{name} makes {_join([c.lower() if not c.isupper() else c for c in cats])} for cars, trucks and Jeeps. "
            f"Shop genuine {name} parts at Dream Auto Builders, with fitment checked for your vehicle.")


def for_page(conn, brand_id, storage=None):
    """What the brand page needs: {"name", "logo", "image", "boxed", "blurb", "parts"} or None."""
    r = conn.execute("""SELECT b.name, b.banner_key, b.banner_boxed, b.banner_blurb,
                               (SELECT count(*) FROM products p WHERE p.brand_id = b.id AND p.status = 'active' AND p.price IS NOT NULL)
                        FROM brands b WHERE b.id = %s""", (brand_id,)).fetchone()
    if not r:
        return None
    name, key, boxed, blurb, parts = r
    storage = storage or get_storage()
    return {"name": name, "image": storage.url(key) if key else None, "boxed": bool(boxed),
            "blurb": (blurb or "").strip() or default_blurb(conn, brand_id, name), "parts": parts}
