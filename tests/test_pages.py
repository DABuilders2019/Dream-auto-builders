"""Storefront page tests: every page renders from the database, and the garage, cart and filters work."""
import pytest
from fastapi.testclient import TestClient

from importer import load
from conftest import make_pies, make_vehicles
from test_search import FITMENT, ITEMS


@pytest.fixture
def client(conn, tmp_path, monkeypatch):
    load.load_brand(conn, "testbrand", make_pies(tmp_path / "p.xml", ITEMS), None,
                    make_vehicles(tmp_path / "v.xlsx", FITMENT), log=lambda *a: None)
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("SITE_PREVIEW", "1")
    from store.app import create_app
    app = create_app(conn.info.dsn)
    with TestClient(app) as c:     # closes the connection pool on exit
        yield c


def slug(client, pn):
    r = client.get("/api/search", params={"q": pn})
    return next(it["slug"] for it in r.json()["items"] if it["part_number"] == pn)


def vehicle_id(client, year, make, model):
    r = client.get("/api/vehicles/models", params={"year": year, "make": make})
    return next(m["id"] for m in r.json() if m["model"] == model)


def test_pages_render(client):
    for path in ("/", "/search", "/search?q=lift+kit", "/c/suspension", "/c/exhaust", "/brand/testbrand", "/brands",
                 "/cart", "/garage", "/track", "/account", "/info/shipping-returns", "/p/" + slug(client, "LK-100")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Dream Auto Builders" in r.text and "Add a vehicle" in r.text, path
        assert r.headers.get("x-robots-tag") == "noindex, nofollow"
    home = client.get("/").text
    assert "Featured Categories" in home and "Satisfaction Guaranteed" in home and "Featured Brands" in home


def test_not_found_pages(client):
    r = client.get("/p/no-such-part")
    assert r.status_code == 404 and "couldn't find that page" in r.text
    assert client.get("/c/no-such-category").status_code == 404
    assert client.get("/nowhere").status_code == 404
    assert client.get("/api/nowhere").headers["content-type"].startswith("application/json")


def test_part_number_search_opens_the_product(client):
    r = client.get("/search", params={"q": "lk100"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/p/" + slug(client, "LK-100")


def test_search_results_and_filters(client):
    r = client.get("/search", params={"q": "leveling kit"})
    assert "LV-200" in r.text and "LV-201" in r.text and "LK-100" not in r.text
    r = client.get("/c/exhaust", params={"price": "0-100"})
    assert "35116" in r.text and "15001" not in r.text
    r = client.get("/c/exhaust", params={"carb": "1"})
    assert "CAT-1" in r.text and "CAT-2" not in r.text


def test_garage_filters_every_page(client):
    ram = vehicle_id(client, 2020, "Ram", "1500")
    r = client.post("/garage/add", data={"vehicle": ram}, params={"next": "/c/suspension"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/c/suspension"
    page = client.get("/c/suspension").text
    assert "Shopping for" in page and "2020 Ram 1500" in page
    assert "LK-100" in page and "LV-201" not in page          # the Tacoma kit is hidden
    assert "hidden that don" in page
    assert "LV-201" in client.get("/c/suspension", params={"all": "1"}).text
    assert "This part fits your 2020 Ram 1500" in client.get("/p/" + slug(client, "LK-100")).text
    assert "does not fit your 2020 Ram 1500" in client.get("/p/" + slug(client, "LV-201")).text
    assert "Fits by size" in client.get("/p/" + slug(client, "TIRE-1")).text

    client.post("/garage/shop-all")
    assert "Shopping for" not in client.get("/").text
    assert "2020 Ram 1500" in client.get("/garage").text      # still saved in the garage
    client.post("/garage/remove", data={"vehicle": ram})
    assert "2020 Ram 1500" not in client.get("/garage").text


def test_vehicle_typed_in_search_is_added(client):
    r = client.get("/search", params={"q": "2019 silverado 1500 leveling kit"})
    assert "Added your" in r.text and "LV-200" in r.text and "LV-201" not in r.text
    assert "Shopping for" in client.get("/").text


def test_cart(client):
    pid = client.get("/api/search", params={"q": "LK-100"}).json()["items"][0]["id"]
    r = client.post("/cart/add", data={"product_id": pid, "qty": 2}, headers={"Accept": "application/json"})
    assert r.json()["count"] == 2 and r.json()["subtotal"] == pytest.approx(1999.90)
    cart = client.get("/cart").text
    assert "LK-100" in cart and "$1,999.90" in cart and "Free" in cart
    client.post("/cart/update", data={"product_id": pid, "qty": 0})
    assert "Your cart is empty" in client.get("/cart").text
    assert client.post("/cart/add", data={"product_id": 999999}, headers={"Accept": "application/json"}).status_code == 404


def test_redirects_stay_on_this_site(client):
    for bad in ("//evil.example", "https://evil.example", "/\\evil.example"):
        r = client.post("/garage/shop-all", params={"next": bad}, follow_redirects=False)
        assert r.headers["location"] == "/", bad


def test_robots(client):
    assert "Disallow: /" in client.get("/robots.txt").text


def test_builder_page_and_saved_builds(client):
    page = client.get("/build").text
    assert 'id="nestedIFrame"' in page and "interactivegarage.com/dreamauto/" in page and "iFrameResize(" in page
    assert "nestedIFrame" not in client.get("/garage").text           # separate pages
    assert 'href="/build"' in client.get("/").text                     # button in the Add a Vehicle bar

    r = client.get("/build/import", params={"items": "lk100:1, TIRE-1:4, NOPE-9", "year": "2020", "make": "Ram",
                                            "model": "1500", "name": "Trail rig"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/garage#build-")
    g = client.get("/garage").text
    assert "Trail rig" in g and "2020 Ram 1500" in g and "4 × " in g and "NOPE-9" in g
    assert "Shopping for" in g                                          # builder's vehicle selected
    bid = r.headers["location"].split("build-")[1]
    client.post(f"/garage/builds/{bid}/cart")
    cart = client.get("/cart").text
    assert "LK-100" in cart and "TIRE-1" in cart

    r = client.post("/build/import", data={"pn": ["LV-200"], "qty": ["2"], "action": "cart"}, follow_redirects=False)
    assert r.headers["location"] == "/cart" and "LV-200" in client.get("/cart").text
    assert "None of the parts" in client.get("/build/import", params={"items": "XX-1"}).text


def test_home_video_hero(client):
    """The homepage opens with the full-screen video; the header turns see-through over it, other pages keep the plain header."""
    import pathlib
    home = client.get("/").text
    assert 'class="has-hero"' in home and 'id="heroVideo"' in home
    assert 'data-src="/static/video/hero-1080.mp4"' in home and 'data-src-small="/static/video/hero-720.mp4"' in home
    assert 'class="logo-light"' in home and 'id="heroPause"' in home
    assert home.count("<h1") == 1                      # the hero headline replaces the old tagline heading
    assert 'class="has-hero"' not in client.get("/brands").text
    static = pathlib.Path(__file__).resolve().parents[1] / "store" / "static"
    for f in ("video/hero-1080.mp4", "video/hero-720.mp4", "video/hero-poster.jpg", "img/logo-header-light.png"):
        assert (static / f).stat().st_size > 1000, f
    r = client.get("/static/video/hero-720.mp4", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100   # iPhones need byte ranges to play video


def test_video_replaces_slideshow(client):
    home = client.get("/").text
    assert 'id="heroVideo"' in home and 'id="pView"' not in home    # no slideshow under the video
