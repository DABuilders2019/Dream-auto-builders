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
    assert im.mode == "RGBA" and not boxed
    assert im.size[0] < 700 and im.size[1] < 500              # trimmed to the part
    assert im.getpixel((im.size[0] // 2, im.size[1] // 2))[3] == 255
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
