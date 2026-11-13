"""Nearby repair shops from Google Maps: shown for orders with parts that need installing, with the disclaimer and
Google attribution; the customer's pick and survey answers are saved (place IDs only); the survey email goes out
after shipping; the admin Installer leads page ranks shops."""
import html
import json
import time

import httpx
import pytest

from store import installers
from test_checkout import paid_event, pid, post_webhook, shop  # noqa: F401  (fixture)


class FakeGoogle:
    def __init__(self):
        self.calls = []

    def __call__(self, request):
        self.calls.append((request.method, request.url.path, request.headers.get("X-Goog-FieldMask")))
        assert request.headers["X-Goog-Api-Key"] == "gkey"
        if request.url.path == "/v1/places:searchText":
            body = json.loads(request.content)
            assert "78701" in body["textQuery"] and body["includedType"] == "car_repair"
            return httpx.Response(200, json={"places": [
                {"id": "pl_1", "displayName": {"text": "Smith's Off-Road"}, "formattedAddress": "10 Oak St, Austin, TX",
                 "googleMapsUri": "https://maps.google.com/?cid=1"},
                {"id": "pl_2", "displayName": {"text": "Austin Auto Care"}, "formattedAddress": "22 Elm St, Austin, TX",
                 "googleMapsUri": "https://maps.google.com/?cid=2"}]})
        if request.url.path.startswith("/v1/places/"):
            pid_ = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"id": pid_, "displayName": {"text": "Smith's Off-Road"},
                                             "formattedAddress": "10 Oak St, Austin, TX", "googleMapsUri": "https://maps.google.com/?cid=1"})
        return httpx.Response(404)


@pytest.fixture
def google(monkeypatch):
    g = FakeGoogle()
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "gkey")
    monkeypatch.setattr(installers, "_transport", httpx.MockTransport(g))
    return g


def buy(c, shop_, pn, n):
    c.post("/cart/add", data={"product_id": pid(c, pn), "qty": 1})
    c.post("/checkout", follow_redirects=False)
    params = shop_.created[-1]
    post_webhook(c, paid_event(f"cs_test_{n}", int(params["metadata"]["order_id"]), total=100))
    return params["metadata"]["order_number"]


