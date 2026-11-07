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
    # only the Pro fields are asked for (keeps it in the cheaper Google tier)
    assert all("rating" not in (fm or "") and "phone" not in (fm or "") for _, _, fm in google.calls)

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
