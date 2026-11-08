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
VERSION = 3         # bump to rebuild every automatic banner


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


def _flood_from_edges(mask):
    """mask: L image, 255 where the pixel could be background. Returns an L image, 255 where it's background
    connected to the picture's edges (so white inside a part, like a chrome highlight, isn't removed)."""
    m = mask.copy()
    w, h = m.size
    step = max(2, min(w, h) // 150)
    seeds = ([(x, 0) for x in range(0, w, step)] + [(x, h - 1) for x in range(0, w, step)]
             + [(0, y) for y in range(0, h, step)] + [(w - 1, y) for y in range(0, h, step)])
    for xy in seeds:
        if m.getpixel(xy) == 255:
            ImageDraw.floodfill(m, xy, 128)
    return m.point(lambda v: 255 if v == 128 else 0)


def _soft_cut(rgb, bg_color, tol, band=4, alpha=None, wide=0):
    """Remove the background colour bg_color around the subject, with soft, de-haloed edges.
    rgb: RGB image. Returns (RGBA image, share of the picture that was background)."""
    import numpy as np
    a = np.asarray(rgb).astype(np.float32)
    bgc = np.array(bg_color, np.float32)
    dist = np.abs(a - bgc).max(axis=2)
    near = Image.fromarray(((dist < tol) * 255).astype(np.uint8))
    bg = np.asarray(_flood_from_edges(near)) > 0
    ring = np.asarray(Image.fromarray((bg * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(band * 2 + 1))) > 0
    edge = ring & ~bg
    al = np.ones(dist.shape, np.float32) if alpha is None else alpha.astype(np.float32) / 255
    al[bg] = 0
    soft = np.clip((dist - tol * 0.4) / (tol * 2.2), 0, 1)
    al[edge] = np.minimum(al[edge], soft[edge])
    shadow = np.zeros(bg.shape, bool)
    if wide:
        # a soft grey shadow on the photo's floor becomes a see-through shadow (so it fades out and shows the banner
        # through it) instead of a grey blob with a hard edge: smooth, grey, darker than the background, and
        # reached from the background without crossing an edge (so a chrome tube next to it isn't touched)
        lum = a @ np.array([0.299, 0.587, 0.114], np.float32)
        bgl = float(bgc @ np.array([0.299, 0.587, 0.114], np.float32))
        texture = np.asarray(Image.fromarray(np.clip(lum, 0, 255).astype(np.uint8)).filter(ImageFilter.FIND_EDGES)
                             .filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.BoxBlur(3))).astype(np.float32)
        maybe = (~bg & ((a.max(axis=2) - a.min(axis=2)) < 14) & (texture < 7) & (lum < bgl - 2) & (lum > bgl * 0.45)) | bg
        flood = np.asarray(_flood_from_edges(Image.fromarray((maybe * 255).astype(np.uint8)))) > 0
        shadow = flood & ~bg
        if shadow.mean() < 0.35:                    # (a whole grey photo isn't a shadow)
            al[shadow] = np.clip(1 - lum[shadow] / bgl, 0, 0.6)
            a[shadow] = 0
        else:
            shadow[:] = False
    # take the background colour back out of the see-through edge pixels, so no light halo is left
    k = edge & (al > 0.02) & ~shadow
    a[k] = np.clip((a[k] - (1 - al[k, None]) * bgc) / al[k, None], 0, 255)
    out = np.dstack([a, al * 255]).round().astype(np.uint8)
    return Image.fromarray(out, "RGBA"), float(bg.mean())


def cutout(raw):
    """WebP image with the photo's background (the light colour around its edges) made transparent and trimmed.
    Returns (bytes, boxed): boxed=True when the photo has no clean light background to remove."""
    im = Image.open(io.BytesIO(raw))
    if im.format == "JPEG":
        im.draft("RGB", (1400, 1400))
    im = im.convert("RGB")
    im.thumbnail((1200, 1200))
    w, h = im.size
    k = max(2, min(w, h) // 100)
    strips = [im.crop(b) for b in [(0, 0, w, k), (0, h - k, w, h), (0, 0, k, h), (w - k, 0, w, h)]]
    px = [p for st in strips for p in st.getdata()]
    px.sort(key=sum)
    bgc = px[len(px) // 2]                                      # the typical edge colour
    boxed = min(bgc) < 200
    if boxed:
        out = im.convert("RGBA")
    else:
        out, share = _soft_cut(im, bgc, tol=24, wide=max(6, min(w, h) // 45))
        if share < 0.12:                                        # hardly anything removed: keep the photo whole
            out, boxed = im.convert("RGBA"), True
    box = out.split()[-1].point(lambda v: 255 if v > 10 else 0).getbbox()
    if box:
        out = out.crop(box)
    buf = io.BytesIO()
    out.save(buf, "WEBP", quality=86, method=5)     # keeps the transparency at a fraction of a PNG's size
    return buf.getvalue(), boxed


BANNER_RED = (138, 15, 21)       # the red behind the logo


def _rel_lum(rgb):
    """WCAG relative luminance of an array of RGB values (0-255)."""
    import numpy as np
    c = rgb.astype(np.float32) / 255
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722], np.float32)


def _clear_counters(im, strong=False):
    """Make the white inside letters see-through (the holes in O, B, D, A, P, R...), so the banner shows through them
    like it does around the logo. A white area inside the logo is cleared only when it looks like a letter's hole:
    small next to the logo, a compact shape with no hole of its own, and ringed by a letter-sized shape of any colour.
    White lettering on a coloured or black panel, and big white parts of a badge, are left alone.
    strong=True (the brand was set to See-through on Admin > Logos): also clears larger and oddly shaped white bits
    inside letters."""
    import numpy as np
    a = np.asarray(im).astype(np.float32)
    al = a[..., 3]
    rgb = a[..., :3]
    h, w = al.shape
    box = Image.fromarray(((al > 40) * 255).astype(np.uint8)).getbbox()
    total = float((box[2] - box[0]) * (box[3] - box[1])) if box else 1.0      # the logo's size
    mn = rgb.min(axis=2)
    sat = rgb.max(axis=2) - mn
    whiteish = (al > 200) & (mn > (205 if strong else 222)) & (sat < 26)
    ink = (al > 120) & ~((mn > 190) & (sat < 40))               # the letters: any colour that isn't white or near it
    work = Image.fromarray((whiteish * 255).astype(np.uint8)).copy()
    ink_img = Image.fromarray((ink * 255).astype(np.uint8)).copy()
    max_size, min_solid, panel = (0.15, 0.22, 25) if strong else (0.10, 0.45, 10)
    clear = np.zeros(al.shape, bool)
    ys, xs = np.nonzero(whiteish)
    for y, x in zip(ys[::5], xs[::5]):
        if work.getpixel((int(x), int(y))) != 255:
            continue
        ImageDraw.floodfill(work, (int(x), int(y)), 128)
        arr = np.asarray(work)
        region = arr == 128
        work = Image.fromarray(np.where(region, 0, arr).astype(np.uint8)).copy()
        n = int(region.sum())
        if n < 10 or n > max_size * total:
            continue
        ry, rx = np.nonzero(region)
        y0, y1, x0, x1 = ry.min(), ry.max(), rx.min(), rx.max()
        if y0 == 0 or x0 == 0 or y1 == h - 1 or x1 == w - 1:
            continue
        if n / ((y1 - y0 + 1) * (x1 - x0 + 1)) < min_solid:    # thin, branching shapes are letters, not holes
            continue
        # a hole of its own (an O or D written in white) means it's lettering
        sub = region[y0:y1 + 1, x0:x1 + 1]
        outside = Image.fromarray(np.pad(~sub, 1, constant_values=True).astype(np.uint8) * 255).copy()
        ImageDraw.floodfill(outside, (0, 0), 128)
        if (np.asarray(outside) == 255).any():
            continue
        ring = np.asarray(Image.fromarray((region * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
        ring &= ~region & (al > 40)
        if not ring.any() or ink[ring].mean() <= 0.55:
            continue
        # the shape around it must be letter-sized: white lettering sits on a big panel instead
        ry2, rx2 = np.nonzero(ring & ink)
        dm = ink_img.copy()
        ImageDraw.floodfill(dm, (int(rx2[0]), int(ry2[0])), 128)
        if (np.asarray(dm) == 128).sum() > panel * n:
            continue
        clear |= region
    if not clear.any():
        return im
    # soften the edge: the letter's anti-aliased edge pixels next to a cleared hole lose their white
    grow = np.asarray(Image.fromarray((clear * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    edge = grow & ~clear & (al > 0) & ~ink
    edge |= grow & ~clear & (al > 0) & (mn > 120)
    out = a.copy()
    out[clear, 3] = 0
    t = np.clip((mn[edge] - 110) / 145, 0, 0.92)                # how much of the pixel was the white hole
    out[edge, 3] = al[edge] * (1 - t)
    keep = edge & (out[..., 3] > 1)
    tk = t[keep[edge]][:, None]
    out[keep, :3] = np.clip((rgb[keep] - tk * 255) / (1 - tk), 0, 255)
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def _clear_points(im, points):
    """Make specific white spots see-through: points are (x, y) as fractions of the logo's width and height; the
    white area at each point is cleared (for a hole the automatic check misses, like Oracle's A)."""
    import numpy as np
    a = np.asarray(im).astype(np.float32)
    al, rgb = a[..., 3], a[..., :3]
    h, w = al.shape
    whiteish = (al > 150) & (rgb.min(axis=2) > 200) & ((rgb.max(axis=2) - rgb.min(axis=2)) < 30)
    work = Image.fromarray((whiteish * 255).astype(np.uint8)).copy()
    clear = np.zeros(al.shape, bool)
    for fx, fy in points:
        x, y = int(fx * (w - 1)), int(fy * (h - 1))
        if work.getpixel((x, y)) != 255:
            continue
        ImageDraw.floodfill(work, (x, y), 128)
        region = np.asarray(work) == 128
        if region.sum() < 0.08 * h * w:                     # never a big part of the logo
            clear |= region
    if not clear.any():
        return im
    grow = np.asarray(Image.fromarray((clear * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 0
    edge = grow & ~clear & (al > 0) & (rgb.min(axis=2) > 120)
    out = a.copy()
    out[clear, 3] = 0
    t = np.clip((rgb.min(axis=2)[edge] - 110) / 145, 0, 0.92)
    out[edge, 3] = al[edge] * (1 - t)
    k = edge & (out[..., 3] > 1)
    tk = t[k[edge]][:, None]
    out[k, :3] = np.clip((rgb[k] - tk * 255) / (1 - tk), 0, 255)
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def _fill_inside(im):
    """Fill the inside of a badge-shaped logo with white (for a logo like CARR's, whose middle is see-through in the
    file or would otherwise lose its white): everything the logo's outline encloses, closing tiny gaps in it."""
    import numpy as np
    a = np.asarray(im).astype(np.float32)
    al = a[..., 3]
    h, w = al.shape
    solid = Image.fromarray(((al > 60) * 255).astype(np.uint8))
    grown = solid.filter(ImageFilter.MaxFilter(11))             # close small gaps in the outline
    pad = Image.new("L", (w + 2, h + 2), 0)
    pad.paste(grown, (1, 1))
    ImageDraw.floodfill(pad, (0, 0), 128)
    inside = (np.asarray(pad)[1:-1, 1:-1] == 0)                 # not reached from outside, and not the outline
    inside = np.asarray(Image.fromarray((inside * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(11))) > 0
    inside &= al < 250
    if inside.sum() < 0.02 * h * w:
        return im
    out = a.copy()
    # white under whatever is there (keeps the drawing's edges smooth)
    t = out[inside, 3:4] / 255
    out[inside, :3] = out[inside, :3] * t + 255 * (1 - t)
    out[inside, 3] = 255
    return Image.fromarray(out.round().astype(np.uint8), "RGBA")


def banner_logo(raw, darken_grey=False, keep_holes=False, strong=False, clear_points=(), fill_inside=False):
    """The brand logo for the banner, in its own colours: only the plain white around it is removed (white inside
    the logo stays). Returns (PNG bytes, outline): outline=True when much of the logo's outer edge has too little
    contrast with the red (black, dark or red lettering), so the page gives it a soft light outline.
    darken_grey: turn light/mid grey lettering on the open background near-black (set per brand in config)."""
    import numpy as np
    im = Image.open(io.BytesIO(raw))
    im.load()
    im = im.convert("RGBA")
    im.thumbnail((900, 360))
    if np.asarray(im.split()[-1]).min() > 250:                  # no transparency: take the white around it out
        im, _ = _soft_cut(im.convert("RGB"), (255, 255, 255), tol=26, band=2)
    if fill_inside:
        im = _fill_inside(im)                                   # a badge whose middle should be white
    if not keep_holes:
        im = _clear_counters(im, strong=strong)                 # and the white inside letters (O, B, D, A...)
    if clear_points:
        box = im.split()[-1].point(lambda v: 255 if v > 10 else 0).getbbox()
        im = _clear_points(im.crop(box) if box else im, clear_points)
    a = np.asarray(im).astype(np.int16)
    al = a[..., 3]
    sat = a[..., :3].max(axis=2) - a[..., :3].min(axis=2)
    lum = (a[..., :3] * [0.299, 0.587, 0.114]).sum(axis=2)
    opaque = al > 40
    near_out = np.asarray(Image.fromarray(((al < 30) * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(7))) > 0
    if darken_grey:
        grey = opaque & (sat < 40) & (lum > 70) & (lum < 215)
        a = a.copy()
        a[grey, :3] = (a[grey, :3] * 0.22).astype(np.int16)    # keeps the shading, just much darker
        im = Image.fromarray(a.astype(np.uint8), "RGBA")
    # contrast of the logo's outer edge against the banner red (WCAG ratio; under ~2.2 is hard to read)
    edge = opaque & near_out
    red_l = float(_rel_lum(np.array(BANNER_RED)))
    pl = _rel_lum(a[..., :3][edge])
    ratio = (np.maximum(pl, red_l) + 0.05) / (np.minimum(pl, red_l) + 0.05)
    low = float((ratio < 2.2).mean()) if ratio.size else 0.0
    box = im.split()[-1].point(lambda v: 255 if v > 10 else 0).getbbox()
    if box:
        im = im.crop(box)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue(), bool(low > 0.2)


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
    if not best:        # a shorter name for a listed brand ("Coker" for "Coker Tire")
        best = min((d for d in data for k in keys if len(k) >= 4 and d.startswith(k)), key=len, default=None)
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


STATIC = pathlib.Path(__file__).resolve().parent.parent / "store" / "static"
LOGO_VERSION = 6


def _tweaks(brand_id, brand_name=None):
    """Per-brand banner logo adjustments from config/home.json "banner_logo_tweaks": {"<brand id or name>": [...]}.
    "darken_grey": grey lettering printed dark; "keep_holes": leave the white inside letters as it is;
    "strong": clear letter holes thoroughly; "fill_inside": fill a badge's inside with white."""
    try:
        cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "config" / "home.json").read_text())
    except (OSError, ValueError):
        return set()
    t = {norm(k): set(x for x in v if isinstance(x, str)) for k, v in (cfg.get("banner_logo_tweaks") or {}).items()}
    return _match(t, brand_id, brand_name) or set()


def _match(table, brand_id, brand_name):
    """A per-brand setting: by brand id or name, or the longest listed name the brand's name starts with
    ("carr" for "CARR Running Boards")."""
    keys = [norm(brand_id), norm(brand_name)]
    for k in keys:
        if k and k in table:
            return table[k]
    best = max((k2 for k2 in table for k in keys if k and len(k2) >= 4 and k.startswith(k2)), key=len, default=None)
    return table[best] if best else None


def _points(brand_id, brand_name=None):
    """Spots to clear for a brand, from config/home.json "banner_logo_clear": {"<brand>": [[x, y], ...]}."""
    try:
        cfg = json.loads((pathlib.Path(__file__).resolve().parent.parent / "config" / "home.json").read_text())
    except (OSError, ValueError):
        return []
    p = {norm(k): v for k, v in (cfg.get("banner_logo_clear") or {}).items()}
    return [tuple(x) for x in (_match(p, brand_id, brand_name) or []) if len(x) == 2]


def logo_for(conn, brand_id, logo_url, storage=None):
    """URL of the brand's logo prepared for the banner, made from logo_url (the logo shown everywhere else) the
    first time and again whenever that logo changes. None when there's no logo or it can't be read."""
    storage = storage or get_storage()
    r = conn.execute("""SELECT banner_logo_key, banner_logo_from, logo_key, name, banner_keep_holes, banner_logo_custom_key
                        FROM brands WHERE id = %s""", (brand_id,)).fetchone()
    if not r:
        return None
    if r[5]:                                # uploaded on Admin > Logos: used exactly as it is
        return storage.url(r[5])
    if not logo_url:
        return None
    tw = _tweaks(brand_id, r[3])
    if r[4] is not None:                    # chosen on Admin > Logos: Keep white, or See-through (thorough)
        tw = (tw | {"keep_holes"}) - {"strong"} if r[4] else (tw - {"keep_holes"}) | {"strong"}
    pts = _points(brand_id, r[3])
    src = f"{logo_url}#v{LOGO_VERSION}" + "".join(f"+{x}" for x in sorted(tw)) + (f"+p{pts}" if pts else "")
    if r[0] and r[1] == src:
        return storage.url(r[0])
    try:
        if logo_url.startswith("/static/"):
            raw = (STATIC / logo_url[len("/static/"):].split("?")[0]).read_bytes()
        elif r[2]:
            raw = read(storage, r[2])
        else:
            return None
        png, dark = banner_logo(raw, darken_grey="darken_grey" in tw, keep_holes="keep_holes" in tw, strong="strong" in tw,
                                clear_points=pts, fill_inside="fill_inside" in tw)
    except Exception:       # noqa: BLE001 - fall back to the plain logo
        log.warning("banner logo for %s not made", brand_id, exc_info=True)
        return logo_url
    key = f"banners/{brand_id}-logo{'-d' if dark else ''}-{hashlib.md5(png).hexdigest()[:10]}.png"
    storage.put(key, png, "image/png")
    if r[0] and r[0] != key and hasattr(storage, "root"):
        (storage.root / r[0]).unlink(missing_ok=True)
    conn.execute("UPDATE brands SET banner_logo_key = %s, banner_logo_from = %s WHERE id = %s", (key, src, brand_id))
    return storage.url(key)


def for_page(conn, brand_id, storage=None, logo_url=None):
    """What the brand page needs: {"name", "logo", "image", "boxed", "blurb", "parts"} or None."""
    r = conn.execute("""SELECT b.name, b.banner_key, b.banner_boxed, b.banner_blurb,
                               (SELECT count(*) FROM products p WHERE p.brand_id = b.id AND p.status = 'active' AND p.price IS NOT NULL)
                        FROM brands b WHERE b.id = %s""", (brand_id,)).fetchone()
    if not r:
        return None
    name, key, boxed, blurb, parts = r
    storage = storage or get_storage()
    return {"name": name, "image": storage.url(key) if key else None, "boxed": bool(boxed),
            "logo": (lg := logo_for(conn, brand_id, logo_url, storage)), "logo_dark": bool(lg and "-logo-d-" in lg),
            "blurb": (blurb or "").strip() or default_blurb(conn, brand_id, name), "parts": parts}