def test_lift_kit_order_lists_nearby_shops_and_saves_pick(shop, google):  # noqa: F811
    c = shop.c
    number = buy(c, shop, "LK-100", 1)
    time.sleep(0.3)
    page = html.unescape(c.get(f"/order/{number}", params={"session_id": "cs_test_1"}).text)
    assert "Need it installed?" in page and "Smith's Off-Road" in page and "10 Oak St, Austin, TX" in page
    assert "not affiliated with" in page and "Google Maps" in page
    # phone numbers are never asked for (the map link has them)
    assert all("phone" not in (fm or "").lower() for _, _, fm in google.calls)

    r = c.post(f"/order/{number}/installer", data={"place_id": "pl_1"}, follow_redirects=True)
    assert "Your shop" in html.unescape(r.text)
    row = shop.conn.execute("SELECT f.place_id, f.picked_at IS NOT NULL FROM install_feedback f JOIN orders o ON o.id = f.order_id WHERE o.number = %s",
                            (number,)).fetchone()
    assert row == ("pl_1", True)
    # nothing about the shop except its place ID is stored
    cols = [r[0] for r in shop.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'install_feedback'")]
    assert not {"name", "address", "shop_name"} & set(cols)


def test_no_key_no_list(shop, monkeypatch):  # noqa: F811
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    c = shop.c
    number = buy(c, shop, "LK-100", 1)
    assert "Need it installed?" not in c.get(f"/order/{number}", params={"session_id": "cs_test_1"}).text


def test_order_page_only_for_the_buyer(shop, google):  # noqa: F811
    c = shop.c
    number = buy(c, shop, "LK-100", 1)
    c.cookies.clear()
    assert c.post(f"/order/{number}/installer", data={"place_id": "pl_1"}).status_code == 404


def test_survey_email_page_and_leads(shop, google, monkeypatch):  # noqa: F811
    c, conn = shop.c, shop.conn
    number = buy(c, shop, "LK-100", 1)
    oid = conn.execute("SELECT id FROM orders WHERE number = %s", (number,)).fetchone()[0]
    conn.execute("UPDATE orders SET status = 'shipped', shipped_at = now() - interval '11 days' WHERE id = %s", (oid,))
    conn.commit()
    shop.emails.clear()
    import psycopg
    with psycopg.connect(conn.info.dsn, autocommit=True) as ac:
        from store.search import Searcher
        from store.app import search_config
        s = Searcher(ac, search_config())
        assert installers.send_due_surveys(ac, s, "https://shop.test") == 1
        assert installers.send_due_surveys(ac, s, "https://shop.test") == 0          # once only
        token = ac.execute("SELECT token FROM install_feedback WHERE order_id = %s", (oid,)).fetchone()[0]
    time.sleep(0.3)
    assert any("How did the install go?" in subj for _, subj in shop.emails)

    c.cookies.clear()                                                                  # the email link works on its own
    page = html.unescape(c.get(f"/install/{token}").text)
    assert "Getting your parts installed" in page and "Smith's Off-Road" in page and "not affiliated with" in page
    r = c.post(f"/install/{token}", data={"installed": "yes", "place_id": "pl_1", "rating": "5", "recommend": "yes",
                                          "comment": "Great work, fast"}, follow_redirects=True)
    assert "Thanks!" in r.text
    fb = conn.execute("SELECT installed, place_id, rating, recommend, comment FROM install_feedback WHERE order_id = %s", (oid,)).fetchone()
    assert fb == ("yes", "pl_1", 5, True, "Great work, fast")

    c.post("/admin/login", data={"password": "pw"})
    admin = html.unescape(c.get("/admin/installers").text)
    assert "Smith's Off-Road" in admin and "5.0 ★" in admin and "1 of 1" in admin and "Great work, fast" in admin


def test_email_pick_link(shop, google):  # noqa: F811
    c, conn = shop.c, shop.conn
    number = buy(c, shop, "LK-100", 1)
    oid = conn.execute("SELECT id FROM orders WHERE number = %s", (number,)).fetchone()[0]
    c.get(f"/order/{number}", params={"session_id": "cs_test_1"})
    token = conn.execute("SELECT token FROM install_feedback WHERE order_id = %s", (oid,)).fetchone()[0]
    c.cookies.clear()
    c.get(f"/install/{token}", params={"pick": "pl_2"})
    assert conn.execute("SELECT place_id FROM install_feedback WHERE order_id = %s", (oid,)).fetchone()[0] == "pl_2"
    assert c.get("/install/not-a-token").status_code == 404


def test_shops_show_distance_open_first_and_closed_with_reason(shop, monkeypatch):  # noqa: F811
    """Each shop shows its distance from the shipping ZIP; open shops come first, nearest first; a temporarily closed
    shop is listed last with the reason and can't be picked; permanently closed ones are left out."""
    def fake(request):
        if request.url.path == "/v1/places:searchText":
            body = json.loads(request.content)
            assert "locationBias" in body                               # results kept around the shipping address
            assert "places.location" in request.headers["X-Goog-FieldMask"]
            return httpx.Response(200, json={"places": [
                {"id": "far", "displayName": {"text": "Far Garage"}, "formattedAddress": "Round Rock, TX",
                 "location": {"latitude": 30.5083, "longitude": -97.6789}, "businessStatus": "OPERATIONAL"},
                {"id": "shut", "displayName": {"text": "Shut Shop"}, "formattedAddress": "Austin, TX",
                 "location": {"latitude": 30.27, "longitude": -97.74}, "businessStatus": "CLOSED_TEMPORARILY"},
                {"id": "gone", "displayName": {"text": "Gone Garage"}, "formattedAddress": "Austin, TX",
                 "location": {"latitude": 30.27, "longitude": -97.74}, "businessStatus": "CLOSED_PERMANENTLY"},
                {"id": "near", "displayName": {"text": "Near Garage"}, "formattedAddress": "Austin, TX", "rating": 4.7, "userRatingCount": 1212,
                 "location": {"latitude": 30.2711, "longitude": -97.7437}, "businessStatus": "OPERATIONAL"}]})
        return httpx.Response(404)
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "gkey")
    monkeypatch.setattr(installers, "_transport", httpx.MockTransport(fake))
    installers.zip_point.cache_clear()
    shops = installers.nearby({"ship_address": {"postal_code": "78701", "city": "Austin", "state": "TX"}})
    assert [x["id"] for x in shops] == ["near", "far", "shut"]
    assert shops[0]["miles"] < 2 and 15 < shops[1]["miles"] < 25
    assert shops[2]["closed"] == "Temporarily closed" and "temporarily closed" in shops[2]["why"]

    c = shop.c
    number = buy(c, shop, "LK-100", 7)
    page = html.unescape(c.get(f"/order/{number}", params={"session_id": "cs_test_7"}).text)
    assert "Near Garage" in page and " mi<" in page and "Temporarily closed" in page and "Unavailable" in page
    assert "Gone Garage" not in page
    assert "4.7" in page and "(1,212)" in page and "Map, Phone &amp; Hours" in c.get(f"/order/{number}", params={"session_id": "cs_test_7"}).text
    assert page.index("Need it installed?") < page.index("Summary")      # shops sit right under the items


def test_miles_and_zip_point():
    assert installers.zip_point("bad") is None
    a = installers.zip_point("10001")
    assert a and abs(a[0] - 40.75) < 0.1
    assert 180 < installers.miles(a, (42.36, -71.06)) < 200                # NYC to Boston, about 190 mi straight line
