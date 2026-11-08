"""Brand page banners: the photo is chosen and cut out, every brand with photos gets one, and the admin can change it."""
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from importer import banners as bn
from importer import load
from importer.storage import LocalStorage
from conftest import make_pies, make_vehicles
from test_search import FITMENT, ITEMS


def photo(size=(1000, 700), part=(200, 150, 800, 550), color=(40, 40, 40), bg=(255, 255, 255)):
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    d.rectangle(part, fill=color)
    d.ellipse((part[0] + 60, part[1] + 60, part[0] + 260, part[1] + 260), fill=(200, 200, 205))   # some detail
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def test_cutout_removes_white_background():
    pic, boxed = bn.cutout(photo())
    im = Image.open(io.BytesIO(pic))
    assert not boxed
    assert im.size[0] < 700 and im.size[1] < 500              # trimmed to the part
    assert im.convert("RGBA").getpixel((im.size[0] // 2, im.size[1] // 2))[3] == 255
    # a photo with no white background is kept whole (marked boxed)
    _, boxed = bn.cutout(photo(bg=(90, 120, 160)))
    assert boxed


def test_score_prefers_clean_white_background():
    good = bn.score(photo())[0]
    busy = bn.score(photo(bg=(120, 140, 90)))[0]
    assert good > busy
    assert bn.score(photo(size=(300, 200), part=(50, 50, 250, 150))) is None   # too small


def test_written_blurbs_match_longer_brand_names():
    assert bn.written_blurb("", "Lucas Oil Products").startswith("Lucas Oil")
    assert bn.written_blurb("", "Crown Automotive Jeep Replacement").startswith("Crown")
    assert bn.written_blurb("", "Fastway Trailer").startswith("Fastway")
    assert bn.written_blurb("", "Nobody Makes This") is None


@pytest.fixture
def shop(conn, tmp_path, monkeypatch):
    load.load_brand(conn, "testbrand", make_pies(tmp_path / "p.xml", ITEMS), None,
                    make_vehicles(tmp_path / "v.xlsx", FITMENT), log=lambda *a: None)
    store = LocalStorage(tmp_path / "photos")
    ids = [r[0] for r in conn.execute("SELECT id FROM products WHERE brand_id = 'testbrand' AND price IS NOT NULL ORDER BY price DESC").fetchall()]
    for i, pid in enumerate(ids):
        key = f"products/testbrand/{pid}.jpg"
        store.put(key, photo(color=(30 + 40 * i, 30, 30)) if i else photo(bg=(150, 160, 120)))   # priciest has a busy photo
        conn.execute("UPDATE products SET main_image = %s WHERE id = %s", (key, pid))
    conn.commit()
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))
    return conn, store, ids


def test_every_brand_with_photos_gets_a_banner(shop):
    conn, store, ids = shop
    conn.autocommit = True
    assert bn.needs_banner(conn) == ["testbrand"]
    assert bn.ensure(conn, storage=store, log=lambda *a: None) == 1
    key, pid, src = conn.execute("SELECT banner_key, banner_product_id, banner_src FROM brands WHERE id = 'testbrand'").fetchone()
    assert store.exists(key) and src == "auto" and pid != ids[0]     # the busy photo isn't chosen
    assert bn.needs_banner(conn) == []
    # its part is discontinued: a new banner is made
    conn.execute("UPDATE products SET status = 'discontinued' WHERE id = %s", (pid,))
    assert bn.needs_banner(conn) == ["testbrand"]
    bn.ensure(conn, storage=store, log=lambda *a: None)
    assert conn.execute("SELECT banner_product_id FROM brands WHERE id = 'testbrand'").fetchone()[0] != pid
    page = bn.for_page(conn, "testbrand", store)
    assert page["image"].startswith("/photos/banners/") and page["parts"] > 0 and "TestBrand" in page["blurb"]


def test_brand_page_and_admin(shop, monkeypatch):
    conn, store, ids = shop
    conn.autocommit = True
    bn.ensure(conn, storage=store, log=lambda *a: None)
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pw")
    monkeypatch.setenv("SITE_PREVIEW", "1")
    from store.app import create_app
    with TestClient(create_app(conn.info.dsn)) as tc:
        page = tc.get("/brand/testbrand").text
        assert "/photos/banners/testbrand-logo-" in page or "bb-logo" not in page
        assert 'class="bbanner' in page and 'class="bb-name">TestBrand' in page and "/photos/banners/" in page
        tc.post("/admin/login", data={"password": "admin-pw"})
        assert "Page banner" in tc.get("/admin").text
        admin = tc.get("/admin/banner/testbrand").text
        assert "Pick a different photo" in admin and 'name="product_id"' in admin
        # write a summary, pick a photo, upload one
        tc.post("/admin/banner/testbrand/text", data={"blurb": "  Our own   words about TestBrand. "})
        assert "Our own words about TestBrand." in tc.get("/brand/testbrand").text
        tc.post("/admin/banner/testbrand/pick", data={"product_id": str(ids[1])})
        assert conn.execute("SELECT banner_product_id, banner_src FROM brands WHERE id = 'testbrand'").fetchone() == (ids[1], "pick")
        r = tc.post("/admin/banner/testbrand/upload", files={"file": ("b.jpg", photo(), "image/jpeg")})
        assert r.status_code == 200 and conn.execute("SELECT banner_src FROM brands WHERE id = 'testbrand'").fetchone()[0] == "upload"
        r = tc.post("/admin/banner/testbrand/upload", files={"file": ("b.txt", b"not an image", "text/plain")})
        assert "a picture the site can read" in r.text
        # an uploaded picture is kept when its brand's parts change
        conn.execute("UPDATE products SET status = 'discontinued' WHERE id = %s", (ids[1],))
        assert bn.needs_banner(conn) == []
        tc.post("/admin/banner/testbrand/pick", data={"product_id": "auto"})
        assert conn.execute("SELECT banner_src FROM brands WHERE id = 'testbrand'").fetchone()[0] == "auto"
        # Admin > Logos: every brand listed, white-inside choice saved
        page = tc.get("/admin/logos").text
        assert "Banner logos" in page and 'id="b-testbrand"' in page
        tc.post("/admin/logos/testbrand", data={"keep": "keep"})
        assert conn.execute("SELECT banner_keep_holes FROM brands WHERE id = 'testbrand'").fetchone()[0] is True
        tc.post("/admin/logos/testbrand", data={"keep": "clear"})
        assert conn.execute("SELECT banner_keep_holes FROM brands WHERE id = 'testbrand'").fetchone()[0] is False
        # upload a finished logo: used exactly as it is, then back to the site-made one
        tc.post("/admin/logos/testbrand/upload", files={"file": ("l.png", _logo(lambda d: d.rectangle((20, 20, 200, 80), fill=(0, 90, 200))), "image/png")})
        key = conn.execute("SELECT banner_logo_custom_key FROM brands WHERE id = 'testbrand'").fetchone()[0]
        assert key and bn.logo_for(conn, "testbrand", "/static/x.png", store) == store.url(key)
        tc.post("/admin/logos/testbrand/reset")
        assert conn.execute("SELECT banner_logo_custom_key FROM brands WHERE id = 'testbrand'").fetchone()[0] is None


def _logo(draw):
    im = Image.new("RGB", (400, 160), (255, 255, 255))
    draw(ImageDraw.Draw(im))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_logo_keeps_its_own_colours():
    # black lettering next to a yellow shape: the white around it goes, every colour stays as it is
    raw = _logo(lambda d: (d.rectangle((20, 40, 200, 70), fill=(0, 0, 0)), d.rectangle((20, 90, 380, 130), fill=(230, 200, 0))))
    png, dark = bn.banner_logo(raw)
    px = list(Image.open(io.BytesIO(png)).convert("RGBA").getdata())
    assert any(p[3] < 10 for p in px)                                         # background see-through
    assert any(p[3] > 200 and max(p[:3]) < 30 for p in px)                     # black still black
    assert any(p[3] > 200 and p[0] > 200 and p[2] < 60 for p in px)            # yellow still yellow
    assert dark                                                                # gets the light outline on the page
    # a bright logo reads on the red without an outline; a dark blue one gets it
    _, outline = bn.banner_logo(_logo(lambda d: d.rectangle((20, 20, 380, 140), fill=(250, 210, 0))))
    assert not outline
    _, outline = bn.banner_logo(_logo(lambda d: d.rectangle((20, 20, 380, 140), fill=(20, 40, 140))))
    assert outline


def test_grey_lettering_darkened_when_asked():
    raw = _logo(lambda d: (d.rectangle((20, 40, 380, 70), fill=(150, 150, 150)), d.rectangle((20, 90, 380, 130), fill=(230, 200, 0))))
    png, _ = bn.banner_logo(raw, darken_grey=True)
    px = list(Image.open(io.BytesIO(png)).convert("RGBA").getdata())
    assert not any(p[3] > 200 and 100 < p[0] < 200 and abs(p[0] - p[2]) < 10 for p in px)   # no mid grey left
    assert any(p[3] > 200 and p[0] > 200 and p[2] < 60 for p in px)                         # yellow untouched


def test_letter_holes_are_cleared_but_white_lettering_kept():
    # a dark "O" (ring) next to a white word on a black panel
    def draw(d):
        d.ellipse((20, 20, 140, 140), fill=(15, 15, 15))
        d.ellipse((55, 55, 105, 105), fill=(255, 255, 255))          # the O's hole
        d.rectangle((170, 30, 390, 130), fill=(15, 15, 15))
        d.rectangle((200, 60, 215, 100), fill=(255, 255, 255))       # white "I" lettering on the panel
        d.rectangle((240, 60, 300, 75), fill=(255, 255, 255))
        d.rectangle((240, 60, 255, 100), fill=(255, 255, 255))       # white "F"
        d.rectangle((240, 78, 285, 90), fill=(255, 255, 255))
    png, _ = bn.banner_logo(_logo(draw))
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    ox, oy = 80 - 20, 80 - 20                                         # the crop starts at the logo's corner
    assert im.getpixel((ox, oy))[3] < 20                              # hole is see-through
    assert im.getpixel((247 - 20, 95 - 20))[3] > 200                  # the white F stays white
    assert im.getpixel((247 - 20, 95 - 20))[0] > 230


def test_red_and_blue_letter_holes_clear(tmp_path):
    def draw(d):
        d.ellipse((20, 20, 140, 140), fill=(220, 25, 40))            # a red O
        d.ellipse((55, 55, 105, 105), fill=(255, 255, 255))
        d.ellipse((180, 20, 300, 140), fill=(25, 50, 170))           # a blue O
        d.ellipse((215, 55, 265, 105), fill=(255, 255, 255))
    png, _ = bn.banner_logo(_logo(draw))
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    assert im.getpixel((60, 60))[3] < 20 and im.getpixel((220, 60))[3] < 20
